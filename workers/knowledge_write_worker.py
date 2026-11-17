"""Knowledge Write Worker — consumes knowledge_write_jobs queue.

This worker runs as a separate process and executes durable write operations
that were submitted as jobs.  It ensures all writes go through validated
domain services, preventing duplicate logic and data inconsistency.

Usage:
    python -m workers.knowledge_write_worker --config config.yaml --daemon

Architecture:
    1. All write operations submit a Job to knowledge_write_jobs table
    2. This worker polls the queue (status='pending')
    3. Worker calls the appropriate domain service (agents/*)
    4. Job status updated: pending → running → completed (or failed)
    5. Failed jobs can be retried or escalated to repair_needed
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from agents.folder_service import rename_folder
from db.schema import init_db
from db.write_job_repo import KnowledgeWriteJobRepo

logger = logging.getLogger(__name__)

# Worker configuration
WORKER_POLL_INTERVAL_SECONDS = 2.0
WORKER_MAX_RETRY_COUNT = 3
WORKER_BATCH_SIZE = 10
WORKER_SHUTDOWN_GRACE_SECONDS = 30

# Global shutdown flag
_shutdown_requested = False


def _signal_handler(signum, frame):
    """Handle graceful shutdown on SIGTERM/SIGINT."""
    global _shutdown_requested
    _shutdown_requested = True
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")


def load_worker_config(config_path: str) -> dict[str, Any]:
    """Load worker configuration from YAML."""
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    
    vault = cfg.get("vault", {})
    database = cfg.get("database", {})
    
    return {
        "vault_path": vault.get("path", "~/Hermes/knowledge"),
        "db_path": database.get("path", "./data/captures.db"),
        "poll_interval": cfg.get("worker", {}).get("poll_interval_seconds", WORKER_POLL_INTERVAL_SECONDS),
        "max_retry": cfg.get("worker", {}).get("max_retry_count", WORKER_MAX_RETRY_COUNT),
        "batch_size": cfg.get("worker", {}).get("batch_size", WORKER_BATCH_SIZE),
    }


class KnowledgeWriteWorker:
    """Worker that processes knowledge_write_jobs queue."""
    
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.vault_path = Path(config["vault_path"]).expanduser()
        self.db_path = config["db_path"]
        self.poll_interval = config["poll_interval"]
        self.max_retry = config["max_retry"]
        self.batch_size = config["batch_size"]
        self.conn: sqlite3.Connection | None = None
        self.repo: KnowledgeWriteJobRepo | None = None
        
    def start(self):
        """Start the worker main loop."""
        logger.info(
            f"Starting Knowledge Write Worker: "
            f"vault={self.vault_path}, db={self.db_path}, "
            f"poll_interval={self.poll_interval}s"
        )
        
        self.conn = init_db(self.db_path)
        self.repo = KnowledgeWriteJobRepo(self.conn)
        
        try:
            self._run_loop()
        except KeyboardInterrupt:
            logger.info("Worker interrupted by user")
        finally:
            self._shutdown()
    
    def _run_loop(self):
        """Main processing loop."""
        global _shutdown_requested
        
        while not _shutdown_requested:
            try:
                processed = self._process_batch()
                if processed == 0:
                    # No jobs available, wait before next poll
                    time.sleep(self.poll_interval)
                else:
                    logger.info(f"Processed {processed} jobs in this batch")
            except Exception as exc:
                logger.exception("Unexpected error in worker loop")
                time.sleep(self.poll_interval)
    
    def _process_batch(self) -> int:
        """Process one batch of pending jobs."""
        if not self.conn or not self.repo:
            return 0

        # Retried jobs go back to 'retrying'. Only this intent belongs to this
        # worker; other bridges use the table as an audit trail and complete
        # their rows inline.
        rows = self.conn.execute(
            """SELECT id, intent, object_type, object_id, payload_json, retry_count
               FROM knowledge_write_jobs
               WHERE status IN ('pending', 'retrying')
                 AND intent = 'rename_folder'
               ORDER BY created_at ASC
               LIMIT ?""",
            (self.batch_size,)
        ).fetchall()
        
        if not rows:
            return 0
        
        processed = 0
        for row in rows:
            if _shutdown_requested:
                logger.info("Shutdown requested, stopping batch processing")
                break
            
            job_id = str(row["id"])
            intent = str(row["intent"])
            object_type = str(row["object_type"])
            object_id = str(row["object_id"])
            payload_json = str(row["payload_json"] or "{}")
            retry_count = int(row["retry_count"] or 0)
            
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                self.repo.fail(
                    job_id,
                    step="parse_payload",
                    error="Invalid payload JSON",
                    status="failed",
                )
                processed += 1
                continue
            
            # Atomic claim: a second worker must not run the same rename.
            if not self.repo.claim(job_id):
                continue

            # Execute the job
            try:
                self._execute_job(job_id, intent, object_type, object_id, payload)
                processed += 1
            except Exception as exc:
                logger.exception(f"Job {job_id} failed: {exc}")
                
                if retry_count < self.max_retry:
                    self.repo.mark_retrying(
                        job_id,
                        step="execution_failed",
                        error=str(exc),
                    )
                else:
                    self.repo.repair_needed(
                        job_id,
                        step="max_retries_exceeded",
                        error=f"Failed after {retry_count} retries: {exc}",
                    )
                processed += 1
        
        return processed
    
    def _execute_job(
        self,
        job_id: str,
        intent: str,
        object_type: str,
        object_id: str,
        payload: dict[str, Any],
    ):
        """Execute a single job based on its intent."""
        if intent == "rename_folder":
            self._execute_rename_folder(job_id, payload)
        else:
            raise ValueError(f"Unknown job intent: {intent}")
    
    def _execute_rename_folder(self, job_id: str, payload: dict[str, Any]):
        """Execute folder rename job."""
        if not self.conn or not self.repo:
            raise RuntimeError("Worker not initialized")

        old_folder = str(payload.get("old_folder", ""))
        new_folder = str(payload.get("new_folder", ""))
        
        if not old_folder or not new_folder:
            self.repo.fail(
                job_id,
                step="validate_payload",
                error="Missing old_folder or new_folder",
            )
            return
        
        logger.info(f"Job {job_id}: Renaming folder {old_folder!r} → {new_folder!r}")
        
        self.repo.mark_step(job_id, status="running", step="rename_folder")
        
        result = rename_folder(
            old_folder=old_folder,
            new_folder=new_folder,
            vault_path=self.vault_path,
            conn=self.conn,
        )
        
        if not result.success():
            error_msg = "; ".join(result.errors)
            self.repo.fail(
                job_id,
                step="rename_folder_failed",
                error=error_msg,
            )
            return
        
        # Record warnings in job metadata if any
        if result.warnings:
            logger.warning(
                f"Job {job_id} completed with warnings: {'; '.join(result.warnings)}"
            )
        
        self.repo.complete(job_id)
        logger.info(
            f"Job {job_id} completed: "
            f"{result.total_updated()} items updated "
            f"({result.updated_drafts} drafts, {result.updated_captures} captures, "
            f"{result.updated_garden_cards} garden cards)"
        )
    
    def _shutdown(self):
        """Clean shutdown: finish current batch, close connections."""
        logger.info("Worker shutting down...")
        
        if self.conn:
            # Give in-flight transactions time to complete
            time.sleep(1)
            self.conn.close()
            self.conn = None
        
        logger.info("Worker shutdown complete")


def main():
    """Worker entry point."""
    parser = argparse.ArgumentParser(description="Knowledge Write Worker")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run as daemon (continuous processing)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    
    # Load configuration
    try:
        config = load_worker_config(args.config)
    except Exception as exc:
        logger.error(f"Failed to load config from {args.config}: {exc}")
        sys.exit(1)
    
    # Start worker
    worker = KnowledgeWriteWorker(config)
    
    if args.daemon:
        logger.info("Starting worker in daemon mode")
        worker.start()
    else:
        logger.info("Processing pending jobs once and exiting")
        worker.conn = init_db(config["db_path"])
        worker.repo = KnowledgeWriteJobRepo(worker.conn)
        processed = worker._process_batch()
        logger.info(f"Processed {processed} jobs")
        worker._shutdown()


if __name__ == "__main__":
    main()

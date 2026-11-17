"""Knowledge write job submission for background folder operations.

A job is a row in ``knowledge_write_jobs``; ``workers.knowledge_write_worker``
polls the table, calls the folder domain service, and records the result.  The
HTTP folder-rename endpoint still runs synchronously, so this queue is opt-in
for batch or async renames, not a second live write path.

Usage:
    from bridge.knowledge_job_submit import submit_folder_rename_job

    job_id = submit_folder_rename_job(
        old_folder="AI工具",
        new_folder="AI产品",
        conn=conn,
    )
    # Check status: SELECT * FROM knowledge_write_jobs WHERE id = job_id
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from db.write_job_repo import KnowledgeWriteJobRepo


def submit_folder_rename_job(
    old_folder: str,
    new_folder: str,
    conn: sqlite3.Connection,
) -> str:
    """Submit a folder rename job to the queue.
    
    This replaces direct SQL/file operations with an async job submission.
    The actual rename is performed by knowledge_write_worker.
    
    Args:
        old_folder: Current folder name (e.g., "AI工具")
        new_folder: Target folder name (e.g., "AI产品")
        conn: SQLite connection
    
    Returns:
        Job ID that can be used to track progress
    
    Raises:
        ValueError: If folder names are invalid
    """
    if not old_folder or not new_folder:
        raise ValueError("Folder names cannot be empty")
    
    if not isinstance(old_folder, str) or not isinstance(new_folder, str):
        raise ValueError("Folder names must be strings")
    
    repo = KnowledgeWriteJobRepo(conn)
    payload = {
        "old_folder": old_folder.strip(),
        "new_folder": new_folder.strip(),
    }
    
    job_id = repo.start(
        intent="rename_folder",
        object_type="folder",
        object_id=old_folder.strip(),
        payload=payload,
    )
    
    return job_id


def get_job_status(
    job_id: str,
    conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    """Get the current status of a job.
    
    Args:
        job_id: Job ID from submit_* function
        conn: SQLite connection
    
    Returns:
        Job row as dict, or None if not found
    """
    row = conn.execute(
        """SELECT id, intent, status, step, error, retry_count, created_at, updated_at
           FROM knowledge_write_jobs
           WHERE id = ?""",
        (job_id,)
    ).fetchone()
    
    if row is None:
        return None
    
    return dict(row)


def wait_for_job(
    job_id: str,
    conn: sqlite3.Connection,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Wait for a job to complete or fail.
    
    Polls the job status until it reaches a terminal state
    (completed, failed, repair_needed).
    
    Args:
        job_id: Job ID from submit_* function
        conn: SQLite connection
        timeout_seconds: Maximum time to wait
    
    Returns:
        Final job status dict
    
    Raises:
        TimeoutError: If job doesn't complete within timeout
        RuntimeError: If job ends in failed/repair_needed state
    """
    import time
    
    terminal_states = {"completed", "failed", "repair_needed"}
    start = time.time()
    
    while True:
        status = get_job_status(job_id, conn)
        if status is None:
            raise ValueError(f"Job {job_id} not found")
        
        if status["status"] in terminal_states:
            if status["status"] == "failed":
                raise RuntimeError(
                    f"Job failed at step {status['step']}: {status['error']}"
                )
            if status["status"] == "repair_needed":
                raise RuntimeError(
                    f"Job needs repair at step {status['step']}: {status['error']}"
                )
            return status
        
        elapsed = time.time() - start
        if elapsed > timeout_seconds:
            raise TimeoutError(
                f"Job {job_id} did not complete within {timeout_seconds}s "
                f"(current status: {status['status']})"
            )
        
        time.sleep(0.5)

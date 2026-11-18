"""Integration tests for knowledge write jobs."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from agents.folder_service import rename_folder
from bridge.knowledge_job_submit import (
    submit_folder_rename_job,
    get_job_status,
    wait_for_job,
)
from db.schema import init_db
from workers.knowledge_write_worker import KnowledgeWriteWorker


@pytest.fixture
def integration_setup(tmp_path):
    """Set up vault, DB, and test data for integration tests."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "captures").mkdir()
    
    # Create test capture file
    capture_path = vault / "captures" / "test-capture.md"
    frontmatter = {
        "id": "capture-1",
        "title": "Test Capture",
        "folder_path": "OldFolder",
        "tags": ["test"],
    }
    body = "Test body content."
    yaml_text = yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
    capture_path.write_text(f"---\n{yaml_text}---\n\n{body}", encoding="utf-8")
    
    # Create database
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)

    # Seed the folder registry the Library reads.
    (vault / ".hermes").mkdir()
    (vault / ".hermes" / "knowledge_folders.json").write_text(
        json.dumps({"folders": ["OldFolder"], "hidden": []}),
        encoding="utf-8",
    )
    
    # Insert test data
    conn.execute(
        """INSERT INTO capture_drafts (id, raw_text, capture_intent, status)
           VALUES (?, ?, ?, ?)""",
        ("draft-1", "Content", json.dumps({"folder_path": "OldFolder"}), "draft_ready"),
    )
    conn.execute(
        """INSERT INTO captures (id, file_path)
           VALUES (?, ?)""",
        ("capture-1", "captures/test-capture.md"),
    )
    conn.execute(
        """INSERT INTO garden_cards (id, title, source_theme, status)
           VALUES (?, ?, ?, ?)""",
        ("card-1", "Test Card", "OldFolder", "seed"),
    )
    conn.commit()
    
    return {
        "vault": vault,
        "db_path": db_path,
        "conn": conn,
    }


def test_submit_folder_rename_job(integration_setup):
    """Test submitting a folder rename job."""
    conn = integration_setup["conn"]
    
    job_id = submit_folder_rename_job("OldFolder", "NewFolder", conn)
    
    assert job_id is not None
    
    # Verify job was inserted
    status = get_job_status(job_id, conn)
    assert status is not None
    assert status["intent"] == "rename_folder"
    assert status["status"] == "pending"


def test_worker_processes_rename_job(integration_setup):
    """Test that worker successfully processes a rename job."""
    vault = integration_setup["vault"]
    db_path = integration_setup["db_path"]
    conn = integration_setup["conn"]
    
    # Submit job
    job_id = submit_folder_rename_job("OldFolder", "NewFolder", conn)
    
    # Create worker and process once
    config = {
        "vault_path": str(vault),
        "db_path": db_path,
        "poll_interval": 1.0,
        "max_retry": 3,
        "batch_size": 10,
    }
    worker = KnowledgeWriteWorker(config)
    worker.conn = conn
    worker.repo = worker.repo or __import__("db.write_job_repo", fromlist=["KnowledgeWriteJobRepo"]).KnowledgeWriteJobRepo(conn)
    
    # Process the batch
    processed = worker._process_batch()
    assert processed == 1
    
    # Check job completed
    status = get_job_status(job_id, conn)
    assert status["status"] == "completed"
    
    # Verify data was actually updated
    draft_row = conn.execute(
        "SELECT capture_intent FROM capture_drafts WHERE id = ?",
        ("draft-1",),
    ).fetchone()
    intent = json.loads(draft_row["capture_intent"])
    assert intent["folder_path"] == "NewFolder"
    
    # Verify Markdown file was updated
    capture_file = vault / "captures" / "test-capture.md"
    content = capture_file.read_text(encoding="utf-8")
    assert "NewFolder" in content
    
    # Verify garden card was updated
    card_row = conn.execute(
        "SELECT source_theme FROM garden_cards WHERE id = ?",
        ("card-1",),
    ).fetchone()
    assert card_row["source_theme"] == "NewFolder"

    # Verify the folder registry moved with it (Library reads this file)
    store = json.loads(
        (vault / ".hermes" / "knowledge_folders.json").read_text(encoding="utf-8")
    )
    assert "NewFolder" in store["folders"]
    assert "OldFolder" not in store["folders"]


def test_worker_picks_up_retrying_jobs(integration_setup):
    """A job parked in 'retrying' must be picked up again by the next batch."""
    vault = integration_setup["vault"]
    db_path = integration_setup["db_path"]
    conn = integration_setup["conn"]

    job_id = submit_folder_rename_job("OldFolder", "NewFolder", conn)
    conn.execute(
        "UPDATE knowledge_write_jobs SET status = 'retrying', retry_count = 1 WHERE id = ?",
        (job_id,),
    )
    conn.commit()

    worker = KnowledgeWriteWorker(
        {
            "vault_path": str(vault),
            "db_path": db_path,
            "poll_interval": 1.0,
            "max_retry": 3,
            "batch_size": 10,
        }
    )
    worker.conn = conn
    worker.repo = worker.repo or __import__(
        "db.write_job_repo", fromlist=["KnowledgeWriteJobRepo"]
    ).KnowledgeWriteJobRepo(conn)

    assert worker._process_batch() == 1
    assert get_job_status(job_id, conn)["status"] == "completed"


def test_worker_recovers_running_job_only_when_owner_releases_lock(integration_setup):
    from bridge.process_lock import try_process_lock
    from db.write_job_repo import KnowledgeWriteJobRepo

    conn = integration_setup["conn"]
    db_path = integration_setup["db_path"]
    job_id = submit_folder_rename_job("OldFolder", "NewFolder", conn)
    worker = KnowledgeWriteWorker({
        "vault_path": str(integration_setup["vault"]),
        "db_path": db_path, "poll_interval": 1.0, "max_retry": 3, "batch_size": 10,
    })
    worker.conn = conn
    worker.repo = KnowledgeWriteJobRepo(conn)
    assert worker.repo.claim(job_id)
    with try_process_lock(Path(db_path).with_suffix(".rename-worker.lock")) as acquired:
        assert acquired
        assert worker._process_batch() == 0
        assert get_job_status(job_id, conn)["status"] == "running"
    assert worker._process_batch() == 1
    assert get_job_status(job_id, conn)["status"] == "completed"


def test_job_submission_validation(integration_setup):
    """Test job submission validation."""
    conn = integration_setup["conn"]
    
    # Empty folder names should raise ValueError
    with pytest.raises(ValueError, match="cannot be empty"):
        submit_folder_rename_job("", "NewFolder", conn)
    
    with pytest.raises(ValueError, match="cannot be empty"):
        submit_folder_rename_job("OldFolder", "", conn)


def test_get_job_status_not_found(integration_setup):
    """Test getting status for non-existent job."""
    conn = integration_setup["conn"]
    
    status = get_job_status("non-existent-job-id", conn)
    assert status is None


def test_wait_for_job_timeout(integration_setup):
    """Test wait_for_job timeout."""
    conn = integration_setup["conn"]
    
    # Submit job
    job_id = submit_folder_rename_job("OldFolder", "NewFolder", conn)
    
    # Wait with very short timeout (job won't complete)
    with pytest.raises(TimeoutError, match="did not complete"):
        wait_for_job(job_id, conn, timeout_seconds=0.1)


def test_worker_handles_partial_failure(integration_setup):
    """Test that worker handles partial failures gracefully."""
    # Note: folder_service treats missing files as warnings, not hard errors
    # So we test that warnings are logged but job still completes
    vault = integration_setup["vault"]
    db_path = integration_setup["db_path"]
    conn = integration_setup["conn"]
    
    # Remove the capture file to simulate partial failure
    capture_file = vault / "captures" / "test-capture.md"
    capture_file.unlink()
    
    # Submit job
    job_id = submit_folder_rename_job("OldFolder", "NewFolder", conn)
    
    # Create worker
    config = {
        "vault_path": str(vault),
        "db_path": db_path,
        "poll_interval": 1.0,
        "max_retry": 3,
        "batch_size": 10,
    }
    worker = KnowledgeWriteWorker(config)
    worker.conn = conn
    worker.repo = __import__("db.write_job_repo", fromlist=["KnowledgeWriteJobRepo"]).KnowledgeWriteJobRepo(conn)
    
    # Process (should complete with warnings)
    worker._process_batch()
    status = get_job_status(job_id, conn)
    assert status["status"] == "completed"
    
    # Verify draft and garden card were still updated
    draft_row = conn.execute(
        "SELECT capture_intent FROM capture_drafts WHERE id = ?",
        ("draft-1",),
    ).fetchone()
    intent = json.loads(draft_row["capture_intent"])
    assert intent["folder_path"] == "NewFolder"
    
    card_row = conn.execute(
        "SELECT source_theme FROM garden_cards WHERE id = ?",
        ("card-1",),
    ).fetchone()
    assert card_row["source_theme"] == "NewFolder"


def test_claim_is_exclusive(integration_setup):
    conn = integration_setup["conn"]
    job_id = submit_folder_rename_job("OldFolder", "NewFolder", conn)
    from db.write_job_repo import KnowledgeWriteJobRepo

    repo = KnowledgeWriteJobRepo(conn)
    assert repo.claim(job_id) is True
    assert repo.claim(job_id) is False


def test_worker_ignores_other_intents(integration_setup):
    from db.write_job_repo import KnowledgeWriteJobRepo

    vault = integration_setup["vault"]
    conn = integration_setup["conn"]
    repo = KnowledgeWriteJobRepo(conn)
    job_id = repo.start(
        intent="run_theme_scan", object_type="theme_scan", object_id="latest"
    )

    worker = KnowledgeWriteWorker(
        {
            "vault_path": str(vault),
            "db_path": integration_setup["db_path"],
            "poll_interval": 1.0,
            "max_retry": 3,
            "batch_size": 10,
        }
    )
    worker.conn = conn
    worker.repo = repo

    assert worker._process_batch() == 0
    assert get_job_status(job_id, conn)["status"] == "pending"

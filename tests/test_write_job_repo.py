import json

from db.schema import init_db
from db.write_job_repo import KnowledgeWriteJobRepo


def test_write_job_repo_records_lifecycle(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = KnowledgeWriteJobRepo(conn)

    job_id = repo.start(
        intent="commit_capture_draft",
        object_type="capture_draft",
        object_id="d1",
        payload={"draft_id": "d1"},
    )
    repo.mark_step(job_id, status="writing_file", step="write_capture_file", file_path="captures/a.md")
    repo.complete(job_id, db_table="captures")

    row = conn.execute("SELECT * FROM knowledge_write_jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "completed"
    assert row["step"] == "completed"
    assert row["file_path"] == "captures/a.md"
    assert row["db_table"] == "captures"
    assert json.loads(row["payload_json"]) == {"draft_id": "d1"}


def test_write_job_repo_records_failure(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = KnowledgeWriteJobRepo(conn)

    job_id = repo.start(
        intent="commit_garden_draft",
        object_type="garden_draft",
        object_id="g1",
    )
    repo.fail(job_id, step="write_card_file", error="disk full")

    row = conn.execute("SELECT * FROM knowledge_write_jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["step"] == "write_card_file"
    assert row["error"] == "disk full"


def test_write_job_repo_records_retrying_state(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = KnowledgeWriteJobRepo(conn)

    job_id = repo.start(
        intent="commit_capture_draft",
        object_type="capture_draft",
        object_id="d1",
    )
    repo.mark_retrying(job_id, step="identity_embedding", error="rate limited")

    row = conn.execute("SELECT * FROM knowledge_write_jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "retrying"
    assert row["step"] == "identity_embedding"
    assert row["error"] == "rate limited"
    assert row["retry_count"] == 1


def test_write_job_repo_records_repair_needed(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = KnowledgeWriteJobRepo(conn)

    job_id = repo.start(
        intent="commit_garden_draft",
        object_type="garden_draft",
        object_id="g1",
    )
    repo.repair_needed(
        job_id,
        step="write_card_file",
        error="file write failed",
        file_path="garden/card.md",
        db_table="garden_cards",
    )

    row = conn.execute("SELECT * FROM knowledge_write_jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "repair_needed"
    assert row["step"] == "write_card_file"
    assert row["error"] == "file write failed"
    assert row["file_path"] == "garden/card.md"
    assert row["db_table"] == "garden_cards"

import json
from datetime import datetime, timedelta, timezone

from db.agent_run_repo import AgentRunRepo
from db.schema import init_db


def test_agent_run_repo_records_success_lifecycle(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = AgentRunRepo(conn)

    run = repo.start_run(
        agent="garden",
        intent="synthesize_garden",
        trigger="wechat",
        input_ref={"capture_ids": ["c1", "c2"]},
    )
    repo.record_event(
        run["id"],
        event_type="tool_call",
        payload={"tool": "synthesize_garden"},
    )
    repo.complete_run(
        run["id"],
        output_ref={"draft_id": "d1"},
        evidence={"draft_created": True, "capture_count": 2},
        warnings=[],
    )

    latest = repo.latest(limit=1)[0]
    assert latest["id"] == run["id"]
    assert latest["status"] == "success"
    assert json.loads(latest["input_json"]) == {"capture_ids": ["c1", "c2"]}
    assert json.loads(latest["output_json"]) == {"draft_id": "d1"}
    assert json.loads(latest["evidence_json"])["draft_created"] is True

    events = repo.events(run["id"])
    assert len(events) == 1
    assert events[0]["event_type"] == "tool_call"


def test_agent_run_repo_marks_error(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = AgentRunRepo(conn)

    run = repo.start_run(
        agent="rag",
        intent="search_knowledge_base",
        trigger="wechat",
        input_ref={"query": "AI"},
    )
    repo.fail_run(run["id"], errors=["boom"], evidence={"has_envelope": False})

    latest = repo.latest(limit=1)[0]
    assert latest["status"] == "error"
    assert json.loads(latest["errors_json"]) == ["boom"]


def test_start_run_recovers_stale_running_rows(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = AgentRunRepo(conn)

    stale = repo.start_run(
        agent="rag",
        intent="search_knowledge_base",
        trigger="wechat",
        input_ref={"query": "old"},
    )
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn.execute(
        "UPDATE agent_runs SET created_at = ?, updated_at = ? WHERE id = ?",
        (stale_at, stale_at, stale["id"]),
    )
    conn.commit()

    current = repo.start_run(
        agent="rag",
        intent="search_knowledge_base",
        trigger="wechat",
        input_ref={"query": "new"},
    )

    stale_row = conn.execute(
        "SELECT * FROM agent_runs WHERE id = ?",
        (stale["id"],),
    ).fetchone()
    current_row = conn.execute(
        "SELECT * FROM agent_runs WHERE id = ?",
        (current["id"],),
    ).fetchone()
    assert stale_row["status"] == "error"
    assert stale_row["completed_at"] is not None
    assert json.loads(stale_row["errors_json"]) == [
        "stale running run recovered after exceeding 3600 seconds"
    ]
    assert current_row["status"] == "running"
    assert [event["event_type"] for event in repo.events(stale["id"])] == [
        "recovered_stale"
    ]


def test_agent_run_repo_records_object_refs(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    repo = AgentRunRepo(conn)

    run = repo.start_run(
        agent="garden",
        intent="synthesize_garden",
        trigger="wechat",
        input_ref={"capture_ids": ["c1"]},
    )
    repo.record_object_refs(
        run["id"],
        [
            {
                "object_type": "capture",
                "object_id": "c1",
                "role": "source",
                "source": "meta.capture_ids",
            },
            {
                "object_type": "capture",
                "object_id": "c1",
                "role": "source",
                "source": "duplicate",
            },
            {
                "object_type": "garden_draft",
                "object_id": "d1",
                "role": "subject",
                "source": "durable_artifacts",
            },
        ],
    )

    rows = repo.object_refs(run["id"])
    assert [(row["object_type"], row["object_id"], row["role"]) for row in rows] == [
        ("capture", "c1", "source"),
        ("garden_draft", "d1", "subject"),
    ]

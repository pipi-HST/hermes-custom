import json

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

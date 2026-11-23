"""Contract v1 endpoints: ``GET /api/health`` and ``GET /api/knowledge/search``."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import main as api_main
from api.routes.health import (
    KNOWLEDGE_CONTRACT_VERSION,
    KNOWLEDGE_SERVICE_NAME,
    create_health_router,
)
from db.repo import CaptureRepo
from db.schema import init_db

LOOPBACK = ("127.0.0.1", 50000)


def _make_app(tmp_path, *, vault_name: str = "vault"):
    return api_main.create_app(
        config=SimpleNamespace(
            db_path=str(tmp_path / "captures.db"),
            vault_path=str(tmp_path / vault_name),
        )
    )


def _write_note(
    vault: Path,
    relative_path: str,
    *,
    title: str,
    story: str,
    body: str,
) -> None:
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"title: {title}\n"
        f"story: {story}\n"
        "tags: [knowledge]\n"
        "source: https://example.test/article\n"
        "source_type: web\n"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )


def _add_capture(
    db_path: str,
    vault: Path,
    *,
    note_name: str,
    title: str,
    story: str,
    body: str,
) -> str:
    conn = init_db(db_path)
    try:
        capture = CaptureRepo(conn).insert(file_path=f"captures/{note_name}")
    finally:
        conn.close()
    _write_note(vault, f"captures/{note_name}", title=title, story=story, body=body)
    return capture["id"]


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------

def test_health_reports_current_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    (tmp_path / "vault").mkdir()
    app = _make_app(tmp_path)

    with TestClient(app, client=LOOPBACK) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == KNOWLEDGE_SERVICE_NAME == "hermes-knowledge"
    assert payload["contract_version"] == KNOWLEDGE_CONTRACT_VERSION
    assert payload["status"] == "ok"
    assert payload["checks"] == {"database": "ok", "vault": "ok"}
    assert isinstance(payload["version"], str) and payload["version"]
    # Health must not leak configured absolute paths.
    assert str(tmp_path) not in response.text


def test_health_degrades_when_database_is_unavailable(tmp_path):
    class BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("database is gone")

    app = FastAPI()
    app.include_router(create_health_router(BrokenConnection(), tmp_path), prefix="/api")

    with TestClient(app, client=LOOPBACK) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["checks"] == {"database": "unavailable", "vault": "ok"}
    assert payload["contract_version"] == KNOWLEDGE_CONTRACT_VERSION


def test_health_degrades_when_vault_is_missing(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    app = FastAPI()
    try:
        app.include_router(
            create_health_router(conn, tmp_path / "missing-vault"),
            prefix="/api",
        )
        with TestClient(app, client=LOOPBACK) as client:
            response = client.get("/api/health")
    finally:
        conn.close()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["checks"] == {"database": "ok", "vault": "unavailable"}


def test_selection_chat_service_builds_item_context(tmp_path, monkeypatch):
    (tmp_path / "vault").mkdir()
    app = _make_app(tmp_path)
    capture_id = _add_capture(
        str(tmp_path / "captures.db"),
        tmp_path / "vault",
        note_name="selection.md",
        title="Selection source",
        story="summary",
        body="正文内容",
    )
    import bridge.research_bridge as research_bridge

    captured = {}

    async def fake_answer(**kwargs):
        captured.update(kwargs)
        return {
            "status": "success",
            "chat_preview": "服务端回答",
            "source_map": [],
            "warnings": [],
            "meta": {},
        }

    monkeypatch.setattr(research_bridge, "async_web_assist_answer", fake_answer)

    with TestClient(app, client=LOOPBACK) as client:
        response = client.post(
            f"/api/knowledge/library/items/capture/{capture_id}/selection-chat",
            json={"question": "这是什么？", "mode": "local_web"},
        )

    assert response.status_code == 200, response.text
    assert captured["article_context"]["title"] == "Selection source"
    assert captured["query"] == "这是什么？"


def test_selection_chat_stream_forwards_answer_deltas(tmp_path, monkeypatch):
    (tmp_path / "vault").mkdir()
    app = _make_app(tmp_path)
    capture_id = _add_capture(
        str(tmp_path / "captures.db"),
        tmp_path / "vault",
        note_name="stream.md",
        title="Streaming source",
        story="summary",
        body="正文内容",
    )
    import bridge.research_bridge as research_bridge

    async def fake_answer(**kwargs):
        on_event = kwargs["event_callback"]
        await on_event({"type": "answer_delta", "delta": "第一段"})
        await on_event({"type": "answer_delta", "delta": "第二段"})
        return {
            "status": "success",
            "chat_preview": "第一段第二段",
            "source_map": [],
            "warnings": [],
            "meta": {"mode": kwargs["mode"]},
        }

    monkeypatch.setattr(research_bridge, "async_web_assist_answer", fake_answer)

    with TestClient(app, client=LOOPBACK) as client:
        response = client.post(
            f"/api/knowledge/library/items/capture/{capture_id}/selection-chat/stream",
            json={"question": "这是什么？", "mode": "local_web"},
        )

    assert response.status_code == 200, response.text
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["event"] for event in events] == ["progress", "progress", "complete"]
    assert events[0] == {"event": "progress", "type": "answer_delta", "delta": "第一段"}
    assert events[1] == {"event": "progress", "type": "answer_delta", "delta": "第二段"}
    assert events[-1]["result"]["chat_preview"] == "第一段第二段"


def test_ai_operation_cancel_service_route(tmp_path):
    (tmp_path / "vault").mkdir()
    app = _make_app(tmp_path)

    with TestClient(app, client=LOOPBACK) as client:
        response = client.post("/api/knowledge/ai-operations/op-test/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancel_requested"
    assert response.json()["operation_id"] == "op-test"


def test_ai_operation_context_reaches_a_service_worker(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from bridge import garden_bridge
    from bridge.ai_cancel import current_operation_id, raise_if_cancelled

    started = Event()
    resume = Event()

    def synthesize(**_kwargs):
        assert current_operation_id() == "garden-example"
        started.set()
        assert resume.wait(5)
        raise_if_cancelled("test.before_write")

    monkeypatch.setattr(garden_bridge, "synthesize_garden", synthesize)
    with TestClient(_make_app(tmp_path), client=LOOPBACK) as client, ThreadPoolExecutor(1) as executor:
        pending = executor.submit(
            client.post,
            "/api/garden/drafts/preview",
            headers={"X-Hermes-AI-Operation-Id": "garden-example"},
            json={"capture_ids": ["example"]},
        )
        try:
            assert started.wait(5)
            acknowledgement = client.post("/api/knowledge/ai-operations/garden-example/cancel")
            assert acknowledgement.json()["status"] == "cancel_requested"
            assert not pending.done()
        finally:
            resume.set()
        response = pending.result(timeout=5)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancelled_stage"] == "test.before_write"


# ---------------------------------------------------------------------------
# GET /api/knowledge/search
# ---------------------------------------------------------------------------

def _seed_search_captures(tmp_path) -> dict[str, str]:
    db_path = str(tmp_path / "captures.db")
    vault = tmp_path / "vault"
    return {
        "both": _add_capture(
            db_path,
            vault,
            note_name="agent.md",
            title="Agent 设计",
            story="agent workflow",
            body="RAG agent 的实现细节。",
        ),
        "one": _add_capture(
            db_path,
            vault,
            note_name="rag.md",
            title="RAG 入门",
            story="检索增强",
            body="与检索无关的正文。",
        ),
        "none": _add_capture(
            db_path,
            vault,
            note_name="recipe.md",
            title="菜谱",
            story="做饭",
            body="番茄炒蛋。",
        ),
    }


def test_search_returns_hydrated_ranked_items(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    ids = _seed_search_captures(tmp_path)
    app = _make_app(tmp_path)

    with TestClient(app, client=LOOPBACK) as client:
        response = client.get("/api/knowledge/search", params={"q": "RAG agent"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "RAG agent"
    assert payload["limit"] == 10
    assert payload["total"] == 2
    assert [item["id"] for item in payload["items"]] == [ids["both"], ids["one"]]

    top = payload["items"][0]
    for key in ("id", "title", "summary", "file_path", "source", "tags", "score"):
        assert key in top
    assert top["title"] == "Agent 设计"
    assert top["summary"] == "agent workflow"
    assert top["source"] == "https://example.test/article"
    assert top["tags"] == ["knowledge"]
    assert top["file_path"] == "captures/agent.md"
    assert top["score"] == 2
    assert payload["items"][1]["score"] == 1


def test_search_handles_no_match_limit_and_whitespace(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    _seed_search_captures(tmp_path)
    app = _make_app(tmp_path)

    with TestClient(app, client=LOOPBACK) as client:
        missing = client.get("/api/knowledge/search", params={"q": "不存在的内容"})
        limited = client.get(
            "/api/knowledge/search",
            params={"q": "agent", "limit": 1},
        )
        blank = client.get("/api/knowledge/search", params={"q": "   "})

    assert missing.status_code == 200
    assert missing.json()["items"] == []
    assert missing.json()["total"] == 0

    assert limited.status_code == 200
    assert limited.json()["limit"] == 1
    assert limited.json()["total"] == 1
    assert len(limited.json()["items"]) == 1

    assert blank.status_code == 200
    assert blank.json()["items"] == []


def test_search_validates_query_parameters(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    app = _make_app(tmp_path)

    with TestClient(app, client=LOOPBACK) as client:
        missing = client.get("/api/knowledge/search")
        empty = client.get("/api/knowledge/search", params={"q": ""})
        too_long = client.get(
            "/api/knowledge/search",
            params={"q": "x" * 201},
        )
        bad_limit = client.get(
            "/api/knowledge/search",
            params={"q": "agent", "limit": 0},
        )

    assert missing.status_code == 422
    assert empty.status_code == 422
    assert too_long.status_code == 422
    assert bad_limit.status_code == 422

def test_dashboard_read_routes_return_dashboard_shapes(tmp_path):
    from fastapi.testclient import TestClient

    from db.schema import init_db

    app = _make_app(tmp_path)
    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        "INSERT INTO capture_drafts (id, raw_text, draft_text, title, status, "
        "capture_intent, tags, word_count, created_at, updated_at) "
        "VALUES ('d1', 'raw', 'draft body', 'Draft', 'draft_ready', "
        "'{\"folder_path\":\"Topic\"}', '[\"tag\"]', 2, '2026-11-17T00:00:00Z', "
        "'2026-11-17T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO reading_drafts (id, session_id, status, title, created_at, updated_at) "
        "VALUES ('r1', 's1', 'open', 'Reading', '2026-11-17T00:00:00Z', "
        "'2026-11-17T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO garden_cards (id, title, source_theme, status, "
        "payload_json) VALUES ('c1', 'Card', 'Topic', 'seed', "
        "'{\"schema_version\":\"garden_card.v2\",\"claim\":\"claim\",\"card_kind\":\"method\"}')"
    )
    conn.execute(
        "INSERT INTO agent_runs (id, agent, intent, status, updated_at) "
        "VALUES ('run1', 'capture', 'capture', 'completed', '2026-11-17T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO agent_run_objects (run_id, object_type, object_id) "
        "VALUES ('run1', 'capture', 'cap1')"
    )
    conn.execute(
        "INSERT INTO captures (id, file_path) "
        "VALUES ('cap1', 'captures/cap1.md')"
    )
    conn.commit()
    conn.close()

    from db.repo import CaptureRepo

    client = TestClient(app, client=("127.0.0.1", 50000))

    drafts = client.get("/api/knowledge/capture-drafts").json()
    assert drafts["drafts"][0]["id"] == "d1"
    assert drafts["open_count"] == 1
    assert client.get("/api/knowledge/capture-drafts/d1").json()["draft"]["id"] == "d1"

    reading = client.get("/api/knowledge/reading-drafts").json()
    assert reading["drafts"][0]["id"] == "r1"
    assert client.get("/api/knowledge/reading-drafts/r1").json()["draft"]["id"] == "r1"

    card = client.get("/api/knowledge/cards/c1").json()["card"]
    assert card["id"] == "c1"
    assert card["source_theme"] == "Topic"

    events = client.get(
        "/api/knowledge/object-events",
        params={"object_type": "capture", "object_id": "cap1"},
    ).json()
    assert events["related_ids"] == ["cap1"]
    assert events["events"][0]["id"] == "run1"

    deps = client.get(
        "/api/knowledge/library/items/capture/cap1/dependencies"
    ).json()
    assert deps["ok"] is True
    assert "garden_cards" in deps["dependencies"]
    assert "policy" in deps["dependencies"]

"""Contract v1 endpoints: ``GET /api/health`` and ``GET /api/knowledge/search``."""
from __future__ import annotations

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

def test_health_reports_contract_v1(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    (tmp_path / "vault").mkdir()
    app = _make_app(tmp_path)

    with TestClient(app, client=LOOPBACK) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == KNOWLEDGE_SERVICE_NAME == "hermes-knowledge"
    assert payload["contract_version"] == KNOWLEDGE_CONTRACT_VERSION == 1
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
    assert payload["contract_version"] == 1


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

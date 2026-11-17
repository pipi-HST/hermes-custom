"""Tests for the reading and knowledge API routes."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db.schema import init_db
from db.repo import CaptureRepo, CaptureDraftRepo
from db.reading_repo import ReadingRepo
from agents.markdown_writer import read_note
from api.routes.reading import create_reading_router
from api.routes.knowledge import create_knowledge_router
from api.routes.garden import create_garden_router
from api.routes.capture_drafts import create_capture_draft_router
from api.routes.captures import create_capture_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    yield conn
    conn.close()


@pytest.fixture
def reading_repo(db):
    return ReadingRepo(db)


@pytest.fixture
def capture_repo(db):
    return CaptureRepo(db)


@pytest.fixture
def draft_repo(db):
    return CaptureDraftRepo(db)


@pytest.fixture
def client(tmp_path, reading_repo, capture_repo, draft_repo):
    app = FastAPI()
    app.include_router(
        create_reading_router(reading_repo, {"vault_path": str(tmp_path)}),
        prefix="/api",
    )
    app.include_router(
        create_knowledge_router(reading_repo, capture_repo, str(tmp_path)),
        prefix="/api",
    )
    app.include_router(
        create_capture_router(capture_repo, str(tmp_path)),
        prefix="/api",
    )
    app.include_router(
        create_capture_draft_router(draft_repo),
        prefix="/api",
    )
    app.include_router(
        create_garden_router(capture_repo, str(tmp_path)),
        prefix="/api",
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Reading session tests
# ---------------------------------------------------------------------------

def test_list_reading_sessions_empty(client):
    """GET /api/reading/sessions returns empty items when no sessions exist."""
    resp = client.get("/api/reading/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_get_reading_session_not_found(client):
    """GET /api/reading/sessions/{id} returns 404 for a nonexistent session."""
    resp = client.get("/api/reading/sessions/doesnotexist")
    assert resp.status_code == 404


def test_get_reading_session_with_turns(client, reading_repo):
    """GET /api/reading/sessions/{id} returns session enriched with turns and evidence."""
    session = reading_repo.create_session(
        source_type="url",
        source_url="https://example.com/article",
        source_title="Test Article",
        focus="testing",
    )
    turn = reading_repo.add_turn(
        session["id"],
        user_message="What is the main point?",
        assistant_answer="The main point is about testing.",
        focus="main idea",
        confidence="high",
    )
    reading_repo.add_evidence(
        session_id=session["id"],
        turn_id=turn["id"],
        source_kind="url",
        title="Evidence Source",
        url="https://example.com/evidence",
        excerpt="Key excerpt from the source.",
    )

    resp = client.get(f"/api/reading/sessions/{session['id']}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["id"] == session["id"]
    assert body["source_title"] == "Test Article"
    assert body["source_type"] == "url"
    assert body["focus"] == "testing"

    # Turns are included
    assert len(body["turns"]) == 1
    assert body["turns"][0]["user_message"] == "What is the main point?"
    assert body["turns"][0]["assistant_answer"] == "The main point is about testing."
    assert body["turns"][0]["confidence"] == "high"

    # Evidence is included
    assert len(body["evidence"]) == 1
    assert body["evidence"][0]["source_kind"] == "url"
    assert body["evidence"][0]["excerpt"] == "Key excerpt from the source."


# ---------------------------------------------------------------------------
# Reading draft tests
# ---------------------------------------------------------------------------

def test_list_reading_drafts_empty(client):
    """GET /api/reading/drafts returns empty items when no drafts exist."""
    resp = client.get("/api/reading/drafts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []


def test_get_reading_draft_detail(client, reading_repo):
    """GET /api/reading/drafts/{id} returns the draft created via repo."""
    session = reading_repo.create_session(source_type="url")
    draft = reading_repo.create_draft(
        session_id=session["id"],
        output_type="summary",
        title="Draft Title",
        summary="A brief summary.",
        draft_text="Full draft content here.",
    )

    resp = client.get(f"/api/reading/drafts/{draft['id']}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["id"] == draft["id"]
    assert body["title"] == "Draft Title"
    assert body["summary"] == "A brief summary."
    assert body["draft_text"] == "Full draft content here."
    assert body["output_type"] == "summary"
    assert body["status"] == "open"


# ---------------------------------------------------------------------------
# Knowledge route tests
# ---------------------------------------------------------------------------

def test_list_knowledge_drafts(client, reading_repo):
    """GET /api/knowledge/drafts returns open reading drafts."""
    session = reading_repo.create_session(source_type="url")
    reading_repo.create_draft(
        session_id=session["id"],
        output_type="local_learning_note",
        title="Knowledge Draft",
        draft_text="Some learning content.",
    )

    resp = client.get("/api/knowledge/drafts")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "Knowledge Draft"
    assert body["items"][0]["status"] == "open"


def test_get_knowledge_draft(client, reading_repo):
    """GET /api/knowledge/drafts/{id} returns a specific draft."""
    session = reading_repo.create_session(source_type="url")
    draft = reading_repo.create_draft(
        session_id=session["id"],
        output_type="knowledge_card",
        title="Specific Draft",
        draft_text="Detailed content.",
    )

    resp = client.get(f"/api/knowledge/drafts/{draft['id']}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["id"] == draft["id"]
    assert body["title"] == "Specific Draft"
    assert body["output_type"] == "knowledge_card"


def test_list_garden_cards_empty(client):
    """GET /api/knowledge/garden-cards returns empty items when no cards exist."""
    resp = client.get("/api/knowledge/garden-cards")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []


def test_knowledge_graph_refresh_and_focus(client, db, tmp_path):
    (tmp_path / "captures").mkdir()
    (tmp_path / "captures" / "one.md").write_text(
        "---\ntitle: 材料一\nstory: 第一篇材料\n---\n\n正文",
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, ?)",
        ("cap1", "captures/one.md", "2026-06-01T00:00:00+00:00"),
    )
    db.execute(
        """INSERT INTO garden_cards
           (id, title, payload_json, capture_ids, status)
           VALUES (?, ?, ?, ?, 'seed')""",
        (
            "card1",
            "知识卡片",
            json.dumps(
                {
                    "schema_version": "garden_card.v2",
                    "title": "知识卡片",
                    "claim": "卡片摘要",
                    "card_kind": "viewpoint",
                    "source_ids": ["cap1"],
                },
                ensure_ascii=False,
            ),
            '["cap1"]',
        ),
    )
    db.commit()

    refresh = client.post("/api/knowledge/graph/refresh")
    assert refresh.status_code == 200
    assert refresh.json()["relation_counts"]["card_uses_capture"] == 1

    resp = client.get("/api/knowledge/graph?focus_kind=capture&focus_id=cap1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["focus_id"] == "cap1"
    assert any(node["id"] == "cap1" and node["open_url"] for node in body["nodes"])
    assert any(edge["relation_type"] == "card_uses_capture" for edge in body["edges"])


# ---------------------------------------------------------------------------
# Capture note tests
# ---------------------------------------------------------------------------

def test_update_capture_note_backs_up_and_runs_library(
    client,
    tmp_path,
    capture_repo,
    monkeypatch,
):
    """PATCH /api/captures/{id} updates Capture Note, creates backup, and maintains Library."""
    capture = capture_repo.insert(
        file_path="captures/test-note.md",
        raw_path="_archive/raw/test-note.md",
    )
    note_path = tmp_path / "captures" / "test-note.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text(
        "---\n"
        f"id: {capture['id']}\n"
        "title: Old Title\n"
        "tags: []\n"
        "story: Old summary\n"
        "word_count: 8\n"
        "---\n\n"
        "Old body",
        encoding="utf-8",
    )
    library_calls: list[tuple[str, bool]] = []

    class FakeLibraryAgent:
        def __init__(self, *_args, **_kwargs):
            pass

        def maintain_capture(self, capture_id: str, *, apply: bool = False):
            library_calls.append((capture_id, apply))
            return {"embedding_index": {"updated": 1}, "outlines": {"updated": 0}}

    monkeypatch.setattr("api.routes.captures.LibraryAgent", FakeLibraryAgent)

    resp = client.patch(
        f"/api/captures/{capture['id']}",
        json={"body": "New edited body for RAG", "title": "User Title"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["backup_path"].startswith("_archive/write_backups/captures/")
    assert (tmp_path / body["backup_path"]).exists()
    assert library_calls == [(capture["id"], True)]
    updated_note = read_note(tmp_path, "captures/test-note.md")
    assert updated_note is not None
    frontmatter, note_body = updated_note
    assert frontmatter["title"] == "User Title"
    assert frontmatter["title_source"] == "user"
    assert frontmatter["word_count"] == len("New edited body for RAG")
    assert note_body == "New edited body for RAG"


def test_capture_list_hydrates_and_filters_folder_path(client, tmp_path, capture_repo):
    """GET /api/captures returns logical folder metadata from frontmatter."""
    capture_repo.insert(file_path="captures/inbox.md")
    product_capture = capture_repo.insert(file_path="captures/product.md")
    (tmp_path / "captures").mkdir(parents=True)
    (tmp_path / "captures" / "inbox.md").write_text(
        "---\n"
        "title: Inbox Note\n"
        "folder_path: Inbox\n"
        "tags: []\n"
        "---\n\n"
        "Inbox body",
        encoding="utf-8",
    )
    (tmp_path / "captures" / "product.md").write_text(
        "---\n"
        "title: Product Note\n"
        "folder_path: AI 产品\n"
        "tags: []\n"
        "---\n\n"
        "Product body",
        encoding="utf-8",
    )

    resp = client.get("/api/captures", params={"folder": "AI 产品"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == product_capture["id"]
    assert body["items"][0]["folder_path"] == "AI 产品"
    assert body["items"][0]["body"] == "Product body"


def test_update_capture_can_change_folder_path_without_replacing_body(
    client,
    tmp_path,
    capture_repo,
    monkeypatch,
):
    """PATCH /api/captures/{id} can update logical folder metadata."""
    capture = capture_repo.insert(file_path="captures/movable.md")
    note_path = tmp_path / "captures" / "movable.md"
    note_path.parent.mkdir(parents=True)
    note_path.write_text(
        "---\n"
        "title: Movable\n"
        "folder_path: Inbox\n"
        "tags: []\n"
        "---\n\n"
        "Keep this body",
        encoding="utf-8",
    )

    class FakeLibraryAgent:
        def __init__(self, *_args, **_kwargs):
            pass

        def maintain_capture(self, capture_id: str, *, apply: bool = False):
            return {"embedding_index": {"updated": 0}}

    monkeypatch.setattr("api.routes.captures.LibraryAgent", FakeLibraryAgent)

    resp = client.patch(
        f"/api/captures/{capture['id']}",
        json={"folder_path": "数据 Agent"},
    )

    assert resp.status_code == 200
    updated_note = read_note(tmp_path, "captures/movable.md")
    assert updated_note is not None
    frontmatter, note_body = updated_note
    assert frontmatter["folder_path"] == "数据 Agent"
    assert note_body == "Keep this body"


# ---------------------------------------------------------------------------
# Bridge-dependent tests (mocked)
# ---------------------------------------------------------------------------

def test_commit_draft_calls_bridge(client, reading_repo):
    """POST /api/reading/drafts/{id}/commit delegates to reading_bridge."""
    session = reading_repo.create_session(source_type="url")
    draft = reading_repo.create_draft(
        session_id=session["id"],
        output_type="summary",
        title="To Commit",
        draft_text="Content to commit.",
    )

    mock_result = {
        "status": "ok",
        "action": "committed",
        "draft_id": draft["id"],
    }

    with patch(
        "bridge.reading_bridge.commit_reading_artifact",
        return_value=mock_result,
    ) as mock_commit:
        token_resp = client.post(f"/api/reading/drafts/{draft['id']}/confirmation")
        assert token_resp.status_code == 200
        token = token_resp.json()["token"]
        resp = client.post(
            f"/api/reading/drafts/{draft['id']}/commit",
            json={"confirmation_token": token},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["action"] == "committed"
    mock_commit.assert_called_once_with(
        draft["id"],
        confirmation_source="api",
        token_verified=True,
    )


def test_save_answer_draft_calls_bridge(client):
    """POST /api/knowledge/answer-drafts delegates to answer_bridge."""
    mock_result = {
        "status": "ok",
        "action": "saved",
        "draft_id": "abc123",
    }

    with patch(
        "bridge.answer_bridge.save_answer_to_knowledge",
        return_value=mock_result,
    ) as mock_save:
        resp = client.post(
            "/api/knowledge/answer-drafts",
            json={
                "hermes_session_id": "sess-1",
                "user_message": "Explain recursion.",
                "assistant_answer": "Recursion is when a function calls itself.",
                "instruction": "Save as learning note.",
                "output_type": "local_learning_note",
                "sources": [],
                "target_capture_id": "",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["draft_id"] == "abc123"
    mock_save.assert_called_once_with(
        hermes_session_id="sess-1",
        user_message="Explain recursion.",
        assistant_answer="Recursion is when a function calls itself.",
        instruction="Save as learning note.",
        output_type="local_learning_note",
        sources=[],
        target_capture_id="",
        folder_path="",
    )


# ---------------------------------------------------------------------------
# Capture draft tests
# ---------------------------------------------------------------------------

def test_list_capture_drafts_empty(client):
    """GET /api/capture-drafts returns empty when no open drafts exist."""
    resp = client.get("/api/capture-drafts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_capture_drafts_with_data(client, draft_repo):
    """GET /api/capture-drafts returns open drafts created via repo."""
    draft_repo.create(
        raw_text="Some raw content about AI.",
        source="https://example.com/ai-article",
        source_type="url",
        title="AI Article",
        draft_text="Drafted summary about AI.",
        status="draft_ready",
    )
    draft_repo.create(
        raw_text="Another raw text.",
        source="wechat",
        source_type="text",
        title="WeChat Note",
        draft_text="Drafted note.",
        status="raw_extracted",
    )

    resp = client.get("/api/capture-drafts")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 2


def test_get_capture_draft_detail(client, draft_repo):
    """GET /api/capture-drafts/{id} returns the draft."""
    draft = draft_repo.create(
        raw_text="Test content.",
        title="Test Draft",
        draft_text="Full draft text.",
        status="draft_ready",
    )

    resp = client.get(f"/api/capture-drafts/{draft['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == draft["id"]
    assert body["title"] == "Test Draft"
    assert body["draft_text"] == "Full draft text."


def test_get_capture_draft_not_found(client):
    """GET /api/capture-drafts/{id} returns 404 for nonexistent draft."""
    resp = client.get("/api/capture-drafts/nonexistent")
    assert resp.status_code == 404


def test_create_capture_draft_preserves_source_metadata(client, monkeypatch):
    """POST /api/capture-drafts forwards source metadata to CaptureAgent."""
    from bridge import capture_bridge

    calls = []

    class FakeCaptureAgent:
        conn = None

        def create_draft(
            self,
            content,
            instruction="",
            source_url="",
            source_type="",
        ):
            calls.append((content, instruction, source_url, source_type))
            return {
                "id": "draft1",
                "title": "Example",
                "summary": "Summary",
                "draft_text": "Captured body",
                "status": "draft_ready",
                "word_count": 13,
                "tags": [],
            }

    monkeypatch.setattr(
        capture_bridge,
        "_agent_from_context",
        lambda _context: (None, FakeCaptureAgent()),
    )

    resp = client.post(
        "/api/capture-drafts",
        json={
            "content": "Captured body",
            "instruction": "Keep the source",
            "source": "https://example.com/article",
            "source_type": "url",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert calls == [
        (
            "Captured body",
            "Keep the source",
            "https://example.com/article",
            "url",
        )
    ]


def test_create_capture_draft_rejects_local_file_input(client, tmp_path, monkeypatch):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("must not be read through the API", encoding="utf-8")

    from bridge import capture_bridge

    monkeypatch.setattr(
        capture_bridge,
        "capture_content",
        lambda **_kwargs: pytest.fail("local file input must be rejected before bridge execution"),
    )

    response = client.post(
        "/api/capture-drafts",
        json={"content": str(secret_file)},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "local file capture is not allowed through the API"


def test_create_editable_capture_draft_calls_bridge(client):
    """POST /api/capture-drafts/editable delegates to editable Capture bridge."""
    mock_result = {
        "status": "success",
        "intent": "create_editable_capture_draft",
        "meta": {"draft_id": "draft1", "title": "AI 标题"},
    }

    with patch(
        "bridge.capture_bridge.create_editable_capture_draft",
        return_value=mock_result,
    ) as mock_create:
        resp = client.post(
            "/api/capture-drafts/editable",
            json={
                "content": "自由粘贴正文",
                "instruction": "整理一下",
                "source": "manual",
                "source_type": "manual",
                "title": "用户标题",
                "folder_path": "AI 产品",
                "existing_folders": ["Inbox", "AI 产品"],
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "create_editable_capture_draft"
    mock_create.assert_called_once_with(
        content="自由粘贴正文",
        instruction="整理一下",
        source="manual",
        source_type="manual",
        title="用户标题",
        folder_path="AI 产品",
        organize=True,
        existing_folders=["Inbox", "AI 产品"],
    )


def test_update_editable_capture_draft_calls_bridge(client, draft_repo):
    """PATCH /api/capture-drafts/{id}/editable delegates to editable Capture bridge."""
    draft = draft_repo.create(
        raw_text="Original.",
        title="AI Title",
        draft_text="Original.",
        status="draft_ready",
        capture_intent={"entry_mode": "editable_capture", "title_source": "ai"},
    )
    mock_result = {
        "status": "success",
        "intent": "update_editable_capture_draft",
        "meta": {"draft_id": draft["id"], "title": "用户标题"},
    }

    with patch(
        "bridge.capture_bridge.update_editable_capture_draft",
        return_value=mock_result,
    ) as mock_update:
        resp = client.patch(
            f"/api/capture-drafts/{draft['id']}/editable",
            json={
                "content": "用户编辑后的正文",
                "title": "用户标题",
                "organize": False,
                "existing_folders": ["Inbox", "AI 产品"],
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "update_editable_capture_draft"
    mock_update.assert_called_once_with(
        draft_id=draft["id"],
        content="用户编辑后的正文",
        title="用户标题",
        summary=None,
        tags=None,
        folder_path=None,
        folder_suggestion=None,
        card_suggestion=None,
        organize=False,
        existing_folders=["Inbox", "AI 产品"],
    )


def test_revise_capture_draft_calls_bridge(client, draft_repo):
    """POST /api/capture-drafts/{id}/revise delegates to capture_bridge."""
    draft = draft_repo.create(
        raw_text="Content.",
        title="To Revise",
        draft_text="Original draft.",
        status="draft_ready",
    )

    mock_result = {
        "status": "ok",
        "intent": "revise_capture_draft",
        "chat_preview": "Revised successfully.",
    }

    with patch(
        "bridge.capture_bridge.revise_capture_draft",
        return_value=mock_result,
    ) as mock_revise:
        resp = client.post(
            f"/api/capture-drafts/{draft['id']}/revise",
            json={"instruction": "Make it shorter."},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    mock_revise.assert_called_once_with(
        draft_id=draft["id"],
        instruction="Make it shorter.",
    )


def test_commit_capture_draft_calls_bridge(client, draft_repo):
    """POST /api/capture-drafts/{id}/commit delegates to capture_bridge."""
    draft = draft_repo.create(
        raw_text="Content to commit.",
        title="To Commit",
        draft_text="Final draft.",
        status="draft_ready",
    )

    mock_result = {
        "status": "ok",
        "intent": "commit_capture_draft",
        "chat_preview": "Committed to vault.",
    }

    with patch(
        "bridge.capture_bridge.commit_capture_draft",
        return_value=mock_result,
    ) as mock_commit:
        token_resp = client.post(f"/api/capture-drafts/{draft['id']}/confirmation")
        assert token_resp.status_code == 200
        token = token_resp.json()["token"]
        resp = client.post(
            f"/api/capture-drafts/{draft['id']}/commit",
            json={"confirmation_token": token},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    mock_commit.assert_called_once_with(
        draft_id=draft["id"],
        confirmation_source="api",
        token_verified=True,
    )


@pytest.mark.parametrize(
    ("patch_target", "path", "payload", "error", "expected_status"),
    [
        (
            "bridge.capture_bridge.capture_content",
            "/api/capture-drafts",
            {"content": ""},
            "内容不能为空",
            400,
        ),
        (
            "bridge.capture_bridge.revise_capture_draft",
            "/api/capture-drafts/missing/revise",
            {"instruction": "shorter"},
            "草稿不存在",
            404,
        ),
        (
            "bridge.reading_bridge.summarize_reading_session",
            "/api/reading/drafts",
            {"session_id": "missing", "output_type": "summary", "instruction": ""},
            "阅读会话不存在",
            404,
        ),
        (
            "bridge.answer_bridge.save_answer_to_knowledge",
            "/api/knowledge/answer-drafts",
            {},
            "回答内容不能为空",
            400,
        ),
        (
            "bridge.answer_bridge.revise_answer_draft",
            "/api/reading/drafts/missing/revise",
            {"instruction": "shorter"},
            "没有找到阅读草稿",
            404,
        ),
        (
            "bridge.answer_bridge.revise_answer_draft",
            "/api/knowledge/drafts/missing/revise",
            {"instruction": "shorter"},
            "草稿不存在",
            404,
        ),
    ],
)
def test_bridge_error_envelopes_return_4xx(
    client,
    patch_target,
    path,
    payload,
    error,
    expected_status,
):
    result = {
        "status": "error",
        "agent": "test",
        "intent": "test",
        "chat_preview": error,
        "durable_artifacts": [],
        "source_map": [],
        "next_actions": [],
        "warnings": [],
        "errors": [error],
        "meta": {},
    }

    with patch(patch_target, return_value=result):
        resp = client.post(path, json=payload) if payload is not None else client.post(path)

    assert resp.status_code == expected_status
    assert resp.json() == result


# ---------------------------------------------------------------------------
# Confirmation tokens (durable writes must be confirmed server-side)
# ---------------------------------------------------------------------------

def test_capture_commit_requires_confirmation_token(client, draft_repo):
    draft = draft_repo.create(
        raw_text="Content.",
        title="To commit",
        draft_text="Final draft.",
        status="draft_ready",
    )

    resp = client.post(f"/api/capture-drafts/{draft['id']}/commit", json={})

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "confirmation_required"


def test_reading_commit_requires_confirmation_token(client, reading_repo):
    session = reading_repo.create_session(source_type="url")
    draft = reading_repo.create_draft(
        session_id=session["id"],
        output_type="summary",
        title="To commit",
        draft_text="Content.",
    )

    resp = client.post(f"/api/reading/drafts/{draft['id']}/commit", json={})

    assert resp.status_code == 403


def test_confirmation_token_is_single_use(client, draft_repo):
    draft = draft_repo.create(
        raw_text="Content.",
        title="To commit",
        draft_text="Final draft.",
        status="draft_ready",
    )
    token = client.post(f"/api/capture-drafts/{draft['id']}/confirmation").json()["token"]

    with patch(
        "bridge.capture_bridge.commit_capture_draft",
        return_value={"status": "ok", "chat_preview": "ok"},
    ):
        first = client.post(
            f"/api/capture-drafts/{draft['id']}/commit",
            json={"confirmation_token": token},
        )
        second = client.post(
            f"/api/capture-drafts/{draft['id']}/commit",
            json={"confirmation_token": token},
        )

    assert first.status_code == 200
    assert second.status_code == 403


def test_confirmation_token_is_bound_to_the_draft(client, draft_repo):
    first = draft_repo.create(raw_text="a", title="A", draft_text="a", status="draft_ready")
    second = draft_repo.create(raw_text="b", title="B", draft_text="b", status="draft_ready")
    token = client.post(f"/api/capture-drafts/{first['id']}/confirmation").json()["token"]

    resp = client.post(
        f"/api/capture-drafts/{second['id']}/commit",
        json={"confirmation_token": token},
    )

    assert resp.status_code == 403


def test_commit_error_envelope_still_maps_to_409(client, draft_repo):
    draft = draft_repo.create(
        raw_text="Content.",
        title="Done",
        draft_text="Final.",
        status="committed",
    )
    token = client.post(f"/api/capture-drafts/{draft['id']}/confirmation").json()["token"]
    error = "草稿已保存，不能重复确认"
    result = {
        "status": "error",
        "chat_preview": error,
        "errors": [error],
    }

    with patch("bridge.capture_bridge.commit_capture_draft", return_value=result):
        resp = client.post(
            f"/api/capture-drafts/{draft['id']}/commit",
            json={"confirmation_token": token},
        )

    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Library permanent delete (Knowledge Service contract)
# ---------------------------------------------------------------------------

def _capture_with_file(capture_repo, tmp_path, name="note.md"):
    capture = capture_repo.insert(file_path=f"captures/{name}", raw_path="")
    target = tmp_path / "captures" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\ntitle: note\n---\nbody", encoding="utf-8")
    return capture, target


def test_permanent_delete_requires_confirmation_token(client, capture_repo, tmp_path):
    capture, target = _capture_with_file(capture_repo, tmp_path)

    resp = client.request(
        "DELETE",
        f"/api/knowledge/library/items/capture/{capture['id']}/permanent",
        json={},
    )

    assert resp.status_code == 403
    assert target.exists()


def test_permanent_delete_removes_rows_and_file(client, capture_repo, tmp_path):
    capture, target = _capture_with_file(capture_repo, tmp_path)
    cur = capture_repo.conn.execute(
        """INSERT INTO capture_search_fts
           (capture_id, kind, section_id, section_title_tokens, index_text_tokens)
           VALUES (?, 'identity', '', 'title', 'body')""",
        (capture["id"],),
    )
    capture_repo.conn.execute(
        """INSERT INTO capture_search_fts_map
           (capture_id, kind, section_id, fts_rowid, source_hash)
           VALUES (?, 'identity', '', ?, 'hash')""",
        (capture["id"], cur.lastrowid),
    )
    capture_repo.conn.commit()
    token = client.post(
        f"/api/knowledge/library/items/capture/{capture['id']}/confirmation"
    ).json()["token"]

    resp = client.request(
        "DELETE",
        f"/api/knowledge/library/items/capture/{capture['id']}/permanent",
        json={"confirmation_token": token},
    )

    assert resp.status_code == 200
    assert capture_repo.get_by_id(capture["id"]) is None
    assert not target.exists()
    assert capture_repo.conn.execute(
        "SELECT COUNT(*) FROM capture_search_fts WHERE capture_id = ?",
        (capture["id"],),
    ).fetchone()[0] == 0
    assert capture_repo.conn.execute(
        "SELECT COUNT(*) FROM capture_search_fts_map WHERE capture_id = ?",
        (capture["id"],),
    ).fetchone()[0] == 0


def test_permanent_delete_blocks_referenced_capture(client, capture_repo, db, tmp_path):
    capture, target = _capture_with_file(capture_repo, tmp_path)
    db.execute(
        "INSERT INTO garden_cards (id, title, capture_ids) VALUES (?, ?, ?)",
        ("gc1", "card", '["' + capture["id"] + '"]'),
    )
    db.commit()
    token = client.post(
        f"/api/knowledge/library/items/capture/{capture['id']}/confirmation"
    ).json()["token"]

    resp = client.request(
        "DELETE",
        f"/api/knowledge/library/items/capture/{capture['id']}/permanent",
        json={"confirmation_token": token},
    )

    assert resp.status_code == 409
    assert capture_repo.get_by_id(capture["id"]) is not None
    assert target.exists()


# ---------------------------------------------------------------------------
# Library permanent delete: dependent rows, backup files and partial status
# ---------------------------------------------------------------------------

def _issue_permanent_delete_token(client, capture_id: str) -> str:
    response = client.post(
        f"/api/knowledge/library/items/capture/{capture_id}/confirmation"
    )
    assert response.status_code == 200
    return response.json()["token"]


def _permanent_delete(client, capture_id: str, token: str):
    return client.request(
        "DELETE",
        f"/api/knowledge/library/items/capture/{capture_id}/permanent",
        json={"confirmation_token": token},
    )


def test_permanent_delete_detaches_committed_capture_draft(
    client, capture_repo, draft_repo, db, tmp_path
):
    capture, target = _capture_with_file(capture_repo, tmp_path, name="draft-source.md")
    draft = draft_repo.create(raw_text="正文", draft_text="正文", status="draft_ready")
    db.execute(
        """UPDATE capture_drafts
           SET status = 'committed', committed_capture_id = ?, pending_capture_id = ?
           WHERE id = ?""",
        (capture["id"], capture["id"], draft["id"]),
    )
    db.commit()

    token = _issue_permanent_delete_token(client, capture["id"])
    resp = _permanent_delete(client, capture["id"], token)

    assert resp.status_code == 200
    row = db.execute(
        "SELECT committed_capture_id, pending_capture_id, status "
        "FROM capture_drafts WHERE id = ?",
        (draft["id"],),
    ).fetchone()
    assert row["committed_capture_id"] == ""
    assert row["pending_capture_id"] == ""
    # Back to draft_ready (not abandoned): the capture is gone but the draft
    # content is still valid, so it re-enters the open list and can be retried.
    assert row["status"] == "draft_ready"


def test_permanent_delete_checks_garden_references_inside_transaction(
    client, capture_repo, tmp_path, monkeypatch
):
    from api.routes import knowledge as knowledge_routes

    capture, target = _capture_with_file(capture_repo, tmp_path, name="tx-check.md")
    observed: dict[str, bool] = {}
    original = knowledge_routes._garden_card_references

    def spy(conn, capture_id):
        observed["in_transaction"] = conn.in_transaction
        return original(conn, capture_id)

    monkeypatch.setattr(knowledge_routes, "_garden_card_references", spy)

    token = _issue_permanent_delete_token(client, capture["id"])
    resp = _permanent_delete(client, capture["id"], token)

    assert resp.status_code == 200
    assert observed["in_transaction"] is True


def test_permanent_delete_removes_review_items_and_capture_run_objects(
    client, capture_repo, db, tmp_path
):
    capture, target = _capture_with_file(capture_repo, tmp_path, name="reviewed.md")
    capture_id = capture["id"]
    db.execute(
        """INSERT INTO review_items (id, object_type, object_id, capture_id, section_id, title)
           VALUES ('ri-by-capture', 'capture', ?, ?, '', 'capture column')""",
        (capture_id, capture_id),
    )
    db.execute(
        """INSERT INTO review_items (id, object_type, object_id, capture_id, section_id, title)
           VALUES ('ri-by-object', 'capture', ?, '', 'sec-1', 'object columns')""",
        (capture_id,),
    )
    db.execute(
        """INSERT INTO review_items (id, object_type, object_id, capture_id, section_id, title)
           VALUES ('ri-keep', 'note', 'other-note', '', '', 'unrelated')"""
    )
    db.execute(
        """INSERT INTO agent_run_objects (run_id, object_type, object_id)
           VALUES ('run-capture', 'capture', ?)""",
        (capture_id,),
    )
    db.execute(
        """INSERT INTO agent_run_objects (run_id, object_type, object_id)
           VALUES ('run-note', 'note', ?)""",
        (capture_id,),
    )
    db.commit()

    token = _issue_permanent_delete_token(client, capture_id)
    resp = _permanent_delete(client, capture_id, token)

    assert resp.status_code == 200
    review_ids = {
        row["id"] for row in db.execute("SELECT id FROM review_items").fetchall()
    }
    assert review_ids == {"ri-keep"}
    run_objects = [
        (row["run_id"], row["object_type"])
        for row in db.execute("SELECT run_id, object_type FROM agent_run_objects").fetchall()
    ]
    assert run_objects == [("run-note", "note")]


def test_permanent_delete_cleans_capture_dependents(
    client, capture_repo, reading_repo, db, tmp_path
):
    capture, target = _capture_with_file(capture_repo, tmp_path, name="deps.md")
    capture_id = capture["id"]
    session = reading_repo.create_session(
        source_type="url", source_url="https://example.test"
    )
    reading_draft = reading_repo.create_draft(
        session_id=session["id"],
        output_type="summary",
        target_capture_id=capture_id,
    )
    reading_repo.add_evidence(
        session_id=session["id"],
        source_kind="capture",
        source_id=capture_id,
        title="linked",
    )
    reading_repo.add_evidence(
        session_id=session["id"],
        source_kind="url",
        source_id="https://example.test",
        title="keep",
    )
    db.execute(
        """INSERT INTO knowledge_edges
           (id, source_kind, source_id, target_kind, target_id, relation_type)
           VALUES ('edge-src', 'capture', ?, 'note', 'n1', 'mentions')""",
        (capture_id,),
    )
    db.execute(
        """INSERT INTO knowledge_edges
           (id, source_kind, source_id, target_kind, target_id, relation_type)
           VALUES ('edge-dst', 'note', 'n2', 'capture', ?, 'mentions')""",
        (capture_id,),
    )
    db.execute(
        """INSERT INTO knowledge_edges
           (id, source_kind, source_id, target_kind, target_id, relation_type)
           VALUES ('edge-keep', 'note', 'n3', 'note', 'n4', 'mentions')"""
    )
    db.execute(
        """INSERT INTO pending_questions
           (id, agent_type, question_text, related_capture_ids, expires_at)
           VALUES ('pq-mixed', 'capture', 'q?', ?, '2030-01-01T00:00:00+00:00')""",
        (json.dumps([capture_id, "keep-me"]),),
    )
    db.execute(
        """INSERT INTO pending_questions
           (id, agent_type, question_text, related_capture_ids, expires_at)
           VALUES ('pq-only', 'capture', 'q?', ?, '2030-01-01T00:00:00+00:00')""",
        (json.dumps([capture_id]),),
    )
    db.commit()

    token = _issue_permanent_delete_token(client, capture_id)
    resp = _permanent_delete(client, capture_id, token)

    assert resp.status_code == 200
    assert db.execute(
        "SELECT target_capture_id FROM reading_drafts WHERE id = ?",
        (reading_draft["id"],),
    ).fetchone()["target_capture_id"] == ""
    evidence = db.execute(
        "SELECT source_kind, source_id FROM reading_evidence WHERE session_id = ?",
        (session["id"],),
    ).fetchall()
    assert [(row["source_kind"], row["source_id"]) for row in evidence] == [
        ("url", "https://example.test")
    ]
    edge_ids = [
        row["id"]
        for row in db.execute("SELECT id FROM knowledge_edges ORDER BY id").fetchall()
    ]
    assert edge_ids == ["edge-keep"]
    mixed = db.execute(
        "SELECT related_capture_ids FROM pending_questions WHERE id = 'pq-mixed'"
    ).fetchone()["related_capture_ids"]
    assert json.loads(mixed) == ["keep-me"]
    only = db.execute(
        "SELECT related_capture_ids FROM pending_questions WHERE id = 'pq-only'"
    ).fetchone()["related_capture_ids"]
    assert json.loads(only) == []


def test_permanent_delete_removes_backup_files(
    client, capture_repo, reading_repo, tmp_path
):
    capture, target = _capture_with_file(capture_repo, tmp_path, name="backed.md")
    sibling = target.with_name(target.name + ".bak-long-directory-20260101120000")
    sibling.write_text("previous version", encoding="utf-8")
    backup_relative = "_archive/write_backups/captures/backed-20260101T000000Z-abc123.md"
    backup_file = tmp_path / backup_relative
    backup_file.parent.mkdir(parents=True, exist_ok=True)
    backup_file.write_text("snapshot", encoding="utf-8")
    reading_repo.record_capture_backup(
        capture_id=capture["id"],
        reading_draft_id="",
        backup_file_path=backup_relative,
    )

    token = _issue_permanent_delete_token(client, capture["id"])
    resp = _permanent_delete(client, capture["id"], token)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "permanently_deleted"
    assert body["warnings"] == []
    assert not target.exists()
    assert not sibling.exists()
    assert not backup_file.exists()


def test_permanent_delete_reports_partial_when_unlink_fails(
    client, capture_repo, tmp_path, monkeypatch
):
    capture, target = _capture_with_file(capture_repo, tmp_path, name="locked.md")
    original_unlink = Path.unlink

    def flaky_unlink(self, missing_ok: bool = False):
        if self.name == "locked.md":
            raise OSError("file is locked by another process")
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    token = _issue_permanent_delete_token(client, capture["id"])
    resp = _permanent_delete(client, capture["id"], token)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "partially_deleted"
    assert body["warnings"]
    assert body["deleted"]["knowledge_file"] == ""
    assert target.exists()
    assert capture_repo.get_by_id(capture["id"]) is None


def test_permanent_delete_does_not_report_a_missing_file_as_deleted(
    client, capture_repo, tmp_path
):
    capture = capture_repo.insert(file_path="captures/already-gone.md", raw_path="")

    token = _issue_permanent_delete_token(client, capture["id"])
    resp = _permanent_delete(client, capture["id"], token)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "permanently_deleted"
    assert body["warnings"] == []
    assert body["deleted"]["knowledge_file"] == ""

# ---------------------------------------------------------------------------
# Capture list pagination metadata
# ---------------------------------------------------------------------------

def test_capture_list_reports_total_as_lower_bound_when_filtered_page_fills(
    client, capture_repo
):
    for index in range(3):
        capture_repo.insert(file_path=f"captures/page-{index}.md")

    truncated = client.get("/api/captures", params={"folder": "Inbox", "limit": 1})
    assert truncated.status_code == 200
    truncated_body = truncated.json()
    assert truncated_body["total"] == 1
    assert truncated_body["total_is_lower_bound"] is True

    complete = client.get("/api/captures", params={"folder": "Inbox", "limit": 10})
    assert complete.status_code == 200
    complete_body = complete.json()
    assert complete_body["total"] == 3
    assert complete_body["total_is_lower_bound"] is False

    unfiltered = client.get("/api/captures")
    assert unfiltered.status_code == 200
    unfiltered_body = unfiltered.json()
    assert unfiltered_body["total"] == 3
    assert unfiltered_body["total_is_lower_bound"] is False


def test_capture_list_rejects_negative_offset(client):
    resp = client.get("/api/captures", params={"offset": -1})

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Garden draft confirmation tokens
# ---------------------------------------------------------------------------

def _create_garden_draft_row(capture_repo, title: str = "待确认卡片") -> str:
    from agents.garden_agent import create_garden_draft

    return create_garden_draft(capture_repo, {
        "title": title,
        "one_liner": "确认后才能落库。",
        "core_arguments": [],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": f"# {title}\n",
        "capture_ids": [],
    })


def test_garden_commit_requires_single_use_confirmation_token(
    client, capture_repo, monkeypatch
):
    from bridge import garden_bridge

    draft_id = _create_garden_draft_row(capture_repo)
    calls: list[dict] = []

    def fake_commit(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "intent": "commit_garden_draft"}

    monkeypatch.setattr(garden_bridge, "commit_garden_draft", fake_commit)

    missing = client.post(f"/api/garden/drafts/{draft_id}/commit", json={})
    assert missing.status_code == 403
    assert calls == []

    invalid = client.post(
        f"/api/garden/drafts/{draft_id}/commit",
        json={"confirmation_token": "not-a-token"},
    )
    assert invalid.status_code == 403

    issued = client.post(f"/api/garden/drafts/{draft_id}/confirmation")
    assert issued.status_code == 200
    issued_body = issued.json()
    assert issued_body["intent"] == "commit_garden_draft"
    assert issued_body["token"]
    assert issued_body["expires_at"]

    committed = client.post(
        f"/api/garden/drafts/{draft_id}/commit",
        json={"confirmation_token": issued_body["token"]},
    )
    assert committed.status_code == 200
    assert committed.json()["intent"] == "commit_garden_draft"
    assert calls == [
        {
            "draft_id": draft_id,
            "confirmation_source": "api",
            "token_verified": True,
            "context": None,
        }
    ]

    reused = client.post(
        f"/api/garden/drafts/{draft_id}/commit",
        json={"confirmation_token": issued_body["token"]},
    )
    assert reused.status_code == 403


def test_garden_commit_rejects_token_for_another_draft(
    client, capture_repo, monkeypatch
):
    from bridge import garden_bridge

    first = _create_garden_draft_row(capture_repo, "卡片一")
    second = _create_garden_draft_row(capture_repo, "卡片二")
    monkeypatch.setattr(
        garden_bridge,
        "commit_garden_draft",
        lambda **kwargs: {"status": "success", "intent": "commit_garden_draft"},
    )
    token = client.post(f"/api/garden/drafts/{first}/confirmation").json()["token"]

    resp = client.post(
        f"/api/garden/drafts/{second}/commit",
        json={"confirmation_token": token},
    )

    assert resp.status_code == 403


def test_garden_draft_creation_never_commits_even_with_legacy_auto_commit(
    client, capture_repo, monkeypatch
):
    from bridge import garden_bridge

    draft_id = _create_garden_draft_row(capture_repo)
    commits: list[dict] = []
    envelope = {
        "status": "success",
        "agent": "garden",
        "intent": "synthesize_garden",
        "chat_preview": "已生成卡片草稿",
        "durable_artifacts": [
            {"type": "garden_draft", "id": draft_id, "title": "卡片", "status": "draft_ready"}
        ],
        "meta": {"draft_id": draft_id},
    }
    monkeypatch.setattr(
        garden_bridge, "synthesize_garden", lambda **kwargs: envelope
    )
    monkeypatch.setattr(
        garden_bridge,
        "commit_garden_draft",
        lambda **kwargs: (commits.append(kwargs) or {"status": "success"}),
    )

    created = client.post("/api/garden/drafts", json={"capture_ids": ["cap-1"]})
    assert created.status_code == 200
    assert created.json() == envelope
    assert commits == []

    # Old clients send auto_commit=True; the field is ignored, not honoured.
    legacy = client.post(
        "/api/garden/drafts",
        json={"capture_ids": ["cap-1"], "auto_commit": True},
    )
    assert legacy.status_code == 200
    assert legacy.json() == envelope
    assert commits == []

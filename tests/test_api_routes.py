"""Tests for the reading and knowledge API routes."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db.schema import init_db
from db.repo import CaptureRepo, CaptureDraftRepo
from db.reading_repo import ReadingRepo
from api.routes.reading import create_reading_router
from api.routes.knowledge import create_knowledge_router
from api.routes.capture_drafts import create_capture_draft_router


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
        create_capture_draft_router(draft_repo),
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
        resp = client.post(f"/api/reading/drafts/{draft['id']}/commit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["action"] == "committed"
    mock_commit.assert_called_once_with(draft["id"])


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
        resp = client.post(f"/api/capture-drafts/{draft['id']}/commit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    mock_commit.assert_called_once_with(draft_id=draft["id"])

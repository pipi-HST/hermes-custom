"""Capture writes must not replace a newer draft or note snapshot."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.capture_agent import CaptureAgent, CaptureConfig
from agents.library_agent import LibraryAgent
from api.routes.captures import apply_capture_update
from api.routes.library import create_library_router
from db.reading_repo import ReadingRepo
from db.repo import CaptureDraftRepo, CaptureRepo
from db.schema import init_db


@pytest.fixture
def capture(tmp_path):
    agent = CaptureAgent(CaptureConfig(
        llm_api_key="",
        qwen_api_key="",
        vault_path=str(tmp_path / "vault"),
        db_path=str(tmp_path / "captures.db"),
    ))
    # Use another connection, as the API and a worker can do.
    api_conn = init_db(agent.config.db_path)
    app = FastAPI()
    app.include_router(create_library_router(
        CaptureRepo(api_conn),
        CaptureDraftRepo(api_conn),
        ReadingRepo(api_conn),
        vault_path=agent.config.vault_path,
    ))
    try:
        with TestClient(app) as client:
            yield agent, client
    finally:
        api_conn.close()
        agent.conn.close()


def _draft(agent):
    return agent.create_editable_draft("original body", title="original title", organize=False)


@pytest.mark.parametrize("route", ["/library/items/capture_draft/", "/capture-drafts/"])
@pytest.mark.parametrize("status", ["committing", "committed", "abandoned"])
def test_patch_only_updates_editable_draft_states(capture, route, status):
    agent, client = capture
    draft = _draft(agent)
    before = agent.draft_repo.update(draft["id"], status=status)

    response = client.patch(f"{route}{draft['id']}", json={"title": "new title"})

    assert response.status_code == 409
    assert agent.draft_repo.get_by_id(draft["id"]) == before


def test_commit_refuses_draft_edited_since_finalization(capture, monkeypatch):
    agent, client = capture
    draft = _draft(agent)
    draft_id = draft["id"]
    original_finalize = agent._finalize_draft_before_commit

    def finalize(snapshot):
        finalized = original_finalize(snapshot)
        response = client.patch(
            f"/library/items/capture_draft/{draft_id}",
            json={"body": "new body", "title": "new title", "folder_path": "New Topic"},
        )
        assert response.status_code == 200
        return finalized

    monkeypatch.setattr(agent, "_finalize_draft_before_commit", finalize)
    # Don't use an embedding or outline provider even if the stale save succeeds.
    monkeypatch.setattr(agent, "_index_committed_identity", lambda *_: None)
    monkeypatch.setattr(agent, "_postprocess_committed_library", lambda *_, **__: {})

    result = agent.commit_draft(draft_id)

    assert "error" in result
    assert not list(Path(agent.config.vault_path).rglob("*.md"))
    assert agent.repo.count() == 0
    current = agent.draft_repo.get_by_id(draft_id)
    assert current["status"] == "draft_ready"
    assert current["title"] == "new title"
    assert current["draft_text"] == "new body"
    assert current["capture_intent"]["folder_path"] == "New Topic"


def test_editable_organization_does_not_overwrite_commit_claim(capture, monkeypatch):
    agent, _client = capture
    draft = _draft(agent)
    other_conn = init_db(agent.config.db_path)
    other_repo = CaptureDraftRepo(other_conn)
    claimed = None

    def organize(*_args, **_kwargs):
        nonlocal claimed
        claimed = other_repo.claim_for_commit(draft["id"])
        assert claimed is not None
        return {"summary": "outdated summary"}

    monkeypatch.setattr(agent, "_organize_editable_body", organize)
    try:
        result = agent.update_editable_draft(draft["id"], content="new body")
    finally:
        other_conn.close()

    assert "error" in result
    assert agent.draft_repo.get_by_id(draft["id"]) == claimed


def test_outline_pass_preserves_title_and_topic_edited_during_generation(capture):
    agent, _client = capture
    vault = Path(agent.config.vault_path)
    path = "captures/note.md"
    capture_id = agent.repo.insert(file_path=path)["id"]
    original_body = "## Opening\n\nA synthetic paragraph.\n\n## Closing\n\nAnother paragraph."
    note_path = vault / path
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        f"---\nid: {capture_id}\ntitle: Original\nfolder_path: Inbox\n---\n\n{original_body}",
        encoding="utf-8",
    )
    edited_bytes = None

    class Outliner:
        def generate(self, body, _word_count):
            nonlocal edited_bytes
            assert body == original_body
            apply_capture_update(
                agent.repo, vault, None, capture_id,
                title="New title", folder_path="New Topic",
            )
            edited_bytes = note_path.read_bytes()
            return [{"title": "Opening"}, {"title": "Closing"}]

    report = LibraryAgent(
        vault, outliner=Outliner(), db_conn=agent.conn, min_outline_chars=1,
    ).maintain_outlines(apply=True)

    assert edited_bytes is not None
    assert note_path.read_bytes() == edited_bytes
    assert report["updated"] == 0
    assert report["items"][0]["action"] == "skipped_note_changed"
    assert not list(note_path.parent.glob("*.bak-*"))
    assert agent.conn.execute(
        "SELECT 1 FROM capture_metadata WHERE capture_id = ?", (capture_id,),
    ).fetchone() is None

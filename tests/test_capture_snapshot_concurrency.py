"""Capture writes must not replace a newer draft or note snapshot."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.capture_agent import CaptureAgent, CaptureConfig
from agents.library_agent import LibraryAgent
from api.routes.captures import apply_capture_update, create_capture_router
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
    app.include_router(create_capture_router(CaptureRepo(api_conn), agent.config.vault_path))
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


@pytest.mark.parametrize("route", ["/captures/{id}", "/library/items/capture/{id}"])
def test_late_editor_cannot_overwrite_another_save(capture, monkeypatch, route):
    agent, client = capture
    monkeypatch.setattr("api.routes.captures._maintain_capture_after_edit", lambda *_: {})
    vault = Path(agent.config.vault_path)
    path = vault / "captures/note.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    capture_id = agent.repo.insert(file_path="captures/note.md")["id"]
    path.write_bytes(b"---\r\ntitle: Original\r\n---\r\n\r\nOriginal body\r\n")
    url = route.format(id=capture_id)
    read_url = f"/library/items/capture/{capture_id}"
    first = client.get(read_url).json()["item"]
    second = client.get(read_url).json()["item"]
    assert first["note_revision"] == second["note_revision"]
    assert client.get(f"/captures/{capture_id}").json()["note_revision"] == second["note_revision"]

    saved = client.patch(url, json={"body": "First edit", "expected_revision": first["note_revision"]})
    assert saved.status_code == 200
    saved_revision = saved.json()["item"]["note_revision"]
    assert saved_revision != first["note_revision"]
    before_conflict = path.read_bytes()
    backup_dir = vault / "_archive" / "write_backups"
    before_backups = sorted(backup_dir.rglob("*.md"))
    assert len(before_backups) == 1

    stale = client.patch(url, json={"body": "Second edit", "expected_revision": second["note_revision"]})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "capture_revision_conflict"
    assert path.read_bytes() == before_conflict
    assert sorted(backup_dir.rglob("*.md")) == before_backups

    # A metadata-only change outside Hermes also invalidates an open editor.
    path.write_bytes(before_conflict.replace(b"Original", b"Edited in another app"))
    stale = client.patch(url, json={"body": "Second edit", "expected_revision": saved_revision})
    assert stale.status_code == 409
    latest = client.get(read_url).json()["item"]
    assert latest["title"] == "Edited in another app"
    assert latest["body"] == "First edit"
    assert client.patch(url, json={"body": "Second edit", "expected_revision": latest["note_revision"]}).status_code == 200


def test_capture_save_rechecks_snapshot_before_replacement(capture, monkeypatch):
    from bridge import knowledge_write_service

    agent, client = capture
    vault = Path(agent.config.vault_path)
    path = vault / "captures/note.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"Original body")
    capture_id = agent.repo.insert(file_path="captures/note.md")["id"]
    url = f"/captures/{capture_id}"
    revision = client.get(url).json()["note_revision"]
    original_write = knowledge_write_service._atomic_write_text

    def external_edit_before_replace(target, content, *, before_replace=None):
        if target == path:
            path.write_bytes(b"New version from another editor")
        return original_write(target, content, before_replace=before_replace)

    monkeypatch.setattr(knowledge_write_service, "_atomic_write_text", external_edit_before_replace)
    response = client.patch(url, json={"body": "Stale text", "expected_revision": revision})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "capture_revision_conflict"
    assert path.read_bytes() == b"New version from another editor"


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

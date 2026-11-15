"""Contract v1.1 library write endpoints (dashboard HTTP mode)."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest
import yaml
from fastapi.testclient import TestClient

from agents.capture_agent import CaptureAgent, CaptureConfig
from api import main as api_main
from api.routes import captures as captures_routes
from bridge.folder_store import STORE_RELATIVE_PATH
from db.reading_repo import ReadingRepo
from db.repo import CaptureDraftRepo, CaptureRepo
from db.schema import init_db

LOOPBACK = ("127.0.0.1", 50000)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """App + config wired to a temp vault, with LLM/maintenance stubbed out."""
    monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    monkeypatch.setattr(
        CaptureAgent,
        "_organize_editable_body",
        lambda self, body, *, existing_folders=None: {},
    )
    monkeypatch.setattr(
        captures_routes,
        "_maintain_capture_after_edit",
        lambda *args, **kwargs: {"skipped": True},
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    config = CaptureConfig(
        llm_api_key="sk-test",
        qwen_api_key="",
        llm_base_url="",
        vault_path=str(vault),
        db_path=str(tmp_path / "captures.db"),
    )
    app = api_main.create_app(config=config)
    return config, app, vault


def _client(app):
    return TestClient(app, client=LOOPBACK)


def _db_conn(config):
    return init_db(config.db_path)


def _write_note(
    vault: Path,
    relative_path: str,
    *,
    title: str,
    body: str,
    folder_path: str,
    story: str = "",
) -> None:
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "title": title,
        "story": story,
        "folder_path": folder_path,
        "tags": ["note"],
        "source": "https://example.test/source",
        "source_type": "web",
    }
    path.write_text(
        "---\n"
        + yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + body,
        encoding="utf-8",
    )


def _add_capture(config, vault: Path, *, name: str, folder: str, body: str = "正文内容。") -> str:
    relative_path = f"captures/{name}.md"
    _write_note(vault, relative_path, title=name, body=body, folder_path=folder)
    conn = _db_conn(config)
    try:
        capture = CaptureRepo(conn).insert(file_path=relative_path)
    finally:
        conn.close()
    return capture["id"]


def _add_draft(config, *, title: str = "草稿", folder: str = "Inbox") -> str:
    conn = _db_conn(config)
    try:
        draft = CaptureDraftRepo(conn).create(
            raw_text="原始内容",
            draft_text="草稿正文",
            title=title,
            status="draft_ready",
            capture_intent={"folder_path": folder},
        )
    finally:
        conn.close()
    return draft["id"]


# ---------------------------------------------------------------------------
# POST /library/items + PATCH/DELETE draft lifecycle
# ---------------------------------------------------------------------------

def test_create_patch_delete_capture_draft_chain(env):
    config, app, vault = env
    with _client(app) as client:
        created = client.post(
            "/api/library/items",
            json={
                "body": "原始正文内容",
                "title": "原始标题",
                "source": "https://example.test/a",
                "source_type": "web",
                "folder_path": "待读",
            },
        )
        assert created.status_code == 200
        body = created.json()
        assert body["ok"] is True
        draft_id = body["draft_id"]
        assert draft_id
        assert body["result"]["status"] == "success"
        item = body["item"]
        assert item["id"] == draft_id
        assert item["kind"] == "capture_draft"
        assert item["title"] == "原始标题"
        assert item["body"] == "原始正文内容"
        assert item["folder_path"] == "待读"
        assert item["source"] == "https://example.test/a"

        patched = client.patch(
            f"/api/library/items/capture_draft/{draft_id}",
            json={
                "title": "新标题",
                "body": "新正文",
                "summary": "新摘要",
                "tags": ["ai", "energy"],
                "folder_path": "AI 产品",
                "folder_suggestion": {"folder_path": "AI 产品", "mode": "existing"},
                "card_suggestion": {"title": "卡片"},
            },
        )
        assert patched.status_code == 200
        patched_body = patched.json()
        patched_item = patched_body["item"]
        assert patched_item["title"] == "新标题"
        assert patched_item["body"] == "新正文"
        assert patched_item["summary"] == "新摘要"
        assert patched_item["tags"] == ["ai", "energy"]
        assert patched_item["folder_path"] == "AI 产品"
        assert patched_item["word_count"] == len("新正文")

        conn = _db_conn(config)
        try:
            row = conn.execute(
                "SELECT * FROM capture_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
        finally:
            conn.close()
        intent = json.loads(row["capture_intent"])
        assert intent["folder_path"] == "AI 产品"
        assert intent["folder_suggestion"]["mode"] == "existing"
        assert intent["card_suggestion"] == {"title": "卡片"}
        assert json.loads(row["tags"]) == ["ai", "energy"]

        deleted = client.delete(f"/api/library/items/capture_draft/{draft_id}")
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "discarded"
        assert _draft_status(config, draft_id) == "abandoned"

        again = client.delete(f"/api/library/items/capture_draft/{draft_id}")
        assert again.status_code == 200
        # Error envelopes keep their own status instead of claiming "discarded".
        assert again.json()["status"] == "error"

        title_only = client.post(
            "/api/library/items",
            json={"title": "只有标题"},
        )
        assert title_only.status_code == 200
        title_only_body = title_only.json()
        assert title_only_body["draft_id"]
        assert title_only_body["item"]["title"] == "只有标题"
        assert title_only_body["item"]["body"] == "只有标题"


def test_quick_manual_note_never_calls_ai_rewrite(env, monkeypatch):
    """POST /library/items is the quick-create path: no synchronous LLM."""
    config, app, vault = env

    def fail_organize(*_args, **_kwargs):
        raise AssertionError("quick manual note must not invoke AI organizing")

    monkeypatch.setattr(CaptureAgent, "_organize_editable_body", fail_organize)
    body_text = "第一段用户原文，必须原样保存。\n\n第二段用户原文，不能被压缩或改写。"

    with _client(app) as client:
        created = client.post(
            "/api/library/items",
            json={"body": body_text, "title": "快速笔记", "folder_path": "读书与随笔"},
        )

    assert created.status_code == 200
    item = created.json()["item"]
    assert item["title"] == "快速笔记"
    assert item["body"] == body_text
    assert item["folder_path"] == "读书与随笔"


def test_capture_draft_direct_endpoints(env):
    config, app, vault = env
    draft_id = _add_draft(config, title="直接草稿")
    with _client(app) as client:
        patched = client.patch(
            f"/api/capture-drafts/{draft_id}",
            json={
                "title": "改后标题",
                "draft_text": "改后正文",
                "summary": "摘要",
                "tags": ["x"],
                "capture_intent": {"folder_path": "临时"},
            },
        )
        assert patched.status_code == 200
        item = patched.json()["item"]
        assert item["title"] == "改后标题"
        assert item["body"] == "改后正文"
        assert item["folder_path"] == "临时"
        assert item["tags"] == ["x"]

        deleted = client.delete(f"/api/capture-drafts/{draft_id}")
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "abandoned"
        assert _draft_status(config, draft_id) == "abandoned"


def _draft_status(config, draft_id: str) -> str:
    conn = _db_conn(config)
    try:
        row = conn.execute(
            "SELECT status FROM capture_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
    finally:
        conn.close()
    return str(row["status"]) if row else ""


# ---------------------------------------------------------------------------
# Library capture edit + archive
# ---------------------------------------------------------------------------

def test_patch_capture_writes_markdown_and_archive_keeps_file(env):
    config, app, vault = env
    capture_id = _add_capture(config, vault, name="note", folder="Inbox", body="旧正文")
    with _client(app) as client:
        patched = client.patch(
            f"/api/library/items/capture/{capture_id}",
            json={"title": "新标题", "body": "新正文内容", "folder_path": "AI 产品"},
        )
        assert patched.status_code == 200
        body = patched.json()
        assert body["ok"] is True
        assert body["item"]["title"] == "新标题"
        assert body["item"]["folder_path"] == "AI 产品"

        archived = client.delete(f"/api/library/items/capture/{capture_id}")
        assert archived.status_code == 200
        archived_body = archived.json()
        assert archived_body["ok"] is True
        assert archived_body["status"] == "archived"
        assert archived_body["id"] == capture_id
        assert set(archived_body["dependencies"]) >= {
            "garden_cards",
            "committed_drafts",
            "event_count",
        }

    note_path = vault / "captures" / "note.md"
    text = note_path.read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["title"] == "新标题"
    assert frontmatter["title_source"] == "user"
    assert frontmatter["folder_path"] == "AI 产品"
    assert "新正文内容" in text

    conn = _db_conn(config)
    try:
        row = conn.execute(
            "SELECT review_state FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row["review_state"] == "archived"
    assert note_path.is_file()  # archiving keeps the source file


# ---------------------------------------------------------------------------
# Folder CRUD
# ---------------------------------------------------------------------------

def test_library_folder_lifecycle(env):
    config, app, vault = env
    capture_id = _add_capture(config, vault, name="in-study", folder="研究")
    draft_id = _add_draft(config, title="研究草稿", folder="研究")
    with _client(app) as client:
        listed = client.get("/api/library/folders")
        assert listed.status_code == 200
        listed_body = listed.json()
        folder_paths = [folder["path"] for folder in listed_body["folders"]]
        assert "Inbox" in folder_paths
        assert any(view["key"] == "recent" for view in listed_body["smart_views"])
        assert any(view["key"] == "review" for view in listed_body["smart_views"])
        study = next(f for f in listed_body["folders"] if f["path"] == "研究")
        assert study["count"] == 2  # capture + draft

        created = client.post("/api/library/folders", json={"name": "待整理"})
        assert created.status_code == 200
        assert created.json()["folder"] == {
            "path": "待整理",
            "name": "待整理",
            "count": 0,
        }
        store = json.loads((vault / STORE_RELATIVE_PATH).read_text(encoding="utf-8"))
        assert "待整理" in store["folders"]

        empty = client.post("/api/library/folders", json={"name": "   "})
        assert empty.status_code == 400
        too_long = client.post("/api/library/folders", json={"name": "x" * 81})
        assert too_long.status_code == 400

        renamed = client.patch(
            f"/api/library/folders/{quote('研究', safe='')}",
            json={"name": "深度研究"},
        )
        assert renamed.status_code == 200
        renamed_body = renamed.json()
        assert renamed_body["ok"] is True
        renamed_paths = [folder["path"] for folder in renamed_body["folders"]]
        assert "深度研究" in renamed_paths
        assert "研究" not in renamed_paths

        inbox = client.patch(
            f"/api/library/folders/{quote('Inbox', safe='')}",
            json={"name": "收件箱"},
        )
        assert inbox.status_code == 400

    note_text = (vault / "captures" / "in-study.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(note_text.split("---", 2)[1])
    assert frontmatter["folder_path"] == "深度研究"

    conn = _db_conn(config)
    try:
        draft_row = conn.execute(
            "SELECT capture_intent FROM capture_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        capture_row = conn.execute(
            "SELECT review_state FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(draft_row["capture_intent"])["folder_path"] == "深度研究"
    assert capture_row["review_state"] != "archived"

    store_after = json.loads((vault / STORE_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert "深度研究" in store_after["folders"]
    assert "研究" not in store_after["folders"]


def test_library_folders_accepts_legacy_plain_list_store(env):
    config, app, vault = env
    store_path = vault / STORE_RELATIVE_PATH
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(["旧文件夹"], ensure_ascii=False), encoding="utf-8")

    with _client(app) as client:
        listed = client.get("/api/library/folders")

    assert listed.status_code == 200
    assert any(folder["path"] == "旧文件夹" for folder in listed.json()["folders"])


# ---------------------------------------------------------------------------
# Reading draft delete + error branches
# ---------------------------------------------------------------------------

def test_delete_reading_draft(env):
    config, app, vault = env
    conn = _db_conn(config)
    try:
        reading_repo = ReadingRepo(conn)
        session = reading_repo.create_session(source_type="url")
        draft = reading_repo.create_draft(
            session_id=session["id"],
            output_type="summary",
            title="阅读草稿",
            draft_text="内容",
        )
    finally:
        conn.close()

    with _client(app) as client:
        deleted = client.delete(f"/api/reading/drafts/{draft['id']}")
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True, "status": "deleted"}
        missing = client.delete("/api/reading/drafts/does-not-exist")
        assert missing.status_code == 404

    conn = _db_conn(config)
    try:
        row = conn.execute(
            "SELECT status FROM reading_drafts WHERE id = ?", (draft["id"],)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "deleted"


def test_library_write_error_branches(env):
    config, app, vault = env
    with _client(app) as client:
        empty_create = client.post("/api/library/items", json={})
        assert empty_create.status_code == 400

        bad_kind_patch = client.patch(
            "/api/library/items/unknown/xyz", json={"title": "x"}
        )
        assert bad_kind_patch.status_code == 400

        bad_kind_delete = client.delete("/api/library/items/unknown/xyz")
        assert bad_kind_delete.status_code == 400

        missing_draft_patch = client.patch(
            "/api/library/items/capture_draft/missing", json={"title": "x"}
        )
        assert missing_draft_patch.status_code == 404

        missing_capture_patch = client.patch(
            "/api/library/items/capture/missing", json={"title": "x"}
        )
        assert missing_capture_patch.status_code == 404

        missing_capture_delete = client.delete("/api/library/items/capture/missing")
        assert missing_capture_delete.status_code == 404

        missing_direct_patch = client.patch(
            "/api/capture-drafts/missing", json={"title": "x"}
        )
        assert missing_direct_patch.status_code == 404

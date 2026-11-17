"""Contract v1.1 library reads and garden/card endpoints."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from agents.capture_agent import CaptureAgent, CaptureConfig
from agents.knowledge_graph import KnowledgeGraphService
from api import main as api_main
from bridge import library_view
from db.embedding_repo import CaptureEmbeddingRepo
from db.metadata_repo import CaptureMetadataRepo
from db.repo import CaptureDraftRepo, CaptureRepo
from db.schema import init_db

LOOPBACK = ("127.0.0.1", 50000)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    monkeypatch.setattr(
        CaptureAgent,
        "_organize_editable_body",
        lambda self, body, *, existing_folders=None: {},
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


def _conn(config):
    return init_db(config.db_path)


def _write_note(
    vault: Path,
    relative_path: str,
    *,
    title: str,
    body: str,
    folder: str = "Inbox",
    story: str = "",
    status: str = "",
) -> None:
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "title": title,
        "story": story,
        "folder_path": folder,
        "tags": ["note"],
        "source": "https://example.test/source",
        "source_type": "web",
    }
    if status:
        frontmatter["status"] = status
    path.write_text(
        "---\n"
        + yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + body,
        encoding="utf-8",
    )


def _add_capture(
    config,
    vault: Path,
    *,
    name: str,
    title: str,
    body: str,
    folder: str = "Inbox",
    story: str = "",
    updated_at: str = "",
    archived: bool = False,
    mirror_metadata: bool = True,
) -> str:
    relative_path = f"captures/{name}.md"
    _write_note(vault, relative_path, title=title, body=body, folder=folder, story=story)
    conn = _conn(config)
    try:
        capture = CaptureRepo(conn).insert(file_path=relative_path)
        if mirror_metadata:
            CaptureMetadataRepo(conn).set_folder_path(capture["id"], folder)
        if updated_at:
            conn.execute(
                "UPDATE captures SET updated_at = ? WHERE id = ?",
                (updated_at, capture["id"]),
            )
        if archived:
            conn.execute(
                "UPDATE captures SET archived_at = '2026-01-01T00:00:00+00:00' WHERE id = ?",
                (capture["id"],),
            )
        conn.commit()
    finally:
        conn.close()
    return capture["id"]


def _add_draft(
    config,
    *,
    title: str,
    draft_text: str = "草稿正文",
    folder: str = "Inbox",
    status: str = "draft_ready",
    committed_capture_id: str = "",
    updated_at: str = "",
) -> str:
    conn = _conn(config)
    try:
        draft = CaptureDraftRepo(conn).create(
            raw_text="原始内容",
            draft_text=draft_text,
            title=title,
            status=status,
            capture_intent={"folder_path": folder},
        )
        conn.execute(
            """UPDATE capture_drafts
               SET status = ?, committed_capture_id = ?, updated_at = ?
               WHERE id = ?""",
            (status, committed_capture_id, updated_at or draft["updated_at"], draft["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return draft["id"]


def _add_card(
    config,
    *,
    card_id: str,
    title: str,
    status: str,
    payload: dict | None = None,
    one_liner: str = "",
    card_kind: str = "viewpoint",
    capture_ids: list[str] | None = None,
    updated_at: str = "2026-01-02T03:04:05+00:00",
    core_arguments: list | None = None,
    key_materials: list | None = None,
    knowledge_links: list | None = None,
    my_view: str = "",
) -> None:
    conn = _conn(config)
    card_payload = dict(payload or {})
    card_payload.setdefault("schema_version", "garden_card.v2")
    card_payload.setdefault("title", title)
    card_payload.setdefault("claim", one_liner)
    card_payload.setdefault("card_kind", card_kind)
    if core_arguments and not card_payload.get("core_judgments"):
        card_payload["core_judgments"] = [
            {
                "id": f"j{index}",
                "statement": str(item.get("claim") or item.get("statement") or ""),
                "reasoning": "",
                "support": [],
            }
            for index, item in enumerate(core_arguments, 1)
            if isinstance(item, dict)
        ]
    card_payload.setdefault("related_card_ids", knowledge_links or [])
    card_payload.setdefault("source_ids", capture_ids or [])
    if my_view:
        card_payload.setdefault(
            "action_items",
            [
                line.lstrip("- ").strip()
                for line in my_view.splitlines()
                if line.strip()
            ],
        )
    if key_materials and card_payload.get("core_judgments"):
        card_payload["core_judgments"][0]["support"] = [
            {
                "summary": str(item.get("content") or item.get("summary") or ""),
                "source_ids": [str(item.get("capture_id") or "")],
            }
            for item in key_materials
            if isinstance(item, dict)
        ]
    try:
        conn.execute(
            """INSERT INTO garden_cards
               (id, title, card_kind, payload_json, status, capture_ids,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card_id,
                title,
                card_kind,
                json.dumps(card_payload, ensure_ascii=False),
                status,
                json.dumps(capture_ids or [], ensure_ascii=False),
                updated_at,
                updated_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# GET /library/items
# ---------------------------------------------------------------------------

def _seed_library(config, vault: Path) -> dict[str, str]:
    ids = {
        "capture_inbox": _add_capture(
            config,
            vault,
            name="alpha",
            title="Alpha 研究",
            body="关于 RAG 的正文内容。",
            folder="Inbox",
            story="Alpha 摘要",
            updated_at="2026-01-05T00:00:00+00:00",
        ),
        "capture_study": _add_capture(
            config,
            vault,
            name="beta",
            title="Beta",
            body="无关正文。",
            folder="研究",
            updated_at="2026-01-03T00:00:00+00:00",
        ),
        "capture_archived": _add_capture(
            config,
            vault,
            name="gamma",
            title="Gamma",
            body="已归档。",
            folder="研究",
            updated_at="2026-01-06T00:00:00+00:00",
            archived=True,
        ),
        "draft_ready": _add_draft(
            config,
            title="草稿一",
            folder="Inbox",
            updated_at="2026-01-02T00:00:00+00:00",
        ),
        "draft_revision": _add_draft(
            config,
            title="草稿二",
            folder="研究",
            status="revision_requested",
            updated_at="2026-01-04T00:00:00+00:00",
        ),
        "draft_committed": _add_draft(
            config,
            title="草稿三",
            status="committed",
            committed_capture_id="cap-committed",
            updated_at="2026-01-07T00:00:00+00:00",
        ),
        "draft_abandoned": _add_draft(
            config,
            title="草稿四",
            status="abandoned",
            updated_at="2026-01-08T00:00:00+00:00",
        ),
    }
    return ids


def test_library_items_list_filters_sorting_and_counts(env):
    config, app, vault = env
    ids = _seed_library(config, vault)
    with _client(app) as client:
        listed = client.get("/api/library/items")
        assert listed.status_code == 200
        body = listed.json()
        # Archived capture and committed/abandoned drafts never show up.
        assert body["counts"] == {"capture": 2, "capture_draft": 2, "all": 4}
        assert body["total"] == 4
        assert [item["id"] for item in body["items"]] == [
            ids["capture_inbox"],  # 2026-01-05
            ids["draft_revision"],  # 2026-01-04
            ids["capture_study"],  # 2026-01-03
            ids["draft_ready"],  # 2026-01-02
        ]
        assert {item["kind"] for item in body["items"]} == {"capture", "capture_draft"}
        top = body["items"][0]
        for key in (
            "id",
            "kind",
            "title",
            "summary",
            "preview",
            "body",
            "folder_path",
            "tags",
            "source",
            "source_type",
            "status",
            "file_path",
            "raw_path",
            "word_count",
            "created_at",
            "updated_at",
            "indexed",
            "title_source",
        ):
            assert key in top
        assert top["body"] == ""  # list items keep the detail shape, empty body

        by_kind = client.get("/api/library/items", params={"kind": "capture"}).json()
        assert by_kind["total"] == 2
        assert all(item["kind"] == "capture" for item in by_kind["items"])
        assert by_kind["counts"] == {"capture": 2, "capture_draft": 2, "all": 4}

        by_folder = client.get("/api/library/items", params={"folder": "研究"}).json()
        assert by_folder["total"] == 2
        assert {item["folder_path"] for item in by_folder["items"]} == {"研究"}
        assert by_folder["counts"] == {"capture": 1, "capture_draft": 1, "all": 2}

        by_status = client.get("/api/library/items", params={"status": "draft_ready"}).json()
        assert by_status["total"] == 1
        assert by_status["items"][0]["id"] == ids["draft_ready"]
        assert by_status["counts"] == {"capture": 0, "capture_draft": 1, "all": 1}

        by_open_status = client.get(
            "/api/library/items", params={"status": "revision_requested"}
        ).json()
        assert by_open_status["total"] == 1
        assert by_open_status["items"][0]["id"] == ids["draft_revision"]

        by_search_body = client.get("/api/library/items", params={"search": "rag"}).json()
        assert by_search_body["total"] == 1
        assert by_search_body["items"][0]["id"] == ids["capture_inbox"]
        assert by_search_body["items"][0]["body"] == ""

        page = client.get("/api/library/items", params={"limit": 2, "offset": 2}).json()
        assert page["total"] == 4
        assert [item["id"] for item in page["items"]] == [
            ids["capture_study"],
            ids["draft_ready"],
        ]

        unknown_kind = client.get("/api/library/items", params={"kind": "other"}).json()
        assert unknown_kind["total"] == 0
        assert unknown_kind["items"] == []

        assert client.get("/api/library/items", params={"limit": 0}).status_code == 422
        assert client.get("/api/library/items", params={"limit": 501}).status_code == 422
        assert client.get("/api/library/items", params={"offset": -1}).status_code == 422


def _index_capture(config, capture_id: str, text: str, file_path: str) -> None:
    conn = _conn(config)
    try:
        CaptureEmbeddingRepo(conn).upsert(
            capture_id=capture_id,
            kind="identity",
            index_text=text,
            embedding=b"\0" * 4096,
            source_hash=capture_id,
            file_path=file_path,
        )
        conn.commit()
    finally:
        conn.close()


def test_library_folder_filter_reads_only_matching_note(env, monkeypatch):
    config, app, vault = env
    _add_capture(
        config, vault, name="inbox", title="Inbox note", body="inbox body", folder="Inbox"
    )
    study_id = _add_capture(
        config, vault, name="study", title="Study note", body="study body", folder="研究"
    )

    reads: list[str] = []
    original = library_view.read_capture_note

    def counting(vault_path, row):
        reads.append(str(row.get("file_path")))
        return original(vault_path, row)

    monkeypatch.setattr(library_view, "read_capture_note", counting)

    with _client(app) as client:
        body = client.get("/api/library/items", params={"folder": "研究"}).json()

    assert body["total"] == 1
    assert body["items"][0]["id"] == study_id
    assert reads == ["captures/study.md"]


def test_library_folder_filter_falls_back_without_metadata(env):
    config, app, vault = env
    capture_id = _add_capture(
        config,
        vault,
        name="legacy-study",
        title="Legacy study",
        body="legacy body",
        folder="研究",
        mirror_metadata=False,
    )

    with _client(app) as client:
        body = client.get("/api/library/items", params={"folder": "研究"}).json()

    assert body["total"] == 1
    assert body["items"][0]["id"] == capture_id


def test_library_search_filter_reads_only_indexed_match(env, monkeypatch):
    config, app, vault = env
    needle_id = _add_capture(
        config, vault, name="needle", title="Needle note", body="a needle here"
    )
    other_id = _add_capture(
        config, vault, name="other", title="Other note", body="unrelated body"
    )
    _index_capture(config, needle_id, "Needle note a needle here", "captures/needle.md")
    _index_capture(config, other_id, "Other note unrelated body", "captures/other.md")

    reads: list[str] = []
    original = library_view.read_capture_note

    def counting(vault_path, row):
        reads.append(str(row.get("file_path")))
        return original(vault_path, row)

    monkeypatch.setattr(library_view, "read_capture_note", counting)

    with _client(app) as client:
        body = client.get("/api/library/items", params={"search": "needle"}).json()

    assert body["total"] == 1
    assert body["items"][0]["id"] == needle_id
    assert reads == ["captures/needle.md"]


def test_library_search_falls_back_without_embeddings(env):
    config, app, vault = env
    capture_id = _add_capture(
        config, vault, name="body-only", title="Body only", body="正文里藏着 needle"
    )

    with _client(app) as client:
        body = client.get("/api/library/items", params={"search": "needle"}).json()

    assert body["total"] == 1
    assert body["items"][0]["id"] == capture_id


def test_library_item_detail(env):
    config, app, vault = env
    capture_id = _add_capture(
        config,
        vault,
        name="detail",
        title="详情标题",
        body="详情正文。",
        folder="待读",
        story="详情摘要",
    )
    draft_id = _add_draft(config, title="草稿详情", draft_text="草稿完整正文")

    with _client(app) as client:
        capture = client.get(f"/api/library/items/capture/{capture_id}")
        assert capture.status_code == 200
        capture_body = capture.json()
        assert capture_body["ok"] is True
        assert capture_body["item"]["id"] == capture_id
        assert capture_body["item"]["kind"] == "capture"
        assert capture_body["item"]["title"] == "详情标题"
        assert capture_body["item"]["summary"] == "详情摘要"
        assert capture_body["item"]["body"] == "详情正文。"
        assert capture_body["item"]["folder_path"] == "待读"
        assert capture_body["item"]["file_path"] == "captures/detail.md"

        draft = client.get(f"/api/library/items/capture_draft/{draft_id}")
        assert draft.status_code == 200
        assert draft.json()["item"]["kind"] == "capture_draft"
        assert draft.json()["item"]["body"] == "草稿完整正文"

        assert client.get("/api/library/items/capture/missing").status_code == 404
        assert client.get("/api/library/items/capture_draft/missing").status_code == 404
        assert client.get("/api/library/items/unknown/xyz").status_code == 400


# ---------------------------------------------------------------------------
# Garden cards + card deletion
# ---------------------------------------------------------------------------

def test_garden_cards_list_and_delete_only_unlinks(env):
    config, app, vault = env
    capture_id = _add_capture(
        config,
        vault,
        name="source",
        title="来源文章",
        body="来源正文。",
        story="来源摘要",
    )
    _add_card(
        config,
        card_id="card-v2",
        title="行标题",
        status="seed",
        payload={
            "title": "卡片标题",
            "claim": "核心观点",
            "card_kind": "method",
            "source_ids": [capture_id],
        },
        core_arguments=[{"claim": "论点", "source_ids": [capture_id]}],
        key_materials=[{"type": "quote", "content": "引用"}],
        knowledge_links=["other"],
        my_view="我的看法",
        updated_at="2026-01-05T00:00:00+00:00",
    )
    _add_card(
        config,
        card_id="card-archived",
        title="旧卡片",
        status="archived",
        one_liner="旧一句话",
        card_kind="fact",
        capture_ids=[capture_id],
        updated_at="2026-01-06T00:00:00+00:00",
    )

    with _client(app) as client:
        listed = client.get("/api/garden/cards")
        assert listed.status_code == 200
        body = listed.json()
        assert body["total"] == 1
        card = body["items"][0]
        assert card["id"] == "card-v2"
        assert card["title"] == "卡片标题"
        assert card["kind"] == "method"
        assert card["summary"] == "核心观点"
        assert card["status"] == "seed"
        assert card["my_view"] == "- 我的看法"
        assert card["core_arguments"] == [{"claim": "论点", "consensus": True, "source_ids": []}]
        assert card["key_materials"] == [{"content": "引用", "capture_id": ""}]
        assert card["knowledge_links"] == ["other"]
        assert card["source_count"] == 1
        assert card["sources"] == [
            {
                "id": capture_id,
                "title": "来源文章",
                "file_path": "captures/source.md",
                "summary": "来源摘要",
            }
        ]

        archived = client.get("/api/garden/cards", params={"status": "archived"}).json()
        assert archived["total"] == 1
        assert archived["items"][0]["id"] == "card-archived"
        assert archived["items"][0]["title"] == "旧卡片"
        assert archived["items"][0]["summary"] == "旧一句话"

        limited = client.get("/api/garden/cards", params={"limit": 1}).json()
        assert limited["total"] == 1
        assert client.get("/api/garden/cards", params={"limit": 0}).status_code == 422

        deleted = client.delete("/api/garden/cards/card-v2")
        assert deleted.status_code == 200
        deleted_body = deleted.json()
        # Archive semantics: the card row survives with status=archived, so the
        # HTTP path matches the dashboard's local DELETE /api/knowledge/cards.
        assert deleted_body.get("status") != "error"
        artifacts = deleted_body.get("durable_artifacts") or []
        assert any(
            item.get("id") == "card-v2" and item.get("status") == "archived"
            for item in artifacts
            if isinstance(item, dict)
        )

        assert client.get("/api/garden/cards").json()["total"] == 0
        # Archiving twice is an error envelope, not a 404 (same as local).
        second = client.delete("/api/garden/cards/card-v2")
        assert second.status_code == 200
        assert second.json().get("status") == "error"

    conn = _conn(config)
    try:
        row = conn.execute(
            "SELECT status FROM garden_cards WHERE id = 'card-v2'"
        ).fetchone()
        capture_row = conn.execute(
            "SELECT id, archived_at FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row["status"] == "archived"
    assert capture_row is not None  # archiving a card never deletes its captures
    assert capture_row["archived_at"] is None
    assert (vault / "captures" / "source.md").is_file()


def test_garden_draft_synthesis_stops_at_the_draft(env, monkeypatch):
    """``POST /garden/drafts`` no longer commits as a side effect."""
    config, app, vault = env
    captured: dict = {}
    commit_calls: list[dict] = []
    envelope = {
        "status": "success",
        "agent": "garden",
        "intent": "synthesize_garden",
        "chat_preview": "已生成卡片草稿",
        "durable_artifacts": [
            {"type": "garden_draft", "id": "draft-1", "title": "卡片", "status": "draft_ready"}
        ],
        "meta": {"draft_id": "draft-1"},
    }

    def fake_synthesize_garden(**kwargs):
        captured.update(kwargs)
        return envelope

    def fake_commit_garden_draft(**kwargs):
        commit_calls.append(kwargs)
        return {"status": "success", "intent": "commit_garden_draft"}

    from bridge import garden_bridge

    monkeypatch.setattr(garden_bridge, "synthesize_garden", fake_synthesize_garden)
    monkeypatch.setattr(garden_bridge, "commit_garden_draft", fake_commit_garden_draft)

    with _client(app) as client:
        response = client.post(
            "/api/garden/drafts",
            json={
                "capture_ids": ["cap-1", "cap-2"],
                "title": "标题",
                "card_kind": "method",
                "instruction": "按方法论整理",
            },
        )

    assert response.status_code == 200
    assert response.json() == envelope
    assert captured["capture_ids"] == ["cap-1", "cap-2"]
    assert captured["title"] == "标题"
    assert captured["card_kind"] == "method"
    assert captured["instruction"] == "按方法论整理"
    assert captured["context"] is not None
    # No token, no commit: synthesis only returns the reviewable draft.
    assert commit_calls == []


# ---------------------------------------------------------------------------
# Existing answer-draft / graph endpoints stay runtime-compatible
# ---------------------------------------------------------------------------

def test_answer_draft_and_graph_routes(env, monkeypatch):
    config, app, vault = env
    paths = {route.path for route in app.routes}
    assert "/api/knowledge/answer-drafts" in paths
    assert "/api/knowledge/graph" in paths
    assert "/api/knowledge/graph/refresh" in paths

    from bridge import answer_bridge
    from agents.knowledge_graph import KnowledgeGraphService

    saved: dict = {}

    def fake_save_answer(**kwargs):
        saved.update(kwargs)
        return {"status": "success", "agent": "answer", "chat_preview": "已保存草稿"}

    monkeypatch.setattr(answer_bridge, "save_answer_to_knowledge", fake_save_answer)
    monkeypatch.setattr(
        KnowledgeGraphService,
        "focus_graph",
        lambda self, **kwargs: {"nodes": [], "edges": [], "focus": kwargs},
    )
    monkeypatch.setattr(
        KnowledgeGraphService,
        "refresh",
        lambda self: {"refreshed": True, "edges": 0},
    )

    with _client(app) as client:
        answer = client.post(
            "/api/knowledge/answer-drafts",
            json={
                "hermes_session_id": "session-1",
                "user_message": "问题",
                "assistant_answer": "答案",
                "instruction": "保存",
                "output_type": "auto",
                "assistant_message_id": "msg-1",
                "sources": [],
                "target_capture_id": "",
                "answer_source": "auto",
            },
        )
        assert answer.status_code == 200
        assert answer.json()["status"] == "success"
        assert saved["hermes_session_id"] == "session-1"

        graph = client.get(
            "/api/knowledge/graph",
            params={"focus_kind": "capture", "focus_id": "cap-1", "depth": 1, "limit": 5},
        )
        assert graph.status_code == 200
        assert graph.json()["focus"]["focus_id"] == "cap-1"

        refreshed = client.post("/api/knowledge/graph/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json() == {"refreshed": True, "edges": 0}

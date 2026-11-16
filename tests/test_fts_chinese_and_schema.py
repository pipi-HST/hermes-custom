"""Batch 1 — 中文 2-gram FTS5 与 V3 schema 迁移。

这些断言在实现之前必须失败，失败原因只能是「表/列不存在」或「命中为 0」，
不能是语法错误或 ImportError。跑红之后再写实现。
"""
from __future__ import annotations

import os
import shutil
import struct
import tempfile
import uuid
from pathlib import Path

import pytest

from agents.embedder import DIM
from db.embedding_repo import CaptureEmbeddingRepo
from db.schema import init_db


def _rebuild_fts(conn) -> int:
    """Import late so a missing implementation fails as an assertion, not a traceback."""
    try:
        from db.embedding_repo import rebuild_fts_from_embeddings
    except ImportError:  # pragma: no cover - red phase only
        pytest.fail("db.embedding_repo.rebuild_fts_from_embeddings 尚未实现（Batch 1 必需）")
    return rebuild_fts_from_embeddings(conn)

DOC = "一文搞懂 MCP 与 Agent 工具调用收敛策略，建议保留不超过 8 个工具。"
NEGATIVE_QUERY = "面试"


def _emb(value: float = 0.1) -> bytes:
    return struct.pack(f"{DIM}f", *([value] * DIM))


def _test_root() -> Path:
    """Temp root that child processes can actually create directories in."""
    root = Path(os.environ.get("HERMES_TEST_TMP") or tempfile.gettempdir())
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def workdir():
    path = _test_root() / f"hermes-b1-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def conn(workdir):
    connection = init_db(str(workdir / "fts.db"))
    try:
        yield connection
    finally:
        connection.close()


def _insert_embedding(
    repo: CaptureEmbeddingRepo,
    *,
    capture_id: str = "cap_mcp",
    kind: str = "section",
    section_id: str = "section-3",
    section_title: str = "工具调用收敛策略",
    index_text: str = DOC,
    source_hash: str = "h1",
) -> None:
    repo.upsert(
        capture_id=capture_id,
        kind=kind,
        section_id=section_id,
        section_title=section_title,
        index_text=index_text,
        embedding=_emb(),
        source_hash=source_hash,
        file_path="captures/mcp.md",
    )


def _fts_match(conn, query: str) -> int:
    from db.fts_tokenizer import build_fts_match_query

    match = build_fts_match_query([query])
    if not match:
        return 0
    row = conn.execute(
        "SELECT count(*) FROM capture_search_fts WHERE capture_search_fts MATCH ?",
        (match,),
    ).fetchone()
    return int(row[0])


def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# --------------------------------------------------------------------------
# 断言 1：中文子串与双字词必须命中（unicode61 与 trigram 都过不了的关）
# --------------------------------------------------------------------------
def test_cjk_substring_and_two_char_words_match(conn):
    _insert_embedding(CaptureEmbeddingRepo(conn))

    assert _fts_match(conn, "收敛策略") >= 1, "4 字中文短语必须命中"
    assert _fts_match(conn, "工具") >= 1, "中文双字词必须命中（trigram 在此为 0）"
    assert _fts_match(conn, NEGATIVE_QUERY) == 0, "库里没有的词必须为 0，防止全表命中"


# --------------------------------------------------------------------------
# 断言 2：schema 迁移后的列与表
# --------------------------------------------------------------------------
def test_schema_records_user_version(conn):
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_reinit_backfills_garden_content_from_payload(tmp_path):
    import json

    db_path = str(tmp_path / "cards.db")
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO garden_cards (id, title, payload_json) VALUES (?, ?, ?)",
        ("card1", "卡片", json.dumps({"content_markdown": "# 正文\n\n完整内容"}, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

    reopened = init_db(db_path)
    try:
        row = reopened.execute(
            "SELECT content_markdown FROM garden_cards WHERE id = 'card1'"
        ).fetchone()
        assert row["content_markdown"] == "# 正文\n\n完整内容"
    finally:
        reopened.close()


def test_garden_cards_recall_index_exists(conn):
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(garden_cards)").fetchall()
    }
    assert "idx_garden_cards_recall" in indexes


def test_captures_migration_keeps_unknown_columns(tmp_path):
    import sqlite3

    db_path = tmp_path / "legacy.db"
    raw = sqlite3.connect(db_path)
    raw.execute(
        """CREATE TABLE captures (
               id TEXT PRIMARY KEY,
               file_path TEXT NOT NULL DEFAULT '',
               legacy_note TEXT NOT NULL DEFAULT ''
           )"""
    )
    raw.execute(
        "INSERT INTO captures (id, file_path, legacy_note) VALUES ('cap1', 'captures/a.md', 'keep me')"
    )
    raw.commit()
    raw.close()

    conn = init_db(str(db_path))
    try:
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(captures)").fetchall()
        }
        assert "legacy_note" in columns
        assert conn.execute(
            "SELECT legacy_note FROM captures WHERE id = 'cap1'"
        ).fetchone()[0] == "keep me"
    finally:
        conn.close()


def test_captures_created_at_index_exists(conn):
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(captures)").fetchall()
    }
    assert "idx_captures_created_at" in indexes


def test_schema_migrations_applied(conn):
    card_columns = _columns(conn, "garden_cards")
    assert {"source_theme", "embedding", "citation_count", "last_cited_at"} <= card_columns

    metadata_columns = _columns(conn, "capture_metadata")
    assert "folder_path" in metadata_columns

    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "citation_events" in tables
    assert "capture_search_fts" in tables


# --------------------------------------------------------------------------
# 断言 3：FTS 是派生数据，必须能从 capture_embeddings 全量重灌
# --------------------------------------------------------------------------
def test_fts_rebuild_restores_derived_rows(conn):
    repo = CaptureEmbeddingRepo(conn)
    _insert_embedding(repo)
    _insert_embedding(
        repo,
        kind="identity",
        section_id="",
        section_title="",
        index_text="标题：一文搞懂 MCP\n摘要：模型上下文协议",
    )

    embedding_count = conn.execute("SELECT count(*) FROM capture_embeddings").fetchone()[0]
    assert embedding_count == 2

    # 模拟「迁移前写入的存量数据」：FTS 表是空的
    conn.execute("DELETE FROM capture_search_fts")
    assert conn.execute("SELECT count(*) FROM capture_search_fts").fetchone()[0] == 0

    rebuilt = _rebuild_fts(conn)

    assert rebuilt == embedding_count
    assert conn.execute("SELECT count(*) FROM capture_search_fts").fetchone()[0] == embedding_count
    assert _fts_match(conn, "收敛策略") >= 1, "重建后必须立刻可检索"


# --------------------------------------------------------------------------
# 断言 4：upsert / delete_missing 必须同步维护 FTS（增量一致性）
# --------------------------------------------------------------------------
def test_upsert_and_delete_keep_fts_in_sync(conn):
    repo = CaptureEmbeddingRepo(conn)
    _insert_embedding(repo)
    _insert_embedding(repo, kind="identity", section_id="", section_title="", index_text="旧概览")
    assert conn.execute("SELECT count(*) FROM capture_search_fts").fetchone()[0] == 2

    # 同一 (capture_id, kind, section_id) 覆盖写不应产生重复 FTS 行
    _insert_embedding(repo, source_hash="h2", index_text="新的工具调用收敛策略正文")
    assert conn.execute("SELECT count(*) FROM capture_search_fts").fetchone()[0] == 2

    # 清理过期 section 时 FTS 也要跟着删
    repo.delete_missing_for_capture("cap_mcp", {("section", "section-3")})
    rows = conn.execute("SELECT kind, section_id FROM capture_search_fts").fetchall()
    assert [(row[0], row[1]) for row in rows] == [("section", "section-3")]
    assert _fts_match(conn, "收敛策略") >= 1


# --------------------------------------------------------------------------
# 断言 6：升级存量库时，空 FTS 必须自动补齐（否则搜索结果"看起来像没数据"）
# --------------------------------------------------------------------------
def test_reinit_backfills_empty_fts(workdir):
    db_path = str(workdir / "upgrade.db")
    conn = init_db(db_path)
    try:
        _insert_embedding(CaptureEmbeddingRepo(conn))
        conn.execute("DELETE FROM capture_search_fts")  # 模拟迁移前的存量库
        conn.commit()
        assert conn.execute("SELECT count(*) FROM capture_search_fts").fetchone()[0] == 0
    finally:
        conn.close()

    upgraded = init_db(db_path)  # 再次打开 = 应用启动
    try:
        embeddings = upgraded.execute("SELECT count(*) FROM capture_embeddings").fetchone()[0]
        fts = upgraded.execute("SELECT count(*) FROM capture_search_fts").fetchone()[0]
        assert embeddings == fts == 1
        assert _fts_match(upgraded, "收敛策略") >= 1
    finally:
        upgraded.close()


# --------------------------------------------------------------------------
# 断言 5：关键词兜底走 FTS，归档资料不得出现，且不再读 500 个磁盘文件
# --------------------------------------------------------------------------
def test_keyword_match_uses_fts_and_skips_archived(workdir):
    from bridge.rag_bridge import _keyword_match_captures
    from db.repo import CaptureRepo

    vault = workdir / "vault"
    (vault / "captures").mkdir(parents=True)
    (vault / "captures" / "mcp.md").write_text(
        "---\ntitle: 一文搞懂 MCP\nstory: 模型上下文协议\ntags: [MCP, 工具]\n---\n\n"
        "## 工具调用收敛策略\n\n建议保留不超过 8 个工具。\n",
        encoding="utf-8",
    )

    conn = init_db(str(workdir / "kb.db"))
    try:
        repo = CaptureRepo(conn)
        capture = repo.insert(file_path="captures/mcp.md")
        _insert_embedding(CaptureEmbeddingRepo(conn), capture_id=capture["id"])

        hits = _keyword_match_captures("工具调用收敛策略怎么设计？", repo, str(vault), limit=3)
        assert hits, "关键词命中必须返回结果"
        assert hits[0]["id"] == capture["id"]
        assert hits[0]["title"] == "一文搞懂 MCP"
        assert hits[0]["matched_kind"] in {"section", "identity"}

        assert _keyword_match_captures("提拉米苏怎么做", repo, str(vault), limit=3) == []

        repo.update(capture["id"], review_state="archived")
        assert _keyword_match_captures("工具调用收敛策略", repo, str(vault), limit=3) == []
    finally:
        conn.close()

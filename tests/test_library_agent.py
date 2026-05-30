"""Tests for Library Agent vault maintenance."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agents.markdown_writer import read_note
from agents.library_agent import LibraryAgent
from db.schema import init_db


class FakeOutliner:
    def __init__(self, outline: list[dict] | None = None, fail: bool = False) -> None:
        self.outline = outline or [
            {
                "level": 1,
                "title": "战略转向",
                "range": "段1-4",
                "summary": "说明产品节奏背后的战略变化。",
            },
            {
                "level": 2,
                "title": "Benchmark 信号",
                "range": "段5-8",
                "summary": "解释测试缺口为什么值得关注。",
            },
        ]
        self.fail = fail
        self.calls: list[tuple[str, int]] = []

    def generate(self, cleaned_text: str, word_count: int) -> list[dict]:
        self.calls.append((cleaned_text, word_count))
        if self.fail:
            raise RuntimeError("outline failed")
        return self.outline


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str) -> bytes:
        self.calls.append(text)
        return text[:8].encode("utf-8")


def _write_capture(
    vault: Path,
    name: str = "long.md",
    *,
    body: str,
    outline: list[dict] | None = None,
) -> str:
    frontmatter = {
        "id": name.removesuffix(".md"),
        "title": "测试长文",
        "date": "2026-05-29",
        "created": "2026-05-29T00:00:00+00:00",
        "source": "https://example.com/post",
        "source_type": "web",
        "tags": ["ai"],
        "story": "",
        "word_count": len(body),
    }
    if outline is not None:
        frontmatter["outline"] = outline

    import yaml

    captures = vault / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    rel_path = f"captures/{name}"
    (vault / rel_path).write_text(
        "---\n"
        + yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + body,
        encoding="utf-8",
    )
    return rel_path


def test_dry_run_reports_long_capture_without_writing(tmp_path: Path) -> None:
    body = "# 测试长文\n\n" + "正文段落。\n\n" * 80
    rel_path = _write_capture(tmp_path, body=body)
    before = (tmp_path / rel_path).read_text(encoding="utf-8")
    outliner = FakeOutliner()

    report = LibraryAgent(tmp_path, outliner=outliner, min_outline_chars=100).maintain_outlines()

    assert report["dry_run"] is True
    assert report["needs_outline"] == 1
    assert report["updated"] == 0
    assert report["items"][0]["action"] == "needs_outline"
    assert (tmp_path / rel_path).read_text(encoding="utf-8") == before
    assert outliner.calls == []


def test_apply_stores_outline_in_db_and_removes_frontmatter_outline(
    tmp_path: Path,
) -> None:
    body = "# 测试长文\n\n第一段。\n\n第二段。\n\n" + "补充内容。\n\n" * 40
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.commit()
    outliner = FakeOutliner()

    report = LibraryAgent(
        tmp_path,
        outliner=outliner,
        min_outline_chars=100,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    assert report["dry_run"] is False
    assert report["updated"] == 1
    note = read_note(tmp_path, rel_path)
    assert note is not None
    frontmatter, updated_body = note
    assert "outline" not in frontmatter
    assert "## 目录纲要" in updated_body
    assert "- 战略转向：说明产品节奏背后的战略变化。" in updated_body
    assert "第一段。\n\n第二段。" in updated_body
    assert updated_body.count("## 目录纲要") == 1
    row = conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        ("long",),
    ).fetchone()
    assert row is not None
    assert "战略转向" in row["outline_json"]
    assert outliner.calls and outliner.calls[0][0] == body


def test_existing_metadata_outline_and_real_headings_are_not_duplicated(tmp_path: Path) -> None:
    outline = [{"level": 1, "title": "已有目录", "summary": "已经整理过。"}]
    body = (
        "# 测试长文\n\n"
        "## 目录纲要\n\n"
        "- 已有目录：已经整理过。\n\n"
        "## 已有目录\n\n"
        "正文。\n\n"
        + "更多。\n\n" * 40
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.execute(
        """INSERT INTO capture_metadata
           (capture_id, outline_json, outline_source_hash)
           VALUES (?, ?, ?)""",
        ("long", '[{"level": 1, "title": "已有目录", "summary": "已经整理过。"}]', "hash"),
    )
    conn.commit()
    before = (tmp_path / rel_path).read_text(encoding="utf-8")
    outliner = FakeOutliner()

    report = LibraryAgent(
        tmp_path,
        outliner=outliner,
        min_outline_chars=100,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    assert report["skipped_existing"] == 1
    assert (tmp_path / rel_path).read_text(encoding="utf-8") == before
    assert outliner.calls == []


def test_apply_inserts_markdown_headings_for_unstructured_long_note(
    tmp_path: Path,
) -> None:
    outline = [
        {"level": 1, "title": "第一部分", "summary": "第一段内容。"},
        {"level": 1, "title": "第二部分", "summary": "第二段内容。"},
    ]
    body = (
        "# 测试长文\n\n"
        "## 目录纲要\n\n"
        "- 第一部分：第一段内容。\n"
        "- 第二部分：第二段内容。\n\n"
        "一、第一部分\n"
        "这里是原来的第一段正文。\n\n"
        "二、第二部分\n"
        "这里是原来的第二段正文。\n"
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.execute(
        """INSERT INTO capture_metadata
           (capture_id, outline_json, outline_source_hash)
           VALUES (?, ?, ?)""",
        (
            "long",
            '[{"level": 1, "title": "第一部分", "summary": "第一段内容。"}, '
            '{"level": 1, "title": "第二部分", "summary": "第二段内容。"}]',
            "hash",
        ),
    )
    conn.commit()

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(outline=outline),
        min_outline_chars=20,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    assert report["updated"] == 1
    assert "## 第一部分\n\n一、第一部分" in updated_body
    assert "## 第二部分\n\n二、第二部分" in updated_body
    assert "这里是原来的第一段正文。" in updated_body
    assert "这里是原来的第二段正文。" in updated_body
    assert updated_body.count("## 第一部分") == 1
    assert updated_body.count("## 第二部分") == 1


def test_repaired_long_note_builds_section_embeddings(tmp_path: Path) -> None:
    outline = [
        {"level": 1, "title": "第一部分", "summary": "第一段内容。"},
        {"level": 1, "title": "第二部分", "summary": "第二段内容。"},
    ]
    body = (
        "# 测试长文\n\n"
        "## 目录纲要\n\n"
        "- 第一部分：第一段内容。\n"
        "- 第二部分：第二段内容。\n\n"
        "一、第一部分\n"
        "这里是原来的第一段正文。\n\n"
        "二、第二部分\n"
        "这里是原来的第二段正文。\n"
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.execute(
        """INSERT INTO capture_metadata
           (capture_id, outline_json, outline_source_hash)
           VALUES (?, ?, ?)""",
        (
            "long",
            '[{"level": 1, "title": "第一部分", "summary": "第一段内容。"}, '
            '{"level": 1, "title": "第二部分", "summary": "第二段内容。"}]',
            "hash",
        ),
    )
    conn.commit()
    agent = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(outline=outline),
        min_outline_chars=20,
        db_conn=conn,
        embedder=FakeEmbedder(),
    )

    agent.maintain_outlines(apply=True)
    report = agent.maintain_embedding_index(apply=True)

    rows = conn.execute(
        "SELECT kind, section_title FROM capture_embeddings ORDER BY kind, section_id"
    ).fetchall()
    assert report["updated"] == 3
    assert [(row["kind"], row["section_title"]) for row in rows] == [
        ("identity", ""),
        ("section", "第一部分"),
        ("section", "第二部分"),
    ]


def test_apply_splits_inline_chinese_section_markers_without_rewriting(
    tmp_path: Path,
) -> None:
    outline = [
        {"level": 1, "title": "第一部分", "summary": "第一段内容。"},
        {"level": 1, "title": "第二部分", "summary": "第二段内容。"},
    ]
    body = (
        "# 测试长文\n\n"
        "## 目录纲要\n\n"
        "- 第一部分：第一段内容。\n"
        "- 第二部分：第二段内容。\n\n"
        "开头一句。一、第一部分这里是原来的第一段正文。"
        "二、第二部分这里是原来的第二段正文。"
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.execute(
        """INSERT INTO capture_metadata
           (capture_id, outline_json, outline_source_hash)
           VALUES (?, ?, ?)""",
        (
            "long",
            '[{"level": 1, "title": "第一部分", "summary": "第一段内容。"}, '
            '{"level": 1, "title": "第二部分", "summary": "第二段内容。"}]',
            "hash",
        ),
    )
    conn.commit()

    LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(outline=outline),
        min_outline_chars=20,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    assert "开头一句。\n\n## 第一部分\n\n一、第一部分这里是原来的第一段正文。" in updated_body
    assert "\n\n## 第二部分\n\n二、第二部分这里是原来的第二段正文。" in updated_body


def test_apply_promotes_bold_section_labels_without_rewriting(tmp_path: Path) -> None:
    outline = [
        {"level": 1, "title": "第一部分", "summary": "第一段内容。"},
        {"level": 1, "title": "第二部分", "summary": "第二段内容。"},
    ]
    body = (
        "# 测试长文\n\n"
        "## 目录纲要\n\n"
        "- 第一部分：第一段内容。\n"
        "- 第二部分：第二段内容。\n\n"
        "**第一部分**\n\n"
        "这里是原来的第一段正文。\n\n"
        "**第二部分**\n\n"
        "这里是原来的第二段正文。\n"
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.execute(
        """INSERT INTO capture_metadata
           (capture_id, outline_json, outline_source_hash)
           VALUES (?, ?, ?)""",
        (
            "long",
            '[{"level": 1, "title": "第一部分", "summary": "第一段内容。"}, '
            '{"level": 1, "title": "第二部分", "summary": "第二段内容。"}]',
            "hash",
        ),
    )
    conn.commit()

    LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(outline=outline),
        min_outline_chars=20,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    assert "## 第一部分\n\n**第一部分**" in updated_body
    assert "## 第二部分\n\n**第二部分**" in updated_body


def test_existing_frontmatter_outline_is_migrated_to_db_and_removed(tmp_path: Path) -> None:
    outline = [{"level": 1, "title": "已有目录", "summary": "已经整理过。"}]
    body = "# 测试长文\n\n## 目录纲要\n\n- 已有目录\n\n正文。\n\n" + "更多。\n\n" * 40
    rel_path = _write_capture(tmp_path, body=body, outline=outline)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.commit()

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(),
        min_outline_chars=100,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    frontmatter, updated_body = note
    assert "outline" not in frontmatter
    assert updated_body.count("## 目录纲要") == 1
    row = conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        ("long",),
    ).fetchone()
    assert row is not None
    assert "已有目录" in row["outline_json"]
    assert report["updated"] == 1


def test_short_capture_is_skipped_without_calling_outliner(tmp_path: Path) -> None:
    _write_capture(tmp_path, name="short.md", body="# 短文\n\n内容。")
    outliner = FakeOutliner()

    report = LibraryAgent(tmp_path, outliner=outliner, min_outline_chars=100).maintain_outlines(
        apply=True
    )

    assert report["skipped_short"] == 1
    assert report["updated"] == 0
    assert outliner.calls == []


def test_short_capture_legacy_outline_is_migrated_without_directory(
    tmp_path: Path,
) -> None:
    outline = [{"level": 1, "title": "短文结构", "summary": "只迁移到数据库。"}]
    rel_path = _write_capture(
        tmp_path,
        name="short.md",
        body="# 短文\n\n内容。",
        outline=outline,
    )
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("short", rel_path))
    conn.commit()
    outliner = FakeOutliner()

    report = LibraryAgent(
        tmp_path,
        outliner=outliner,
        min_outline_chars=100,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    frontmatter, body = note
    assert "outline" not in frontmatter
    assert "## 目录纲要" not in body
    row = conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        ("short",),
    ).fetchone()
    assert row is not None
    assert "短文结构" in row["outline_json"]
    assert report["updated"] == 1
    assert outliner.calls == []


def test_outliner_failure_is_reported_without_writing(tmp_path: Path) -> None:
    body = "# 测试长文\n\n" + "正文段落。\n\n" * 80
    rel_path = _write_capture(tmp_path, body=body)
    before = (tmp_path / rel_path).read_text(encoding="utf-8")

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(fail=True),
        min_outline_chars=100,
    ).maintain_outlines(apply=True)

    assert report["errors"] == 1
    assert report["items"][0]["action"] == "outline_error"
    assert "outline failed" in report["items"][0]["error"]
    assert (tmp_path / rel_path).read_text(encoding="utf-8") == before


def test_library_maintenance_runner_uses_configured_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "# 测试长文\n\n" + "正文段落。\n\n" * 80
    _write_capture(tmp_path, body=body)

    import scripts.library_maintenance as library_maintenance

    class Config:
        vault_path = str(tmp_path)
        db_path = str(tmp_path / "captures.db")
        llm_api_key = "sk-test"
        llm_model = "test-model"
        llm_base_url = "https://example.com"
        qwen_api_key = "qwen-test"

    monkeypatch.setattr(library_maintenance, "load_config", lambda _path: Config())
    created_models: list[str] = []

    def fake_outliner(**kwargs):
        created_models.append(kwargs["model"])
        return FakeOutliner()

    monkeypatch.setattr(library_maintenance, "Embedder", lambda api_key: FakeEmbedder())

    sync_calls: list[dict] = []

    def fake_repair_data(db_path, vault_path, *, apply, rename_readable):
        sync_calls.append(
            {
                "db_path": db_path,
                "vault_path": vault_path,
                "apply": apply,
                "rename_readable": rename_readable,
            }
        )
        return {"dry_run": not apply, "planned_actions": []}

    monkeypatch.setattr(library_maintenance, "Outliner", fake_outliner)
    monkeypatch.setattr("agents.library_agent.repair_data", fake_repair_data)

    report = library_maintenance.run_library_maintenance(
        config_path="test.yaml",
        min_outline_chars=100,
    )

    assert report["dry_run"] is True
    assert report["database_sync"]["dry_run"] is True
    assert report["outlines"]["needs_outline"] == 1
    assert report["property_gaps"]["planned_action_count"] == 1
    assert "embedding_index" in report
    assert sync_calls == [
        {
            "db_path": Path(Config.db_path),
            "vault_path": Path(Config.vault_path),
            "apply": False,
            "rename_readable": True,
        }
    ]
    assert created_models == ["deepseek-v4-flash"]


def test_library_agent_database_sync_delegates_to_repair_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    def fake_repair_data(db_path, vault_path, *, apply, rename_readable):
        calls.append(
            {
                "db_path": db_path,
                "vault_path": vault_path,
                "apply": apply,
                "rename_readable": rename_readable,
            }
        )
        return {"dry_run": not apply, "planned_actions": [{"type": "delete_missing_capture"}]}

    monkeypatch.setattr("agents.library_agent.repair_data", fake_repair_data)

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(),
        db_path=tmp_path / "captures.db",
    ).maintain_database_sync(apply=True)

    assert report["planned_actions"] == [{"type": "delete_missing_capture"}]
    assert calls == [
        {
            "db_path": tmp_path / "captures.db",
            "vault_path": tmp_path,
            "apply": True,
            "rename_readable": True,
        }
    ]


def test_library_agent_reports_capture_and_raw_property_gaps(tmp_path: Path) -> None:
    capture_body = "# 长文\n\n正文"
    _write_capture(tmp_path, name="cap.md", body=capture_body)
    raw_dir = tmp_path / "_archive" / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "raw.md").write_text(
        "---\nid: raw-1\ntitle: 原文\nstatus: raw\n---\n\n原文",
        encoding="utf-8",
    )

    report = LibraryAgent(tmp_path, outliner=FakeOutliner()).audit_property_gaps()

    assert report["capture_notes_scanned"] == 1
    assert report["raw_notes_scanned"] == 1
    assert report["capture_gap_count"] == 1
    assert report["raw_gap_count"] == 1
    capture_gap = report["capture_gaps"][0]
    assert capture_gap["file_path"] == "captures/cap.md"
    assert "type" in capture_gap["missing_fields"]
    assert "reviewable" in capture_gap["missing_fields"]
    raw_gap = report["raw_gaps"][0]
    assert raw_gap["file_path"] == "_archive/raw/raw.md"
    assert "type" in raw_gap["missing_fields"]
    assert "capture_id" in raw_gap["missing_fields"]


def _write_raw_for_property_test(vault: Path, rel_path: str) -> None:
    raw_path = vault / rel_path
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(
        "\n".join(
            [
                "---",
                "id: raw-1",
                "title: 原文",
                "date: '2026-05-29'",
                "source: https://example.com",
                "source_type: link",
                "---",
                "",
                "原文",
            ]
        ),
        encoding="utf-8",
    )


def _write_property_test_db(db_path: Path, capture_rel: str, raw_rel: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE captures (id TEXT PRIMARY KEY, file_path TEXT, raw_path TEXT)")
    conn.execute(
        "INSERT INTO captures (id, file_path, raw_path) VALUES (?, ?, ?)",
        ("cap-1", capture_rel, raw_rel),
    )
    conn.commit()
    conn.close()


def test_library_agent_dry_run_reports_safe_property_backfills_without_writing(
    tmp_path: Path,
) -> None:
    capture_rel = _write_capture(tmp_path, name="cap-1.md", body="# 笔记\n\n正文")
    raw_rel = "_archive/raw/raw-1.md"
    raw_path = tmp_path / raw_rel
    _write_raw_for_property_test(tmp_path, raw_rel)
    db_path = tmp_path / "captures.db"
    _write_property_test_db(db_path, capture_rel, raw_rel)
    before = raw_path.read_text(encoding="utf-8")

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(),
        db_path=db_path,
    ).maintain_property_gaps()

    assert report["dry_run"] is True
    assert report["planned_action_count"] == 2
    assert {
        (action["file_path"], tuple(action["fields"].keys()))
        for action in report["planned_actions"]
    } == {
        ("captures/cap-1.md", ("type", "status")),
        ("_archive/raw/raw-1.md", ("type", "status", "capture_id")),
    }
    assert raw_path.read_text(encoding="utf-8") == before


def test_library_agent_apply_backfills_only_safe_property_fields(tmp_path: Path) -> None:
    capture_rel = _write_capture(tmp_path, name="cap-1.md", body="# 笔记\n\n正文")
    raw_rel = "_archive/raw/raw-1.md"
    _write_raw_for_property_test(tmp_path, raw_rel)
    db_path = tmp_path / "captures.db"
    _write_property_test_db(db_path, capture_rel, raw_rel)

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(),
        db_path=db_path,
    ).maintain_property_gaps(apply=True)

    assert report["dry_run"] is False
    assert report["applied_action_count"] == 2
    capture_frontmatter, _capture_body = read_note(tmp_path, capture_rel)
    raw_frontmatter, _raw_body = read_note(tmp_path, raw_rel)
    assert capture_frontmatter["type"] == "capture"
    assert capture_frontmatter["status"] == "processed"
    assert "reviewable" not in capture_frontmatter
    assert "importance" not in capture_frontmatter
    assert raw_frontmatter["type"] == "raw"
    assert raw_frontmatter["status"] == "archived"
    assert raw_frontmatter["capture_id"] == "cap-1"
    assert report["after"]["capture_gap_count"] == 1
    assert report["after"]["capture_gaps"][0]["missing_fields"] == [
        "reviewable",
        "importance",
    ]


def test_library_agent_dry_run_reports_missing_embedding_index(tmp_path: Path) -> None:
    rel_path = _write_capture(
        tmp_path,
        body="## 第一部分\n这是一篇关于 AI Agent 的长文。\n\n## 第二部分\n补充内容。",
    )
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("cap1", rel_path))
    conn.commit()
    embedder = FakeEmbedder()

    agent = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(),
        db_conn=conn,
        embedder=embedder,
    )
    report = agent.maintain_embedding_index(apply=False)

    assert report["dry_run"] is True
    assert report["scanned"] == 1
    assert report["missing_or_stale"] == 3
    assert report["updated"] == 0
    assert report["items"][0]["action"] == "needs_embedding_index"
    assert embedder.calls == []


def test_library_agent_apply_builds_identity_and_section_embeddings(tmp_path: Path) -> None:
    rel_path = _write_capture(
        tmp_path,
        body="## 第一部分\n内容 A。\n\n## 第二部分\n内容 B。",
    )
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("cap1", rel_path))
    conn.commit()
    embedder = FakeEmbedder()

    agent = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(),
        db_conn=conn,
        embedder=embedder,
    )
    report = agent.maintain_embedding_index(apply=True)

    rows = conn.execute(
        "SELECT kind, section_title FROM capture_embeddings ORDER BY kind, section_id"
    ).fetchall()
    assert report["dry_run"] is False
    assert report["updated"] == 3
    assert [row["kind"] for row in rows].count("identity") == 1
    assert [row["kind"] for row in rows].count("section") == 2
    assert len(embedder.calls) == 3


def test_library_agent_apply_reports_missing_embedder_without_crashing(tmp_path: Path) -> None:
    rel_path = _write_capture(tmp_path, body="## 第一部分\n内容 A。")
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("cap1", rel_path))
    conn.commit()

    agent = LibraryAgent(tmp_path, outliner=FakeOutliner(), db_conn=conn)
    report = agent.maintain_embedding_index(apply=True)

    assert report["errors"] == 1
    assert report["updated"] == 0
    assert report["items"][0]["action"] == "embedding_error"
    assert "embedder_not_configured" in report["items"][0]["error"]

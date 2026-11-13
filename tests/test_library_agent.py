"""Tests for Library Agent vault maintenance."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agents.markdown_writer import read_note
from agents.library_agent import (
    LibraryAgent,
    _directory_absolute_url_free,
    _directory_links_match_content_headings,
    _has_directory_content_heading_coverage_gap,
    _has_directory_primary_heading_coverage_gap,
    _render_directory,
    render_capture_body,
    _restore_readability_emphasis,
)
from db.schema import init_db


def test_rendered_body_restores_llm_emphasis_without_changing_words():
    source = "## 正文\n\n**端到端测试** 必须检查最终产物和下游可见性。\n"
    rendered = "## 正文\n\n端到端测试必须检查最终产物和下游可见性。\n"

    updated = _restore_readability_emphasis(source, rendered)

    assert "**端到端测试**必须检查最终产物和下游可见性。" in updated
    assert updated.replace("**", "") == rendered


def test_rendered_body_adds_deterministic_emphasis_fallback():
    source = "## 正文\n\n端到端测试必须检查最终产物和下游可见性。\n"
    rendered = "## 正文\n\n端到端测试必须检查最终产物和下游可见性。\n"

    updated = _restore_readability_emphasis(source, rendered, ensure_fallback=True)

    assert "**端到端测试必须检查最终产物和下游可见性**。" in updated
    assert updated.replace("**", "") == rendered


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


def test_render_directory_uses_relative_outline_levels() -> None:
    directory = _render_directory(
        [
            {"level": 2, "title": "三层方法概述"},
            {"level": 2, "title": "先定义理想体验"},
            {"level": 3, "title": "线上实验"},
        ]
    )

    assert "\n- [三层方法概述](#三层方法概述)" in directory
    assert "\n- [先定义理想体验](#先定义理想体验)" in directory
    assert "\n  - [线上实验](#线上实验)" in directory
    assert "\n  - [三层方法概述](#三层方法概述)" not in directory


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
    assert "## 目录纲要" not in updated_body
    assert "- 战略转向" not in updated_body
    assert "- 战略转向：说明产品节奏背后的战略变化。" not in updated_body
    assert "第一段。\n\n第二段。" in updated_body
    row = conn.execute(
        "SELECT outline_json, directory_rendered_at FROM capture_metadata WHERE capture_id = ?",
        ("long",),
    ).fetchone()
    assert row is not None
    assert "战略转向" in row["outline_json"]
    assert "说明产品节奏背后的战略变化" in row["outline_json"]
    assert row["directory_rendered_at"] is None
    assert report["items"][0]["warnings"] == ["capture_body_unbound_outline_items:2"]
    assert outliner.calls and outliner.calls[0][0] == body


def test_existing_metadata_outline_and_real_headings_are_not_duplicated(tmp_path: Path) -> None:
    body = (
        "# 测试长文\n\n"
        "## 目录纲要\n\n"
        "- [已有目录](#已有目录)：已经整理过。\n\n"
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

    assert report["updated"] == 1
    updated = (tmp_path / rel_path).read_text(encoding="utf-8")
    assert updated != before
    assert "- [已有目录](#已有目录)\n\n## 已有目录" in updated
    assert "：已经整理过。" not in updated
    assert outliner.calls == []


def test_existing_directory_is_rewritten_with_title_only_link(
    tmp_path: Path,
) -> None:
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

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(outline=outline),
        min_outline_chars=100,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    assert report["updated"] == 1
    assert "- [已有目录](#已有目录)\n\n## 已有目录" in updated_body
    assert "- 已有目录：已经整理过。" not in updated_body
    row = conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        ("long",),
    ).fetchone()
    assert row is not None
    assert "已经整理过" in row["outline_json"]
    assert "anchor_id" in row["outline_json"]


def test_existing_multi_colon_directory_is_rewritten_with_title_only_link(
    tmp_path: Path,
) -> None:
    outline = [
        {
            "level": 1,
            "title": "三层工程",
            "summary": "描述模型周围的三层工程：提示词工程、上下文工程和Harness工程。",
        }
    ]
    body = (
        "# Agent Harness\n\n"
        "## 目录纲要\n\n"
        "- 三层工程：描述模型周围的三层工程：提示词工程、上下文工程和Harness工程。\n\n"
        "## 三层工程\n\n"
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
        (
            "long",
            '[{"level": 1, "title": "三层工程", '
            '"summary": "描述模型周围的三层工程：提示词工程、上下文工程和Harness工程。"}]',
            "hash",
        ),
    )
    conn.commit()

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(outline=outline),
        min_outline_chars=100,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    assert report["updated"] == 1
    assert "- [三层工程](#三层工程)\n\n## 三层工程" in updated_body
    assert "- 三层工程：描述模型周围" not in updated_body


def test_outline_repair_removes_adjacent_bold_pseudo_heading_duplicate(tmp_path: Path) -> None:
    body = (
        "# 测试长文\n\n"
        "## 目录纲要\n\n"
        "- 2. 学会用 git：版本控制。\n\n"
        "### 2. 学会用 git\n\n"
        "**2. 学会用 git**\n\n"
        "正文说明 git 为什么重要。\n\n"
        + "更多内容。\n\n" * 40
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.execute(
        """INSERT INTO capture_metadata
           (capture_id, outline_json, outline_source_hash)
           VALUES (?, ?, ?)""",
        ("long", '[{"level": 2, "title": "2. 学会用 git", "summary": "版本控制。"}]', "hash"),
    )
    conn.commit()

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(),
        min_outline_chars=100,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    assert report["updated"] == 1
    assert "### 2. 学会用 git" in updated_body
    assert "**2. 学会用 git**" not in updated_body
    assert "正文说明 git 为什么重要。" in updated_body.replace("**", "")


def test_existing_directory_with_partial_headings_gets_missing_outline_headings(tmp_path: Path) -> None:
    outline = [
        {"level": 1, "title": "引言：vibecoding 一年回顾", "summary": "开篇。"},
        {"level": 1, "title": "经验一：命令清晰且聚焦", "summary": "聚焦小需求。"},
        {"level": 1, "title": "经验二：学会用Git", "summary": "保留版本。"},
        {"level": 1, "title": "经验七：不要迷信模型能力，掌握决策主动权", "summary": "保留决策。"},
        {"level": 2, "title": "附：vibecoding经验提示词汇总", "summary": "汇总提示词。"},
    ]
    body = (
        "## 目录纲要\n\n"
        "- 引言：vibecoding 一年回顾：开篇。\n"
        "- 经验一：命令清晰且聚焦：聚焦小需求。\n"
        "- 经验二：学会用Git：保留版本。\n"
        "- 经验七：不要迷信模型能力，掌握决策主动权：保留决策。\n"
        "  - 附：vibecoding经验提示词汇总：汇总提示词。\n\n"
        "## 引言：vibecoding 一年回顾\n\n"
        "作者回顾了一年的变化。命令清晰且聚焦 很多刚入门的人会提出宏伟目标。"
        "学会使用 Git 可以备份代码。不要迷信模型能力，掌握决策的主动权。"
        "附上蒸馏我的vibecoding经验后的形成的提示词。"
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
            '[{"level": 1, "title": "引言：vibecoding 一年回顾", "summary": "开篇。"}, '
            '{"level": 1, "title": "经验一：命令清晰且聚焦", "summary": "聚焦小需求。"}, '
            '{"level": 1, "title": "经验二：学会用Git", "summary": "保留版本。"}, '
            '{"level": 1, "title": "经验七：不要迷信模型能力，掌握决策主动权", "summary": "保留决策。"}, '
            '{"level": 2, "title": "附：vibecoding经验提示词汇总", "summary": "汇总提示词。"}]',
            "hash",
        ),
    )
    conn.commit()

    outliner = FakeOutliner(outline=outline)
    report = LibraryAgent(
        tmp_path,
        outliner=outliner,
        min_outline_chars=20,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    assert report["updated"] == 1
    assert "## 引言：vibecoding 一年回顾\n\n作者回顾" in updated_body
    assert "## 经验一：命令清晰且聚焦" not in updated_body
    assert "## 经验二：学会用Git" not in updated_body
    assert "## 经验七：不要迷信模型能力，掌握决策主动权" not in updated_body
    assert "### 附：vibecoding经验提示词汇总" not in updated_body
    assert (
        "- [引言：vibecoding 一年回顾](#引言-vibecoding-一年回顾)\n\n"
        "## 引言：vibecoding 一年回顾"
    ) in updated_body
    assert updated_body.count("## 引言：vibecoding 一年回顾") == 1
    assert report["items"][0]["warnings"] == ["capture_body_unbound_outline_items:4"]
    assert outliner.calls == []


def test_apply_keeps_unstructured_long_note_without_fabricated_headings(
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
    assert "## 目录纲要" not in updated_body
    assert "## 第一部分\n\n这里是原来的第一段正文。" not in updated_body
    assert "## 第二部分\n\n这里是原来的第二段正文。" not in updated_body
    assert "一、第一部分" in updated_body
    assert "二、第二部分" in updated_body
    assert "这里是原来的第一段正文。" in updated_body
    assert "这里是原来的第二段正文。" in updated_body
    assert report["items"][0]["warnings"] == ["capture_body_unbound_outline_items:2"]


def test_markdown_numbered_sections_get_linked_directory_for_outline_items(
    tmp_path: Path,
) -> None:
    outline = [
        {"level": 1, "title": "01 评测框架概览", "summary": "框架。"},
        {"level": 1, "title": "02 数据集设计", "summary": "数据。"},
        {"level": 1, "title": "03 自动化评测", "summary": "自动化。"},
        {"level": 1, "title": "04 人工评估", "summary": "人工。"},
        {"level": 1, "title": "05 闭环迭代", "summary": "闭环。"},
        {"level": 1, "title": "06 AI评测与数据飞轮", "summary": "飞轮。"},
    ]
    expected_titles = [item["title"] for item in outline]
    body = (
        "# AI-Native 产品经理\n\n"
        "## 目录纲要\n\n"
        "- 01 评测框架概览：框架。\n"
        "- 02 数据集设计：数据。\n"
        "- 03 自动化评测：自动化。\n"
        "- 04 人工评估：人工。\n"
        "- 05 闭环迭代：闭环。\n"
        "- 06 AI评测与数据飞轮：飞轮。\n\n"
        "## 01 评测框架概览\n\n框架正文。\n\n"
        "## 02 数据集设计\n\n数据正文。\n\n"
        "## 03 自动化评测\n\n自动化正文。\n\n"
        "## 04 人工评估\n\n人工正文。\n\n"
        "## 05 闭环迭代\n\n闭环正文。\n\n"
        "## 06 AI评测与数据飞轮\n\n飞轮正文。\n\n"
        "## 07 从评测到评估\n\n评估正文。\n"
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
            (
                '[{"level": 1, "title": "01 评测框架概览", "summary": "框架。"}, '
                '{"level": 1, "title": "02 数据集设计", "summary": "数据。"}, '
                '{"level": 1, "title": "03 自动化评测", "summary": "自动化。"}, '
                '{"level": 1, "title": "04 人工评估", "summary": "人工。"}, '
                '{"level": 1, "title": "05 闭环迭代", "summary": "闭环。"}, '
                '{"level": 1, "title": "06 AI评测与数据飞轮", "summary": "飞轮。"}]'
            ),
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
    for title in expected_titles:
        assert f"- [{title}]" in updated_body
        assert f"## {title}" in updated_body
        assert f"{title}：" not in updated_body
    assert "- [07 从评测到评估](#07-从评测到评估)" in updated_body
    assert "## 07 从评测到评估\n\n评估正文。" in updated_body
    row = conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        ("long",),
    ).fetchone()
    assert row is not None
    assert "07 从评测到评估" in row["outline_json"]


def test_existing_directory_is_rewritten_to_cover_all_primary_headings(
    tmp_path: Path,
) -> None:
    outline = [{"level": 1, "title": "中途切模型的三层代价", "summary": "说明切模型的代价。"}]
    headings = [
        "模型和Harness不是简单分离",
        "工具格式影响模型行动",
        "提示词也要按模型适配",
        "中途切模型的三层代价",
        "更好的方式：用 subagent 开新现场",
        "多 agent 的编排问题",
    ]
    body = (
        "# 别在任务过程中随便切模型\n\n"
        "## 目录纲要\n\n"
        "- [中途切模型的三层代价](#中途切模型的三层代价)：说明切模型的代价。\n\n"
        + "\n\n".join(f"## {title}\n\n{title} 正文。" for title in headings)
        + "\n\n## 参考来源\n\n- https://example.com\n"
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.execute(
        """INSERT INTO capture_metadata
           (capture_id, outline_json, outline_source_hash)
           VALUES (?, ?, ?)""",
        ("long", '[{"level": 1, "title": "中途切模型的三层代价", "summary": "说明切模型的代价。"}]', "hash"),
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
    directory = updated_body.split("## 模型和Harness不是简单分离", maxsplit=1)[0]
    for title in headings:
        assert f"- [{title}]" in directory
        assert f"## {title}" in updated_body
    assert "参考来源" not in directory
    assert "说明切模型的代价" not in directory


def test_absolute_url_directory_is_rebuilt_with_exact_heading_titles(tmp_path: Path) -> None:
    headings = [
        "模型和 Harness 不是简单分离",
        "工具格式会影响模型行动",
        "更好的方式：用 subagent 开新现场",
    ]
    body = (
        "# 别在任务过程中随便切模型\n\n"
        "## 目录纲要\n\n"
        "- [模型和Harness并非简单分离]"
        "(http://127.0.0.1:5173/knowledge-library#模型和Harness并非简单分离)\n\n"
        + "\n\n".join(f"## {title}\n\n" + f"{title}正文。" * 40 for title in headings)
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.commit()

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(fail=True),
        min_outline_chars=100,
        db_conn=conn,
    ).repair_long_capture_directories(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    directory = updated_body.split(f"## {headings[0]}", maxsplit=1)[0]
    assert report["repaired"] == 1
    assert _directory_absolute_url_free(updated_body) is True
    assert _directory_links_match_content_headings(updated_body) is True
    assert "127.0.0.1" not in directory
    assert "- [模型和 Harness 不是简单分离](#模型和-Harness-不是简单分离)" in directory
    assert "模型和Harness并非简单分离" not in directory
    for title in headings:
        assert f"## {title}" in updated_body


def test_capture_processing_note_is_removed_from_formal_body(tmp_path: Path) -> None:
    body = (
        "# 录音整理稿\n\n"
        "> **整理说明：** 以下为音频转写后的可读性整理稿，只修正明显转写错误、术语、标点和段落结构。\n\n"
        "## 第一部分\n\n"
        + "第一部分正文。" * 120
        + "\n\n## 第二部分\n\n"
        + "第二部分正文。" * 120
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.commit()

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(fail=True),
        min_outline_chars=100,
        db_conn=conn,
    ).repair_long_capture_directories(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    assert report["repaired"] == 1
    assert "整理说明" not in updated_body
    assert "- [第一部分](#第一部分)" in updated_body
    assert "- [第二部分](#第二部分)" in updated_body


def test_repair_long_capture_directories_syncs_committed_draft_cache(tmp_path: Path) -> None:
    body = (
        "# 测试长文\n\n"
        "## 第一部分\n\n"
        + "第一部分正文。" * 80
        + "\n\n"
        "## 第二部分\n\n"
        + "第二部分正文。" * 80
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
            '[{"level": 1, "title": "第一部分", "summary": "第一部分摘要。"}, '
            '{"level": 1, "title": "第二部分", "summary": "第二部分摘要。"}]',
            "hash",
        ),
    )
    conn.execute(
        """INSERT INTO capture_drafts
           (id, title, status, committed_capture_id, raw_text, draft_text, word_count)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("draft-1", "测试长文", "committed", "long", body, "过期草稿正文", 6),
    )
    conn.commit()
    outliner = FakeOutliner(fail=True)
    agent = LibraryAgent(tmp_path, outliner=outliner, min_outline_chars=100, db_conn=conn)

    dry_run = agent.repair_long_capture_directories(apply=False)
    report = agent.repair_long_capture_directories(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    assert dry_run["needs_repair"] == 1
    assert report["repaired"] == 1
    assert report["items"][0]["synced_committed_drafts"] == 1
    assert Path(report["items"][0]["backup_path"]).exists()
    assert "- [第一部分](#第一部分)" in updated_body
    assert "- [第二部分](#第二部分)" in updated_body
    assert "第一部分摘要" not in updated_body.split("## 第一部分", maxsplit=1)[0]
    draft = conn.execute(
        "SELECT draft_text, word_count FROM capture_drafts WHERE id = ?",
        ("draft-1",),
    ).fetchone()
    assert draft["draft_text"] == updated_body
    assert draft["word_count"] == len(updated_body)
    assert outliner.calls == []


def test_directory_primary_heading_coverage_allows_nested_directory_items() -> None:
    body = (
        "# 测试长文\n\n"
        "## 目录纲要\n\n"
        "- [第一部分](#第一部分)\n"
        "  - [第一小节](#第一小节)\n"
        "- [第二部分](#第二部分)\n\n"
        "## 第一部分\n\n"
        "正文。\n\n"
        "### 第一小节\n\n"
        "正文。\n\n"
        "## 第二部分\n\n"
        "正文。"
    )

    assert _has_directory_primary_heading_coverage_gap(body) is False


def test_directory_primary_heading_coverage_detects_missing_primary_heading() -> None:
    body = (
        "# 测试长文\n\n"
        "## 目录纲要\n\n"
        "- [第一部分](#第一部分)\n"
        "  - [第一小节](#第一小节)\n\n"
        "## 第一部分\n\n"
        "正文。\n\n"
        "### 第一小节\n\n"
        "正文。\n\n"
        "## 第二部分\n\n"
        "正文。"
    )

    assert _has_directory_primary_heading_coverage_gap(body) is True


def test_long_directory_rebuilds_from_final_markdown_headings(tmp_path: Path) -> None:
    headings = [
        "开场：模型和 Harness 不是简单分离",
        "工具格式会影响模型行动",
        "提示词也要按模型适配",
        "中途切模型的三层代价",
        "更好的方式：用 subagent 开新现场",
        "多 agent 的编排问题",
    ]
    body = (
        "# 别在任务过程中随便切模型\n\n"
        "## 目录纲要\n\n"
        "- [中途切模型的三层代价](#中途切模型的三层代价)："
        "中途切换模型会带来 KV 缓存失效、连续性丢失和旧现场继承困难。\n\n"
        + "\n\n".join(f"## {title}\n\n" + f"{title}正文。" * 35 for title in headings)
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
            '[{"level": 1, "title": "中途切模型的三层代价", '
            '"summary": "LLM 只绑定到了中间章节。"}]',
            "hash",
        ),
    )
    conn.commit()

    agent = LibraryAgent(tmp_path, outliner=FakeOutliner(fail=True), min_outline_chars=100, db_conn=conn)
    dry_run = agent.repair_long_capture_directories(apply=False)
    report = agent.repair_long_capture_directories(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    directory = updated_body.split(f"## {headings[0]}", maxsplit=1)[0]
    assert dry_run["needs_repair"] == 1
    assert report["repaired"] == 1
    for title in headings:
        assert f"- [{title}]" in directory
        assert f"## {title}" in updated_body
    assert "LLM 只绑定到了中间章节" not in directory
    for line in directory.splitlines():
        if line.strip().startswith("- "):
            assert ")：" not in line
            assert "):" not in line
    assert _has_directory_content_heading_coverage_gap(updated_body) is False
    assert _directory_links_match_content_headings(updated_body) is True


def test_hengqu_article_uses_only_explicit_sections_and_removes_wechat_noise(
    tmp_path: Path,
) -> None:
    outline = [
        {
            "level": 1,
            "title": "引言：横渠四句的由来与当代意义",
            "summary": "引出横渠四句及其品读方法。",
        },
        {
            "level": 1,
            "title": "一读：从张载的人生经历看四句的诞生",
            "summary": "讲述张载的人生历程。",
        },
        {
            "level": 2,
            "title": "为天地立心：安身立命的宏大视野",
            "summary": "解释为天地立心。",
        },
        {
            "level": 2,
            "title": "为生民立命：躬身入局的民生担当",
            "summary": "解释为生民立命。",
        },
        {
            "level": 1,
            "title": "二读：逐句解读四句的内涵",
            "summary": "进入逐句解读。",
        },
        {
            "level": 1,
            "title": "三读：横渠四句的千年回响与当代价值",
            "summary": "讨论当代价值。",
        },
        {"level": 1, "title": "结语：思想星河的永恒光芒", "summary": "收束全文。"},
    ]
    body = (
        "▲ 戳蓝色字关注我们！\n\n"
        "为天地立心：安身立命的宏大视野\n"
        "为天地立心，\n\n"
        "为生民立命：躬身入局的民生担当\n"
        "为生民立命，\n\n"
        "图源：视觉中国\n\n"
        "杭州万松书院里的张载雕像 图源：视觉中国一读“横渠四句”，首先要从读张载开始。\n\n"
        "## 引言：横渠四句的由来与当代意义\n\n"
        "为万世开太平。这番话出自北宋思想家、理学家张载。\n\n"
        "## 一读：从张载的人生经历看四句的诞生\n\n"
        "一读横渠四句，首先要从读张载这个人开始。\n\n"
        "## 二读：逐句解读四句的内涵\n\n"
        "立心，在天地间寻安身立命的根本。\n\n"
        "## 三读：横渠四句的千年回响与当代价值\n\n"
        "穿越历史，横渠四句始终熠熠闪耀着光芒。\n\n"
        "## 结语：思想星河的永恒光芒\n\n"
        "只要这些哲思智慧还在发光，人类思想的星河就永不黯淡。\n\n"
        "声明：稿件未经授权，不得转载。\n"
        "▼ 点击下方名片关注浙江宣传 ▼\n"
        "往 期 精 彩 内 容\n"
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
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
    assert "戳蓝色字关注我们" not in updated_body
    assert "图源：视觉中国" not in updated_body
    assert "杭州万松书院里的张载雕像" not in updated_body
    assert "一读“横渠四句”，首先要从读张载开始。" in updated_body
    assert "声明：稿件未经授权" not in updated_body
    assert "往 期 精 彩" not in updated_body
    assert (
        "- [引言：横渠四句的由来与当代意义](#引言-横渠四句的由来与当代意义)"
        in updated_body
    )
    assert "- 引言：横渠四句的由来与当代意义：引出" not in updated_body
    assert "- 为天地立心：安身立命的宏大视野" not in updated_body
    assert "\n## 为天地立心：安身立命的宏大视野" not in updated_body
    assert "为天地立心：安身立命的宏大视野\n为天地立心，" in updated_body
    assert "## 引言：横渠四句的由来与当代意义" in updated_body
    assert "## 一读：从张载的人生经历看四句的诞生" in updated_body
    assert "## 二读：逐句解读四句的内涵" in updated_body
    assert "## 三读：横渠四句的千年回响与当代价值" in updated_body
    assert "## 结语：思想星河的永恒光芒" in updated_body
    assert sorted(report["items"][0]["warnings"]) == [
        "capture_body_noise_blocks_removed:4",
        "capture_body_unbound_outline_items:2",
    ]
    row = conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        ("long",),
    ).fetchone()
    assert row is not None
    assert "为天地立心：安身立命的宏大视野" in row["outline_json"]
    assert "解释为天地立心" in row["outline_json"]


def test_outline_repair_removes_empty_headings_and_outline_spill_lines(
    tmp_path: Path,
) -> None:
    outline = [
        {"level": 1, "title": "1 编排循环（Orchestration Loop）", "summary": "说明编排循环。"},
        {"level": 1, "title": "2 工具（Tools）", "summary": "说明工具设计。"},
        {"level": 1, "title": "3 记忆（Memory）", "summary": "说明记忆。"},
        {"level": 1, "title": "5 提示词构建（Prompt Construction）", "summary": "说明提示词。"},
    ]
    body = (
        "# Agent Harness\n\n"
        "## 目录纲要\n\n"
        "- 1 编排循环（Orchestration Loop）：说明编排循环。\n"
        "- 2 工具（Tools）：说明工具设计。\n"
        "- 3 记忆（Memory）：说明记忆。\n"
        "- 5 提示词构建（Prompt Construction）：说明提示词。\n\n"
        "###\n\n"
        "image.png\n\n"
        "1 编排循环\n\n"
        "### 1 编排循环（Orchestration Loop）\n\n"
        "1 编排循环\n\n"
        "编排循环负责驱动模型、工具和状态之间的往复。\n\n"
        "2 工具\n\n"
        "工具是代理与外部系统交互的接口。\n\n"
        "3 记忆\n\n"
        "记忆让代理能够保留跨轮次状态。\n\n"
        "5 提示词构建\n\n"
        "提示词构建决定模型每轮看到的任务边界。\n"
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
            (
                '[{"level": 1, "title": "1 编排循环（Orchestration Loop）", "summary": "说明编排循环。"}, '
                '{"level": 1, "title": "2 工具（Tools）", "summary": "说明工具设计。"}, '
                '{"level": 1, "title": "3 记忆（Memory）", "summary": "说明记忆。"}, '
                '{"level": 1, "title": "5 提示词构建（Prompt Construction）", "summary": "说明提示词。"}]'
            ),
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
    assert "\n###\n" not in updated_body
    assert "图片占位：image.png" in updated_body
    assert "\n1 编排循环\n\n### 1 编排循环" not in updated_body
    assert "\n### 1 编排循环（Orchestration Loop）\n\n1 编排循环\n" not in updated_body
    assert "\n2 工具\n\n工具是代理" in updated_body
    assert "\n3 记忆\n\n记忆让代理" in updated_body
    assert "\n5 提示词构建\n\n提示词构建" in updated_body
    assert "## 2 工具（Tools）\n\n工具是代理与外部系统交互的接口。" not in updated_body
    assert "## 3 记忆（Memory）\n\n记忆让代理能够保留跨轮次状态。" not in updated_body
    assert "## 5 提示词构建（Prompt Construction）\n\n提示词构建决定模型每轮看到的任务边界。" not in updated_body


def test_apply_inserts_markdown_headings_from_valid_paragraph_indexes(
    tmp_path: Path,
) -> None:
    outline = [
        {
            "level": 1,
            "title": "开篇判断",
            "start_paragraph_index": 1,
            "summary": "提出主问题。",
        },
        {
            "level": 1,
            "title": "Codex 路线",
            "start_paragraph_index": 3,
            "summary": "说明被动触发。",
        },
        {
            "level": 1,
            "title": "Claude 路线",
            "start_paragraph_index": 4,
            "summary": "说明主动委派。",
        },
    ]
    body = (
        "# 测试长文\n\n"
        "第一段提出 subagent 的核心问题。\n\n"
        "第二段解释上下文污染。\n\n"
        "第三段解释 Codex 为什么被动触发。\n\n"
        "第四段解释 Claude 为什么主动委派。\n"
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
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
    assert "- [开篇判断](#开篇判断)" in updated_body
    assert "## 开篇判断\n\n第一段提出 subagent 的核心问题。" in updated_body
    assert "## Codex 路线\n\n第三段解释 Codex 为什么被动触发。" in updated_body
    assert "## Claude 路线\n\n第四段解释 Claude 为什么主动委派。" in updated_body
    assert "第一段提出 subagent 的核心问题。" in updated_body
    assert "第三段解释 Codex 为什么被动触发。" in updated_body
    row = conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        ("long",),
    ).fetchone()
    assert row is not None
    assert "start_paragraph_index" in row["outline_json"]
    assert "paragraph_index" in row["outline_json"]


def test_invalid_paragraph_indexes_do_not_generate_jump_directory(
    tmp_path: Path,
) -> None:
    outline = [
        {
            "level": 1,
            "title": "第二段先开始",
            "start_paragraph_index": 2,
            "summary": "无效顺序。",
        },
        {
            "level": 1,
            "title": "第一段后开始",
            "start_paragraph_index": 1,
            "summary": "无效顺序。",
        },
    ]
    body = (
        "# 测试长文\n\n"
        "第一段正文。\n\n"
        "第二段正文。\n\n"
        "第三段正文。\n"
    )
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
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
    assert "## 目录纲要" not in updated_body
    assert "## 第二段先开始" not in updated_body
    assert "第一段正文。\n\n第二段正文。" in updated_body
    assert "capture_body_invalid_outline_paragraph_indexes" in report["items"][0]["warnings"]


def test_markdown_heading_scan_ignores_code_fences() -> None:
    body = (
        "# 测试长文\n\n"
        "```md\n"
        "## 代码里的标题\n"
        "```\n\n"
        "## 正文标题\n\n"
        "正文。"
    )
    result = render_capture_body(
        body,
        [{"level": 1, "title": "正文标题", "summary": "真实章节。"}],
    )

    assert "- [正文标题](#正文标题)" in result.capture_body
    assert all(item.get("title") != "代码里的标题" for item in result.outline_metadata)
    assert "capture_body_unbound_outline_items" not in ",".join(result.warnings)


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
        "## 第一部分\n"
        "这里是原来的第一段正文。\n\n"
        "## 第二部分\n"
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
    assert "## 第一部分" not in updated_body
    assert "## 第二部分" not in updated_body
    assert "开头一句。一、第一部分这里是原来的第一段正文。" in updated_body
    assert "二、第二部分这里是原来的第二段正文。" in updated_body


def test_apply_keeps_bold_section_labels_without_fabricated_headings(tmp_path: Path) -> None:
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
    assert "## 目录纲要" not in updated_body
    assert "## 第一部分\n\n这里是原来的第一段正文。" not in updated_body
    assert "## 第二部分\n\n这里是原来的第二段正文。" not in updated_body
    assert "**第一部分**" in updated_body
    assert "**第二部分**" in updated_body


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
    assert "## 目录纲要" not in updated_body
    assert "- 已有目录" not in updated_body
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


def test_short_organized_capture_renders_directory_when_source_was_long(tmp_path: Path) -> None:
    body = "## 核心判断\n\n**模型会压缩内容，但代码必须保留目录合同。**"
    rel_path = _write_capture(tmp_path, name="compressed.md", body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("compressed", rel_path))
    conn.commit()

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(),
        min_outline_chars=2000,
        db_conn=conn,
    ).maintain_capture("compressed", apply=True, force_directory=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    _frontmatter, updated_body = note
    assert "## 目录纲要" in updated_body
    assert "- [核心判断](#核心判断)" in updated_body
    assert "## 核心判断" in updated_body
    assert report["pipeline"]["directory_rendered"] is True


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


def test_library_agent_apply_deletes_stale_section_embeddings(tmp_path: Path) -> None:
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
    agent.maintain_embedding_index(apply=True)
    _write_capture(tmp_path, body="## 第一部分\n内容 A。")

    report = agent.maintain_embedding_index(apply=True)

    rows = conn.execute(
        "SELECT kind, section_id, section_title FROM capture_embeddings ORDER BY kind, section_id"
    ).fetchall()
    assert report["missing_or_stale"] == 1
    assert report["updated"] == 0
    assert report["items"][0]["action"] == "deleted_stale_embedding_rows"
    assert [(row["kind"], row["section_id"], row["section_title"]) for row in rows] == [
        ("identity", "", ""),
        ("section", "section-1", "第一部分"),
    ]


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

class _ConcurrentEditOutliner(FakeOutliner):
    """Simulates a second editor touching the note during the LLM window."""

    def __init__(self, note_path: Path, replacement: str) -> None:
        super().__init__()
        self.note_path = note_path
        self.replacement = replacement

    def generate(self, cleaned_text: str, word_count: int) -> list[dict]:
        text = self.note_path.read_text(encoding="utf-8")
        self.note_path.write_text(
            text.replace("正文段落。", self.replacement, 1),
            encoding="utf-8",
        )
        return super().generate(cleaned_text, word_count)


def test_apply_skips_rewrite_when_note_changed_during_outline(tmp_path: Path) -> None:
    body = "# 测试长文\n\n" + "正文段落。\n\n" * 80
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.commit()
    outliner = _ConcurrentEditOutliner(tmp_path / rel_path, "维护期间的新编辑。")

    report = LibraryAgent(
        tmp_path,
        outliner=outliner,
        min_outline_chars=100,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    note = read_note(tmp_path, rel_path)
    assert note is not None
    frontmatter, updated_body = note
    # The concurrent edit wins: maintenance must not write its stale body.
    assert "维护期间的新编辑。" in updated_body
    assert "正文段落。" in updated_body
    assert report["updated"] == 0
    assert report["items"][0]["action"] == "skipped_body_changed"
    # A skipped pass writes neither a backup nor stale outline metadata.
    assert not list((tmp_path / "captures").glob(f"{Path(rel_path).name}.bak-*"))
    assert conn.execute(
        "SELECT 1 FROM capture_metadata WHERE capture_id = ?", ("long",)
    ).fetchone() is None

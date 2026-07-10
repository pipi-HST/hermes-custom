"""Tests for Markdown writer utility."""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.markdown_writer import (
    HERMES_THEME_END,
    HERMES_THEME_START,
    NoteMeta,
    _make_slug,
    append_links,
    read_moc,
    read_note,
    update_moc,
    write_moc,
    write_note,
)


@pytest.fixture
def sample_meta() -> NoteMeta:
    return NoteMeta(
        id="test-001",
        title="AI Agent 入门指南",
        date="2026-05-26",
        created="2026-05-26T10:00:00+00:00",
        source="https://example.com/ai-agent",
        source_type="web",
        tags=["ai-agent", "guide"],
        story="A guide to AI Agent concepts",
        word_count=1500,
    )


def test_write_note_creates_file(tmp_path: Path, sample_meta: NoteMeta) -> None:
    body = "# AI Agent 入门\n\nThis is a test note."
    file_path = write_note(tmp_path, sample_meta, body)

    full_path = tmp_path / file_path
    assert full_path.exists()

    content = full_path.read_text(encoding="utf-8")
    assert content.startswith("---")
    assert "id: test-001" in content
    assert body in content


def test_write_note_slug_generation() -> None:
    assert _make_slug("刚刚，国产AI自己造了AI，全球首例！") == "ai-builds-ai"
    assert _make_slug("AI Agent 四大核心组件") == "ai-agent"
    assert _make_slug("") == "untitled"


def test_write_note_file_naming(tmp_path: Path, sample_meta: NoteMeta) -> None:
    body = "test content"
    file_path = write_note(tmp_path, sample_meta, body)
    assert file_path == "captures/AI Agent 入门指南-2026-05-26-test-001.md"


def test_write_note_filename_sanitizes_title_and_keeps_title_first(
    tmp_path: Path, sample_meta: NoteMeta
) -> None:
    meta = NoteMeta(
        id="abcdef123456",
        title='AI Harness深度解析：用/最便宜的模型？"可靠性"',
        date="2026-05-28",
        created=sample_meta.created,
        source=sample_meta.source,
        source_type=sample_meta.source_type,
    )
    file_path = write_note(tmp_path, meta, "body")

    assert file_path == "captures/AI Harness深度解析：用最便宜的模型可靠性-2026-05-28-abcdef12.md"


def test_write_note_overwrite_existing(
    tmp_path: Path, sample_meta: NoteMeta
) -> None:
    body_v1 = "version 1 content"
    body_v2 = "version 2 content"

    path1 = write_note(tmp_path, sample_meta, body_v1)
    path2 = write_note(tmp_path, sample_meta, body_v2)

    assert path1 == path2

    full_path = tmp_path / path1
    content = full_path.read_text(encoding="utf-8")
    assert body_v2 in content
    assert body_v1 not in content


def test_write_note_stage_raw_minimal_frontmatter(
    tmp_path: Path, sample_meta: NoteMeta
) -> None:
    body = "# Raw Note\n\nMinimal extraction."
    file_path = write_note(tmp_path, sample_meta, body, stage="raw")

    full_path = tmp_path / file_path
    assert full_path.exists()
    assert file_path.startswith("_archive/raw/")

    content = full_path.read_text(encoding="utf-8")
    assert content.startswith("---")
    assert "id: test-001" in content
    assert "title: AI Agent 入门指南" in content
    assert "status: raw" in content
    assert "created:" not in content
    assert "story:" not in content
    assert "tags:" not in content
    assert "word_count:" not in content


def test_write_note_stage_captures_full_frontmatter(
    tmp_path: Path, sample_meta: NoteMeta
) -> None:
    body = "# Captured Note\n\nFull processing."
    file_path = write_note(tmp_path, sample_meta, body, stage="captures")

    full_path = tmp_path / file_path
    assert full_path.exists()
    assert file_path.startswith("captures/")

    content = full_path.read_text(encoding="utf-8")
    assert content.startswith("---")
    assert "id: test-001" in content
    assert "status" not in content
    assert "created:" in content
    assert "tags:" in content
    assert "story:" in content
    assert "word_count:" in content


def test_write_note_stage_captures_omits_agent_outline_from_frontmatter(
    tmp_path: Path, sample_meta: NoteMeta
) -> None:
    meta = NoteMeta(
        id=sample_meta.id,
        title=sample_meta.title,
        date=sample_meta.date,
        created=sample_meta.created,
        source=sample_meta.source,
        source_type=sample_meta.source_type,
        tags=sample_meta.tags,
        story=sample_meta.story,
        word_count=sample_meta.word_count,
        outline=[
            {
                "level": 1,
                "title": "算力瓶颈",
                "range": "段1-3",
                "summary": "解释电力基础设施为什么成为AI扩张瓶颈。",
            }
        ],
    )

    file_path = write_note(tmp_path, meta, "body", stage="captures")
    result = read_note(tmp_path, file_path)

    assert result is not None
    frontmatter, body = result
    assert "outline" not in frontmatter
    assert body == "body"


def test_write_note_stage_raw_omits_outline(
    tmp_path: Path, sample_meta: NoteMeta
) -> None:
    meta = NoteMeta(
        id=sample_meta.id,
        title=sample_meta.title,
        date=sample_meta.date,
        created=sample_meta.created,
        source=sample_meta.source,
        source_type=sample_meta.source_type,
        outline=[{"level": 1, "title": "原文结构"}],
    )

    file_path = write_note(tmp_path, meta, "raw body", stage="raw")
    result = read_note(tmp_path, file_path)

    assert result is not None
    frontmatter, _body = result
    assert "outline" not in frontmatter


def test_read_note_roundtrip(tmp_path: Path, sample_meta: NoteMeta) -> None:
    body = "Roundtrip test body content."
    file_path = write_note(tmp_path, sample_meta, body)

    result = read_note(tmp_path, file_path)
    assert result is not None
    frontmatter, body_text = result
    assert frontmatter["id"] == sample_meta.id
    assert frontmatter["title"] == sample_meta.title
    assert frontmatter["tags"] == sample_meta.tags
    assert body_text == body


def test_read_note_rejects_absolute_path_outside_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside secret", encoding="utf-8")

    assert read_note(vault, str(outside)) is None


def test_read_note_rejects_symlink_escape_from_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside secret", encoding="utf-8")
    (vault / "linked.md").symlink_to(outside)

    assert read_note(vault, "linked.md") is None


# ── append_links ──


def _write_raw_note(path: Path, body: str) -> str:
    """Helper: write a minimal .md with frontmatter, return relative path."""
    captures_dir = path / "captures"
    captures_dir.mkdir(parents=True, exist_ok=True)
    file_path = "captures/2026-05-27-test-note.md"
    full_path = path / file_path
    full_path.write_text(
        "---\nid: test-001\ntitle: 测试笔记\n---\n\n" + body,
        encoding="utf-8",
    )
    return file_path


def test_append_links_creates_section(tmp_path: Path) -> None:
    """文件没有 ## 相关笔记 段落时，应追加该段落并写入链接。"""
    file_path = _write_raw_note(tmp_path, "正文内容。")
    links = [
        {"title": "芯片又杀疯了", "reason": "寒武纪市值与AI教育关联"},
        {"title": "AI时代的电力协议", "reason": "算力基础设施"},
    ]

    appended = append_links(tmp_path, file_path, links)
    assert appended == 2

    content = (tmp_path / file_path).read_text(encoding="utf-8")
    assert "## 相关笔记" in content
    assert "[[芯片又杀疯了]]" in content
    assert "[[AI时代的电力协议]]" in content
    assert "寒武纪市值与AI教育关联" in content


def test_append_links_appends_to_existing_section(tmp_path: Path) -> None:
    """已有 ## 相关笔记 段落时，应在其末尾追加新链接。"""
    body = (
        "正文。\n\n"
        "## 相关笔记\n"
        "- [[已有笔记]] — 已有理由\n"
    )
    file_path = _write_raw_note(tmp_path, body)
    links = [{"title": "新笔记", "reason": "新理由"}]

    appended = append_links(tmp_path, file_path, links)
    assert appended == 1

    content = (tmp_path / file_path).read_text(encoding="utf-8")
    assert "[[已有笔记]]" in content
    assert "[[新笔记]]" in content


def test_append_links_deduplicates(tmp_path: Path) -> None:
    """已有相同 [[标题]] 链接时，不应重复追加。"""
    body = (
        "正文。\n\n"
        "## 相关笔记\n"
        "- [[芯片又杀疯了]] — 旧理由\n"
    )
    file_path = _write_raw_note(tmp_path, body)
    links = [
        {"title": "芯片又杀疯了", "reason": "新理由"},
        {"title": "另一篇", "reason": "新内容"},
    ]

    appended = append_links(tmp_path, file_path, links)
    assert appended == 1  # only the new one

    content = (tmp_path / file_path).read_text(encoding="utf-8")
    # Original link preserved exactly once
    assert content.count("[[芯片又杀疯了]]") == 1
    assert "[[另一篇]]" in content


def test_append_links_empty_list(tmp_path: Path) -> None:
    """空链接列表应不修改文件。"""
    file_path = _write_raw_note(tmp_path, "正文。")

    appended = append_links(tmp_path, file_path, [])
    assert appended == 0

    content = (tmp_path / file_path).read_text(encoding="utf-8")
    assert "## 相关笔记" not in content


def test_append_links_file_not_found(tmp_path: Path) -> None:
    """文件不存在时应返回 -1，不抛异常。"""
    appended = append_links(tmp_path, "captures/nonexistent.md", [
        {"title": "X", "reason": "Y"},
    ])
    assert appended == -1


def test_append_links_preserves_frontmatter(tmp_path: Path) -> None:
    """追加链接后 frontmatter 和正文应完整保留。"""
    file_path = _write_raw_note(tmp_path, "正文内容。")
    links = [{"title": "新链接", "reason": "理由"}]

    append_links(tmp_path, file_path, links)

    content = (tmp_path / file_path).read_text(encoding="utf-8")
    assert content.startswith("---")
    assert "id: test-001" in content
    assert "正文内容。" in content


# ── write_moc ──


def test_write_moc_creates_file(tmp_path: Path) -> None:
    """write_moc 应在 themes/ 目录创建 MOC 页。"""
    body = "# AI与教育变革\n\n主题描述...\n\n## 开放问题\n- AI 会怎样改变学习路径？"
    file_path = write_moc(
        tmp_path,
        theme_name="AI与教育变革",
        body=body,
        tags=["ai-education"],
        capture_count=5,
    )

    assert file_path.startswith("themes/")
    full_path = tmp_path / file_path
    assert full_path.exists()

    content = full_path.read_text(encoding="utf-8")
    assert content.startswith("---")
    assert "type: theme" in content
    assert "status: active" in content
    assert "theme_name: AI与教育变革" in content
    assert "captures: 5" in content
    assert "last_scanned:" in content
    assert "open_questions:" in content
    assert "AI 会怎样改变学习路径？" in content
    assert "ai-education" in content
    assert HERMES_THEME_START in content
    assert HERMES_THEME_END in content
    assert "主题描述..." in content
    assert "## 我的补充" in content


def test_write_moc_slug_filename(tmp_path: Path) -> None:
    """MOC 文件名应基于主题名的 slug。"""
    file_path = write_moc(
        tmp_path,
        theme_name="AI与教育变革",
        body="内容",
        tags=[],
        capture_count=3,
    )

    assert "ai-education" in file_path or "ai" in file_path
    assert file_path.endswith(".md")


def test_write_moc_overwrite_existing(tmp_path: Path) -> None:
    """重复调用应覆盖旧文件。"""
    write_moc(tmp_path, "AI主题", "版本1", tags=[], capture_count=3)
    write_moc(tmp_path, "AI主题", "版本2", tags=[], capture_count=5)

    file_path = write_moc(tmp_path, "AI主题", "版本3", tags=[], capture_count=7)
    content = (tmp_path / file_path).read_text(encoding="utf-8")
    assert "版本3" in content
    assert "captures: 7" in content


# ── read_moc ──


def test_read_moc_returns_frontmatter_and_body(tmp_path: Path) -> None:
    """read_moc 应解析 frontmatter 和 body。"""
    file_path = write_moc(
        tmp_path, "AI与教育", "# AI与教育\n\n描述。", tags=["ai"], capture_count=3,
    )
    result = read_moc(tmp_path, file_path)
    assert result is not None
    frontmatter, body = result
    assert frontmatter["theme_name"] == "AI与教育"
    assert frontmatter["captures"] == 3
    assert "# AI与教育" in body


def test_read_moc_returns_none_when_missing(tmp_path: Path) -> None:
    """文件不存在时返回 None。"""
    assert read_moc(tmp_path, "themes/nonexistent.md") is None


# ── update_moc ──


def test_update_moc_updates_readable_map_and_reference_list(tmp_path: Path) -> None:
    """update_moc 应更新可读关系地图，而不是另起一块相关笔记。"""
    file_path = write_moc(
        tmp_path, "AI与教育",
        "# AI与教育\n\n"
        "## 主题脉络\n\n"
        "### 核心材料\n"
        "- [[已有笔记]] — 旧摘要\n\n"
        "## 关键联系\n"
        "- 旧联系。\n\n"
        "## 相关笔记引用\n"
        "- [[已有笔记]]\n",
        tags=["ai"], capture_count=1,
    )

    updated = update_moc(tmp_path, file_path, {
        "title": "新笔记", "summary": "新内容摘要",
    })
    assert updated is True

    content = (tmp_path / file_path).read_text(encoding="utf-8")
    assert "[[已有笔记]]" in content
    assert "[[新笔记]]" in content
    assert "新内容摘要" in content
    assert "## 主题脉络" in content
    assert "### 后续发现" in content
    assert "## 相关笔记引用" in content
    assert content.count("### 相关笔记") == 0
    # captures count incremented
    assert "captures: 2" in content
    assert "last_scanned:" in content


def test_update_moc_keeps_system_records_at_bottom(tmp_path: Path) -> None:
    """系统记录应放在底部，不能把相关笔记乱插成第二个阅读区。"""
    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    file_path = "themes/manual.md"
    original_body = (
        "# 手写主题\n\n"
        "## 主题脉络\n"
        "这里是我自己维护的解释。\n\n"
        "## 相关笔记引用\n"
        "- [[手写笔记]]\n\n"
        "## 我的判断\n"
        "这段内容不应该被工具改写。\n"
    )
    (tmp_path / file_path).write_text(
        "---\n"
        "type: theme\n"
        "theme_name: 手写主题\n"
        "captures: 1\n"
        "---\n\n"
        f"{original_body}",
        encoding="utf-8",
    )

    updated = update_moc(tmp_path, file_path, {
        "title": "新笔记", "summary": "新内容摘要",
    })

    assert updated is True
    content = (tmp_path / file_path).read_text(encoding="utf-8")
    assert "这里是我自己维护的解释。" in content
    assert "## 系统记录" in content
    assert content.count("## 系统记录") == 1
    assert content.count("### 相关笔记") == 0
    assert "<!-- HERMES-CHANGELOG:START -->" in content
    assert "<!-- HERMES-CHANGELOG:END -->" in content
    assert "[[新笔记]]" in content
    assert "这段内容不应该被工具改写。" in content


def test_update_moc_adds_changelog(tmp_path: Path) -> None:
    """update_moc 应在更新日志顶部追加新记录。"""
    file_path = write_moc(
        tmp_path, "AI与教育",
        "# AI与教育\n\n## 主题脉络\n- [[已有]] — 旧\n\n"
        "## 相关笔记引用\n- [[已有]]\n\n"
        "## 系统记录\n<!-- HERMES-CHANGELOG:START -->\n"
        "### 更新日志\n- 2026-05-26: 主题创建\n"
        "<!-- HERMES-CHANGELOG:END -->\n",
        tags=[], capture_count=1,
    )

    update_moc(tmp_path, file_path, {"title": "新笔记", "summary": "新内容"})

    content = (tmp_path / file_path).read_text(encoding="utf-8")
    assert "### 更新日志" in content
    assert "新笔记" in content
    assert "2026-05-26: 主题创建" in content


def test_update_moc_deduplicates(tmp_path: Path) -> None:
    """MOC 中已有相同标题的笔记时不应重复追加。"""
    file_path = write_moc(
        tmp_path, "AI与教育",
        "# AI与教育\n\n## 主题脉络\n- [[已有笔记]] — 旧\n\n"
        "## 相关笔记引用\n- [[已有笔记]]\n",
        tags=[], capture_count=1,
    )

    updated = update_moc(tmp_path, file_path, {
        "title": "已有笔记", "summary": "重复",
    })
    assert updated is False  # nothing to add

    content = (tmp_path / file_path).read_text(encoding="utf-8")
    assert content.count("[[已有笔记]]") == 2
    # captures count unchanged
    assert "captures: 1" in content


def test_update_moc_file_not_found(tmp_path: Path) -> None:
    """MOC 文件不存在时应返回 False。"""
    result = update_moc(tmp_path, "themes/missing.md", {"title": "X"})
    assert result is False

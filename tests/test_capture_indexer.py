from __future__ import annotations

from agents.capture_indexer import build_identity_index, build_section_indexes


def test_identity_index_uses_title_story_tags_outline_and_body_hint():
    frontmatter = {
        "title": "Opus 4.8 发布",
        "story": "Anthropic 通过 41 天补丁包调整开发者策略。",
        "tags": ["ai-agent", "anthropic"],
        "outline": [{"title": "Terminal-Bench", "summary": "未补的坑很关键。"}],
    }
    body = "正文第一段讲 41 天节奏狙击。\n\n第二段讲 Terminal-Bench。"

    item = build_identity_index("cap1", "captures/a.md", frontmatter, body)

    assert item.kind == "identity"
    assert item.capture_id == "cap1"
    assert item.file_path == "captures/a.md"
    assert "Opus 4.8 发布" in item.index_text
    assert "ai-agent" in item.index_text
    assert "Terminal-Bench" in item.index_text
    assert "41 天节奏狙击" in item.index_text
    assert item.source_hash


def test_identity_index_uses_metadata_outline_when_frontmatter_has_no_outline():
    metadata_outline = [
        {"title": "数据库里的目录", "summary": "Agent 专用结构。"},
    ]

    item = build_identity_index(
        "cap1",
        "captures/a.md",
        {"title": "标题", "story": "摘要", "tags": ["ai"]},
        "正文第一段。",
        outline=metadata_outline,
    )

    assert "数据库里的目录" in item.index_text
    assert "Agent 专用结构" in item.index_text


def test_section_indexes_split_markdown_headings():
    frontmatter = {"title": "长文"}
    body = "# 长文\n\n## 第一部分\n内容 A。\n\n## 第二部分\n内容 B。"

    sections = build_section_indexes("cap1", "captures/a.md", frontmatter, body)

    assert [section.section_title for section in sections] == ["第一部分", "第二部分"]
    assert [section.section_id for section in sections] == ["section-1", "section-2"]
    assert "长文" in sections[0].index_text
    assert "内容 A" in sections[0].index_text
    assert "内容 B" in sections[1].index_text


def test_section_indexes_return_empty_for_unsectioned_short_note():
    frontmatter = {"title": "短文"}
    body = "只有一小段内容。"

    assert build_section_indexes("cap1", "captures/a.md", frontmatter, body) == []


def test_section_indexes_can_use_metadata_outline_without_rewriting_body():
    frontmatter = {"title": "原文长文"}
    body = "# 原文长文\n\n这是一篇没有 Markdown 二级标题的原文。"
    outline = [
        {"title": "第一部分", "summary": "这里是第一部分的结构摘要。"},
        {"title": "第二部分", "summary": "这里是第二部分的结构摘要。"},
    ]

    sections = build_section_indexes(
        "cap1",
        "captures/a.md",
        frontmatter,
        body,
        outline=outline,
    )

    assert [section.section_title for section in sections] == ["第一部分", "第二部分"]
    assert "这里是第一部分的结构摘要" in sections[0].index_text
    assert "原文长文" in sections[0].index_text


def test_section_indexes_skip_managed_sections():
    frontmatter = {"title": "AI Agent 工程化"}
    body = """# AI Agent 工程化

## 核心方法
这里是可以回答问题的正文内容。

## 相关笔记
- [[Building Effective AI Agents]]

## 知识关联
- 这只是管理区链接。

## 参考来源
[1] 原文

## 后续判断
这里也是可以回答问题的正文内容。
"""

    sections = build_section_indexes("cap1", "captures/a.md", frontmatter, body)

    assert [section.section_title for section in sections] == ["核心方法", "后续判断"]
    assert all("相关笔记" not in section.index_text for section in sections)
    assert all("知识关联" not in section.index_text for section in sections)
    assert all("参考来源" not in section.index_text for section in sections)

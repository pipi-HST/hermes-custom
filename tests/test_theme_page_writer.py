from __future__ import annotations

from pathlib import Path

import yaml

from agents.markdown_writer import (
    HERMES_THEME_END,
    HERMES_THEME_START,
    update_theme_page,
    write_moc,
    write_theme_page,
)


def _read_theme_file(path: Path, rel_path: str) -> tuple[dict, str]:
    text = (path / rel_path).read_text(encoding="utf-8")
    parts = text.split("---", 2)
    assert len(parts) >= 3
    return yaml.safe_load(parts[1]) or {}, parts[2]


def test_write_theme_page_creates_managed_theme_map(tmp_path: Path) -> None:
    body = """# AI Agent 工程化

## 主题判断

这些材料共同讨论 Agent 工程化。

## 相关材料

- [[Building Effective AI Agents]] — 提供基础框架。

## 材料之间的关系

- A 提供框架，B 提供案例。

## 可沉淀的知识卡片方向

- 方法卡：判断什么时候需要 agent workflow。
"""
    captures = [
        {"title": "Building Effective AI Agents", "file_path": "captures/a.md"},
        {"title": "Opus 4.8", "file_path": "captures/b.md"},
        {"title": "Claude Code 配置", "file_path": "captures/c.md"},
    ]
    cards = [
        {
            "title": "AI Agent 工程化：从单次对话到并行协作团队",
            "path": "garden/agent-card.md",
        }
    ]

    rel_path = write_theme_page(
        tmp_path,
        theme_name="AI Agent 工程化",
        body=body,
        captures=captures,
        garden_cards=cards,
    )

    assert rel_path.startswith("themes/")
    frontmatter, rendered = _read_theme_file(tmp_path, rel_path)
    assert frontmatter["type"] == "theme"
    assert frontmatter["status"] == "active"
    assert frontmatter["theme_name"] == "AI Agent 工程化"
    assert frontmatter["related_captures"] == [
        "captures/a.md",
        "captures/b.md",
        "captures/c.md",
    ]
    assert frontmatter["related_garden_cards"] == ["garden/agent-card.md"]
    assert HERMES_THEME_START in rendered
    assert HERMES_THEME_END in rendered
    for heading in (
        "## 主题判断",
        "## 相关材料",
        "## 材料之间的关系",
        "## 可沉淀的知识卡片方向",
        "## 已沉淀的知识卡片",
        "## 我的补充",
    ):
        assert heading in rendered


def test_update_theme_page_preserves_user_notes(tmp_path: Path) -> None:
    original = """# AI Agent 工程化

<!-- HERMES-THEME:START -->
## 主题判断

旧判断。

## 已沉淀的知识卡片

- [[旧卡片]]
<!-- HERMES-THEME:END -->

## 我的补充

这段是我手写的，不允许覆盖。
"""
    rel_path = write_theme_page(
        tmp_path,
        theme_name="AI Agent 工程化",
        body=original,
        captures=[{"title": "A", "file_path": "captures/a.md"}],
        garden_cards=[{"title": "旧卡片", "path": "garden/old.md"}],
    )

    updated_body = """# AI Agent 工程化

## 主题判断

新的主题判断。

## 相关材料

- [[A]] — 新说明。

## 材料之间的关系

- A 是主题起点。

## 可沉淀的知识卡片方向

- 观点卡：新的方向。
"""
    ok = update_theme_page(
        tmp_path,
        rel_path,
        theme_name="AI Agent 工程化",
        body=updated_body,
        captures=[{"title": "A", "file_path": "captures/a.md"}],
        garden_cards=[
            {"title": "旧卡片", "path": "garden/old.md"},
            {"title": "旧卡片", "path": "garden/old.md"},
            {"title": "新卡片", "path": "garden/new.md"},
        ],
    )

    assert ok is True
    frontmatter, rendered = _read_theme_file(tmp_path, rel_path)
    assert "新的主题判断。" in rendered
    assert "旧判断。" not in rendered
    assert "这段是我手写的，不允许覆盖。" in rendered
    assert frontmatter["related_garden_cards"] == ["garden/old.md", "garden/new.md"]
    assert rendered.count("[[旧卡片]]") == 1
    assert rendered.count("[[新卡片]]") == 1


def test_write_moc_uses_theme_page_contract_for_compatibility(tmp_path: Path) -> None:
    rel_path = write_moc(
        tmp_path,
        theme_name="AI与教育",
        body="# AI与教育\n\n## 主题判断\n\n共同讨论 AI 教育。",
        tags=["ai", "education"],
        capture_count=3,
    )

    frontmatter, rendered = _read_theme_file(tmp_path, rel_path)
    assert frontmatter["type"] == "theme"
    assert frontmatter["theme_name"] == "AI与教育"
    assert frontmatter["tags"] == ["ai", "education"]
    assert HERMES_THEME_START in rendered
    assert "## 已沉淀的知识卡片" in rendered
    assert "## 我的补充" in rendered

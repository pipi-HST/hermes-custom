"""Regression tests: user bodies and captured sources are never compressed away."""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.capture_agent import AI_READING_GUIDE_HEADING, CaptureAgent, CaptureConfig
from agents.extractors.text_extractor import TextExtractor
from agents.markdown_writer import read_note


LONG_ORIGINAL = (
    "用户写下的第一段完整观点：知识库的价值不是收藏更多文章，而是让同一主题的多次输入互相印证。\n\n"
    "用户写下的第二段完整观点：如果系统在保存时把正文压缩成摘要，用户就失去了回看原始论证和案例的能力。\n\n"
    "用户写下的第三段完整观点：因此保存动作必须保真，AI 只能补充导读、标签和分类建议。"
)


@pytest.fixture
def agent(tmp_path: Path) -> CaptureAgent:
    config = CaptureConfig(
        llm_api_key="sk-test",
        llm_provider="openai",
        llm_model="gpt-4o",
        llm_base_url="https://api.deepseek.com",
        vault_path=str(tmp_path),
        db_path=str(tmp_path / "test.db"),
    )
    agent = CaptureAgent(config)
    agent.extractors = [TextExtractor()]
    agent._capture_planning_enabled = False
    return agent


def _organizer(body_markdown: str):
    def fake(_text, *, existing_tags=None, existing_folders=None):
        return {
            "title": "AI 建议标题",
            "summary": "AI 建议摘要。",
            "tags": ["ai-product"],
            "body_markdown": body_markdown,
            "folder_suggestion": {},
            "card_suggestion": {},
            "warnings": [],
        }

    return fake


def test_create_editable_draft_keeps_original_when_ai_compresses(agent, monkeypatch):
    monkeypatch.setattr(
        agent.summarizer,
        "organize_editable_draft",
        _organizer("知识库要保真，AI 只补导读。"),
    )

    draft = agent.create_editable_draft(LONG_ORIGINAL)

    assert draft["draft_text"] == LONG_ORIGINAL
    assert any("保留你输入的完整正文" in item for item in draft["capture_intent"]["warnings"])


def test_create_editable_draft_accepts_structure_only_rewrite(agent, monkeypatch):
    structured = LONG_ORIGINAL.replace("\n\n", "\n\n### 分段\n\n")
    monkeypatch.setattr(
        agent.summarizer,
        "organize_editable_draft",
        _organizer(structured),
    )

    draft = agent.create_editable_draft(LONG_ORIGINAL)

    assert draft["draft_text"] == structured


def test_update_editable_draft_keeps_user_body_when_ai_compresses(agent, monkeypatch):
    monkeypatch.setattr(
        agent.summarizer,
        "organize_editable_draft",
        _organizer(LONG_ORIGINAL),
    )
    draft = agent.create_editable_draft(LONG_ORIGINAL)

    monkeypatch.setattr(
        agent.summarizer,
        "organize_editable_draft",
        _organizer("只剩一句摘要。"),
    )
    updated = agent.update_editable_draft(
        draft["id"],
        content=LONG_ORIGINAL,
        organize=True,
    )

    assert updated is not None
    assert updated["draft_text"] == LONG_ORIGINAL


def test_capture_with_reading_guide_keeps_full_original_below_guide(agent, tmp_path):
    source = LONG_ORIGINAL
    draft = agent.create_draft(
        source,
        instruction="保存并把核心观点提炼存入知识库，做一份核心导读",
    )

    assert draft["status"] == "draft_ready"
    assert draft["capture_intent"]["content_shape"] == "guide_plus_original"
    assert draft["draft_text"].startswith(AI_READING_GUIDE_HEADING)
    assert source in draft["draft_text"]

    committed = agent.commit_draft(draft["id"])
    assert "error" not in committed

    capture_note = read_note(tmp_path, committed["file_path"])
    raw_note = read_note(tmp_path, committed["raw_path"])
    assert capture_note is not None and raw_note is not None
    _frontmatter, capture_body = capture_note
    _raw_frontmatter, raw_body = raw_note
    assert capture_body.startswith(AI_READING_GUIDE_HEADING)
    assert source in capture_body
    assert raw_body == source

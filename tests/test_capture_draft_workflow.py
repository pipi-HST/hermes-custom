from __future__ import annotations

from pathlib import Path

import pytest

from agents.capture_agent import CaptureAgent, CaptureConfig
from agents.extractors.text_extractor import TextExtractor


TEST_CONTENT = (
    "AI时代的电力协议讨论了算力中心、电网扩容和中国变压器供应链之间的关系。"
    "文章指出AI发展的瓶颈不仅是芯片和模型，也包括稳定电力基础设施。"
    "其中关于中国变压器供应链的部分尤其重要，因为电力设备交付周期长，"
    "会直接影响数据中心扩张速度。补充文字确保内容长度足够通过质量检测。"
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
    return agent


def test_create_draft_without_instruction_does_not_write_obsidian(agent: CaptureAgent, tmp_path: Path):
    draft = agent.create_draft(TEST_CONTENT)

    assert draft["status"] == "raw_extracted"
    assert draft["raw_text"]
    assert draft["draft_text"] == ""
    assert not (tmp_path / "raw").exists()
    assert not (tmp_path / "captures").exists()


def test_create_draft_with_instruction_returns_draft_text(agent: CaptureAgent, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "核心观点：AI瓶颈在电力。")
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "AI电力协议",
            "summary": "AI发展受电力基础设施制约。",
            "tags": ["ai-energy"],
        },
    )

    draft = agent.create_draft(TEST_CONTENT, instruction="总结全文核心观点")

    assert draft["status"] == "draft_ready"
    assert draft["draft_text"] == "核心观点：AI瓶颈在电力。"
    assert draft["summary"] == "AI发展受电力基础设施制约。"
    assert draft["tags"] == ["ai-energy"]
    assert not (tmp_path / "captures").exists()


def test_revise_draft_uses_raw_text_and_updates_current_draft(agent: CaptureAgent, monkeypatch):
    draft = agent.create_draft(TEST_CONTENT)

    def fake_llm(prompt: str) -> str:
        assert "中国变压器" in prompt
        return "局部提取：中国变压器是电力供应链关键瓶颈。"

    monkeypatch.setattr(agent, "_call_llm", fake_llm)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "中国变压器瓶颈",
            "summary": "中国变压器供应链影响AI数据中心扩张。",
            "tags": ["power-grid"],
        },
    )

    revised = agent.revise_draft(draft["id"], "提取中国变压器部分")

    assert revised["id"] == draft["id"]
    assert revised["status"] == "draft_ready"
    assert "中国变压器" in revised["draft_text"]
    assert revised["instruction"] == "提取中国变压器部分"


def test_commit_draft_writes_raw_and_capture_files(agent: CaptureAgent, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "最终草稿：AI瓶颈在电力。")
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "AI电力协议",
            "summary": "AI发展受电力基础设施制约。",
            "tags": ["ai-energy"],
        },
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结全文核心观点")

    committed = agent.commit_draft(draft["id"])

    assert committed["status"] == "committed"
    assert committed["capture_id"]
    assert committed["raw_path"].startswith("raw/")
    assert committed["file_path"].startswith("captures/")
    assert (tmp_path / committed["raw_path"]).exists()
    assert (tmp_path / committed["file_path"]).exists()

    record = agent.repo.get_by_id(committed["capture_id"])
    assert record["raw_path"] == committed["raw_path"]
    assert record["file_path"] == committed["file_path"]


def test_revise_committed_draft_rejected(agent: CaptureAgent, monkeypatch):
    """已入库的草稿不允许再修改。"""
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "草稿内容")
    monkeypatch.setattr(
        agent.summarizer, "summarize",
        lambda text, existing_tags=None: {"summary": "摘要", "tags": ["test"]},
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结")
    agent.commit_draft(draft["id"])

    result = agent.revise_draft(draft["id"], "再改改")
    assert result is not None
    assert "error" in result
    assert "入库" in result["error"]


def test_commit_already_committed_draft_rejected(agent: CaptureAgent, monkeypatch):
    """已入库的草稿不允许重复提交。"""
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "草稿内容")
    monkeypatch.setattr(
        agent.summarizer, "summarize",
        lambda text, existing_tags=None: {"summary": "摘要", "tags": ["test"]},
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结")
    agent.commit_draft(draft["id"])

    result = agent.commit_draft(draft["id"])
    assert result is not None
    assert "error" in result
    assert "已入库" in result["error"]


def test_revise_abandoned_draft_rejected(agent: CaptureAgent, monkeypatch):
    """已废弃的草稿不允许修改。"""
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "草稿内容")
    monkeypatch.setattr(
        agent.summarizer, "summarize",
        lambda text, existing_tags=None: {"summary": "摘要", "tags": ["test"]},
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结")
    agent.draft_repo.update(draft["id"], status="abandoned")

    result = agent.revise_draft(draft["id"], "再改改")
    assert result is not None
    assert "error" in result
    assert "废弃" in result["error"]

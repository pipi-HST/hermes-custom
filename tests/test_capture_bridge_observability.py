from __future__ import annotations

import json

from agents.capture_agent import CaptureAgent, CaptureConfig
from agents.extractors.text_extractor import TextExtractor
from bridge import capture_bridge
from db.agent_run_repo import AgentRunRepo


SOURCE_TEXT = (
    "AI产品评测不只是跑分，而是把产品判断力写进可重复执行的评估体系。"
    "文章讨论了人工评测、数据飞轮、错误样本沉淀和持续迭代。"
    "它还强调，AI产品经理要把主观判断转化为可验证的样本、指标和流程。"
)


def _install_agent(monkeypatch, tmp_path) -> CaptureAgent:
    config = CaptureConfig(
        llm_api_key="sk-test",
        llm_provider="openai",
        llm_model="gpt-4o",
        llm_base_url="https://api.deepseek.com",
        vault_path=str(tmp_path),
        db_path=str(tmp_path / "captures.db"),
    )
    agent = CaptureAgent(config)
    agent.extractors = [TextExtractor()]
    monkeypatch.setattr(capture_bridge, "_config", config)
    monkeypatch.setattr(capture_bridge, "_agent", agent)
    return agent


def test_draft_preview_skips_conversational_preamble():
    message, _ = capture_bridge._draft_saved_preview(
        {
            "title": "AI产品评测",
            "summary": "AI产品经理要把判断力注入评测体系。",
            "status": "draft_ready",
            "capture_intent": {"emphasis": []},
            "draft_text": (
                "好的，我们开始。\n\n"
                "---\n\n"
                "AI产品评测不只是跑分，而是把产品判断力写进可重复执行的评估体系。"
            ),
        },
        "生成",
    )

    key_response = message.split("重点回应：", 1)[1].split("\n\n总结：", 1)[0]
    assert "好的，我们开始" not in key_response
    assert key_response.strip().startswith("AI产品评测不只是跑分")


def test_capture_content_records_run_ledger_and_strips_preamble(monkeypatch, tmp_path):
    agent = _install_agent(monkeypatch, tmp_path)
    monkeypatch.setattr(
        agent,
        "_call_llm",
        lambda prompt: "好的，我们开始。\n\nAI产品评测的核心是把判断力变成可复用的评估流程。",
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "AI产品评测",
            "summary": "AI产品经理要把判断力注入评测体系。",
            "tags": ["ai-product"],
        },
    )

    result = capture_bridge.capture_content(SOURCE_TEXT, instruction="稍加总结，尽可能地提取原文")

    assert result["status"] == "success"
    assert "好的，我们开始" not in result["chat_preview"]
    draft_id = result["durable_artifacts"][0]["id"]
    assert not agent.draft_repo.get_by_id(draft_id)["draft_text"].startswith("好的")

    repo = AgentRunRepo(agent.conn)
    latest = repo.latest(limit=1)[0]
    evidence = json.loads(latest["evidence_json"])
    output = json.loads(latest["output_json"])
    events = [event["event_type"] for event in repo.events(latest["id"])]

    assert latest["agent"] == "capture"
    assert latest["intent"] == "create_capture_draft"
    assert latest["status"] == "success"
    assert evidence["draft_id"] == draft_id
    assert evidence["draft_status"] == "draft_ready"
    assert output["title"].startswith("AI产品评测")
    assert events == ["started", "completed"]

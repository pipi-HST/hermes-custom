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
    assert "原文已提取，已生成待确认初稿" in result["chat_preview"]
    draft_id = result["durable_artifacts"][0]["id"]
    assert agent.draft_repo.get_by_id(draft_id)["draft_text"] == ""

    repo = AgentRunRepo(agent.conn)
    latest = repo.latest(limit=1)[0]
    evidence = json.loads(latest["evidence_json"])
    output = json.loads(latest["output_json"])
    events = [event["event_type"] for event in repo.events(latest["id"])]

    assert latest["agent"] == "capture"
    assert latest["intent"] == "create_capture_draft"
    assert latest["status"] == "success"
    assert evidence["draft_id"] == draft_id
    assert evidence["draft_status"] == "raw_extracted"
    assert output["title"].startswith("AI产品评测")
    assert events == ["started", "completed"]


def test_capture_content_records_stage_timings(monkeypatch, tmp_path):
    agent = _install_agent(monkeypatch, tmp_path)
    monkeypatch.setattr(
        agent,
        "_call_llm",
        lambda prompt: "AI产品评测的核心是把判断力变成可复用的评估流程。",
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

    result = capture_bridge.capture_content(SOURCE_TEXT, instruction="整理保存")

    assert result["status"] == "success"
    repo = AgentRunRepo(agent.conn)
    latest = repo.latest(limit=1)[0]
    evidence = json.loads(latest["evidence_json"])
    timings = evidence["stage_timings_ms"]
    assert {
        "extract_clean",
        "intent_planning",
        "draft_create",
        "revision_llm",
        "metadata_summary",
        "repo_update",
        "total",
    } <= set(timings)
    assert timings["revision_llm"] == 0
    assert timings["metadata_summary"] == 0
    assert all(isinstance(timings[key], int) and timings[key] >= 0 for key in timings)


def test_editable_draft_parse_diagnostics_reach_run_ledger(monkeypatch, tmp_path):
    agent = _install_agent(monkeypatch, tmp_path)
    monkeypatch.setattr(
        agent.summarizer,
        "_call_llm",
        lambda _prompt, *, max_tokens=4000, json_mode=False: '{"title": "半截"',
    )

    result = capture_bridge.create_editable_capture_draft(
        "这是一段用于验证诊断落库的长文。" * 20,
        existing_folders=["Inbox", "AI 产品"],
    )

    assert result["status"] == "success"
    draft_id = result["durable_artifacts"][0]["id"]
    draft = agent.draft_repo.get_by_id(draft_id)
    diagnostics = draft["capture_intent"]["diagnostics"]
    assert diagnostics["parse_error_kind"] == "json_parse_failed"
    assert diagnostics["response_len"] == len('{"title": "半截"')
    assert diagnostics["response_head"].startswith("{")
    assert diagnostics["response_tail"].endswith('"半截"')

    latest = AgentRunRepo(agent.conn).latest(limit=1)[0]
    evidence = json.loads(latest["evidence_json"])
    assert evidence["draft_id"] == draft_id
    assert evidence["llm_parse_failed"] is True
    assert evidence["llm_parse_error_kind"] == "json_parse_failed"
    assert evidence["llm_response_len"] == len('{"title": "半截"')


def test_draft_preview_uses_body_after_emphasis_heading():
    from bridge import capture_bridge

    message, _ = capture_bridge._draft_saved_preview(
        {
            "title": "冰火两重天",
            "summary": "市场节奏受世界杯影响。",
            "draft_text": (
                "# 冰火两重天：极端割裂下的市场节奏与风险\n\n"
                "## 二、世界杯魔咒：市场节奏的核心变量\n\n"
                "世界杯会吸引短线投机资金从股市抽离，导致交投清淡、流动性阶段性收紧。"
                "在科技板块高度拥挤时，这会放大踩踏风险。\n\n"
                "## 三、其他内容\n\n"
                "其他段落。"
            ),
            "capture_intent": {"emphasis": ["世界杯期间市场节奏"]},
        },
        "生成",
    )

    key_response = message.split("重点回应：", 1)[1].split("\n\n总结：", 1)[0]
    assert "世界杯会吸引短线投机资金" in key_response
    assert "冰火两重天：极端割裂" not in key_response

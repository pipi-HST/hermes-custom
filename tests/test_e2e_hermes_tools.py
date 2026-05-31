"""E2E 集成测试。

Layer 1 (Hermes): schema + handler — 跑在 Hermes venv
  cd ~/Downloads/hermes-agent-main
  PYTHONPATH=/Users/pipi/hermes-custom uv run pytest /Users/pipi/hermes-custom/tests/test_e2e_hermes_tools.py -v -k "hermes"

Layer 2 (Bridge): bridge → agent 数据流 — Python 3.9 OK
  cd ~/hermes-custom && python3 -m pytest tests/test_e2e_hermes_tools.py -v -k "bridge"

Layer 3 (Real API): 真实 LLM/Embedding — 手动触发
  RUN_REAL_TESTS=1 python3 -m pytest tests/test_e2e_hermes_tools.py -v -k "real"
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, "/Users/pipi/hermes-custom")
HERMES_DIR = "/Users/pipi/Downloads/hermes-agent-main"


def _artifact(result: dict, artifact_type: str = "capture_draft") -> dict:
    matches = [
        item for item in result.get("durable_artifacts", [])
        if item.get("type") == artifact_type
    ]
    assert matches, f"missing {artifact_type} artifact in {result}"
    return matches[0]


def _tool_envelope(result: str) -> dict:
    from bridge.result_envelope import ENVELOPE_FIELDS

    envelope = json.loads(result)
    assert list(envelope.keys()) == list(ENVELOPE_FIELDS)
    return envelope


def _skip_if_no_hermes():
    try:
        sys.path.insert(0, HERMES_DIR)
        from tools.registry import registry  # noqa: F401
    except Exception:
        pytest.skip("Hermes tools not importable")


# ═══════ Layer 1: Hermes Schema & Handler ═══════

def _get_hermes_tools():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    import tools.capture_tool  # noqa
    import tools.refine_capture_tool  # noqa
    import tools.revise_capture_draft_tool  # noqa
    import tools.commit_capture_draft_tool  # noqa
    import tools.show_capture_draft_tool  # noqa
    import tools.search_notes_tool  # noqa
    import tools.theme_scan_tool  # noqa
    import tools.synthesize_garden_tool  # noqa
    import tools.revise_garden_draft_tool  # noqa
    import tools.commit_garden_draft_tool  # noqa
    import tools.record_life_event_tool  # noqa
    import tools.life_tick_tool  # noqa
    from tools.registry import registry
    return registry._tools


def test_hermes_capture_schema_has_content():
    _skip_if_no_hermes()
    tools = _get_hermes_tools()
    description = tools["capture_content"].schema.get("description", "")
    params = tools["capture_content"].schema.get("parameters", {})
    props = params.get("properties", {})
    assert "content" in props, f"missing content, props={list(props.keys())}"
    assert "instruction" in props, f"missing instruction, props={list(props.keys())}"
    assert "content" in params.get("required", [])
    assert "微信" in description
    assert "待确认整理稿" in description
    assert "USER_VISIBLE_REPLY" in description


def test_hermes_capture_schema_preserves_full_text_save_instruction():
    _skip_if_no_hermes()
    tools = _get_hermes_tools()
    description = tools["capture_content"].schema.get("description", "")
    instruction_description = (
        tools["capture_content"]
        .schema.get("parameters", {})
        .get("properties", {})
        .get("instruction", {})
        .get("description", "")
    )

    assert "全文保存" in description
    assert "全文存进数据库" in description
    assert "不要只传 URL" in description
    assert "全文存储" in instruction_description
    assert "instruction" in instruction_description


def test_hermes_draft_tools_registered():
    _skip_if_no_hermes()
    tools = _get_hermes_tools()
    assert "revise_capture_draft" in tools
    assert "commit_capture_draft" in tools
    assert "show_capture_draft" in tools
    assert "微信" in tools["revise_capture_draft"].schema.get("description", "")
    assert "展开草稿" in tools["show_capture_draft"].schema.get("description", "")
    assert "待确认整理稿" in tools["refine_capture"].schema.get("description", "")
    assert "USER_VISIBLE_REPLY" in tools["refine_capture"].schema.get("description", "")


def test_hermes_search_schema_has_query():
    _skip_if_no_hermes()
    tools = _get_hermes_tools()
    params = tools["search_my_notes"].schema.get("parameters", {})
    assert "query" in params.get("properties", {})


def test_hermes_theme_scan_schema():
    _skip_if_no_hermes()
    tools = _get_hermes_tools()
    params = tools["theme_scan"].schema.get("parameters", {})
    assert params.get("type") == "object"
    props = params.get("properties", {})
    assert "rescan_all" in props
    assert "重建" in tools["theme_scan"].schema.get("description", "")
    assert "可沉淀方向" in tools["theme_scan"].schema.get("description", "")


def test_hermes_life_tools_registered():
    _skip_if_no_hermes()
    tools = _get_hermes_tools()

    assert "record_life_event" in tools
    assert "life_tick" in tools
    assert tools["record_life_event"].toolset == "personal-assistant"
    assert tools["life_tick"].toolset == "personal-assistant"
    record_description = tools["record_life_event"].schema.get("description", "")
    assert "生活" in record_description
    assert "长期决定" in record_description
    assert "补充下次复盘时间" in record_description
    assert "不要用于知识笔记" in record_description
    assert "手动状态查询" in record_description
    assert "dry_run" in tools["life_tick"].schema.get("parameters", {}).get("properties", {})


def test_runtime_prompt_prioritizes_life_voice_records_over_capture():
    config_path = os.path.expanduser("~/.hermes/config.yaml")
    if not os.path.exists(config_path):
        pytest.skip("Hermes runtime config not found")
    yaml = pytest.importorskip("yaml")

    with open(config_path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    prompt = config.get("agent", {}).get("system_prompt", "")

    assert "Life routing has priority over capture_content" in prompt
    assert "long-term decisions" in prompt
    assert "review time follow-ups" in prompt
    assert "Clear life/health records" in prompt
    assert prompt.index("Life routing has priority over capture_content") < prompt.index("Clear life/health records")


def test_hermes_tools_have_name_description_parameters():
    _skip_if_no_hermes()
    tools = _get_hermes_tools()
    for name, t in tools.items():
        if t.toolset != "personal-assistant":
            continue
        s = t.schema
        assert s.get("name"), f"{name}: missing name"
        assert s.get("description"), f"{name}: missing description"
        p = s.get("parameters", {})
        assert p.get("type") == "object", f"{name}: params missing type"


def test_hermes_capture_handler_with_content():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from tools.capture_tool import _handler
    result = _handler({"content": "AI Agent 四个核心组件"})
    envelope = _tool_envelope(result)
    assert envelope["status"] in {"success", "error"}
    assert len(envelope["chat_preview"]) > 0


def test_hermes_capture_handler_empty_args_no_crash():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from tools.capture_tool import _handler
    result = _handler({})
    envelope = _tool_envelope(result)
    assert envelope["status"] == "error"
    assert len(envelope["chat_preview"]) > 0


def test_hermes_capture_dispatch_exception_returns_envelope(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    import tools.capture_tool as capture_tool
    from tools.registry import registry

    monkeypatch.setattr(
        capture_tool,
        "do_capture",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = registry.dispatch("capture_content", {"content": "测试"})
    envelope = _tool_envelope(result)

    assert envelope["status"] == "error"
    assert "boom" in " ".join(envelope["errors"])
    assert envelope["chat_preview"]


def test_hermes_capture_handler_preserves_next_actions(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import next_action, success_envelope
    import tools.capture_tool as capture_tool

    expected = success_envelope(
        agent="capture",
        intent="create_capture_draft",
        chat_preview="已生成草稿。",
        next_actions=[
            next_action(
                label="存库",
                intent="commit_capture_draft",
                payload={"draft_id": "d1"},
                requires_confirmation=True,
            )
        ],
    )
    monkeypatch.setattr(capture_tool, "do_capture", lambda *args, **kwargs: expected)

    envelope = _tool_envelope(capture_tool._handler({"content": "测试"}))

    assert envelope["chat_preview"] == "已生成草稿。"
    assert envelope["next_actions"][0]["requires_confirmation"] is True


def test_hermes_commit_handler_empty_args_no_crash():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from tools.commit_capture_draft_tool import _handler
    result = _handler({})
    envelope = _tool_envelope(result)
    assert envelope["status"] in {"success", "error"}
    assert len(envelope["chat_preview"]) > 0


def test_hermes_revise_handler_preserves_envelope(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import success_envelope
    import tools.revise_capture_draft_tool as revise_tool

    expected = success_envelope(
        agent="capture",
        intent="revise_capture_draft",
        chat_preview="已更新草稿。",
    )
    monkeypatch.setattr(revise_tool, "revise_capture_draft", lambda *args: expected)

    envelope = _tool_envelope(revise_tool._handler({"instruction": "改短"}))

    assert envelope["intent"] == "revise_capture_draft"
    assert envelope["chat_preview"] == "已更新草稿。"


def test_hermes_show_capture_draft_handler_preserves_envelope(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import success_envelope
    import tools.show_capture_draft_tool as show_tool

    expected = success_envelope(
        agent="capture",
        intent="show_capture_draft",
        chat_preview="第 1/2 页\n草稿正文",
    )
    monkeypatch.setattr(show_tool, "show_capture_draft", lambda *args: expected)

    envelope = _tool_envelope(show_tool._handler({"page": 1}))

    assert envelope["intent"] == "show_capture_draft"
    assert envelope["chat_preview"] == "第 1/2 页\n草稿正文"


def test_hermes_refine_handler_preserves_envelope(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import success_envelope
    import tools.refine_capture_tool as refine_tool

    expected = success_envelope(
        agent="capture",
        intent="revise_capture_draft",
        chat_preview="已加工草稿。",
    )
    monkeypatch.setattr(refine_tool, "do_revise", lambda *args: expected)

    envelope = _tool_envelope(refine_tool._handler({"instruction": "改短"}))

    assert envelope["intent"] == "revise_capture_draft"
    assert envelope["chat_preview"] == "已加工草稿。"


def test_hermes_search_handler_runs():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from tools.search_notes_tool import _handler
    result = _handler({"query": "测试查询"})
    envelope = _tool_envelope(result)
    assert envelope["agent"] == "rag"
    assert len(envelope["chat_preview"]) > 0
    assert "handler_instruction" in envelope["meta"]


def test_hermes_search_handler_preserves_source_map(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import source_ref, success_envelope
    import tools.search_notes_tool as search_notes_tool

    expected = success_envelope(
        agent="rag",
        intent="search_knowledge_base",
        chat_preview="答案 [1]",
        source_map=[
            source_ref(
                role="evidence",
                type="capture_note",
                id="cap1",
                title="来源",
                path="captures/source.md",
            )
        ],
    )
    monkeypatch.setattr(search_notes_tool, "do_search", lambda query: expected)

    envelope = _tool_envelope(search_notes_tool._handler({"query": "测试"}))

    assert envelope["chat_preview"] == "答案 [1]"
    assert envelope["source_map"][0]["id"] == "cap1"
    assert "handler_instruction" in envelope["meta"]


def test_hermes_theme_scan_handler_runs():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from tools.theme_scan_tool import _handler
    result = _handler({})
    envelope = _tool_envelope(result)
    assert envelope["agent"] == "theme"
    assert len(envelope["chat_preview"]) > 0


def test_hermes_tool_executor_uses_chat_preview_for_model_content():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from agent.hermes_custom_envelope_adapter import agent_result_envelope_content_for_model
    from bridge.result_envelope import envelope_json, source_ref, success_envelope

    result = envelope_json(
        success_envelope(
            agent="rag",
            intent="search_knowledge_base",
            chat_preview="这是给用户看的短回答 [1]",
            source_map=[
                source_ref(
                    role="evidence",
                    type="capture_note",
                    id="cap1",
                    title="来源",
                    path="captures/source.md",
                )
            ],
        )
    )
    content = agent_result_envelope_content_for_model(result)

    assert content is not None
    assert content.startswith("USER_VISIBLE_REPLY:\n这是给用户看的短回答 [1]")
    assert "Return USER_VISIBLE_REPLY as-is" in content
    assert "next_actions field is a list of user-facing options" in content
    assert "do not call those tools unless the user explicitly asks" in content
    assert "STRUCTURED_AGENT_RESULT_DO_NOT_REWRITE" in content
    assert '"source_map"' in content
    assert '"chat_preview"' not in content


def test_hermes_graph_report_handler_preserves_artifacts(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import artifact_ref, success_envelope
    import tools.graph_report_tool as graph_report_tool

    expected = success_envelope(
        agent="theme",
        intent="run_graph_report",
        chat_preview="报告",
        durable_artifacts=[
            artifact_ref(
                type="librarian_report",
                id="r1",
                title="知识图谱健康报告",
                path="data/reports/r1.md",
                status="reported",
            )
        ],
    )
    monkeypatch.setattr(graph_report_tool, "run_graph_report", lambda: expected)

    envelope = _tool_envelope(graph_report_tool._handler({}))

    assert envelope["durable_artifacts"][0]["type"] == "librarian_report"


def test_hermes_daily_review_handler_preserves_envelope(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import success_envelope
    import tools.daily_review_tool as daily_review_tool

    expected = success_envelope(
        agent="review",
        intent="run_daily_review",
        chat_preview="今日复习",
        meta={"item_count": 1},
    )
    monkeypatch.setattr(daily_review_tool, "run_daily_review", lambda: expected)

    envelope = _tool_envelope(daily_review_tool._handler({}))

    assert envelope["agent"] == "review"
    assert envelope["meta"]["item_count"] == 1


def test_hermes_record_life_event_handler_preserves_envelope(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import success_envelope
    import tools.record_life_event_tool as life_tool

    expected = success_envelope(
        agent="life",
        intent="record_life_event",
        chat_preview="已记录午餐：牛肉饭。",
        meta={"event_type": "meal", "should_notify": False},
    )
    monkeypatch.setattr(life_tool, "do_record_life_event", lambda *args, **kwargs: expected)

    envelope = _tool_envelope(
        life_tool._handler(
            {"message": "午饭吃了牛肉饭", "received_at": "2026-05-30T12:00:00+08:00"}
        )
    )

    assert envelope["agent"] == "life"
    assert envelope["intent"] == "record_life_event"
    assert envelope["meta"]["event_type"] == "meal"


def test_hermes_life_tick_handler_preserves_should_notify(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import success_envelope
    import tools.life_tick_tool as life_tick_tool

    expected = success_envelope(
        agent="life",
        intent="life_tick",
        chat_preview="Life Assistant 本次 tick 无需提醒。",
        meta={"should_notify": False, "selected_reminder_type": ""},
    )
    monkeypatch.setattr(life_tick_tool, "do_life_tick", lambda *args, **kwargs: expected)

    envelope = _tool_envelope(life_tick_tool._handler({"dry_run": True}))

    assert envelope["agent"] == "life"
    assert envelope["intent"] == "life_tick"
    assert envelope["meta"]["should_notify"] is False


def test_hermes_audit_library_handler_preserves_confirmation(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import next_action, success_envelope
    import tools.librarian_tool as librarian_tool

    expected = success_envelope(
        agent="librarian",
        intent="audit_library",
        chat_preview="Librarian 审计完成。",
        next_actions=[
            next_action(
                label="执行安全修复",
                intent="apply_librarian_repair",
                requires_confirmation=True,
            )
        ],
    )
    monkeypatch.setattr(librarian_tool, "run_librarian_audit", lambda **kwargs: expected)

    envelope = _tool_envelope(librarian_tool._handler({}))

    assert envelope["agent"] == "librarian"
    assert envelope["intent"] == "audit_library"
    assert envelope["next_actions"][0]["requires_confirmation"] is True


def test_hermes_synthesize_garden_handler_preserves_confirmation(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import artifact_ref, next_action, success_envelope
    import tools.synthesize_garden_tool as garden_tool

    expected = success_envelope(
        agent="garden",
        intent="synthesize_garden",
        chat_preview="已生成 Garden 草稿。",
        durable_artifacts=[
            artifact_ref(
                type="garden_draft",
                id="d1",
                title="观点草稿",
                status="draft_ready",
            )
        ],
        next_actions=[
            next_action(
                label="确认观点入库",
                intent="commit_garden_draft",
                payload={"draft_id": "d1"},
                requires_confirmation=True,
            )
        ],
    )
    monkeypatch.setattr(garden_tool, "do_synthesize", lambda **kwargs: expected)

    envelope = _tool_envelope(
        garden_tool._handler({"capture_ids": ["c1"]}, task_id="t1", user_task="u1")
    )

    assert envelope["agent"] == "garden"
    assert envelope["next_actions"][0]["requires_confirmation"] is True
    assert envelope["next_actions"][0]["intent"] == "commit_garden_draft"


def test_hermes_commit_garden_draft_handler_preserves_artifact(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import artifact_ref, success_envelope
    import tools.commit_garden_draft_tool as commit_tool

    expected = success_envelope(
        agent="garden",
        intent="commit_garden_draft",
        chat_preview="已存入知识花园。",
        durable_artifacts=[
            artifact_ref(
                type="garden_card",
                id="g1",
                title="观点",
                path="garden/观点-g1.md",
                status="seed",
            )
        ],
    )
    monkeypatch.setattr(commit_tool, "do_commit", lambda **kwargs: expected)

    envelope = _tool_envelope(
        commit_tool._handler({"draft_id": "d1"}, task_id="t1", user_task="u1")
    )

    assert envelope["agent"] == "garden"
    assert envelope["intent"] == "commit_garden_draft"
    assert envelope["durable_artifacts"][0]["type"] == "garden_card"


def test_hermes_toolset_has_6_tools():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from toolsets import TOOLSETS
    tools = TOOLSETS["personal-assistant"]["tools"]
    assert len(tools) >= 16, f"Expected draft and life tools, got {len(tools)}: {tools}"
    expected = {"capture_content", "refine_capture", "search_my_notes", "log_decision",
                "wellness_check", "theme_scan", "graph_report", "send_message",
                "revise_capture_draft", "commit_capture_draft", "synthesize_garden",
                "revise_garden_draft", "commit_garden_draft", "daily_review",
                "show_capture_draft", "audit_library",
                "record_life_event", "life_tick", "review_feedback", "review_followup"}
    assert set(tools) == expected, f"Mismatch: {set(tools) ^ expected}"


# ═══════ Layer 2: Bridge → Agent Pipeline ═══════

@patch("agents.capture_agent.Summarizer.summarize")
@patch("agents.extractors.link_extractor.LinkExtractor.extract")
def test_bridge_capture_url(mock_extract, mock_summarize):
    from bridge.capture_bridge import capture_content
    from agents.extractors.base import ExtractionResult
    mock_extract.return_value = ExtractionResult(
        raw_text=("这是一篇关于人工智能和深度学习的文章。人工智能技术正在快速发展，"
                  "深度学习模型在各种任务中取得了显著的成果。从图像识别到自然语言处理，"
                  "AI的应用越来越广泛。未来AI将继续改变我们的生活方式。"
                  "补充文本让中文字符数量超过一百个以确保通过内容质量检测。"),
        source_type="wechat_article", source_url="https://mp.weixin.qq.com/s/test",
    )
    mock_summarize.return_value = {
        "title": "标题", "summary": "摘要", "tags": ["ai"],
    }
    r = capture_content("https://mp.weixin.qq.com/s/test")
    assert r["status"] == "success"
    assert _artifact(r)["status"] == "raw_extracted"
    assert _artifact(r)["id"]
    assert not any(a["type"] == "raw_evidence" for a in r["durable_artifacts"])


@patch("agents.capture_agent.Summarizer.summarize")
def test_bridge_capture_text(mock_summarize):
    from bridge.capture_bridge import capture_content
    mock_summarize.return_value = {"title": "KM", "summary": "S", "tags": ["km"]}
    r = capture_content(
        "知识管理的核心是将碎片信息转化为可复用的认知资产。"
        "个人知识管理有三个层次：捕获、整理、内化。这需要长期坚持和积累。"
        "补充：人工智能技术正在快速发展，深度学习模型取得了显著成果。"
        "从图像识别到自然语言处理，AI应用越来越广泛地改变着我们的世界。")
    assert r["status"] == "success"
    assert _artifact(r)["status"] == "raw_extracted"
    assert _artifact(r)["id"]


@patch("agents.capture_agent.CaptureAgent._call_llm")
@patch("agents.capture_agent.Summarizer.summarize")
def test_bridge_capture_text_with_instruction_returns_draft(mock_summarize, mock_llm):
    from bridge.capture_bridge import capture_content
    mock_llm.return_value = "核心观点：知识管理需要捕获、整理、内化。"
    mock_summarize.return_value = {"title": "KM", "summary": "S", "tags": ["km"]}
    r = capture_content(
        "知识管理的核心是将碎片信息转化为可复用的认知资产。"
        "个人知识管理有三个层次：捕获、整理、内化。这需要长期坚持和积累。"
        "补充：人工智能技术正在快速发展，深度学习模型取得了显著成果。"
        "从图像识别到自然语言处理，AI应用越来越广泛地改变着我们的世界。",
        instruction="总结全文核心观点",
    )
    assert r["status"] == "success"
    assert _artifact(r)["status"] == "draft_ready"
    assert "完整草稿已保存" in r["chat_preview"]
    assert "标题：" in r["chat_preview"]
    assert "重点回应：" in r["chat_preview"]
    assert "总结：" in r["chat_preview"]
    assert "草稿预览" not in r["chat_preview"]
    assert "标签：" not in r["chat_preview"]
    assert "目录纲要" not in r["chat_preview"]
    assert "参考来源" not in r["chat_preview"]
    assert "核心观点" in r["chat_preview"]
    assert "__HERMES_DIRECT_REPLY__" not in r["chat_preview"]


@patch("agents.capture_agent.CaptureAgent._call_llm")
@patch("agents.capture_agent.Summarizer.summarize")
def test_bridge_capture_full_text_save_does_not_summarize_body(mock_summarize, mock_llm):
    from bridge.capture_bridge import capture_content

    mock_llm.side_effect = AssertionError("full text save should not call LLM")
    mock_summarize.return_value = {
        "title": "全文保存",
        "summary": "原文全文已保存。",
        "tags": ["source-archive"],
    }

    r = capture_content(
        "知识管理的核心是将碎片信息转化为可复用的认知资产。"
        "个人知识管理有三个层次：捕获、整理、内化。这需要长期坚持和积累。"
        "补充：人工智能技术正在快速发展，深度学习模型取得了显著成果。"
        "从图像识别到自然语言处理，AI应用越来越广泛地改变着我们的世界。",
        instruction="全文存进数据库",
    )

    assert r["status"] == "success"
    assert _artifact(r)["status"] == "draft_ready"
    assert "全文草稿" in r["chat_preview"]
    assert "满意回复「存库」" in r["chat_preview"]
    assert r["meta"]["capture_intent"]["output_shape"] == ["full_text"]


def test_draft_preview_key_response_uses_user_emphasis():
    from bridge.capture_bridge import _draft_saved_preview

    message, _ = _draft_saved_preview(
        {
            "title": "Opus 4.8",
            "summary": "这篇文章讨论 Opus 4.8 的战略转向。",
            "status": "draft_ready",
            "capture_intent": {
                "emphasis": [
                    "41天发版的另一层是节奏狙击",
                    "Terminal-Bench那个故意没补的坑",
                ],
            },
            "draft_text": (
                "Anthropic 发布 Opus 4.8，表面看只是 benchmark 小幅提升，"
                "但文章真正关心的是产品战略和 agentic coding 的变化。\n\n"
                "## 41 天发版的另一层：节奏狙击\n\n"
                "41 天发版不是普通补丁节奏，而是在 GPT-5.6 传言前切出中间态产品，"
                "用 post-training 快速迭代形成市场卡位。\n\n"
                "## Terminal-Bench 那个故意没补的坑\n\n"
                "Terminal-Bench 落后说明 Anthropic 没有把纯终端能力当主战场，"
                "它更愿意押注 IDE 风格的长链路 agent execution。"
            ),
        },
        "生成",
    )

    key_response = message.split("重点回应：", 1)[1].split("\n\n总结：", 1)[0]
    assert "41天发版的另一层是节奏狙击" in key_response
    assert "Terminal-Bench那个故意没补的坑" in key_response
    assert "benchmark 小幅提升" not in key_response


@patch("agents.capture_agent.CaptureAgent._call_llm")
def test_bridge_capture_instruction_timeout_returns_saved_raw_draft(mock_llm):
    import httpx
    from bridge.capture_bridge import capture_content

    mock_llm.side_effect = httpx.ReadTimeout("timed out")

    r = capture_content(
        "知识管理的核心是将碎片信息转化为可复用的认知资产。"
        "个人知识管理有三个层次：捕获、整理、内化。这需要长期坚持和积累。"
        "补充：人工智能技术正在快速发展，深度学习模型取得了显著成果。"
        "从图像识别到自然语言处理，AI应用越来越广泛地改变着我们的世界。",
        instruction="总结全文核心观点",
    )

    assert r["status"] == "success"
    assert _artifact(r)["status"] == "raw_extracted"
    assert _artifact(r)["id"]
    assert "已提取" in r["chat_preview"]
    assert "稍后继续整理" in r["chat_preview"]
    assert r["meta"]["draft_status"] == "raw_extracted"
    assert r["meta"]["revision_error"] == "timed out"


@patch("agents.capture_agent.CaptureAgent._call_llm")
@patch("agents.capture_agent.Summarizer.summarize")
def test_bridge_capture_with_instruction_truncates_long_message_but_keeps_full_draft(mock_summarize, mock_llm):
    from bridge.capture_bridge import capture_content

    long_draft = "核心观点：" + ("长内容" * 1000)
    mock_llm.return_value = long_draft
    mock_summarize.return_value = {"title": "长草稿", "summary": "S", "tags": ["draft"]}

    r = capture_content(
        "知识管理的核心是将碎片信息转化为可复用的认知资产。"
        "个人知识管理有三个层次：捕获、整理、内化。这需要长期坚持和积累。"
        "补充：人工智能技术正在快速发展，深度学习模型取得了显著成果。"
        "从图像识别到自然语言处理，AI应用越来越广泛地改变着我们的世界。",
        instruction="总结全文核心观点",
    )

    assert r["status"] == "success"
    assert _artifact(r)["status"] == "draft_ready"
    assert r["meta"]["preview_truncated"] is True
    assert "核心观点" in r["chat_preview"]
    assert len(r["chat_preview"]) <= 1600
    assert "完整草稿已保存" in r["chat_preview"]


@patch("agents.capture_agent.CaptureAgent._call_llm")
def test_bridge_capture_incomplete_draft_does_not_offer_commit(mock_llm):
    from agents.capture_agent import LLMResult
    from bridge.capture_bridge import capture_content

    mock_llm.return_value = LLMResult(text="半截草稿，而是", incomplete=True)

    r = capture_content(
        "知识管理的核心是将碎片信息转化为可复用的认知资产。"
        "个人知识管理有三个层次：捕获、整理、内化。这需要长期坚持和积累。"
        "补充：人工智能技术正在快速发展，深度学习模型取得了显著成果。"
        "从图像识别到自然语言处理，AI应用越来越广泛地改变着我们的世界。",
        instruction="总结全文核心观点",
    )

    assert r["status"] == "success"
    assert _artifact(r)["status"] == "draft_incomplete"
    assert "暂不能存库" in r["chat_preview"]
    assert all(action["intent"] != "commit_capture_draft" for action in r["next_actions"])


@patch("agents.capture_agent.CaptureAgent._call_llm")
@patch("agents.capture_agent.Summarizer.summarize")
def test_bridge_revise_no_direct_reply_marker(mock_summarize, mock_llm):
    from bridge.capture_bridge import capture_content, revise_capture_draft

    mock_llm.return_value = "核心观点：经济学顶刊转向复杂社会系统。"
    mock_summarize.return_value = {
        "title": "经济学顶刊转向",
        "summary": "经济学顶刊转向复杂社会系统研究。",
        "tags": ["economics"],
    }
    draft = capture_content(
        "经济学顶刊正在研究信任、网络、国家能力和长期动态效应。"
        "这些主题显示经济学正在从市场均衡走向复杂社会系统研究。"
        "补充文本确保内容长度足够通过质量检测，并覆盖政策、制度和行为机制。"
    )

    revised = revise_capture_draft(_artifact(draft)["id"], "提炼核心要点")

    assert revised["status"] == "success"
    assert "__HERMES_DIRECT_REPLY__" not in revised["chat_preview"]
    assert "完整草稿已保存" in revised["chat_preview"]
    assert "标题：" in revised["chat_preview"]
    assert "重点回应：" in revised["chat_preview"]
    assert "总结：" in revised["chat_preview"]
    assert "草稿预览" not in revised["chat_preview"]
    assert "核心观点" in revised["chat_preview"]


@patch("agents.capture_agent.CaptureAgent._call_llm")
@patch("agents.capture_agent.Summarizer.summarize")
def test_bridge_revise_truncates_long_message_but_keeps_full_draft(mock_summarize, mock_llm):
    from bridge.capture_bridge import capture_content, revise_capture_draft

    long_draft = "核心观点：" + ("长内容" * 1000)
    mock_llm.return_value = long_draft
    mock_summarize.return_value = {
        "title": "长草稿",
        "summary": "长草稿摘要",
        "tags": ["draft"],
    }
    draft = capture_content(
        "这是一段足够长的原始内容，用来生成待确认草稿。"
        "补充文本确保内容长度足够通过质量检测，并覆盖背景、事实和分析。"
        "它还会继续描述资料来源、观点结构、证据链条、反方意见和后续行动建议，"
        "以便测试草稿工作流能够通过最小内容质量门槛并进入修订阶段。"
    )

    revised = revise_capture_draft(_artifact(draft)["id"], "扩写")

    assert revised["status"] == "success"
    assert revised["meta"]["preview_truncated"] is True
    assert "核心观点" in revised["chat_preview"]
    assert len(revised["chat_preview"]) <= 1600
    assert "完整草稿已保存" in revised["chat_preview"]


def test_bridge_show_capture_draft_defaults_to_latest_open(monkeypatch):
    from bridge.capture_bridge import show_capture_draft

    class FakeDraftRepo:
        def get_latest_open(self):
            return {
                "id": "draft1",
                "title": "长草稿",
                "draft_text": "第一段内容。" * 300,
                "raw_text": "",
                "status": "draft_ready",
            }

    class FakeAgent:
        draft_repo = FakeDraftRepo()

    monkeypatch.setattr("bridge.capture_bridge._init", lambda: (None, FakeAgent()))

    result = show_capture_draft(page=1)

    assert result["status"] == "success"
    assert result["intent"] == "show_capture_draft"
    assert "草稿《长草稿》" in result["chat_preview"]
    assert "第 1/" in result["chat_preview"]
    assert result["meta"]["has_next"] is True


def test_bridge_show_capture_draft_page_out_of_range(monkeypatch):
    from bridge.capture_bridge import show_capture_draft

    class FakeDraftRepo:
        def get_by_id(self, draft_id):
            return {
                "id": draft_id,
                "title": "短草稿",
                "draft_text": "短内容",
                "raw_text": "",
                "status": "draft_ready",
            }

    class FakeAgent:
        draft_repo = FakeDraftRepo()

    monkeypatch.setattr("bridge.capture_bridge._init", lambda: (None, FakeAgent()))

    result = show_capture_draft("draft1", page=3)

    assert result["status"] == "error"
    assert "只有 1 页" in result["chat_preview"]


def test_bridge_show_capture_draft_keeps_footer_with_long_title(monkeypatch):
    from bridge.capture_bridge import show_capture_draft

    class FakeDraftRepo:
        def get_latest_open(self):
            return {
                "id": "draft1",
                "title": "很长的标题" * 40,
                "draft_text": "正文内容" * 220,
                "raw_text": "",
                "status": "draft_ready",
            }

    class FakeAgent:
        draft_repo = FakeDraftRepo()

    monkeypatch.setattr("bridge.capture_bridge._init", lambda: (None, FakeAgent()))

    result = show_capture_draft(page=1)

    assert result["status"] == "success"
    assert len(result["chat_preview"]) <= 1500
    assert "回复修改意见继续改" in result["chat_preview"]
    assert not result["chat_preview"].endswith("…")


@patch("bridge.rag_bridge._indexed_search")
@patch("bridge.rag_bridge.vector_search")
@patch("agents.rag_agent.RagAgent.query")
@patch("agents.embedder.Embedder.embed")
def test_bridge_rag(mock_embed, mock_query, mock_vector_search, mock_indexed_search):
    from bridge.rag_bridge import search_knowledge_base
    mock_embed.return_value = b"\x00" * 4096
    mock_indexed_search.return_value = []
    mock_vector_search.return_value = [{"id": "1", "file_path": "", "raw_path": ""}]
    mock_query.return_value = {
        "answer": "四个模块", "sources": [{"title": "T", "summary": "S", "feishu_doc_url": ""}],
    }
    r = search_knowledge_base("Agent 架构")
    assert r["status"] == "success"
    assert "四个模块" in r["chat_preview"]
    assert r["source_map"][0]["id"] == "1"
    assert r["source_map"][0]["type"] == "capture_note"


def test_bridge_theme_scan_runs(real_db):
    from bridge.theme_bridge import run_theme_scan
    result = run_theme_scan()
    assert result["status"] == "success"
    assert result["agent"] == "theme"
    assert len(result["chat_preview"]) > 0


def test_bridge_review_imports_and_runs():
    from bridge.review_bridge import run_daily_review
    result = run_daily_review()
    assert result["status"] == "success"
    assert result["agent"] == "review"
    assert isinstance(result["chat_preview"], str)


# ═══════ Layer 3: Real API (manual trigger only) ═══════

def test_real_capture_wechat_url():
    if not os.environ.get("RUN_REAL_TESTS"):
        pytest.skip("RUN_REAL_TESTS=1 to run")
    from bridge.capture_bridge import capture_content
    r = capture_content("https://mp.weixin.qq.com/s/Esk0AFhP-apyWY8eWruv-Q")
    assert r["status"] == "success" and len(r["meta"]["title"]) > 0 and len(r["meta"]["summary"]) > 0


def test_real_rag_query():
    if not os.environ.get("RUN_REAL_TESTS"):
        pytest.skip("RUN_REAL_TESTS=1 to run")
    from bridge.rag_bridge import search_knowledge_base
    r = search_knowledge_base("Agent 架构")
    assert len(r["chat_preview"]) > 0

# Patch: skip theme_scan tests that need real DB
# (These work locally but fail in test env without migration)

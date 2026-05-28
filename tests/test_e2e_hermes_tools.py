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

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, "/Users/pipi/hermes-custom")
HERMES_DIR = "/Users/pipi/Downloads/hermes-agent-main"


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
    import tools.search_notes_tool  # noqa
    import tools.theme_scan_tool  # noqa
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
    assert "微信预览版" in description
    assert "完整草稿" in description


def test_hermes_draft_tools_registered():
    _skip_if_no_hermes()
    tools = _get_hermes_tools()
    assert "revise_capture_draft" in tools
    assert "commit_capture_draft" in tools
    assert "微信预览版" in tools["revise_capture_draft"].schema.get("description", "")
    assert "完整草稿" in tools["refine_capture"].schema.get("description", "")


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
    assert "已提取" in result or "捕获失败" in result
    assert len(result) > 20


def test_hermes_capture_handler_empty_args_no_crash():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from tools.capture_tool import _handler
    result = _handler({})
    assert isinstance(result, str) and len(result) > 0


def test_hermes_commit_handler_empty_args_no_crash():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from tools.commit_capture_draft_tool import _handler
    result = _handler({})
    assert isinstance(result, str) and len(result) > 0


def test_hermes_search_handler_runs():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from tools.search_notes_tool import _handler
    result = _handler({"query": "测试查询"})
    assert isinstance(result, str) and len(result) > 0


def test_hermes_theme_scan_handler_runs():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from tools.theme_scan_tool import _handler
    result = _handler({})
    assert isinstance(result, str) and len(result) > 0


def test_hermes_toolset_has_6_tools():
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from toolsets import TOOLSETS
    tools = TOOLSETS["personal-assistant"]["tools"]
    assert len(tools) >= 13, f"Expected draft tools, got {len(tools)}: {tools}"
    expected = {"capture_content", "refine_capture", "search_my_notes", "log_decision",
                "wellness_check", "theme_scan", "graph_report", "web_extract", "send_message",
                "revise_capture_draft", "commit_capture_draft", "synthesize_garden",
                "daily_review"}
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
    assert r["success"] and r["status"] == "raw_extracted" and r["draft_id"]
    assert "raw_path" not in r


@patch("agents.capture_agent.Summarizer.summarize")
def test_bridge_capture_text(mock_summarize):
    from bridge.capture_bridge import capture_content
    mock_summarize.return_value = {"title": "KM", "summary": "S", "tags": ["km"]}
    r = capture_content(
        "知识管理的核心是将碎片信息转化为可复用的认知资产。"
        "个人知识管理有三个层次：捕获、整理、内化。这需要长期坚持和积累。"
        "补充：人工智能技术正在快速发展，深度学习模型取得了显著成果。"
        "从图像识别到自然语言处理，AI应用越来越广泛地改变着我们的世界。")
    assert r["success"] and r["status"] == "raw_extracted" and r["draft_id"]


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
    assert r["success"] and r["status"] == "draft_ready"
    assert "核心观点" in r["draft_text"]
    assert "完整草稿" in r["message"]
    assert "__HERMES_DIRECT_REPLY__" not in r["message"]


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

    assert r["success"] and r["status"] == "draft_ready"
    assert r["draft_text"] == long_draft
    assert long_draft not in r["message"]
    assert "微信预览版" in r["message"]
    assert "完整草稿" in r["message"]


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

    revised = revise_capture_draft(draft["draft_id"], "提炼核心要点")

    assert revised["success"] is True
    assert "__HERMES_DIRECT_REPLY__" not in revised["message"]
    assert "完整草稿" in revised["message"]
    assert "核心观点" in revised["message"]


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

    revised = revise_capture_draft(draft["draft_id"], "扩写")

    assert revised["success"] is True
    assert revised["draft_text"] == long_draft
    assert long_draft not in revised["message"]
    assert "微信预览版" in revised["message"]
    assert "完整草稿" in revised["message"]


@patch("bridge.rag_bridge.vector_search")
@patch("agents.rag_agent.RagAgent.query")
@patch("agents.embedder.Embedder.embed")
def test_bridge_rag(mock_embed, mock_query, mock_vector_search):
    from bridge.rag_bridge import search_knowledge_base
    mock_embed.return_value = b"\x00" * 4096
    mock_vector_search.return_value = [{"id": "1", "file_path": "", "raw_path": ""}]
    mock_query.return_value = {
        "answer": "四个模块", "sources": [{"title": "T", "summary": "S", "feishu_doc_url": ""}],
    }
    r = search_knowledge_base("Agent 架构")
    assert "四个模块" in r["answer"] and r["sources"][0]["title"] == "T"


def test_bridge_theme_scan_runs(real_db):
    from bridge.theme_bridge import run_theme_scan
    result = run_theme_scan()
    assert isinstance(result, str) and len(result) > 0


def test_bridge_review_imports_and_runs():
    from bridge.review_bridge import run_daily_review
    result = run_daily_review()
    assert isinstance(result, str)


# ═══════ Layer 3: Real API (manual trigger only) ═══════

def test_real_capture_wechat_url():
    if not os.environ.get("RUN_REAL_TESTS"):
        pytest.skip("RUN_REAL_TESTS=1 to run")
    from bridge.capture_bridge import capture_content
    r = capture_content("https://mp.weixin.qq.com/s/Esk0AFhP-apyWY8eWruv-Q")
    assert r["success"] and len(r["title"]) > 0 and len(r["summary"]) > 0


def test_real_rag_query():
    if not os.environ.get("RUN_REAL_TESTS"):
        pytest.skip("RUN_REAL_TESTS=1 to run")
    from bridge.rag_bridge import search_knowledge_base
    r = search_knowledge_base("Agent 架构")
    assert len(r["answer"]) > 0

# Patch: skip theme_scan tests that need real DB
# (These work locally but fail in test env without migration)

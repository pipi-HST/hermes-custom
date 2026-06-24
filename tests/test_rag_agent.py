"""Tests for RAG agent."""
from __future__ import annotations

from unittest.mock import patch
from agents.rag_agent import RagAgent, format_answer, build_rag_prompt


def test_format_answer_with_sources():
    answer = "RAG 有三种架构：朴素、高级和模块化。"
    sources = [
        {"title": "RAG 技术综述", "story": "介绍了三种 RAG 架构",
         "file_path": "captures/rag.md", "summary": "旧字段兼容"},
    ]
    result = format_answer(answer, sources)
    assert "RAG 有三种架构" in result
    assert "RAG 技术综述" in result
    assert "[1]" in result


def test_format_answer_only_lists_cited_sources():
    answer = "这个回答只使用第一篇材料。[1]"
    sources = [
        {"title": "真正来源", "story": "被引用的材料", "file_path": "captures/real.md"},
        {"title": "候选材料", "story": "只是检索候选", "file_path": "captures/candidate.md"},
    ]

    result = format_answer(answer, sources)

    assert "[1] 真正来源" in result
    assert "候选材料" not in result
    assert "[2]" not in result


def test_format_answer_lists_all_sources_when_answer_has_no_citations():
    answer = "AI Agent 工程化需要把模型能力放进可控流程。"
    sources = [
        {"title": f"来源{i}", "story": "", "section_title": f"章节{i}"}
        for i in range(1, 7)
    ]

    result = format_answer(answer, sources)

    for i in range(1, 7):
        assert f"[{i}] 来源{i} / 章节{i}" in result


def test_format_answer_keeps_source_list_under_preview_budget():
    answer = "AI Agent 工程化需要把模型能力放进可控流程。" * 80
    sources = [
        {
            "title": f"很长的来源标题{i}：这里还有一串为了模拟真实中文标题的描述",
            "story": "",
            "section_title": f"章节{i}",
        }
        for i in range(1, 7)
    ]

    result = format_answer(answer, sources)

    assert len(result) <= 1400
    assert "📎 来源" in result
    assert "[6] 很长的来源标题6" in result


def test_format_answer_uses_raw_answer_citations_when_preview_is_truncated():
    answer = (
        "第一段只引用第一篇材料。[1]" * 80
        + "\n\n后面虽然被预览截掉，但完整答案实际也用了第六篇材料。[6]"
    )
    sources = [
        {"title": f"来源{i}", "story": "", "section_title": ""}
        for i in range(1, 7)
    ]

    result = format_answer(answer, sources)

    assert "回复“继续展开”" in result
    assert "[1] 来源1" in result
    assert "[6] 来源6" in result


def test_format_answer_removes_dangling_partial_citation():
    answer = "AI Agent 工程化依赖 Harness 约束层。[1][2]["
    sources = [
        {"title": "来源一", "story": "", "file_path": "captures/one.md"},
        {"title": "来源二", "story": "", "file_path": "captures/two.md"},
    ]

    result = format_answer(answer, sources)

    body = result.split("\n\n📎 来源", 1)[0]
    assert body.endswith("[1][2]")
    assert not body.endswith("[")


def test_format_answer_does_not_truncate_inside_citation_marker():
    answer = "这是一段很长的回答。" * 40 + "[1][2]"
    sources = [
        {"title": "来源一", "story": "", "file_path": "captures/one.md"},
        {"title": "来源二", "story": "", "file_path": "captures/two.md"},
    ]

    result = format_answer(answer, sources)

    body = result.split("\n\n📎 来源", 1)[0]
    assert not body.endswith("[")
    assert not body.endswith("[1")


def test_format_answer_truncates_long_body_at_sentence_boundary_with_continue_hint():
    answer = (
        "第一段说明工程化要从简单工作流开始。" * 80
        + "\n\n第二段会继续解释调度器工作者这种复杂工作流模式，以及后续治理要求。[1]"
    )
    sources = [
        {"title": "来源一", "story": "摘要", "file_path": "captures/one.md"},
    ]

    result = format_answer(answer, sources)
    body = result.split("\n\n📎 来源", 1)[0]

    assert "复杂工..." not in body
    assert "回复“继续展开”" in body
    assert body.endswith("可查看更多。）")
    assert "📎 来源" in result
    assert "[1] 来源一" in result


def test_format_answer_drops_orphan_heading_when_truncated():
    answer = "第一段解释。" * 180 + "\n\n### 3."
    sources = [
        {"title": "来源一", "story": "", "file_path": "captures/one.md"},
    ]

    result = format_answer(answer, sources)
    body = result.split("\n\n📎 来源", 1)[0]

    assert not body.rstrip().endswith("### 3.")
    assert "回复“继续展开”" in body


def test_format_answer_without_url():
    sources = [{"title": "测试", "story": "概要", "file_path": ""}]
    result = format_answer("答案", sources)
    assert "测试" in result
    assert "概要" in result


def test_format_answer_omits_long_source_summary_instead_of_mid_phrase_ellipsis():
    sources = [{
        "title": "长来源",
        "story": "AI编程助手个人普及但组织收益滞后，工程底座成熟度决定AI放大收益还是放大风险。",
        "file_path": "captures/source.md",
    }]

    result = format_answer("答案", sources)

    assert "[1] 长来源" in result
    assert "放..." not in result
    assert " — " not in result


def test_build_prompt_includes_context():
    captures = [
        {"title": "RAG 入门", "story": "RAG 简介",
         "body": "RAG即检索增强生成。"},
    ]
    prompt = build_rag_prompt("什么是 RAG？", captures[:1])
    assert "什么是 RAG？" in prompt
    assert "RAG即检索增强生成" in prompt
    assert "RAG 入门" in prompt


def test_build_prompt_falls_back_to_summary():
    captures = [{"title": "测试", "story": "只用摘要", "body": ""}]
    prompt = build_rag_prompt("query", captures)
    assert "只用摘要" in prompt


def test_build_prompt_marks_long_source_excerpt_at_natural_boundary():
    sentence = "自然边界句子说明知识库证据。"
    long_body = sentence * 1200 + "不应该进入提示"
    captures = [{"title": "长材料", "story": "", "body": long_body}]

    prompt = build_rag_prompt("query", captures)

    assert "该来源过长，已省略后续内容" in prompt
    assert "不应该进入提示" not in prompt
    body = prompt.split("[1] 《长材料》\n", 1)[1].split("\n\n请用", 1)[0]
    assert body.rstrip().endswith("。）")
    excerpt = body.split("\n\n（该来源过长", 1)[0]
    assert excerpt.endswith("。")


def test_build_prompt_includes_garden_and_theme_as_orientation_only():
    captures = [{"title": "材料", "story": "", "body": "正文证据"}]
    context_pack = {
        "garden_cards": [
            {"title": "AI 工程化", "one_liner": "复杂度要受控。"}
        ],
        "theme_pages": [
            {"title": "Agent 方法论", "path": "themes/Agent 方法论.md"}
        ],
    }

    prompt = build_rag_prompt("怎么理解？", captures, context_pack=context_pack)

    assert "高层线索" in prompt
    assert "AI 工程化" in prompt
    assert "Agent 方法论" in prompt
    assert "不要把高层线索当作编号来源" in prompt
    assert "[1] 《材料》" in prompt


def test_build_prompt_includes_graph_theme_groups_as_orientation_only():
    captures = [{"title": "材料", "story": "", "body": "正文证据"}]
    context_pack = {
        "garden_cards": [
            {"title": "Agent 工程化", "one_liner": "用流程和护栏管理模型。"}
        ],
        "theme_groups": [
            {"title": "复杂度控制", "capture_ids": ["cap1", "cap2"]}
        ],
        "graph_trace": {"seed_count": 1, "garden_count": 1, "theme_count": 1, "final_count": 1},
    }

    prompt = build_rag_prompt("怎么理解？", captures, context_pack=context_pack)

    assert "检索路径" in prompt
    assert "复杂度控制" in prompt
    assert "Garden/Theme 是理解线索" in prompt
    assert "[1] 《材料》" in prompt


@patch("agents.rag_agent.RagAgent._call_llm")
def test_rag_query_returns_answer_and_sources(mock_call):
    mock_call.return_value = "RAG 是一种技术。"
    agent = RagAgent(llm_api_key="test", llm_model="deepseek-v4-pro",
                      llm_base_url="https://api.deepseek.com")
    captures = [{
        "id": "1", "title": "测试文章", "story": "test",
        "body": "RAG 即检索增强生成。",
        "file_path": "captures/test.md",
    }]
    result = agent.query("什么是 RAG？", captures)
    assert result["answer"] == "RAG 是一种技术。"
    assert len(result["sources"]) == 1
    assert result["sources"][0]["title"] == "测试文章"


@patch("agents.rag_agent.RagAgent._call_llm")
def test_rag_query_empty_captures(mock_call):
    agent = RagAgent(llm_api_key="test")
    result = agent.query("问题", [])
    assert "没有找到" in result["answer"]
    assert result["sources"] == []


def test_call_llm_continues_once_when_generation_hits_length(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, content: str, finish_reason: str):
            self.content = content
            self.finish_reason = finish_reason

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": self.content},
                        "finish_reason": self.finish_reason,
                    }
                ]
            }

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, headers, json):
            requests.append(json)
            if len(requests) == 1:
                return FakeResponse("第一段没写完", "length")
            return FakeResponse("，第二段补完。", "stop")

    monkeypatch.setattr("agents.rag_agent.httpx.Client", FakeClient)

    agent = RagAgent(llm_api_key="test")
    answer = agent._call_llm("请回答")

    assert answer == "第一段没写完，第二段补完。"
    assert len(requests) == 2
    assert "继续完成上一条回答" in requests[1]["messages"][-1]["content"]


def test_call_llm_uses_configured_model_for_short_prompt(monkeypatch):
    requests = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": "回答"},
                        "finish_reason": "stop",
                    }
                ]
            }

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, headers, json):
            requests.append(json)
            return FakeResponse()

    monkeypatch.setattr("agents.rag_agent.httpx.Client", FakeClient)

    agent = RagAgent(llm_api_key="test", llm_model="deepseek-v4-pro")
    answer = agent._call_llm("短问题")

    assert answer == "回答"
    assert requests[0]["model"] == "deepseek-v4-pro"

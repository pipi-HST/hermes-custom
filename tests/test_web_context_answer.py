import asyncio

import pytest

from bridge import web_context_answer


def test_web_context_answer_uses_summary_materials_without_extract(monkeypatch):
    llm_calls = []

    def fake_llm_json(prompt, *, timeout_seconds):
        llm_calls.append(prompt)
        if "生成网页搜索词" in prompt:
            return {"queries": ["DeepSeek 招聘 Harness"]}
        if "选择网页材料" in prompt:
            return {"selected_refs": ["W1", "W2"], "open_refs": [], "answer_shape": "直接列岗位"}
        return {}

    def fake_search(query, limit):
        assert query == "DeepSeek 招聘 Harness"
        assert limit == web_context_answer.WEB_CONTEXT_RESULTS_PER_QUERY
        return {
            "results": [
                {
                    "title": "DeepSeek Harness团队正大力招人",
                    "url": "https://example.com/harness",
                    "description": "Harness团队开放研究员、研发工程师、产品经理岗位。",
                },
                {
                    "title": "DeepSeek招聘",
                    "url": "https://jobs.example.com/deepseek",
                    "description": "DeepSeek还招聘全栈开发、算法、运维和数据中心岗位。",
                },
            ]
        }

    def fail_extract(_urls):
        raise AssertionError("summary-only answer should not extract pages")

    def fake_synthesize(payload):
        refs = [source["ref"] for source in payload["web_sources"]]
        assert refs == ["W1", "W2"]
        assert all(source["extract_status"] == "summary" for source in payload["web_sources"])
        return "DeepSeek 主要在招 Harness 研究员、研发工程师、产品经理，也有全栈、算法和运维岗位 [W1][W2]，不存在的引用会被移除 [W9]。"

    monkeypatch.setattr(web_context_answer, "_llm_json", fake_llm_json)

    result = web_context_answer.web_context_answer(
        query="DeepSeek现在在招哪些人？",
        mode="web_only",
        search_adapter=fake_search,
        extract_adapter=fail_extract,
        synthesize_adapter=fake_synthesize,
    )

    assert result["status"] == "success"
    assert result["intent"] == "web_context_answer"
    assert result["meta"]["path"] == "web_context"
    assert result["meta"]["legacy_research"] is False
    assert result["meta"]["web_search_attempted"] is True
    assert result["meta"]["provider_status"]["summary_only_pages"] == 2
    assert result["meta"]["provider_status"]["extracted_pages"] == 0
    assert result["meta"]["search_quality"] == {"passed": True, "reason": "materials_available"}
    assert "[W9]" not in result["chat_preview"]
    assert [source["ref"] for source in result["meta"]["web_sources"]] == ["W1", "W2"]
    assert [item["ref"] for item in result["source_map"]] == ["W1", "W2"]
    assert len(llm_calls) == 2


def test_web_context_answer_extracts_only_llm_requested_pages(monkeypatch):
    extracted_urls = []

    def fake_llm_json(prompt, *, timeout_seconds):
        if "生成网页搜索词" in prompt:
            return {"queries": ["DeepSeek 华为 芯片 训练"]}
        if "选择网页材料" in prompt:
            return {"selected_refs": ["W1", "W2"], "open_refs": ["W2"], "answer_shape": "区分训练和适配"}
        return {}

    def fake_search(_query, _limit):
        return {
            "results": [
                {
                    "title": "DeepSeek 使用 NVIDIA GPU 训练",
                    "url": "https://example.com/nvidia",
                    "description": "公开技术报告提到训练使用 NVIDIA H800。",
                },
                {
                    "title": "DeepSeek 适配华为昇腾芯片",
                    "url": "https://example.com/ascend",
                    "description": "报道提到模型适配昇腾芯片。",
                },
            ]
        }

    def fake_extract(urls):
        extracted_urls.extend(urls)
        return {
            "results": [
                {
                    "url": "https://example.com/ascend",
                    "content": "全文材料：报道称这是模型发布后的昇腾芯片适配，不等同于基础模型预训练。",
                }
            ]
        }

    def fake_synthesize(payload):
        statuses = {source["url"]: source["extract_status"] for source in payload["web_sources"]}
        assert statuses["https://example.com/nvidia"] == "summary"
        assert statuses["https://example.com/ascend"] == "full"
        assert "全文材料" in payload["web_sources"][1]["excerpt"]
        return "不能简单说 DeepSeek 基于华为芯片训练；公开信息更像是训练硬件和后期适配要分开看 [W1][W2]。"

    monkeypatch.setattr(web_context_answer, "_llm_json", fake_llm_json)

    result = web_context_answer.web_context_answer(
        query="DeepSeek 是基于华为芯片训练的吗",
        mode="web_only",
        search_adapter=fake_search,
        extract_adapter=fake_extract,
        synthesize_adapter=fake_synthesize,
    )

    assert extracted_urls == ["https://example.com/ascend"]
    assert result["meta"]["provider_status"]["extracted_pages"] == 1
    assert result["meta"]["provider_status"]["summary_only_pages"] == 1


def test_async_web_context_answer_emits_candidate_trace(monkeypatch):
    events = []

    def fake_llm_json(prompt, *, timeout_seconds):
        if "生成网页搜索词" in prompt:
            return {"queries": ["Harness engineer"]}
        if "选择网页材料" in prompt:
            return {"selected_refs": ["W1"], "open_refs": []}
        return {}

    def fake_search(_query, _limit):
        return {
            "results": [
                {
                    "title": "什么是 Harness Engineering",
                    "url": "https://example.com/harness-engineering",
                    "description": "Harness Engineer 负责给 Agent 构建可控运行框架。",
                }
            ]
        }

    def fake_synthesize(_payload):
        return "Harness Engineer 更接近为 Agent 构建可控运行框架的工程角色 [W1]。"

    monkeypatch.setattr(web_context_answer, "_llm_json", fake_llm_json)

    result = asyncio.run(
        web_context_answer.async_web_context_answer(
            query="什么是 Harness engineer",
            mode="web_only",
            search_adapter=fake_search,
            extract_adapter=lambda _urls: {"results": []},
            synthesize_adapter=fake_synthesize,
            event_callback=lambda event: events.append(event),
        )
    )

    ranked = [event for event in events if event["type"] == "web_ranked"]
    assert result["meta"]["research_trace"]["candidate_sources"][0]["ref"] == "W1"
    assert ranked
    assert ranked[0]["web_sources"][0]["title"] == "什么是 Harness Engineering"


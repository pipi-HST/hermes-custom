import asyncio
import time

import pytest

from bridge import web_context_answer


def patch_research_plan(
    monkeypatch,
    *,
    queries=None,
    needs_web=None,
    intent_label="planner",
    answer_depth="normal",
    extra=None,
):
    def fake_plan(query, *, mode="local_web", article_context=None, selection_context=None):
        del article_context, selection_context
        mode_allows_web = mode in {"local_web", "web_only"}
        resolved_needs_web = mode_allows_web if needs_web is None else bool(needs_web)
        if mode in {"current_article_only", "local_only"}:
            resolved_needs_web = False
        planned_queries = list(queries) if queries is not None else ([query] if resolved_needs_web else [])
        plan = {
            "intent_label": intent_label,
            "needs_web": resolved_needs_web,
            "mode": mode,
            "subject": {},
            "selection_role": "none",
            "risk_level": "low",
            "freshness_sensitive": False,
            "answer_depth": answer_depth,
            "queries": planned_queries,
            "answer_claims_needed": [],
            "claim_requirements": {},
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {},
            "answer_contract": {},
            "privacy": {"may_use_selection_text": False, "allowed_context_terms": []},
            "plan_status": "planned",
        }
        plan.update(extra or {})
        return plan

    monkeypatch.setattr(web_context_answer, "plan_research", fake_plan)


@pytest.fixture(autouse=True)
def default_research_plan(monkeypatch):
    patch_research_plan(monkeypatch)


def test_web_context_search_query_timeout_env(monkeypatch):
    monkeypatch.delenv("HERMES_WEB_CONTEXT_SEARCH_QUERY_TIMEOUT_SECONDS", raising=False)
    assert web_context_answer._web_context_search_query_timeout_seconds() == 18.0

    monkeypatch.setenv("HERMES_WEB_CONTEXT_SEARCH_QUERY_TIMEOUT_SECONDS", "2")
    assert web_context_answer._web_context_search_query_timeout_seconds() == 3.0

    monkeypatch.setenv("HERMES_WEB_CONTEXT_SEARCH_QUERY_TIMEOUT_SECONDS", "99")
    assert web_context_answer._web_context_search_query_timeout_seconds() == 45.0

    monkeypatch.setenv("HERMES_WEB_CONTEXT_SEARCH_QUERY_TIMEOUT_SECONDS", "bad")
    assert web_context_answer._web_context_search_query_timeout_seconds() == 18.0


def test_web_context_answer_uses_summary_materials_without_extract(monkeypatch):
    llm_calls = []
    patch_research_plan(monkeypatch, queries=["DeepSeek 招聘 Harness"])

    def fake_llm_json(prompt, *, timeout_seconds):
        llm_calls.append(prompt)
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
        return "从搜索结果看，DeepSeek 主要在招 Harness 研究员、研发工程师、产品经理，也有全栈、算法和运维岗位 [W1][W2]，不存在的引用会被移除 [W9]。"

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
    assert result["meta"]["provider_status"]["stage_timings"].keys() == {
        "planning_ms",
        "local_search_ms",
        "search_ms",
        "extract_ms",
        "synthesis_ms",
    }
    assert "critic_ms" not in result["meta"]["provider_status"]["stage_timings"]
    assert result["meta"]["search_quality"] == {"passed": True, "reason": "materials_available"}
    assert "[W9]" not in result["chat_preview"]
    assert not result["chat_preview"].startswith("从搜索结果看")
    assert [source["ref"] for source in result["meta"]["web_sources"]] == ["W1", "W2"]
    assert [source["ref"] for source in result["meta"]["retrieved_web_sources"]] == ["W1", "W2"]
    assert [item["ref"] for item in result["source_map"]] == ["W1", "W2"]
    assert len(llm_calls) == 1


def test_web_context_answer_extracts_only_llm_requested_pages(monkeypatch):
    extracted_urls = []
    patch_research_plan(monkeypatch, queries=["DeepSeek 华为 芯片 训练"])

    def fake_llm_json(prompt, *, timeout_seconds):
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


def test_web_context_answer_keeps_web_sources_when_answer_only_cites_knowledge(monkeypatch):
    patch_research_plan(monkeypatch, queries=["AI PM traditional PM difference"])

    def fake_llm_json(prompt, *, timeout_seconds):
        if "选择网页材料" in prompt:
            return {"selected_refs": ["W1", "W2"], "open_refs": []}
        return {}

    def fake_search(_query, _limit):
        return {
            "results": [
                {
                    "title": "AI product manager role",
                    "url": "https://example.com/ai-pm",
                    "description": "AI PM works with models, data and product delivery.",
                },
                {
                    "title": "Traditional product manager role",
                    "url": "https://example.com/pm",
                    "description": "Traditional PM focuses on users, roadmap and business goals.",
                },
            ]
        }

    monkeypatch.setattr(web_context_answer, "_llm_json", fake_llm_json)

    result = web_context_answer.web_context_answer(
        query="AI产品经理和传统产品经理有哪些区别",
        mode="local_web",
        local_search_adapter=lambda _query, _warnings: (
            "AI PM 多了数据、模型评估和不确定性管理。",
            [{"ref": "K1", "type": "knowledge", "id": "cap-1", "title": "AI产品经理笔记"}],
        ),
        search_adapter=fake_search,
        extract_adapter=lambda _urls: {"results": []},
        synthesize_adapter=lambda _payload: "AI PM 和传统 PM 的核心差异在于生产资料和不确定性管理 [K1]。",
    )

    assert result["chat_preview"].endswith("[K1]。")
    assert [source["ref"] for source in result["meta"]["knowledge_sources"]] == ["K1"]
    assert [source["ref"] for source in result["meta"]["web_sources"]] == ["W1", "W2"]
    assert [item["ref"] for item in result["source_map"]] == ["K1", "W1", "W2"]
    assert result["meta"]["used_web"] is True


def test_web_context_answer_times_out_stuck_search_query(monkeypatch):
    events = []

    def fake_search(query, _limit):
        if query == "slow search":
            time.sleep(0.35)
            return {"results": [{"title": "too late", "url": "https://example.com/late", "description": "late"}]}
        return {
            "results": [
                {
                    "title": "fast result",
                    "url": "https://example.com/fast",
                    "description": "fast material",
                }
            ]
        }

    monkeypatch.setattr(web_context_answer, "_web_context_search_query_timeout_seconds", lambda: 0.05)

    started = time.monotonic()
    warnings = []
    candidates, query_statuses = web_context_answer._search_candidates(
        ["slow search", "fast search"],
        search_adapter=fake_search,
        warnings=warnings,
        progress_callback=lambda event, **kwargs: events.append({"type": event, **kwargs}),
    )

    elapsed = time.monotonic() - started
    assert elapsed < 0.25
    assert [source["url"] for source in candidates] == ["https://example.com/fast"]
    assert [status["status"] for status in query_statuses] == ["timeout", "success"]
    assert query_statuses[0]["query"] == "slow search"
    assert query_statuses[0]["result_count"] == 0
    assert query_statuses[0]["error"]
    assert query_statuses[1]["result_count"] == 1
    assert any(event["type"] == "web_query_error" and event["query"] == "slow search" for event in events)
    assert warnings == []


def test_web_context_answer_records_query_statuses_and_readable_warnings(monkeypatch):
    patch_research_plan(monkeypatch, queries=["slow search", "fast search"])

    def fake_llm_json(prompt, *, timeout_seconds):
        if "选择网页材料" in prompt:
            return {"selected_refs": ["W1"], "open_refs": []}
        return {}

    def fake_search(query, _limit):
        if query == "slow search":
            time.sleep(0.35)
            return {"results": [{"title": "too late", "url": "https://example.com/late", "description": "late"}]}
        return {
            "results": [
                {
                    "title": "fast result",
                    "url": "https://example.com/fast",
                    "description": "fast material",
                }
            ]
        }

    monkeypatch.setattr(web_context_answer, "_web_context_search_query_timeout_seconds", lambda: 0.05)
    monkeypatch.setattr(web_context_answer, "_llm_json", fake_llm_json)

    result = web_context_answer.web_context_answer(
        query="解释这个术语",
        mode="web_only",
        search_adapter=fake_search,
        extract_adapter=lambda _urls: {"results": []},
        synthesize_adapter=lambda payload: "这是基于可用材料的解释 [W1]。",
    )

    warnings = result["warnings"]
    provider_status = result["meta"]["provider_status"]
    research_trace = result["meta"]["research_trace"]
    query_statuses = provider_status["query_statuses"]

    assert len(query_statuses) == 2
    assert {status["status"] for status in query_statuses} == {"timeout", "success"}
    assert all("duration_ms" in status for status in query_statuses)
    assert query_statuses[0]["error"]
    assert query_statuses[1]["result_count"] == 1
    assert warnings == ["1 条查询超时，已跳过；已用 1 个可用来源继续回答。"]
    assert provider_status["warnings"] == warnings
    assert research_trace["warnings"] == warnings
    assert research_trace["query_statuses"] == query_statuses
    assert provider_status["total_elapsed_ms"] > 0
    assert research_trace["total_elapsed_ms"] == provider_status["total_elapsed_ms"]
    assert set(provider_status["stage_timings"]) == {
        "planning_ms",
        "local_search_ms",
        "search_ms",
        "extract_ms",
        "synthesis_ms",
    }
    assert provider_status["stage_timings"]["search_ms"] > 0


def test_web_context_answer_records_light_path_stage_timings(monkeypatch):
    def slow_plan(query, *, mode="local_web", article_context=None, selection_context=None):
        del article_context, selection_context
        time.sleep(0.01)
        return {
            "intent_label": "planner",
            "needs_web": True,
            "mode": mode,
            "subject": {},
            "selection_role": "none",
            "risk_level": "low",
            "freshness_sensitive": False,
            "answer_depth": "normal",
            "queries": [query],
            "answer_claims_needed": [],
            "claim_requirements": {},
            "evidence_contract": {"minimum_independent_sources": 1},
            "source_preferences": {},
            "answer_contract": {},
            "privacy": {"may_use_selection_text": False, "allowed_context_terms": []},
            "plan_status": "planned",
        }

    def fake_local_search(_query, _warnings):
        time.sleep(0.01)
        return "本地材料", [{"ref": "K1", "type": "knowledge", "id": "k1", "title": "本地材料"}]

    def fake_search(_query, _limit):
        time.sleep(0.01)
        return {"results": [{"title": "网页材料", "url": "https://example.com/web", "description": "网页摘要"}]}

    def fake_llm_json(prompt, *, timeout_seconds):
        if "选择网页材料" in prompt:
            return {"selected_refs": ["W1"], "open_refs": []}
        return {}

    def fake_synthesize(_payload):
        time.sleep(0.01)
        return "这是综合答案 [K1][W1]。"

    monkeypatch.setattr(web_context_answer, "plan_research", slow_plan)
    monkeypatch.setattr(web_context_answer, "_llm_json", fake_llm_json)

    result = web_context_answer.web_context_answer(
        query="解释轻链路",
        mode="local_web",
        search_adapter=fake_search,
        extract_adapter=lambda _urls: {"results": []},
        local_search_adapter=fake_local_search,
        synthesize_adapter=fake_synthesize,
    )

    timings = result["meta"]["provider_status"]["stage_timings"]
    assert timings["planning_ms"] > 0
    assert timings["local_search_ms"] > 0
    assert timings["search_ms"] > 0
    assert timings["synthesis_ms"] > 0


def test_web_context_answer_skips_synthesis_when_web_has_no_material(monkeypatch):
    patch_research_plan(monkeypatch, queries=["slow query"])
    monkeypatch.setattr(web_context_answer, "_web_context_search_query_timeout_seconds", lambda: 0.05)

    def slow_search(_query, _limit):
        time.sleep(0.35)
        return {"results": [{"title": "late", "url": "https://example.com/late", "description": "late"}]}

    result = web_context_answer.web_context_answer(
        query="HTML 承载复杂信息 可视化 原型 vibecoding",
        mode="web_only",
        article_context={
            "title": "本地文章",
            "body": "这篇文章里有足够多的本地文字，但 web_only 下不能把它当作答案材料。",
        },
        selection_context={"selected_text": "复杂信息可视化"},
        search_adapter=slow_search,
        extract_adapter=lambda _urls: {"results": []},
        synthesize_adapter=lambda _payload: (_ for _ in ()).throw(
            AssertionError("empty material should not synthesize")
        ),
    )

    assert result["chat_preview"] == "暂时没有足够材料回答这个问题。"
    assert result["meta"]["web_search_attempted"] is True
    assert result["meta"]["used_web"] is False
    assert result["meta"]["provider_status"]["used_sources"] == 0
    assert result["meta"]["provider_status"]["synthesis_skipped"] is True
    assert result["meta"]["research_trace"]["synthesis_skipped"] is True
    assert result["warnings"] == [
        "1 条查询超时，已跳过；暂无可用联网来源。",
        "没有可用材料，已跳过空材料回答生成。",
    ]


def test_web_context_query_cap_preserves_planner_tiers():
    normal_plan = {
        "needs_web": True,
        "answer_depth": "normal",
        "risk_level": "low",
        "freshness_sensitive": False,
        "queries": ["q1", "q2", "q3", "q4"],
        "evidence_contract": {"minimum_independent_sources": 1},
    }
    detailed_plan = {
        **normal_plan,
        "answer_depth": "detailed",
        "evidence_contract": {"minimum_independent_sources": 2},
    }
    high_risk_plan = {**normal_plan, "risk_level": "high"}

    assert web_context_answer._query_cap("web_only", normal_plan) == 2
    assert web_context_answer._query_cap("web_only", detailed_plan) == web_context_answer.MAX_WEB_QUERIES
    assert web_context_answer._query_cap("web_only", high_risk_plan) == web_context_answer.MAX_WEB_QUERIES


def test_web_context_answer_prompt_includes_article_term_context(monkeypatch):
    prompts = []

    def fake_llm_json(prompt, *, timeout_seconds):
        if "选择网页材料" in prompt:
            return {"selected_refs": [], "open_refs": []}
        return {}

    def fake_call_text_llm(prompt, **_kwargs):
        prompts.append(("answer", prompt))
        return "这里的 skills 指可被 AI/Agent 加载的文本规则或能力说明，用来引导模型思考方式。"

    monkeypatch.setattr(web_context_answer, "_llm_json", fake_llm_json)
    monkeypatch.setattr(web_context_answer, "_call_text_llm", fake_call_text_llm)

    result = web_context_answer.web_context_answer(
        query="这是什么意思",
        mode="current_article_only",
        article_context={
            "title": "基本无害的vibecoding实践技巧（文科生友好版）",
            "body": "工具分化：文字规则（CLAUDE.md、AGENTS.md、记忆、skills）适合引导思考方式，约束力相对弱；hook、cron、脚本适合强制执行确定性流程。",
            "folder_path": "AI/实践",
            "summary": "文章总结了 vibecoding 中如何用规则、记忆、hook 和脚本降低反复犯错。",
            "tags": ["vibecoding", "agent"],
            "file_path": "/Users/pipi/Hermes/knowledge/captures/vibecoding.md",
        },
        selection_context={
            "selected_text": "skills",
            "surrounding_before": "工具分化：文字规则（CLAUDE.md、AGENTS.md、记忆、",
            "surrounding_after": "）适合引导思考方式，约束力相对弱；hook、cron、脚本适合强制执行确定性流程。",
            "heading_path": ["总结", "5. 从错误中获得提升：把常犯的错自动化"],
        },
        search_adapter=lambda *_args: {"results": []},
        extract_adapter=lambda _urls: {"results": []},
    )

    assert result["chat_preview"].startswith("这里的 skills")
    answer_prompt = [prompt for kind, prompt in prompts if kind == "answer"][-1]
    assert "短词、英文术语或指代性问题" in answer_prompt
    assert "不要只给通用词典释义" in answer_prompt
    assert "当前选区：skills" in answer_prompt
    assert "选区前文：工具分化：文字规则（CLAUDE.md、AGENTS.md、记忆、" in answer_prompt
    assert "选区后文：）适合引导思考方式" in answer_prompt
    assert "标题路径：总结 > 5. 从错误中获得提升：把常犯的错自动化" in answer_prompt
    assert "文章标题：基本无害的vibecoding实践技巧（文科生友好版）" in answer_prompt
    assert "文章上下文：工具分化：文字规则" in answer_prompt
    assert "文件夹：AI/实践" in answer_prompt
    assert "标签：vibecoding, agent" in answer_prompt
    assert "文件路径：/Users/pipi/Hermes/knowledge/captures/vibecoding.md" in answer_prompt


def test_current_article_mode_strips_refs_and_uses_no_sources(monkeypatch):
    search_calls = []
    local_calls = []

    result = web_context_answer.web_context_answer(
        query="这是什么意思",
        mode="current_article_only",
        article_context={"title": "文章", "body": "skills 在本文里指 Agent 可加载的能力说明。"},
        selection_context={"selected_text": "skills"},
        search_adapter=lambda *args: search_calls.append(args) or {"results": []},
        extract_adapter=lambda _urls: {"results": []},
        local_search_adapter=lambda query, _warnings: local_calls.append(query) or ("本地答案", [{"ref": "K1"}]),
        synthesize_adapter=lambda _payload: "这里的 skills 是能力说明 [K1][W1][C1]\\[A12\\]。",
    )

    assert result["chat_preview"] == "这里的 skills 是能力说明。"
    assert result["source_map"] == []
    assert result["meta"]["knowledge_sources"] == []
    assert result["meta"]["web_sources"] == []
    assert result["meta"]["web_search_attempted"] is False
    assert result["meta"]["used_local"] is False
    assert result["meta"]["used_web"] is False
    trace = result["meta"]["research_trace"]
    assert trace["selected_text_present"] is True
    assert trace["selected_text_chars"] == len("skills")
    assert trace["history_message_count"] == 0
    assert trace["history_summary_version"] == 0
    assert trace["needs_web"] is False
    assert trace["needs_local"] is False
    assert trace["queries"] == []
    assert search_calls == []
    assert local_calls == []


def test_current_article_prompt_prioritizes_history_for_followups(monkeypatch):
    prompts = []

    def fake_call_text_llm(prompt, **_kwargs):
        prompts.append(prompt)
        return "表格对比 Cursor、OpenAI、Claude、OpenCode。"

    monkeypatch.setattr(web_context_answer, "_call_text_llm", fake_call_text_llm)

    result = web_context_answer.web_context_answer(
        query="画个表格对比他们的区别和联系",
        mode="current_article_only",
        article_context={
            "title": "Agent Harness",
            "body": "文章还讨论中途切换主模型和使用 Subagent 的区别。",
        },
        selection_context={"selected_text": ""},
        history=[
            {"role": "user", "content": "[当轮选区: Cursor、OpenAI、Claude、OpenCode] 这些有什么区别吗"},
            {"role": "assistant", "content": "Cursor 和 OpenCode 是 Agent 产品，OpenAI 和 Claude 是模型相关对象。"},
        ],
        history_summary="上一轮讨论了 Cursor、OpenAI、Claude、OpenCode 的区别。",
        history_summary_version=3,
        search_adapter=lambda *_args: {"results": []},
        extract_adapter=lambda _urls: {"results": []},
    )

    assert prompts
    answer_prompt = prompts[-1]
    assert "直出模式上下文优先级：本轮问题 > 本轮选区 > 最近历史 > 长期摘要 > 当前文章。" in answer_prompt
    assert "最近历史里的 [当轮选区: ...] 是历史选区，不是本轮选区；只能用于理解追问指代。" in answer_prompt
    assert "优先根据最近历史判断指代对象" in answer_prompt
    assert "[当轮选区: Cursor、OpenAI、Claude、OpenCode] 这些有什么区别吗" in answer_prompt
    trace = result["meta"]["research_trace"]
    assert trace["selected_text_present"] is False
    assert trace["selected_text_chars"] == 0
    assert trace["history_message_count"] == 2
    assert trace["history_summary_version"] == 3
    assert trace["needs_web"] is False
    assert trace["needs_local"] is False
    assert trace["queries"] == []


def test_local_only_forces_local_and_forbids_web(monkeypatch):
    search_calls = []
    local_calls = []

    result = web_context_answer.web_context_answer(
        query="解释这个概念",
        mode="local_only",
        search_adapter=lambda *args: search_calls.append(args) or {"results": []},
        extract_adapter=lambda _urls: {"results": []},
        local_search_adapter=lambda query, _warnings: local_calls.append(query) or ("本地解释", [{"ref": "K1", "type": "knowledge", "id": "cap-1", "title": "本地笔记"}]),
        synthesize_adapter=lambda _payload: "这是本地解释 [K1]。",
    )

    assert result["chat_preview"] == "这是本地解释 [K1]。"
    assert [item["ref"] for item in result["source_map"]] == ["K1"]
    assert result["meta"]["web_search_attempted"] is False
    assert result["meta"]["used_local"] is True
    assert search_calls == []
    assert local_calls == ["解释这个概念"]


def test_web_only_forces_web_when_planner_skips(monkeypatch):
    search_calls = []
    patch_research_plan(monkeypatch, queries=[], needs_web=False)

    result = web_context_answer.web_context_answer(
        query="checkpoint 是什么",
        mode="web_only",
        search_adapter=lambda query, limit: search_calls.append((query, limit)) or {
            "results": [{"title": "Checkpoint", "url": "https://example.com/checkpoint", "description": "状态快照。"}]
        },
        extract_adapter=lambda _urls: {"results": []},
        synthesize_adapter=lambda _payload: "checkpoint 是状态快照 [W1]。",
    )

    assert search_calls == [("checkpoint 是什么", web_context_answer.WEB_CONTEXT_RESULTS_PER_QUERY)]
    assert result["meta"]["web_search_attempted"] is True
    assert result["meta"]["used_web"] is True
    assert result["chat_preview"] == "checkpoint 是状态快照 [W1]。"


def test_local_web_can_skip_web_and_records_skipped_queries(monkeypatch):
    search_calls = []
    local_calls = []
    patch_research_plan(monkeypatch, queries=["vibecoding skills CLAUDE AGENTS"], needs_web=False)

    result = web_context_answer.web_context_answer(
        query="这是什么意思",
        mode="local_web",
        selection_context={"selected_text": "skills"},
        search_adapter=lambda *args: search_calls.append(args) or {"results": []},
        extract_adapter=lambda _urls: {"results": []},
        local_search_adapter=lambda query, _warnings: local_calls.append(query) or ("本地材料", []),
        synthesize_adapter=lambda _payload: "这里的 skills 指 Agent 可加载的能力说明。",
    )

    query_statuses = result["meta"]["research_trace"]["query_statuses"]
    assert search_calls == []
    assert local_calls == ["选区：\nskills\n\n问题：\n这是什么意思"]
    assert result["meta"]["web_search_attempted"] is False
    assert result["meta"]["used_local"] is True
    assert result["meta"]["research_trace"]["intent_label"] == "planner"
    assert query_statuses == [
        {
            "query": "vibecoding skills CLAUDE AGENTS",
            "status": "skipped",
            "duration_ms": 0,
            "result_count": 0,
            "error": "web_not_requested",
        }
    ]


def test_local_web_force_web_runs_search_even_when_planner_skips(monkeypatch):
    search_calls = []
    patch_research_plan(monkeypatch, queries=[], needs_web=False)

    result = web_context_answer.web_context_answer(
        query="补充最新公开资料",
        mode="local_web",
        force_web=True,
        search_adapter=lambda query, limit: search_calls.append((query, limit)) or {
            "results": [
                {
                    "title": "官方更新",
                    "url": "https://example.com/update",
                    "description": "最新公开资料。",
                }
            ]
        },
        extract_adapter=lambda _urls: {"results": []},
        local_search_adapter=lambda _query, _warnings: ("本地材料", []),
        synthesize_adapter=lambda _payload: "已补充最新公开资料 [W1]。",
    )

    assert search_calls == [
        ("补充最新公开资料", web_context_answer.WEB_CONTEXT_RESULTS_PER_QUERY)
    ]
    assert result["meta"]["web_search_attempted"] is True
    assert result["meta"]["research_trace"]["force_web"] is True
    assert result["meta"]["research_plan"]["force_web"] is True


def test_history_overflow_updates_summary_version(monkeypatch):
    monkeypatch.setattr(
        web_context_answer,
        "_summarize_history",
        lambda previous, overflow: f"{previous} 已压缩 {len(overflow)} 条。".strip(),
    )

    result = web_context_answer.web_context_answer(
        query="继续解释",
        mode="current_article_only",
        article_context={"title": "测试文章", "body": "正文"},
        history_summary="旧摘要。",
        history_summary_version=2,
        history_overflow=[
            {"role": "user", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
        ],
        search_adapter=lambda *_args: {"results": []},
        extract_adapter=lambda _urls: {"results": []},
        synthesize_adapter=lambda _payload: "当前回答。",
    )

    assert result["meta"]["updated_history_summary"] == "旧摘要。 已压缩 2 条。"
    assert result["meta"]["updated_history_summary_version"] == 3
    assert result["meta"].get("history_summary_failed") is not True


def test_history_summary_failure_is_visible_without_breaking_answer(monkeypatch):
    monkeypatch.setattr(web_context_answer, "_call_text_llm", lambda *_args, **_kwargs: "")

    result = web_context_answer.web_context_answer(
        query="继续解释",
        mode="current_article_only",
        article_context={"title": "测试文章", "body": "正文"},
        history_overflow=[
            {"role": "user", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
        ],
        search_adapter=lambda *_args: {"results": []},
        extract_adapter=lambda _urls: {"results": []},
        synthesize_adapter=lambda _payload: "当前回答。",
    )

    assert result["chat_preview"] == "当前回答。"
    assert result["meta"]["history_summary_failed"] is True
    assert "summary provider returned empty content" in result["meta"]["history_summary_error"]
    assert any("长期记忆压缩失败" in warning for warning in result["warnings"])


def test_async_web_context_answer_emits_candidate_trace(monkeypatch):
    events = []
    patch_research_plan(monkeypatch, queries=["Harness engineer"])

    def fake_llm_json(prompt, *, timeout_seconds):
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
    query_events = [event for event in events if event["type"] == "queries_ready"]
    query_done_events = [event for event in events if event["type"] == "web_query_done"]
    material_events = [event for event in events if event["type"] == "materials_selected"]
    assert result["meta"]["research_trace"]["candidate_sources"][0]["ref"] == "W1"
    assert query_events and query_events[0]["queries"][:1] == ["Harness engineer"]
    assert query_events[0]["query_count"] == 1
    assert query_done_events and query_done_events[0]["candidate_count"] == 1
    assert material_events and material_events[0]["material_count"] == 1
    assert ranked
    assert ranked[0]["web_sources"][0]["title"] == "什么是 Harness Engineering"


def test_async_web_context_answer_does_not_emit_synthesis_start_when_skipped(monkeypatch):
    events = []
    patch_research_plan(monkeypatch, queries=["slow query"])
    monkeypatch.setattr(web_context_answer, "_web_context_search_query_timeout_seconds", lambda: 0.05)

    def slow_search(_query, _limit):
        time.sleep(0.35)
        return {"results": [{"title": "late", "url": "https://example.com/late", "description": "late"}]}

    result = asyncio.run(
        web_context_answer.async_web_context_answer(
            query="HTML 承载复杂信息 可视化 原型 vibecoding",
            mode="web_only",
            article_context={"title": "本地文章", "body": "本地文章不能在 web_only 下触发合成。"},
            search_adapter=slow_search,
            extract_adapter=lambda _urls: {"results": []},
            synthesize_adapter=lambda _payload: (_ for _ in ()).throw(
                AssertionError("empty web_only material should not synthesize")
            ),
            event_callback=lambda event: events.append(event),
        )
    )

    assert result["meta"]["provider_status"]["synthesis_skipped"] is True
    assert "synthesis_start" not in [event["type"] for event in events]


def test_async_web_context_answer_streams_answer_delta(monkeypatch):
    events = []
    patch_research_plan(monkeypatch, queries=["Coding agent"])

    def fake_llm_json(prompt, *, timeout_seconds):
        if "选择网页材料" in prompt:
            return {"selected_refs": ["W1"], "open_refs": []}
        return {}

    def fake_search(_query, _limit):
        return {
            "results": [
                {
                    "title": "Coding agent guide",
                    "url": "https://example.com/coding-agent",
                    "description": "Coding agent 能规划、修改代码并运行检查。",
                }
            ]
        }

    def fake_call_text_llm_stream(_prompt, *, answer_delta_callback, **_kwargs):
        answer_delta_callback("Coding agent")
        answer_delta_callback(" 是能处理代码任务的 AI 系统 [W1]")
        return "Coding agent 是能处理代码任务的 AI 系统 [W1]"

    monkeypatch.setattr(web_context_answer, "_llm_json", fake_llm_json)
    monkeypatch.setattr(web_context_answer, "_call_text_llm_stream", fake_call_text_llm_stream)

    result = asyncio.run(
        web_context_answer.async_web_context_answer(
            query="什么是 Coding agent",
            mode="web_only",
            search_adapter=fake_search,
            extract_adapter=lambda _urls: {"results": []},
            event_callback=lambda event: events.append(event),
        )
    )

    deltas = [event["delta"] for event in events if event["type"] == "answer_delta"]
    assert "".join(deltas) == "Coding agent 是能处理代码任务的 AI 系统 [W1]"
    assert result["chat_preview"] == "Coding agent 是能处理代码任务的 AI 系统 [W1]"


def test_async_web_context_answer_emits_summary_update(monkeypatch):
    events = []
    monkeypatch.setattr(web_context_answer, "_summarize_history", lambda _previous, _overflow: "长期摘要 v1")

    result = asyncio.run(
        web_context_answer.async_web_context_answer(
            query="继续",
            mode="current_article_only",
            article_context={"title": "测试文章", "body": "正文"},
            history_overflow=[
                {"role": "user", "content": "旧问题"},
                {"role": "assistant", "content": "旧回答"},
            ],
            search_adapter=lambda *_args: {"results": []},
            extract_adapter=lambda _urls: {"results": []},
            synthesize_adapter=lambda _payload: "当前回答。",
            event_callback=lambda event: events.append(event),
        )
    )

    summary_events = [event for event in events if event["type"] == "summary_updated"]
    assert result["meta"]["updated_history_summary"] == "长期摘要 v1"
    assert summary_events == [
        {
            "type": "summary_updated",
            "path": "web_context",
            "updated_history_summary": "长期摘要 v1",
            "updated_history_summary_version": 1,
        }
    ]


def test_reasoning_capture_preserves_provider_reasoning_fields():
    capture = {}

    web_context_answer._append_reasoning_capture(capture, "第一段")
    web_context_answer._append_reasoning_capture(capture, "第二段")

    assert web_context_answer._extract_reasoning_value({"reasoning_content": "推理"}) == "推理"
    assert web_context_answer._reasoning_trace_from_capture(capture) == {
        "source": "api",
        "api_reasoning": "第一段第二段",
    }
    assert web_context_answer._reasoning_trace_from_capture({})["source"] == "missing"


def test_web_context_answer_prompt_uses_generic_answer_contract(monkeypatch):
    prompts = []
    patch_research_plan(monkeypatch, queries=["Codex Claude Code difference"])

    def fake_llm_json(prompt, *, timeout_seconds):
        if "选择网页材料" in prompt:
            return {"selected_refs": ["W1", "W2"], "open_refs": []}
        return {}

    def fake_search(_query, _limit):
        return {
            "results": [
                {
                    "title": "OpenAI Codex docs",
                    "url": "https://openai.com/codex",
                    "description": "Codex is an OpenAI coding agent.",
                },
                {
                    "title": "Claude Code docs",
                    "url": "https://docs.anthropic.com/claude-code",
                    "description": "Claude Code is an agentic coding tool.",
                },
            ]
        }

    def fake_call_text_llm(prompt, **_kwargs):
        prompts.append(prompt)
        return "Codex 和 Claude Code 都是 AI 编程 Agent，区别主要在默认工作流和生态。"

    monkeypatch.setattr(web_context_answer, "_llm_json", fake_llm_json)
    monkeypatch.setattr(web_context_answer, "_call_text_llm", fake_call_text_llm)

    result = web_context_answer.web_context_answer(
        query="Codex 和 Claude Code 的区别是什么？",
        mode="web_only",
        search_adapter=fake_search,
        extract_adapter=lambda _urls: {"results": []},
    )

    assert result["chat_preview"].startswith("Codex 和 Claude Code")
    assert prompts
    answer_prompt = prompts[-1]
    assert "自行判断用户真正想知道什么" in answer_prompt
    assert "不要套固定题型模板" in answer_prompt
    assert "回答应以解决用户问题为第一目标" in answer_prompt
    assert "表格只能辅助理解，不能替代回答" in answer_prompt
    assert "不要为了形式整齐而省略关键解释" in answer_prompt
    assert "1-3" not in answer_prompt
    assert "产品对比题" not in answer_prompt


def test_web_context_answer_local_search_query_includes_short_selection(monkeypatch):
    local_queries = []

    def fake_local_search(query, _warnings):
        local_queries.append(query)
        return "本地答案", [{"ref": "K1", "type": "knowledge", "id": "cap-1", "title": "智能问数"}]

    result = web_context_answer.web_context_answer(
        query="这两个有什么区别",
        mode="local_only",
        selection_context={"selected_text": "Text-to-Metrics vs Text-to-SQL"},
        search_adapter=lambda *_args: {"results": []},
        extract_adapter=lambda _urls: {"results": []},
        local_search_adapter=fake_local_search,
        synthesize_adapter=lambda payload: "Text-to-Metrics 和 Text-to-SQL 的区别是前者走指标语义层，后者直接生成 SQL [K1]。",
    )

    assert result["chat_preview"].startswith("Text-to-Metrics")
    assert local_queries == ["选区：\nText-to-Metrics vs Text-to-SQL\n\n问题：\n这两个有什么区别"]


def test_web_context_answer_normalizes_escaped_refs_and_filters_sources(monkeypatch):
    result = web_context_answer.web_context_answer(
        query="这是什么意思",
        mode="local_only",
        search_adapter=lambda *_args: {"results": []},
        extract_adapter=lambda _urls: {"results": []},
        local_search_adapter=lambda _query, _warnings: (
            "本地答案",
            [
                {"ref": "K1", "type": "knowledge", "id": "cap-1", "title": "未引用来源"},
                {
                    "ref": "K2",
                    "type": "knowledge",
                    "id": "cap-2",
                    "title": "目标文章",
                    "path": "captures/target.md",
                    "section": "目标章节",
                    "excerpt": "目标摘录",
                },
            ],
        ),
        synthesize_adapter=lambda _payload: "这里指目标文章里的具体概念 \\[K2\\]。",
    )

    assert result["chat_preview"] == "这里指目标文章里的具体概念 [K2]。"
    assert [source["ref"] for source in result["meta"]["knowledge_sources"]] == ["K2"]
    assert result["source_map"] == [
        {
            "ref": "K2",
            "role": "context",
            "type": "knowledge",
            "id": "cap-2",
            "title": "目标文章",
            "url": "",
            "path": "captures/target.md",
            "file_path": "captures/target.md",
            "section": "目标章节",
            "excerpt": "目标摘录",
            "domain": "",
            "status": "",
        }
    ]


def test_web_context_answer_prioritizes_current_article_over_ai_answer_notes(monkeypatch):
    seen_payloads = []

    def fake_synthesize(payload):
        seen_payloads.append(payload)
        assert [source["title"] for source in payload["knowledge_sources"]] == [
            "当前文章",
            "历史 AI 问答",
        ]
        return "这里应优先结合当前文章解释 [K2]。"

    result = web_context_answer.web_context_answer(
        query="这是什么意思",
        mode="local_only",
        article_context={
            "title": "当前文章",
            "body": "## 当前章节\n这里讨论一个短词在本文里的含义。",
            "file_path": "captures/current.md",
        },
        selection_context={
            "selected_text": "term",
            "heading_path": ["当前章节"],
        },
        search_adapter=lambda *_args: {"results": []},
        extract_adapter=lambda _urls: {"results": []},
        local_search_adapter=lambda _query, _warnings: (
            "本地答案",
            [
                {
                    "ref": "K1",
                    "type": "knowledge",
                    "id": "ai-note",
                    "title": "历史 AI 问答",
                    "path": "captures/ai-note.md",
                    "file_path": "captures/ai-note.md",
                    "excerpt": "历史回答",
                    "reading_session_id": "session-1",
                    "reading_draft_id": "draft-1",
                    "source_kind": "AI 解释",
                },
                {
                    "ref": "K2",
                    "type": "knowledge",
                    "id": "current",
                    "title": "当前文章",
                    "path": "captures/current.md",
                    "file_path": "captures/current.md",
                    "section": "当前章节",
                    "excerpt": "当前章节摘录",
                },
            ],
        ),
        synthesize_adapter=fake_synthesize,
    )

    assert seen_payloads
    assert result["chat_preview"] == "这里应优先结合当前文章解释 [K2]。"
    assert [source["ref"] for source in result["meta"]["retrieved_knowledge_sources"]] == ["K2", "K1"]
    assert [source["ref"] for source in result["meta"]["knowledge_sources"]] == ["K2"]


def test_web_context_answer_local_search_query_includes_article_context():
    local_queries = []

    result = web_context_answer.web_context_answer(
        query="这是什么意思",
        mode="local_only",
        article_context={
            "title": "基本无害的vibecoding实践技巧（文科生友好版）",
            "body": "## 5. 从错误中获得提升\n常犯的错误固化为规则、记忆、skill、hook、cron 或脚本。",
            "summary": "文章讨论 vibecoding 实践技巧。",
        },
        selection_context={
            "selected_text": "cron",
            "heading_path": ["5. 从错误中获得提升"],
        },
        search_adapter=lambda *_args: {"results": []},
        extract_adapter=lambda _urls: {"results": []},
        local_search_adapter=lambda query, _warnings: (local_queries.append(query) or ("本地答案", [])),
        synthesize_adapter=lambda _payload: "这里的 cron 指定时任务。",
    )

    assert result["chat_preview"] == "这里的 cron 指定时任务。"
    assert len(local_queries) == 1
    local_query = local_queries[0]
    assert "选区：\ncron" in local_query
    assert "文章标题：\n基本无害的vibecoding实践技巧（文科生友好版）" in local_query
    assert "标题路径：\n5. 从错误中获得提升" in local_query
    assert "当前文章上下文：\n## 5. 从错误中获得提升" in local_query
    assert "常犯的错误固化为规则、记忆、skill、hook、cron 或脚本。" in local_query


def test_web_context_answer_local_search_query_without_selection_uses_question():
    local_queries = []

    result = web_context_answer.web_context_answer(
        query="Text-to-SQL 是什么",
        mode="local_only",
        search_adapter=lambda *_args: {"results": []},
        extract_adapter=lambda _urls: {"results": []},
        local_search_adapter=lambda query, _warnings: (local_queries.append(query) or ("本地答案", [])),
        synthesize_adapter=lambda payload: "Text-to-SQL 是把自然语言转成 SQL。",
    )

    assert result["chat_preview"] == "Text-to-SQL 是把自然语言转成 SQL。"
    assert local_queries == ["Text-to-SQL 是什么"]

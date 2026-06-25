import asyncio
import sys
import time
import types
from pathlib import Path

import pytest

from bridge import research_bridge


@pytest.fixture(autouse=True)
def disable_default_query_planner(monkeypatch):
    monkeypatch.setattr(research_bridge, "_default_query_planner", lambda payload: [])


def test_default_query_planner_loads_repo_root_config(monkeypatch):
    recorded = {}

    def fake_load_config(path):
        recorded["config_path"] = path
        return types.SimpleNamespace(
            llm_api_key="test-key",
            llm_model="test-model",
            llm_base_url="https://llm.example",
        )

    import agents.capture_agent as capture_agent
    monkeypatch.setattr(capture_agent, "load_config", fake_load_config)

    import bridge.research.synthesis as synthesis

    def fake_call_llm(**kwargs):
        recorded["llm_kwargs"] = kwargs
        return '{"needs_web":true,"queries":["OpenAI official"],"subject":{"name":"OpenAI"}}'

    monkeypatch.setattr(synthesis, "_call_llm", fake_call_llm)

    raw = research_bridge._original_default_query_planner({"query": "OpenAI 是什么", "mode": "web_only"})

    expected = Path(research_bridge.__file__).resolve().parent.parent / "config.yaml"
    assert recorded["config_path"] == str(expected)
    assert raw
    assert recorded["llm_kwargs"]["api_key"] == "test-key"


def test_research_planner_accepts_structured_plan():
    plan = research_bridge.plan_research(
        "Anthropic是什么东西？",
        mode="web_only",
        selection_context={"selected_text": "Anthropic 的四层系统：从混乱数据到可信答案"},
        query_planner_adapter=lambda payload: {
            "intent_label": "entity background",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "Anthropic", "aliases": [], "type_hint": "company"},
            "selection_role": "context_only",
            "risk_level": "low",
            "freshness_sensitive": False,
            "queries": ["Anthropic company overview", "Anthropic Claude official"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {
                "prefer_primary_sources": True,
                "preferred_source_kinds": ["official", "documentation"],
                "preferred_domains": ["anthropic.com"],
                "avoid_topics": ["valuation"],
            },
            "answer_contract": {
                "primary_answer_first": True,
                "explain_selection_relation": True,
                "avoid_unasked_side_topics": True,
            },
            "privacy": {"may_use_selection_text": False, "allowed_context_terms": ["Anthropic"]},
        },
    )

    assert plan["plan_status"] == "planned"
    assert plan["intent_label"] == "entity background"
    assert plan["subject"]["name"] == "Anthropic"
    assert plan["evidence_contract"]["must_cover_subject"] is True
    assert plan["queries"] == ["Anthropic company overview", "Anthropic Claude official"]
    assert plan["source_preferences"]["preferred_domains"] == ["anthropic.com"]
    assert plan["answer_contract"]["explain_selection_relation"] is True


def test_research_planner_rejects_invalid_json_and_uses_mechanical_fallback():
    plan = research_bridge.plan_research(
        "什么是τ定律",
        mode="web_only",
        selection_context={
            "selected_text": "τ定律",
            "surrounding_after": "这是一段不应被 fallback 拼进搜索词的私有上下文。",
        },
        query_planner_adapter=lambda payload: "not-json",
    )

    assert plan["queries"] == ["什么是τ定律", "什么是tau定律"]
    assert all("私有上下文" not in query for query in plan["queries"])
    assert plan["planner_fallback_reason"] == "invalid_json"
    assert plan["planner_raw_preview"] == "not-json"
    assert plan["planner_parse_error"]


def test_research_planner_handles_adapter_timeout_with_fallback():
    plan = research_bridge.plan_research(
        "Explain λ calculus",
        mode="web_only",
        query_planner_adapter=lambda payload: (_ for _ in ()).throw(TimeoutError("planner timeout")),
    )

    assert plan["queries"] == ["Explain λ calculus", "Explain lambda calculus"]
    assert plan["planner_fallback_reason"] == "planner_timeout"
    assert "TimeoutError" in plan["planner_error"]
    assert isinstance(plan["planner_elapsed_ms"], int)


def test_research_planner_records_schema_rejected_payload():
    plan = research_bridge.plan_research(
        "这是什么",
        mode="web_only",
        query_planner_adapter=lambda payload: {
            "needs_web": True,
            "queries": [],
            "subject": {"name": ""},
        },
    )

    assert plan["plan_status"] == "fallback"
    assert plan["planner_fallback_reason"] == "schema_rejected"
    assert plan["planner_parse_error"] == "schema rejected planner payload"
    assert "needs_web" in plan["planner_raw_preview"]


def test_research_trace_exposes_planner_diagnostics():
    plan = research_bridge.plan_research(
        "OpenAI 是什么",
        mode="web_only",
        query_planner_adapter=lambda payload: "not-json",
    )

    trace = research_bridge._build_research_trace(
        {
            "mode": "web_only",
            "research_plan": plan,
            "plan_status": plan["plan_status"],
            "provider_status": {},
            "search_quality": {},
            "web_queries": [],
            "rewritten_web_queries": [],
        },
        [],
    )

    assert trace["planner_fallback_reason"] == "invalid_json"
    assert trace["planner_raw_preview"] == "not-json"
    assert trace["planner_parse_error"]


def test_research_planner_fallback_extracts_subject_before_followup_instruction():
    plan = research_bridge.plan_research(
        "OpenAI 是什么？请用联网证据简短回答，并保留引用编号。",
        mode="web_only",
        query_planner_adapter=lambda payload: [],
    )

    assert plan["plan_status"] == "fallback"
    assert plan["subject"]["name"] == "OpenAI"
    assert plan["queries"] == ["OpenAI 是什么？请用联网证据简短回答，并保留引用编号"]


def test_search_contract_stops_before_calling_backend_when_budget_exhausted():
    warnings = []
    token = research_bridge._RESEARCH_DEADLINE.set(time.monotonic() - 1)
    try:
        results = research_bridge.collect_web_search_results(
            ["OpenAI official"],
            search_adapter=lambda _query, _limit: (_ for _ in ()).throw(AssertionError("should not search")),
            warnings=warnings,
        )
    finally:
        research_bridge._RESEARCH_DEADLINE.reset(token)

    assert results == []
    assert any("预算已耗尽" in warning for warning in warnings)


def test_llm_contract_stops_before_network_when_budget_exhausted():
    token = research_bridge._RESEARCH_DEADLINE.set(time.monotonic() - 1)
    try:
        with pytest.raises(TimeoutError, match="research budget exhausted"):
            research_bridge._call_llm(
                api_key="test",
                model="test",
                base_url="https://example.invalid",
                prompt="{}",
            )
    finally:
        research_bridge._RESEARCH_DEADLINE.reset(token)


def test_web_search_gets_minimum_window_when_planner_consumes_reserve():
    token = research_bridge._RESEARCH_DEADLINE.set(time.monotonic() + 25)
    try:
        timeout = research_bridge._web_search_stage_timeout()
    finally:
        research_bridge._RESEARCH_DEADLINE.reset(token)

    assert 10 <= timeout <= research_bridge.WEB_SEARCH_TIMEOUT_SECONDS


def test_evidence_critic_reserve_allows_late_stage_request_window():
    token = research_bridge._RESEARCH_DEADLINE.set(time.monotonic() + 20)
    try:
        timeout = research_bridge._stage_timeout(
            research_bridge.EVIDENCE_CRITIC_TIMEOUT_SECONDS,
            reserve_seconds=research_bridge.EVIDENCE_CRITIC_RESERVE_SECONDS,
        )
    finally:
        research_bridge._RESEARCH_DEADLINE.reset(token)

    assert timeout >= 10


def test_low_remaining_budget_skips_broad_evidence_retry():
    sources = [
        {
            "id": f"https://source-{index}.example/openai",
            "url": f"https://source-{index}.example/openai",
            "domain": f"source-{index}.example",
            "title": f"OpenAI source {index}",
            "excerpt": "OpenAI overview.",
            "extract_status": "summary",
        }
        for index in range(1, 6)
    ]
    calls = []

    def rejecting_critic(payload):
        calls.append(payload)
        return {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "unknown",
                    "source_role": "clue",
                    "allowed_usage": ["background"],
                    "confidence": "low",
                    "supports_claims": [],
                    "adoption": "reject",
                    "reason": "not enough evidence",
                }
                for source in payload["sources"]
            ]
        }

    warnings = []
    token = research_bridge._RESEARCH_DEADLINE.set(time.monotonic() + 20)
    try:
        adopted, rejected = research_bridge._apply_evidence_critic(
            "OpenAI 官方网站如何介绍 OpenAI？",
            {
                "mode": "web_only",
                "risk_level": "low",
                "freshness_sensitive": False,
                "answer_depth": "detailed",
                "subject": {"name": "OpenAI"},
                "answer_claims_needed": ["answer_user_question"],
                "evidence_contract": {"allow_summary_only": True, "minimum_independent_sources": 1},
                "source_preferences": {},
            },
            sources,
            warnings,
            critic_adapter=rejecting_critic,
        )
    finally:
        research_bridge._RESEARCH_DEADLINE.reset(token)

    assert adopted == []
    assert len(rejected) == len(sources)
    assert len(calls) == 3
    assert "跳过证据裁判后的总 claim 复核" in " ".join(warnings)


def test_low_remaining_budget_skips_post_critic_fallback(monkeypatch):
    monkeypatch.setattr(research_bridge, "_available_fallback_search_backends", lambda _primary: ["ddgs"])
    fallback_calls = []
    monkeypatch.setattr(
        research_bridge,
        "_default_web_search",
        lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "OpenAI source",
                        "url": "https://source.example/openai",
                        "description": "OpenAI overview.",
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_web_search_with_backend",
        lambda query, limit, backend: fallback_calls.append((query, backend)) or {"success": True, "data": {"web": []}},
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "unknown",
                    "source_role": "clue",
                    "allowed_usage": ["background"],
                    "confidence": "low",
                    "supports_claims": [],
                    "adoption": "reject",
                    "reason": "not enough evidence",
                }
                for source in payload["sources"]
            ]
        },
    )
    warnings = []
    token = research_bridge._RESEARCH_DEADLINE.set(time.monotonic() + 20)
    try:
        result = research_bridge._run_web_research(
            "OpenAI 官方网站如何介绍 OpenAI？",
            warnings,
            research_plan={
                "intent_label": "entity background",
                "needs_web": True,
                "mode": "web_only",
                "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
                "selection_role": "none",
                "risk_level": "low",
                "freshness_sensitive": False,
                "answer_depth": "normal",
                "queries": ["OpenAI official"],
                "answer_claims_needed": ["answer_user_question"],
                "claim_requirements": {},
                "evidence_contract": {"allow_summary_only": True, "minimum_independent_sources": 1},
                "source_preferences": {},
                "answer_contract": {},
                "privacy": {},
                "plan_status": "planned",
            },
            article_context={},
            selection_context={},
            search_adapter=None,
            extract_adapter=lambda urls: {"results": [{"url": url, "content": "OpenAI overview."} for url in urls]},
        )
    finally:
        research_bridge._RESEARCH_DEADLINE.reset(token)

    assert fallback_calls == []
    assert result["provider_status"]["fallback_attempts"] == []
    assert result["sources"] == []
    assert result["search_quality"]["reason"] == "evidence_critic_rejected_all"
    assert "跳过补充搜索" in " ".join(warnings)


def test_mid_remaining_budget_skips_post_critic_fallback(monkeypatch):
    monkeypatch.setattr(research_bridge, "_available_fallback_search_backends", lambda _primary: ["ddgs"])
    fallback_calls = []
    monkeypatch.setattr(
        research_bridge,
        "_default_web_search",
        lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "OpenAI source",
                        "url": "https://source.example/openai",
                        "description": "OpenAI overview.",
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_web_search_with_backend",
        lambda query, limit, backend: fallback_calls.append((query, backend)) or {"success": True, "data": {"web": []}},
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "unknown",
                    "source_role": "clue",
                    "allowed_usage": ["background"],
                    "confidence": "low",
                    "supports_claims": [],
                    "adoption": "reject",
                    "reason": "not enough evidence",
                }
                for source in payload["sources"]
            ]
        },
    )
    warnings = []
    token = research_bridge._RESEARCH_DEADLINE.set(time.monotonic() + 70)
    try:
        result = research_bridge._run_web_research(
            "OpenAI 官方网站如何介绍 OpenAI？",
            warnings,
            research_plan={
                "intent_label": "entity background",
                "needs_web": True,
                "mode": "web_only",
                "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
                "selection_role": "none",
                "risk_level": "low",
                "freshness_sensitive": False,
                "answer_depth": "normal",
                "queries": ["OpenAI official"],
                "answer_claims_needed": ["answer_user_question"],
                "claim_requirements": {},
                "evidence_contract": {"allow_summary_only": True, "minimum_independent_sources": 1},
                "source_preferences": {},
                "answer_contract": {},
                "privacy": {},
                "plan_status": "planned",
            },
            article_context={},
            selection_context={},
            search_adapter=None,
            extract_adapter=lambda urls: {"results": [{"url": url, "content": "OpenAI overview."} for url in urls]},
        )
    finally:
        research_bridge._RESEARCH_DEADLINE.reset(token)

    assert fallback_calls == []
    assert result["provider_status"]["fallback_attempts"] == []
    assert "跳过补充搜索" in " ".join(warnings)


def test_low_remaining_budget_skips_async_post_critic_fallback(monkeypatch):
    monkeypatch.setattr(research_bridge, "_available_fallback_search_backends", lambda _primary: ["ddgs"])
    fallback_calls = []
    monkeypatch.setattr(
        research_bridge,
        "_default_web_search",
        lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "OpenAI source",
                        "url": "https://source.example/openai",
                        "description": "OpenAI overview.",
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_web_search_with_backend",
        lambda query, limit, backend: fallback_calls.append((query, backend)) or {"success": True, "data": {"web": []}},
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "unknown",
                    "source_role": "clue",
                    "allowed_usage": ["background"],
                    "confidence": "low",
                    "supports_claims": [],
                    "adoption": "reject",
                    "reason": "not enough evidence",
                }
                for source in payload["sources"]
            ]
        },
    )
    warnings = []
    token = research_bridge._RESEARCH_DEADLINE.set(time.monotonic() + 20)
    try:
        result = asyncio.run(
            research_bridge._run_web_research_async(
                "OpenAI 官方网站如何介绍 OpenAI？",
                warnings,
                research_plan={
                    "intent_label": "entity background",
                    "needs_web": True,
                    "mode": "web_only",
                    "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
                    "selection_role": "none",
                    "risk_level": "low",
                    "freshness_sensitive": False,
                    "answer_depth": "normal",
                    "queries": ["OpenAI official"],
                    "answer_claims_needed": ["answer_user_question"],
                    "claim_requirements": {},
                    "evidence_contract": {"allow_summary_only": True, "minimum_independent_sources": 1},
                    "source_preferences": {},
                    "answer_contract": {},
                    "privacy": {},
                    "plan_status": "planned",
                },
                article_context={},
                selection_context={},
                search_adapter=None,
                extract_adapter=lambda urls: {"results": [{"url": url, "content": "OpenAI overview."} for url in urls]},
            )
        )
    finally:
        research_bridge._RESEARCH_DEADLINE.reset(token)

    assert fallback_calls == []
    assert result["provider_status"]["fallback_attempts"] == []
    assert result["sources"] == []
    assert result["search_quality"]["reason"] == "evidence_critic_rejected_all"
    assert "跳过补充搜索" in " ".join(warnings)


def test_research_planner_empty_result_uses_original_query_only():
    queries = research_bridge.plan_web_queries(
        "苹果现在的市值多少",
        query_planner_adapter=lambda payload: [],
    )

    assert queries == ["苹果现在的市值多少"]
    assert all("AAPL" not in query and "market cap" not in query.lower() for query in queries)


def test_research_planner_fallback_recovers_public_selected_subject_for_deictic_query():
    plan = research_bridge.plan_research(
        "这个期刊怎么样",
        mode="web_only",
        selection_context={"selected_text": "《Review of Economic Studies》"},
        query_planner_adapter=lambda payload: [],
    )

    assert plan["plan_status"] == "fallback"
    assert plan["selection_role"] == "primary_subject"
    assert plan["subject"]["name"] == "Review of Economic Studies"
    assert plan["queries"][0] == "Review of Economic Studies 这个期刊怎么样"
    assert "Review of Economic Studies" in " ".join(plan["queries"])


def test_mode_contract_overrides_planner_mode_field():
    plan = research_bridge.plan_research(
        "什么叫波动率",
        mode="web_only",
        query_planner_adapter=lambda payload: {
            "needs_web": True,
            "mode": "local_web",
            "queries": ["什么叫波动率"],
            "subject": {"name": "波动率", "aliases": [], "type_hint": "concept"},
        },
    )

    assert plan["mode"] == "web_only"


def test_research_planner_trims_overlong_query():
    long_query = "x" * 180
    queries = research_bridge.plan_web_queries(
        "用户问题",
        query_planner_adapter=lambda payload: {"queries": [long_query]},
    )

    assert len(queries) == 1
    assert len(queries[0]) <= 120


def test_research_planner_removes_long_selection_from_planned_query():
    selected_text = "私有选区" * 80
    queries = research_bridge.plan_web_queries(
        "用户问题",
        selection_context={"selected_text": selected_text},
        query_planner_adapter=lambda payload: {"queries": [f"{selected_text} public clue"]},
    )

    assert queries == ["public clue"]


def test_research_planner_removes_short_private_selection_unless_allowed():
    plan = research_bridge.plan_research(
        "这是什么？",
        mode="web_only",
        selection_context={"selected_text": "内部代号阿尔法项目", "surrounding_after": "不要泄露到搜索词"},
        query_planner_adapter=lambda payload: {
            "needs_web": True,
            "mode": "web_only",
            "queries": ["内部代号阿尔法项目 public meaning", "不要泄露到搜索词 public context"],
            "privacy": {"may_use_selection_text": False, "allowed_context_terms": []},
        },
    )

    assert all("内部代号阿尔法项目" not in query for query in plan["queries"])
    assert all("不要泄露到搜索词" not in query for query in plan["queries"])


def test_research_planner_keeps_allowed_context_term():
    plan = research_bridge.plan_research(
        "这是什么？",
        mode="web_only",
        selection_context={"selected_text": "Coding agent"},
        query_planner_adapter=lambda payload: {
            "needs_web": True,
            "mode": "web_only",
            "queries": ["Coding agent definition"],
            "privacy": {"may_use_selection_text": False, "allowed_context_terms": ["Coding agent"]},
        },
    )

    assert plan["queries"] == ["Coding agent definition"]


def test_research_planner_preserves_explicit_user_query_anchor_without_topic_hardcoding():
    plan = research_bridge.plan_research(
        "为什么说改稻为桑是个死局",
        mode="web_only",
        query_planner_adapter=lambda payload: {
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "改稻为桑", "aliases": ["以桑代稻"], "type_hint": "historical_policy"},
            "queries": ["以桑代稻 历史 僵局", "农业结构调整 失败 分析"],
            "answer_depth": "detailed",
        },
    )

    assert plan["queries"][:2] == ["为什么说改稻为桑是个死局", "改稻为桑"]
    assert "以桑代稻 历史 僵局" in plan["queries"]


def test_detailed_research_plan_keeps_full_user_question_even_when_core_term_is_present():
    plan = research_bridge.plan_research(
        "为什么说改稻为桑是个死局",
        mode="web_only",
        query_planner_adapter=lambda payload: {
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "改稻为桑", "aliases": [], "type_hint": "historical_policy"},
            "queries": ["改稻为桑 死局 原因", "大明王朝 改稻为桑 政策 分析"],
            "answer_depth": "detailed",
        },
    )

    assert plan["queries"][0] == "为什么说改稻为桑是个死局"
    assert "改稻为桑 死局 原因" in plan["queries"]


def test_preferred_domains_are_added_as_site_queries():
    queries = research_bridge._queries_with_preferred_domains(
        ["Federal Reserve Chair 2026"],
        {"preferred_domains": ["federalreserve.gov"]},
    )

    assert queries == [
        "Federal Reserve Chair 2026",
        "Federal Reserve Chair 2026 site:federalreserve.gov",
    ]


def test_preferred_domain_queries_get_reserved_slots():
    queries = research_bridge._queries_with_preferred_domains(
        ["q1", "q2", "q3", "q4"],
        {"preferred_domains": ["baike.baidu.com", "people.com.cn"]},
    )

    assert queries == [
        "q1",
        "q2",
        "q1 site:baike.baidu.com",
        "q1 site:people.com.cn",
    ]


def test_low_budget_preferred_domain_query_keeps_site_probe():
    queries = research_bridge._queries_with_preferred_domains(
        ["OpenAI overview", "OpenAI background"],
        {"preferred_domains": ["openai.com"]},
        max_queries=2,
    )

    assert queries == [
        "OpenAI overview",
        "OpenAI overview site:openai.com",
    ]


def test_source_kind_query_domains_add_site_queries_without_mutating_plan_preferences():
    queries = research_bridge._queries_with_preferred_domains(
        ["volatility definition finance", "什么是波动率"],
        {"preferred_source_kinds": ["finance_glossary"]},
    )

    assert queries == [
        "volatility definition finance",
        "什么是波动率",
        "volatility definition finance site:investopedia.com",
        "volatility definition finance site:corporatefinanceinstitute.com",
    ]


def test_financial_data_source_kind_adds_market_data_site_queries():
    queries = research_bridge._queries_with_preferred_domains(
        ["Nvidia market cap latest", "英伟达 市值 最新"],
        {"preferred_source_kinds": ["financial_data"]},
    )

    assert queries == [
        "Nvidia market cap latest",
        "英伟达 市值 最新",
        "Nvidia market cap latest site:companiesmarketcap.com",
        "Nvidia market cap latest site:finance.yahoo.com",
    ]


def test_finance_term_type_hint_adds_default_source_kind_without_keyword_routing():
    plan = research_bridge._research_plan_from_payload(
        {
            "needs_web": True,
            "mode": "web_only",
            "queries": ["什么叫波动率"],
            "subject": {"name": "波动率", "aliases": ["volatility"], "type_hint": "finance_term"},
            "source_preferences": {},
        },
        query="什么叫波动率",
        mode="web_only",
    )

    assert plan is not None
    assert plan["source_preferences"]["preferred_source_kinds"] == ["finance_glossary"]
    assert plan["source_preferences"]["preferred_domains"] == []
    assert plan["evidence_contract"]["minimum_independent_sources"] == 2


def test_explicit_planner_minimum_is_not_raised_by_source_preferences():
    plan = research_bridge._research_plan_from_payload(
        {
            "needs_web": True,
            "mode": "web_only",
            "queries": ["OpenAI official about"],
            "risk_level": "low",
            "answer_depth": "normal",
            "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
            "source_preferences": {
                "preferred_source_kinds": ["encyclopedia", "official", "reputable_media"],
                "preferred_domains": ["openai.com"],
            },
            "evidence_contract": {"minimum_independent_sources": 1},
        },
        query="OpenAI 是什么",
        mode="web_only",
    )

    assert plan is not None
    assert plan["evidence_contract"]["minimum_independent_sources"] == 1


def test_low_risk_simple_plan_caps_executed_web_queries(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "simple definition",
            "needs_web": True,
            "mode": "web_only",
            "risk_level": "low",
            "freshness_sensitive": False,
            "answer_depth": "normal",
            "subject": {"name": "example", "aliases": [], "type_hint": "concept"},
            "queries": ["example q1", "example q2", "example q3", "example q4"],
            "evidence_contract": {
                "minimum_independent_sources": 1,
                "must_use_primary_source": False,
                "allow_summary_only": True,
            },
        },
    )
    calls = []

    def fake_search(query, limit):
        calls.append(query)
        return {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": f"{query} result",
                        "url": f"https://example-{query}.com/page",
                        "description": f"{query} gives a readable source.",
                    }
                ]
            },
        }

    result = research_bridge.research_answer(
        query="什么是 example",
        mode="web_only",
        search_adapter=fake_search,
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "Readable extracted evidence."} for url in urls]},
        synthesize_adapter=lambda payload: "Example 是一个说明性概念 [W1]",
    )

    assert sorted(calls) == ["example q1", "example q2"]
    assert result["meta"]["web_queries"] == ["example q1", "example q2"]
    assert result["meta"]["provider_status"]["fallback_attempts"] == []


def test_full_budget_plan_keeps_four_executed_web_queries(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "current metric",
            "needs_web": True,
            "mode": "web_only",
            "risk_level": "high",
            "freshness_sensitive": True,
            "answer_depth": "detailed",
            "subject": {"name": "指标", "aliases": [], "type_hint": "metric"},
            "queries": ["指标 q1", "指标 q2", "指标 q3", "指标 q4"],
            "answer_claims_needed": ["answer_user_question"],
            "evidence_contract": {
                "minimum_independent_sources": 2,
                "must_use_primary_source": True,
                "allow_summary_only": False,
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "primary",
                    "source_role": "primary_evidence",
                    "allowed_usage": ["fact"],
                    "confidence": "high",
                    "supports_claims": ["answer_user_question"],
                    "adoption": "adopt",
                    "reason": "usable source",
                }
                for source in payload["sources"]
            ]
        },
    )
    calls = []

    def fake_search(query, limit):
        calls.append(query)
        return {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": f"{query} official result",
                        "url": f"https://official-{query}.example/report",
                        "description": f"{query} official evidence.",
                    }
                ]
            },
        }

    result = research_bridge.research_answer(
        query="最新指标是多少",
        mode="web_only",
        search_adapter=fake_search,
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "Official extracted evidence."} for url in urls]},
        synthesize_adapter=lambda payload: "最新指标需要以官方来源为准 [W1][W2]",
    )

    assert len(calls) == 4
    assert result["meta"]["web_queries"] == ["最新指标是多少", "指标 q1", "指标 q2", "指标 q3"]


def test_detailed_answer_depth_requires_multiple_independent_sources_without_topic_keywords():
    plan = research_bridge._research_plan_from_payload(
        {
            "needs_web": True,
            "mode": "web_only",
            "queries": ["interpretive explanation"],
            "answer_depth": "detailed",
            "evidence_contract": {"minimum_independent_sources": 1},
        },
        query="interpretive explanation",
        mode="web_only",
    )

    assert plan is not None
    assert plan["answer_depth"] == "detailed"
    assert plan["evidence_contract"]["minimum_independent_sources"] == 2


def test_query_planner_prompt_assigns_finance_terms_to_glossary_sources():
    prompt = research_bridge._build_query_planner_prompt(
        {
            "current_date": "2026-06-22",
            "mode": "web_only",
            "query": "什么叫波动率",
            "selected_text": "",
            "surrounding_before": "",
            "surrounding_after": "",
            "heading_path": [],
            "article_title": "",
            "article_summary": "",
        }
    )

    assert "subject.type_hint 应写 finance_term" in prompt
    assert "preferred_source_kinds 应包含 finance_glossary" in prompt
    assert "英文术语 definition 查询" in prompt


def test_evidence_critic_prompt_prioritizes_direct_definition_pages_over_adjacent_concepts():
    prompt = research_bridge._build_evidence_critic_prompt(
        {
            "query": "什么叫波动率",
            "current_date": "2026-06-22",
            "subject": {"name": "波动率", "aliases": ["volatility"], "type_hint": "finance_term"},
            "answer_claims_needed": ["answer_user_question"],
            "claim_requirements": {},
            "evidence_contract": {"must_cover_subject": True},
            "sources": [],
        }
    )

    assert "直接定义页/官方页和相邻概念页" in prompt
    assert "相邻概念页即使正文提到研究主体，也只能作为 clue" in prompt


def test_evidence_critic_prompt_honors_multi_source_contract_for_interpretation():
    prompt = research_bridge._build_evidence_critic_prompt(
        {
            "query": "为什么说改稻为桑是个死局",
            "current_date": "2026-06-22",
            "subject": {"name": "改稻为桑", "aliases": [], "type_hint": "interpretive_topic"},
            "answer_claims_needed": ["interpretive_explanation"],
            "claim_requirements": {},
            "evidence_contract": {"minimum_independent_sources": 2, "allow_summary_only": True},
            "sources": [],
        }
    )

    assert "证据合同要求多个独立来源" in prompt
    assert "不要只选择一个最佳来源" in prompt
    assert "minimum_source_repair" in prompt


def test_minimal_evidence_critic_prompt_allows_commentary_for_low_risk_interpretation():
    prompt = research_bridge._build_evidence_critic_prompt(
        {
            "minimal_critic": True,
            "query": "为什么说改稻为桑是个死局",
            "subject": {"name": "改稻为桑"},
            "answer_claims_needed": ["answer_user_question"],
            "evidence_contract": {"allow_summary_only": True, "minimum_independent_sources": 2},
            "sources": [],
        }
    )

    assert "commentary_secondary" in prompt
    assert "interpretive_evidence" in prompt
    assert "不要因为它不是官方来源就拒绝" in prompt


def test_finance_glossary_definition_uses_full_critic_with_late_stage_budget():
    assert research_bridge._prefer_minimal_evidence_critic(
        {
            "mode": "web_only",
            "risk_level": "low",
            "freshness_sensitive": False,
            "answer_depth": "normal",
            "subject": {"name": "volatility", "type_hint": "finance_term"},
            "source_preferences": {"preferred_source_kinds": ["finance_glossary"]},
            "evidence_contract": {
                "allow_summary_only": True,
                "must_use_primary_source": False,
            },
        },
        ["answer_user_question"],
    ) is False


def test_low_risk_simple_identity_can_use_minimal_critic():
    assert research_bridge._prefer_minimal_evidence_critic(
        {
            "mode": "web_only",
            "risk_level": "low",
            "freshness_sensitive": False,
            "answer_depth": "normal",
            "subject": {"name": "OpenAI", "type_hint": "company"},
            "source_preferences": {"preferred_source_kinds": ["official"]},
            "evidence_contract": {
                "allow_summary_only": True,
                "must_use_primary_source": False,
            },
        },
        ["identity"],
    ) is True


def test_planner_source_kinds_do_not_add_domain_hints_in_code():
    plan = research_bridge.plan_research(
        "段永平是谁",
        mode="web_only",
        query_planner_adapter=lambda payload: {
            "intent_label": "人物简介",
            "needs_web": True,
            "queries": ["段永平 简介"],
            "source_preferences": {
                "preferred_source_kinds": ["encyclopedia", "profile", "reputable_media"],
            },
        },
    )

    assert plan["source_preferences"]["preferred_domains"] == []
    assert plan["source_preferences"]["preferred_source_kinds"] == ["encyclopedia", "profile", "reputable_media"]


def test_official_domain_guardrails_are_planner_supplied_not_code_inferred():
    plan = research_bridge.plan_research(
        "美联储主席是谁",
        mode="web_only",
        query_planner_adapter=lambda payload: {
            "needs_web": True,
            "queries": ["Federal Reserve Chair 2026"],
            "source_preferences": {},
        },
    )

    assert plan["freshness_sensitive"] is False
    assert plan["source_preferences"]["prefer_primary_sources"] is False
    assert plan["source_preferences"]["preferred_source_kinds"] == []
    assert plan["source_preferences"]["preferred_domains"] == []


def test_mixed_case_entity_query_does_not_infer_official_domain():
    plan = research_bridge.plan_research(
        "OpenAI是什么意思",
        mode="web_only",
        query_planner_adapter=lambda payload: {
            "needs_web": True,
            "queries": ["OpenAI是什么意思"],
            "source_preferences": {},
        },
    )

    assert plan["source_preferences"]["preferred_domains"] == []
    assert plan["source_preferences"]["prefer_primary_sources"] is False
    assert plan["freshness_sensitive"] is False


def test_should_search_web_is_mode_based_not_keyword_based():
    assert research_bridge.should_search_web("任意问题", "web_only") is True
    assert research_bridge.should_search_web("任意问题", "local_web") is True
    assert research_bridge.should_search_web("任意问题", "local_only") is False
    assert research_bridge.should_search_web("任意问题", "current_article_only") is False


def test_local_web_mode_forces_web_even_when_planner_says_no_web(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "definition",
            "needs_web": False,
            "mode": "local_web",
            "queries": [],
        },
    )
    monkeypatch.setattr(research_bridge, "_search_local", lambda *_args: ("", []))
    calls = []

    result = research_bridge.research_answer(
        query="SQL是什么意思",
        mode="local_web",
        selection_context={"selected_text": "SQL 语句"},
        search_adapter=lambda query, limit: (
            calls.append(query)
            or {
                "success": True,
                "data": {
                    "web": [
                        {
                            "title": "SQL",
                            "url": "https://example.com/sql",
                            "description": "SQL 是结构化查询语言。",
                        }
                    ]
                },
            }
        ),
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "SQL 是结构化查询语言，用于查询和管理关系型数据库。"}]},
        synthesize_adapter=lambda payload: "SQL 是结构化查询语言 [W1]",
    )

    assert calls[0] == "SQL是什么意思"
    assert "SQL" in calls
    assert result["meta"]["web_search_attempted"] is True
    assert result["meta"]["used_web"] is True
    assert result["meta"]["research_plan"]["plan_status"] == "planned_web_forced_by_mode"
    assert result["chat_preview"] == "SQL 是结构化查询语言 [W1]"


def test_search_collector_dedupes_urls():
    def fake_search(query, limit):
        return {
            "success": True,
            "data": {
                "web": [
                    {"title": f"{query} A", "url": "https://example.com/a", "description": "first"},
                    {"title": f"{query} B", "url": "https://example.com/a", "description": "duplicate"},
                ]
            },
        }

    warnings = []
    results = research_bridge.collect_web_search_results(
        ["q1", "q2"],
        search_adapter=fake_search,
        warnings=warnings,
    )

    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/a"
    assert warnings == []


def test_search_parser_cleans_dashscope_url_with_inline_metadata():
    results = research_bridge._parse_web_search(
        {
            "results": [
                {
                    "title": "人民网结果",
                    "url": "https://www.people.com.cn），搜索时间：2026年6月21日，关键词：“段永平”",
                    "description": "段永平相关资料。",
                }
            ]
        }
    )

    assert results[0]["url"] == "https://www.people.com.cn"
    assert research_bridge._domain(results[0]["url"]) == "people.com.cn"


def test_ranker_uses_planner_preferred_domains_without_inferring_intent():
    ranked = research_bridge.rank_web_sources(
        "Anthropic是什么东西？",
        [
            {
                "title": "Secondary writeup",
                "url": "https://example.com/articles/anthropic",
                "description": "A secondary article.",
            },
            {
                "title": "Introducing Claude",
                "url": "https://www.anthropic.com/claude",
                "description": "Claude is Anthropic's family of AI assistants.",
            },
        ],
        source_preferences={"preferred_domains": ["anthropic.com"]},
    )

    preferred = next(source for source in ranked if source["domain"] == "anthropic.com")
    assert [source["domain"] for source in ranked] == ["example.com", "anthropic.com"]
    assert "planner_preferred_domain" in preferred["quality_tags"]
    assert "query_type" not in preferred


def test_ranker_prefers_planner_domain_for_english_entity():
    ranked = research_bridge.rank_web_sources(
        "OpenAI是什么意思",
        [
            {
                "title": "应用人工智能实验室",
                "url": "https://baike.baidu.com/item/OpenAI",
                "description": "OpenAI 百科摘要。",
            },
            {
                "title": "OpenAI",
                "url": "https://openai.com/",
                "description": "OpenAI official site.",
            },
        ],
        source_preferences={"preferred_domains": ["openai.com"]},
    )

    preferred = next(source for source in ranked if source["domain"] == "openai.com")
    assert [source["domain"] for source in ranked] == ["baike.baidu.com", "openai.com"]
    assert "planner_preferred_domain" in preferred["quality_tags"]


def test_ranker_penalizes_low_quality_domains():
    ranked = research_bridge.rank_web_sources(
        "四月份A股的走势和行情是什么",
        [
            {
                "title": "短视频结果",
                "url": "https://www.douyin.com/shipin/7606912622541178930",
                "description": "个股短视频。",
            },
            {
                "title": "普通网页",
                "url": "https://example.com/page",
                "description": "有文字摘要的普通网页。",
            },
        ],
    )

    low_quality = next(source for source in ranked if source["domain"] == "douyin.com")
    assert [source["domain"] for source in ranked] == ["douyin.com", "example.com"]
    assert "low_quality_domain" in low_quality["quality_tags"]


def test_ranker_marks_ai_study_pages_as_weak_secondary():
    ranked = research_bridge.rank_web_sources(
        "OpenAI 是什么",
        [
            {
                "title": "openai什么意思",
                "url": "https://aistudy.baidu.com/site/example",
                "description": "OpenAI 是一家人工智能研究公司。",
            }
        ],
    )

    assert ranked[0]["domain"] == "aistudy.baidu.com"
    assert "weak_secondary_source" in ranked[0]["quality_tags"]


def test_quality_gate_keeps_low_quality_result_as_retrieval_hint():
    ranked = research_bridge.rank_web_sources(
        "四月份A股的走势和行情是什么",
        [
            {
                "title": "短视频结果",
                "url": "https://www.douyin.com/shipin/7606912622541178930",
                "description": "个股短视频。",
            }
        ],
    )

    quality = research_bridge.evaluate_search_quality("四月份A股的走势和行情是什么", ranked)

    assert quality["passed"] is True
    assert quality["reason"] == "passed"
    assert quality["top_result_hints"]["low_quality"] is True


def test_person_profile_rejects_forum_and_seo_qa_only_sources(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "人物简介",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "段永平", "aliases": ["Duan Yongping"], "type_hint": "person"},
            "queries": ["段永平 简介", "段永平 投资 步步高 OPPO vivo"],
            "evidence_contract": {"must_cover_subject": True, "minimum_independent_sources": 2},
            "source_preferences": {
                "prefer_primary_sources": False,
                "preferred_source_kinds": ["encyclopedia", "profile", "interview", "reputable_media"],
                "preferred_domains": [],
                "avoid_topics": ["论坛", "股吧", "SEO问答"],
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda _payload: {
            "decisions": [
                {
                    "source_id": "https://baike.baidu.com/item/段永平/6041736",
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "source_role": "supporting_evidence",
                    "allowed_usage": ["fact", "background"],
                    "confidence": "medium",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "profile"}],
                    "adoption": "adopt",
                }
            ]
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://www.federalreserve.gov/aboutthefed/bios/board/default.htm",
                    "subject_match": "yes",
                    "source_authority": "official",
                    "source_role": "primary_evidence",
                    "supports_claims": ["answer_user_question"],
                    "adoption": "adopt",
                    "reason": "Official summary result directly supports the current role answer.",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="段永平是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "段永平:巴菲特的一句话,影响了我一生",
                        "url": "http://k.sina.com.cn/article_7857201856_1d45362c001906l20o.html",
                        "description": "自媒体搬运文章。",
                    },
                    {
                        "title": "段永平 经典投资案例",
                        "url": "https://guba.sina.com.cn/?s=thread&bid=9908&tid=23510",
                        "description": "股吧帖子。",
                    },
                    {
                        "title": "段永平投资多少公司",
                        "url": "https://aiqicha.baidu.com/details/ugknowledge?id=3ddb1a760cab5a40e57d7f2bd1956bae",
                        "description": "SEO 问答聚合页。",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "弱来源正文"} for url in urls]},
        synthesize_adapter=lambda payload: (_ for _ in ()).throw(AssertionError("weak person-profile evidence should not synthesize")),
    )

    assert result["meta"]["used_web"] is False
    assert result["meta"]["web_sources"] == []
    assert result["meta"]["search_quality"]["passed"] is False
    assert "足够可靠的公开资料" in result["chat_preview"]


def test_person_profile_filters_low_quality_sources_from_evidence(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "人物简介",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "段永平", "aliases": ["Duan Yongping"], "type_hint": "person"},
            "queries": ["段永平 简介"],
            "evidence_contract": {"must_cover_subject": True, "minimum_independent_sources": 2},
            "source_preferences": {
                "prefer_primary_sources": False,
                "preferred_source_kinds": ["encyclopedia", "profile", "interview", "reputable_media"],
                "preferred_domains": [],
                "avoid_topics": ["论坛", "股吧", "SEO问答"],
            },
        },
    )

    seen_sources = {}
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "profile"}],
                    "adoption": "adopt",
                    "reason": "profile source",
                }
                if "k.sina.com.cn" not in source["source_id"]
                else {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "weak_secondary",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "no", "reason": "self-media repost"}],
                    "adoption": "reject",
                    "reason": "weak repost source",
                }
                for source in payload["sources"]
            ]
        },
    )

    result = research_bridge.research_answer(
        query="段永平是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "段永平",
                        "url": "https://baike.baidu.com/item/段永平/6041736",
                        "description": "段永平是企业家。",
                    },
                    {
                        "title": "段永平访谈",
                        "url": "https://people.com.cn/duan-yongping-profile",
                        "description": "段永平谈步步高与长期投资。",
                    },
                    {
                        "title": "段永平:巴菲特的一句话,影响了我一生",
                        "url": "http://k.sina.com.cn/article_7857201856_1d45362c001906l20o.html",
                        "description": "自媒体搬运文章。",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "页面正文"} for url in urls]},
        synthesize_adapter=lambda payload: (
            seen_sources.setdefault("domains", [source["domain"] for source in payload["web_sources"]])
            and "段永平是企业家 [W1][W2]"
        ),
    )

    assert seen_sources["domains"] == ["baike.baidu.com", "people.com.cn"]
    assert [source["domain"] for source in result["meta"]["web_sources"]] == ["baike.baidu.com", "people.com.cn"]
    assert result["chat_preview"] == "段永平是企业家 [W1][W2]"


def test_source_kind_preferences_do_not_hide_other_critic_accepted_sources(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "interpretive explanation",
            "needs_web": True,
            "mode": "web_only",
            "answer_depth": "normal",
            "subject": {"name": "改稻为桑", "aliases": [], "type_hint": "interpretive_topic"},
            "queries": ["改稻为桑 为什么是死局"],
            "answer_claims_needed": ["interpretive_explanation"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {"preferred_source_kinds": ["encyclopedia"]},
        },
    )
    seen = {}

    def fake_critic(payload):
        seen["critic_domains"] = [source["domain"] for source in payload["sources"]]
        return {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "source_role": "interpretive_evidence",
                    "allowed_usage": ["interpretation", "background"],
                    "confidence": "medium",
                    "supports_claims": ["interpretive_explanation"],
                    "adoption": "adopt",
                    "reason": "supports the explanation",
                }
                for source in payload["sources"]
            ]
        }

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", fake_critic)

    result = research_bridge.research_answer(
        query="为什么说改稻为桑是个死局",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "改稻为桑",
                        "url": "https://baike.baidu.com/item/改稻为桑/123",
                        "description": "改稻为桑 相关背景。",
                    },
                    {
                        "title": "改稻为桑为何是一步死棋",
                        "url": "https://history.sohu.com/a/example",
                        "description": "改稻为桑 为什么是死局 的解释。",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [{"url": url, "content": "改稻为桑 为什么是死局。"} for url in urls]
        },
        synthesize_adapter=lambda payload: (
            seen.__setitem__("synthesis_domains", [source["domain"] for source in payload["web_sources"]])
            or "改稻为桑是多重矛盾叠加的死局 [W1][W2]"
        ),
    )

    assert seen["critic_domains"] == ["baike.baidu.com", "history.sohu.com"]
    assert seen["synthesis_domains"] == ["baike.baidu.com", "history.sohu.com"]
    assert result["meta"]["research_plan"]["evidence_contract"]["minimum_independent_sources"] == 2
    assert result["meta"]["research_trace"]["source_counts"]["adopted_independent_domains"] == 2


def test_preferred_domain_and_source_kind_do_not_override_critic_rejection(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "profile",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "Example Person", "aliases": [], "type_hint": "person"},
            "queries": ["Example Person profile"],
            "answer_claims_needed": ["subject_overview"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {
                "preferred_source_kinds": ["encyclopedia"],
                "preferred_domains": ["baike.baidu.com"],
            },
        },
    )
    seen = {}

    def fake_critic(payload):
        seen["retrieval_hints"] = payload["sources"][0]["retrieval_hints"]
        return {
            "decisions": [
                {
                    "source_id": payload["sources"][0]["source_id"],
                    "subject_match": "no",
                    "source_authority": "reputable_secondary",
                    "source_role": "reject",
                    "allowed_usage": [],
                    "confidence": "low",
                    "claim_support": [{"claim_id": "subject_overview", "support": "no", "reason": "wrong subject"}],
                    "adoption": "reject",
                    "reason": "critic rejected the preferred source",
                }
            ]
        }

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", fake_critic)

    result = research_bridge.research_answer(
        query="Example Person 是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Example Person",
                        "url": "https://baike.baidu.com/item/Example%20Person/123",
                        "description": "Preferred source result.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "Preferred source result."}]},
        synthesize_adapter=lambda payload: (_ for _ in ()).throw(
            AssertionError("critic-rejected preferred source should not synthesize")
        ),
    )

    assert "planner_preferred_domain" in seen["retrieval_hints"]
    assert result["meta"]["used_web"] is False
    assert result["meta"]["web_sources"] == []
    assert result["meta"]["rejected_sources"][0]["quality_reason"] == "evidence_critic_rejected"


def test_weak_secondary_hint_blocks_authoritative_contract_adoption(monkeypatch):
    seen = {}

    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "company overview",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
            "queries": ["OpenAI 是什么"],
            "answer_claims_needed": ["identity"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: seen.__setitem__("critic_sources", payload["sources"])
        or {
            "decisions": [
                {
                    "source_id": payload["sources"][0]["source_id"],
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "source_role": "supporting_evidence",
                    "allowed_usage": ["fact"],
                    "confidence": "medium",
                    "claim_support": [{"claim_id": "identity", "support": "yes", "reason": "critic accepted it"}],
                    "adoption": "adopt",
                    "reason": "critic accepted weak tutorial source",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="OpenAI 是什么",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "OpenAI官网入口 OpenAI生成图片官方网址入口",
                        "url": "https://m.php.cn/faq/openai-image.html",
                        "description": "第三方教程聚合页。",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "OpenAI 第三方教程。"}]},
        synthesize_adapter=lambda payload: (_ for _ in ()).throw(
            AssertionError("weak secondary evidence should not synthesize under authoritative contract")
        ),
    )

    assert seen["critic_sources"][0]["domain"] == "m.php.cn"
    assert "weak_secondary_source" in seen["critic_sources"][0]["retrieval_hints"]
    assert result["meta"]["used_web"] is False
    assert result["meta"]["web_sources"] == []
    assert result["meta"]["rejected_sources"][0]["quality_reason"] == "source_quality_below_contract"


def test_person_profile_allows_single_reliable_source_as_limited_evidence(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "人物简介",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "段永平", "aliases": ["Duan Yongping"], "type_hint": "person"},
            "queries": ["段永平 简介"],
            "evidence_contract": {"must_cover_subject": True, "minimum_independent_sources": 2},
            "source_preferences": {
                "preferred_source_kinds": ["encyclopedia", "profile"],
                "preferred_domains": ["baike.baidu.com"],
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda _payload: {
            "decisions": [
                {
                    "source_id": "https://baike.baidu.com/item/段永平/6041736",
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "source_role": "supporting_evidence",
                    "allowed_usage": ["fact", "background"],
                    "confidence": "medium",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "profile"}],
                    "adoption": "adopt",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="段永平是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "段永平",
                        "url": "https://baike.baidu.com/item/段永平/6041736",
                        "description": "段永平是企业家。",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "段永平是企业家。"}]},
        synthesize_adapter=lambda payload: (
            "证据有限：可确认段永平是一位企业家；更完整履历仍需更多独立可靠来源交叉核验 [W1]"
        ),
    )

    assert result["meta"]["web_sources"][0]["domain"] == "baike.baidu.com"
    assert result["meta"]["search_quality"]["reason"] == "limited_evidence"
    assert result["meta"]["search_quality"]["evidence_status"] == "limited"
    assert result["meta"]["research_trace"]["source_counts"]["evidence"] == 1
    assert any("可靠来源较少" in warning for warning in result["warnings"])


def test_person_profile_rejects_wrong_entity_even_on_preferred_domain(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "人物简介",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "段永平", "aliases": ["Duan Yongping"], "type_hint": "person"},
            "queries": ["Duan Yongping biography"],
            "evidence_contract": {"must_cover_subject": True, "minimum_independent_sources": 2},
            "source_preferences": {
                "preferred_source_kinds": ["encyclopedia", "profile"],
                "preferred_domains": ["baike.baidu.com"],
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda _payload: {
            "decisions": [
                {
                    "source_id": "https://www.federalreserve.gov/aboutthefed/bios/board/default.htm",
                    "subject_match": "yes",
                    "source_authority": "primary",
                    "source_role": "primary_evidence",
                    "allowed_usage": ["fact"],
                    "confidence": "high",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "official board page"}],
                    "adoption": "adopt",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="段永平是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "杜安平",
                        "url": "https://baike.baidu.com/item/杜安平/67488553",
                        "description": "杜安平是中医专家。",
                    },
                    {
                        "title": "杜永平",
                        "url": "https://baike.baidu.com/item/杜永平/10133922",
                        "description": "杜永平是画家。",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "错实体正文"} for url in urls]},
        synthesize_adapter=lambda payload: (_ for _ in ()).throw(AssertionError("wrong entity sources should not synthesize")),
    )

    assert result["meta"]["web_sources"] == []
    assert result["meta"]["search_quality"]["passed"] is False
    assert result["meta"]["search_quality"]["reason"] == "evidence_critic_rejected_all"
    assert "无法给出有来源支撑" in result["chat_preview"]


def test_aer_rejects_unrelated_homepage_and_uses_subject_matching_sources(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "journal_evaluation",
            "needs_web": True,
            "mode": "web_only",
            "subject": {
                "name": "American Economic Review",
                "aliases": ["AER"],
                "type_hint": "academic_journal",
            },
            "queries": ["American Economic Review journal overview"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 2,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {},
        },
    )

    seen_sources = {}
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes" if "people.com.cn" not in source["source_id"] else "no",
                    "source_authority": "official" if "aeaweb.org" in source["source_id"] else "reputable_secondary",
                    "source_role": "supporting_evidence",
                    "allowed_usage": ["fact", "background"],
                    "confidence": "medium",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "subject source"}]
                    if "people.com.cn" not in source["source_id"]
                    else [{"claim_id": "answer_user_question", "support": "no", "reason": "homepage"}],
                    "adoption": "adopt" if "people.com.cn" not in source["source_id"] else "reject",
                    "reason": "structured test decision",
                }
                for source in payload["sources"]
            ]
        },
    )
    result = research_bridge.research_answer(
        query="《American Economic Review》这个期刊怎么样",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "人民网_网上的人民日报",
                        "url": "https://people.com.cn/",
                        "description": "人民网首页。",
                    },
                    {
                        "title": "American Economic Review",
                        "url": "https://www.aeaweb.org/journals/aer",
                        "description": "American Economic Review is a journal of the American Economic Association.",
                    },
                    {
                        "title": "American Economic Review journal profile",
                        "url": "https://example.edu/aer-profile",
                        "description": "AER is widely treated as a leading economics journal.",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": url,
                    "content": "American Economic Review journal information."
                    if "people.com.cn" not in url
                    else "人民网首页内容。",
                }
                for url in urls
            ]
        },
        synthesize_adapter=lambda payload: (
            seen_sources.setdefault("titles", [source["title"] for source in payload["web_sources"]])
            and "AER 是经济学领域重要期刊 [W1][W2]"
        ),
    )

    assert seen_sources["titles"] == ["American Economic Review", "American Economic Review journal profile"]
    assert "人民网_网上的人民日报" not in [source["title"] for source in result["meta"]["web_sources"]]
    rejected_homepage = next(
        source for source in result["meta"]["rejected_sources"]
        if source["title"] == "人民网_网上的人民日报"
    )
    assert rejected_homepage["quality_reason"] == "evidence_critic_rejected"
    assert "generic_homepage" in rejected_homepage["quality_tags"]
    assert [source["ref"] for source in result["meta"]["web_sources"]] == ["W1", "W2"]


def test_evidence_critic_keeps_only_sources_that_support_claims(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "journal evaluation",
            "needs_web": True,
            "mode": "local_web",
            "subject": {
                "name": "Econometric Theory",
                "aliases": [],
                "type_hint": "academic_journal",
            },
            "queries": ["Econometric Theory journal ranking"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "answer_claims_needed": [
                "期刊所属领域",
                "官方指标或出版方信息",
                "学术地位/分级",
            ],
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_search_local",
        lambda *_args: (
            "本地资料认为 Econometric Theory 是计量经济学领域重要期刊。",
            [{"ref": "K1", "type": "knowledge", "id": "journal-1", "title": "经济学期刊分级笔记"}],
        ),
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://baike.baidu.com/item/俞学文/66770070",
                    "subject_match": "no",
                    "supports_claims": [],
                    "adoption": "reject",
                    "reason": "页面主体是人物，不是 Econometric Theory 期刊。",
                },
                    {
                        "source_id": "https://www.cambridge.org/core/journals/econometric-theory/information",
                        "subject_match": "yes",
                        "source_authority": "official",
                        "supports_claims": ["期刊所属领域", "官方指标或出版方信息"],
                        "adoption": "adopt",
                        "reason": "Cambridge 期刊信息页直接讨论 Econometric Theory 并给出期刊信息。",
                },
                {
                    "source_id": "https://xueshu.baidu.com/usercenter/paper/show?paperid=40d4c7036483efa826e705c93991db3e",
                    "subject_match": "no",
                    "supports_claims": [],
                    "adoption": "reject",
                    "reason": "论文单页不能支撑期刊评价。",
                },
            ]
        },
    )
    seen_sources = {}

    def synthesize(payload):
        seen_sources["web_sources"] = payload["web_sources"]
        return "《Econometric Theory》是计量经济学方向的重要期刊，具体指标以 Cambridge 页面为准 [K1][W1]"

    result = research_bridge.research_answer(
        query="这个期刊怎么样",
        mode="local_web",
        selection_context={"selected_text": "《Econometric Theory》"},
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "俞学文",
                        "url": "https://baike.baidu.com/item/俞学文/66770070",
                        "description": "人物百科页面。",
                    },
                    {
                        "title": "Econometric Theory information",
                        "url": "https://www.cambridge.org/core/journals/econometric-theory/information",
                        "description": "Econometric Theory journal information from Cambridge University Press.",
                    },
                    {
                        "title": "Methodological Problems",
                        "url": "https://xueshu.baidu.com/usercenter/paper/show?paperid=40d4c7036483efa826e705c93991db3e",
                        "description": "Econometric Theory article page.",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": url,
                    "content": (
                        "Econometric Theory is a Cambridge University Press journal with journal metrics."
                        if "cambridge.org" in url
                        else "This page does not support journal evaluation claims."
                    ),
                }
                for url in urls
            ]
        },
        synthesize_adapter=synthesize,
    )

    assert [source["domain"] for source in seen_sources["web_sources"]] == ["cambridge.org"]
    assert result["chat_preview"] == "《Econometric Theory》是计量经济学方向的重要期刊，具体指标以 Cambridge 页面为准 [K1][W1]"
    assert [source["ref"] for source in result["meta"]["web_sources"]] == ["W1"]
    assert result["meta"]["web_sources"][0]["domain"] == "cambridge.org"
    rejected_domains = {source["domain"] for source in result["meta"]["rejected_sources"]}
    assert {"baike.baidu.com", "xueshu.baidu.com"} <= rejected_domains


def test_evidence_critic_rejects_near_miss_journal_title_and_preserves_claim_support(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "journal evaluation",
            "needs_web": True,
            "mode": "local_web",
            "subject": {
                "name": "Journal of Economic Literature",
                "aliases": ["JEL"],
                "type_hint": "academic_journal",
            },
            "queries": ["Journal of Economic Literature overview"],
            "answer_claims_needed": ["publisher_scope", "latest_impact_factor"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": False,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_search_local",
        lambda *_args: (
            "本地资料认为 Journal of Economic Literature 是经济学重要综述期刊。",
            [{"ref": "K1", "type": "knowledge", "id": "journal-1", "title": "经济学期刊分级笔记"}],
        ),
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                    {
                        "source_id": "https://baike.baidu.com/item/Journal%20of%20Economic%20Literature/65067201",
                        "subject_match": "yes",
                        "source_authority": "reputable_secondary",
                        "claim_support": [
                            {"claim_id": "publisher_scope", "support": "yes", "reason": "JEL page supports scope"},
                            {"claim_id": "latest_impact_factor", "support": "no", "reason": "metric not verified"},
                    ],
                    "adoption": "partial",
                    "reason": "Correct journal page supports only stable scope claim.",
                },
                {
                    "source_id": "https://baike.baidu.com/item/Journal%20of%20Economic%20Behavior%20%26%20Organization/64884599",
                    "subject_match": "no",
                    "claim_support": [
                        {"claim_id": "publisher_scope", "support": "no", "reason": "Different journal"},
                        {"claim_id": "latest_impact_factor", "support": "no", "reason": "Different journal"},
                    ],
                    "adoption": "reject",
                    "reason": "Near-miss title for a different journal.",
                },
            ]
        },
    )
    seen = {}

    def synthesize(payload):
        seen["web_sources"] = payload["web_sources"]
        return "JEL 是经济学重要综述期刊 [K1][W1]"

    result = research_bridge.research_answer(
        query="这个期刊怎么样",
        mode="local_web",
        selection_context={"selected_text": "《Journal of Economic Literature》"},
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Journal of Economic Literature",
                        "url": "https://baike.baidu.com/item/Journal%20of%20Economic%20Literature/65067201",
                        "description": "Journal of Economic Literature page.",
                    },
                    {
                        "title": "Journal of Economic Behavior & Organization",
                        "url": "https://baike.baidu.com/item/Journal%20of%20Economic%20Behavior%20%26%20Organization/64884599",
                        "description": "Different journal page.",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": url,
                    "content": (
                        "Journal of Economic Literature is a journal of economic literature."
                        if "Literature" in url
                        else "Journal of Economic Behavior & Organization is a different journal."
                    ),
                }
                for url in urls
            ]
        },
        synthesize_adapter=synthesize,
    )

    assert [source["title"] for source in seen["web_sources"]] == ["Journal of Economic Literature"]
    assert seen["web_sources"][0]["supports_claims"] == ["publisher_scope"]
    assert seen["web_sources"][0]["unsupported_claims"] == ["latest_impact_factor"]
    assert result["meta"]["web_sources"][0]["ref"] == "W1"
    assert result["meta"]["web_sources"][0]["supports_claims"] == ["publisher_scope"]
    assert any(
        source["title"] == "Journal of Economic Behavior & Organization"
        and source["quality_reason"] == "evidence_critic_rejected"
        for source in result["meta"]["rejected_sources"]
    )
    assert "Behavior" not in result["chat_preview"]


def test_claim_requirement_strips_metric_claim_from_insufficient_authority_source(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "journal evaluation",
            "needs_web": True,
            "mode": "web_only",
            "subject": {
                "name": "Journal of Economic Literature",
                "aliases": ["JEL"],
                "type_hint": "academic_journal",
            },
            "queries": ["Journal of Economic Literature impact factor"],
            "answer_claims_needed": ["publisher_scope", "current_impact_factor"],
            "claim_requirements": [
                {"claim_id": "publisher_scope", "required_authority": "any_reliable"},
                {"claim_id": "current_impact_factor", "required_authority": "primary_or_index"},
            ],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": False,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://baike.baidu.com/item/Journal%20of%20Economic%20Literature/65067201",
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "claim_support": [
                        {"claim_id": "publisher_scope", "support": "yes", "reason": "scope is described"},
                        {"claim_id": "current_impact_factor", "support": "yes", "reason": "metric is listed"},
                    ],
                    "adoption": "partial",
                    "reason": "Secondary source with mixed claim support.",
                }
            ]
        },
    )
    seen = {}

    def synthesize(payload):
        seen["web_sources"] = payload["web_sources"]
        return "JEL 是经济学重要综述期刊 [W1]"

    result = research_bridge.research_answer(
        query="这个期刊怎么样",
        mode="web_only",
        selection_context={"selected_text": "《Journal of Economic Literature》"},
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Journal of Economic Literature",
                        "url": "https://baike.baidu.com/item/Journal%20of%20Economic%20Literature/65067201",
                        "description": "Journal page with metrics.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "JEL scope and metrics."}]},
        synthesize_adapter=synthesize,
    )

    assert seen["web_sources"][0]["supports_claims"] == ["publisher_scope"]
    assert "current_impact_factor" in seen["web_sources"][0]["unsupported_claims"]
    assert result["meta"]["web_sources"][0]["supports_claims"] == ["publisher_scope"]
    assert result["meta"]["web_sources"][0]["source_authority"] == "reputable_secondary"


def test_strict_metric_plan_gets_stable_subject_claim_without_lowering_high_risk_contract():
    metric_plan = research_bridge.plan_research(
        "这个数值是多少",
        mode="web_only",
        query_planner_adapter=lambda _payload: {
            "intent_label": "metric check",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "Example Journal", "type_hint": "academic_journal"},
            "queries": ["Example Journal metric"],
            "answer_claims_needed": ["current_metric"],
            "claim_requirements": [{"claim_id": "current_metric", "required_authority": "primary_or_index"}],
            "evidence_contract": {"must_cover_subject": True, "allow_summary_only": False},
        },
    )
    assert metric_plan["answer_claims_needed"] == ["subject_overview", "current_metric"]

    high_risk_plan = research_bridge.plan_research(
        "这个药有什么禁忌",
        mode="web_only",
        query_planner_adapter=lambda _payload: {
            "intent_label": "medical",
            "needs_web": True,
            "mode": "web_only",
            "risk_level": "high",
            "subject": {"name": "Example Drug", "type_hint": "medical_term"},
            "queries": ["Example Drug contraindication"],
            "answer_claims_needed": ["contraindication"],
            "claim_requirements": [{"claim_id": "contraindication", "required_authority": "primary_or_index"}],
            "evidence_contract": {"must_cover_subject": True, "must_use_primary_source": True, "allow_summary_only": False},
        },
    )
    assert high_risk_plan["answer_claims_needed"] == ["contraindication"]


def test_planned_generic_queries_are_anchored_with_subject():
    plan = research_bridge.plan_research(
        "这本书怎么样",
        mode="web_only",
        selection_context={"selected_text": "《Capital in the Twenty-First Century》"},
        query_planner_adapter=lambda _payload: {
            "intent_label": "book overview",
            "needs_web": True,
            "mode": "web_only",
            "subject": {
                "name": "Capital in the Twenty-First Century",
                "aliases": ["21世纪资本论"],
                "type_hint": "book",
            },
            "queries": ["book summary", "main arguments"],
            "answer_claims_needed": ["book_overview"],
            "evidence_contract": {"must_cover_subject": True, "allow_summary_only": True},
        },
    )

    assert plan["queries"][0] == "Capital in the Twenty-First Century book summary"


def test_strict_metric_claim_requires_visible_observed_value(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "metric check",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "Example Journal", "type_hint": "journal"},
            "queries": ["Example Journal impact factor"],
            "answer_claims_needed": ["journal_identity", "impact_factor_value"],
            "claim_requirements": [{"claim_id": "impact_factor_value", "required_authority": "primary_or_index"}],
            "evidence_contract": {
                "must_cover_subject": True,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": True,
                "allow_summary_only": False,
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://webofscience.com/journal/example",
                    "subject_match": "yes",
                    "source_authority": "primary",
                    "claim_support": [
                        {"claim_id": "journal_identity", "support": "yes", "reason": "journal profile"},
                        {"claim_id": "impact_factor_value", "support": "yes", "reason": "database page"},
                    ],
                    "adoption": "partial",
                    "reason": "Primary page without visible metric value.",
                }
            ]
        },
    )
    seen = {}

    def synthesize(payload):
        seen["web_sources"] = payload["web_sources"]
        return "Example Journal 是可确认的期刊；该数值未确认 [W1]"

    result = research_bridge.research_answer(
        query="这个期刊影响因子是多少",
        mode="web_only",
        selection_context={"selected_text": "《Example Journal》"},
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Clarivate",
                        "url": "https://webofscience.com/journal/example",
                        "description": "Example Journal profile in Web of Science.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "Example Journal profile page."}]},
        synthesize_adapter=synthesize,
    )

    assert seen["web_sources"][0]["supports_claims"] == ["journal_identity"]
    assert "impact_factor_value" in seen["web_sources"][0]["unsupported_claims"]
    assert result["meta"]["web_sources"][0]["supports_claims"] == ["journal_identity"]


def test_unsupported_metric_values_are_removed_from_final_answer():
    payload = {
        "web_sources": [
            {
                "ref": "W1",
                "title": "Example Journal",
                "url": "https://example.com/journal",
                "description": "Example Journal overview.",
                "supports_claims": ["journal_identity"],
            }
        ],
        "knowledge_sources": [],
    }
    warnings = []

    answer = research_bridge._ensure_no_unsupported_metric_values(
        "Example Journal 是重要期刊 [W1]\n影响因子 12.5，JCR Q1。\n它以综述文章为主。",
        payload,
        warnings,
    )

    assert "12.5" not in answer
    assert "Q1" not in answer
    assert "它以综述文章为主" in answer
    assert warnings


def test_metric_stripping_cannot_leave_uncited_web_only_residue():
    warnings = []
    payload = {
        "mode": "web_only",
        "web_queries": ["NVIDIA market cap"],
        "rewritten_web_queries": [],
        "search_quality": {"reason": "missing_required_preferred_source", "trusted_sources": 0},
        "web_sources": [
            {
                "ref": "W1",
                "title": "NVIDIA company profile",
                "url": "https://example.com/nvidia",
                "description": "NVIDIA company profile.",
                "supports_claims": ["company_identity"],
            }
        ],
        "knowledge_sources": [],
    }

    answer = research_bridge._ensure_no_unsupported_metric_values(
        "英伟达市值约为 4.5 万亿美元 [W1]\n\n以下是几个理解该数值的要点：\n\n* 公司确认：英伟达是半导体公司。",
        payload,
        warnings,
    )
    answer, cited_knowledge_sources, cited_web_sources = research_bridge._normalize_cited_sources_and_answer(
        answer,
        [],
        payload["web_sources"],
    )
    answer, cited_knowledge_sources, cited_web_sources = research_bridge._ensure_source_backed_final_answer(
        answer,
        payload,
        cited_knowledge_sources,
        cited_web_sources,
        warnings,
    )

    assert "没有找到足够可靠" in answer
    assert cited_knowledge_sources == []
    assert cited_web_sources == []
    assert any("最终回答缺少可引用来源" in warning for warning in warnings)


def test_supported_metric_values_are_preserved_in_final_answer():
    payload = {
        "web_sources": [
            {
                "ref": "W1",
                "title": "NVIDIA Market Cap",
                "url": "https://companiesmarketcap.com/nvidia/marketcap/",
                "excerpt": "NVIDIA market cap is $4.5 trillion.",
                "supports_claims": ["current_market_cap"],
            }
        ],
        "knowledge_sources": [],
    }

    answer = research_bridge._ensure_no_unsupported_metric_values(
        "英伟达市值约为 $4.5 trillion [W1]",
        payload,
        [],
    )

    assert "$4.5 trillion" in answer


def test_weak_secondary_authority_is_rejected_when_contract_prefers_authoritative_sources(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "company overview",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "ExampleCo", "aliases": [], "type_hint": "company"},
            "queries": ["ExampleCo overview"],
            "answer_claims_needed": ["identity"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://example.com/exampleco",
                    "subject_match": "yes",
                    "source_authority": "weak_secondary",
                    "claim_support": [{"claim_id": "identity", "support": "yes", "reason": "blog recap"}],
                    "adoption": "adopt",
                    "reason": "critic mistake",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="ExampleCo 是什么公司",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {"web": [{"title": "ExampleCo overview", "url": "https://example.com/exampleco", "description": "blog"}]},
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "ExampleCo blog recap."}]},
        synthesize_adapter=lambda payload: "ExampleCo 是一家公司 [W1]",
    )

    assert result["meta"]["used_web"] is False
    assert result["meta"]["web_sources"] == []
    assert result["meta"]["rejected_sources"][0]["quality_reason"] == "source_authority_below_contract"
    assert "ExampleCo 是一家公司" not in result["chat_preview"]


def test_short_cjk_person_title_conflict_is_passed_as_critic_hint_only(monkeypatch):
    seen = {}

    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "person profile",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "段永平", "aliases": ["Duan Yongping"], "type_hint": "person"},
            "queries": ["段永平 简介"],
            "answer_claims_needed": ["subject_overview"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "allow_summary_only": True,
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: seen.__setitem__("critic_sources", payload["sources"])
        or {
            "decisions": [
                {
                    "source_id": "https://baike.baidu.com/item/杜永平/10133922",
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "claim_support": [{"claim_id": "subject_overview", "support": "yes", "reason": "profile"}],
                    "adoption": "adopt",
                    "reason": "critic confused near-name person",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="段永平是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "杜永平",
                        "url": "https://baike.baidu.com/item/杜永平/10133922",
                        "description": "杜永平是画家。",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "杜永平是画家。"}]},
        synthesize_adapter=lambda payload: seen.__setitem__(
            "synthesis_titles",
            [source["title"] for source in payload["web_sources"]],
        )
        or "段永平是企业家 [W1]",
    )

    assert seen["critic_sources"][0]["title"] == "杜永平"
    assert "subject_title_conflict" in seen["critic_sources"][0]["retrieval_hints"]
    assert seen["synthesis_titles"] == ["杜永平"]
    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["title"] == "杜永平"
    assert not any(
        source.get("quality_reason") == "source_quality_below_contract"
        for source in result["meta"]["rejected_sources"]
    )


def test_redundant_same_domain_adopted_sources_are_pruned():
    kept, rejected = research_bridge._prune_redundant_same_domain_adopted_sources(
        [
            {
                "ref": "W1",
                "title": "Direct definition",
                "url": "https://baike.baidu.com/item/volatility",
                "domain": "baike.baidu.com",
                "supports_claims": ["answer_user_question"],
            },
            {
                "ref": "W2",
                "title": "Duplicate translation",
                "url": "https://baike.baidu.com/item/volatility-translation",
                "domain": "baike.baidu.com",
                "supports_claims": ["answer_user_question"],
            },
            {
                "ref": "W3",
                "title": "Another domain",
                "url": "https://investopedia.com/volatility",
                "domain": "investopedia.com",
                "supports_claims": ["answer_user_question"],
            },
        ]
    )

    assert [source["ref"] for source in kept] == ["W1", "W3"]
    assert rejected[0]["quality_reason"] == "redundant_same_domain_evidence"


def test_interpretive_web_only_can_adopt_topical_commentary_without_generic_homepage(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "interpretive explanation",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "改稻为桑", "aliases": [], "type_hint": "drama_policy"},
            "queries": ["改稻为桑 为什么是死局", "大明王朝1566 改稻为桑 死局 解析"],
            "answer_claims_needed": [],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 2,
                "prefer_authoritative_sources": False,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {
                "prefer_primary_sources": False,
                "preferred_source_kinds": ["encyclopedia", "reputable_media"],
                "preferred_domains": ["baike.baidu.com", "people.com.cn"],
                "avoid_topics": [],
            },
        },
    )
    seen = {}

    def critic(payload):
        seen.setdefault("critic_sources", []).extend(payload["sources"])
        return {
            "decisions": [
                {
                    "source_id": "https://history.sohu.com/a/example",
                    "subject_match": "yes",
                    "source_authority": "commentary_secondary",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "directly explains the deadlock"}],
                    "adoption": "partial",
                    "reason": "Topical commentary for a low-risk interpretive question.",
                },
                {
                    "source_id": "https://www.zhihu.com/question/example",
                    "subject_match": "yes",
                    "source_authority": "commentary_secondary",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "directly discusses the same interpretation"}],
                    "adoption": "partial",
                    "reason": "Topical commentary for a low-risk interpretive question.",
                },
            ]
        }

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", critic)

    result = research_bridge.research_answer(
        query="为什么说改稻为桑是个死局",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {"title": "百度百科_全球领先的中文百科全书", "url": "https://baike.baidu.com/", "description": "百度百科首页"},
                    {"title": "人民网_网上的人民日报", "url": "https://people.com.cn/", "description": "人民网首页"},
                    {"title": "新浪网", "url": "https://www.sina.com.cn/", "description": "新浪网首页"},
                    {
                        "title": "嘉靖想敛财,严党想夺权,百姓想活命:“改稻为桑”为何是一步死棋?",
                        "url": "https://history.sohu.com/a/example",
                        "description": "大明王朝1566 改稻为桑 死局 解析。",
                    },
                    {
                        "title": "明朝嘉靖年间推行“改稻为桑”为什么最后成为灾难?",
                        "url": "https://www.zhihu.com/question/example",
                        "description": "改稻为桑 剧情讨论。",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [
                {"url": url, "content": "改稻为桑 死局 解析。"} for url in urls
            ]
        },
        synthesize_adapter=lambda payload: "改稻为桑的死局来自财政、民生和执行矛盾 [W1][W2]",
    )

    assert {"history.sohu.com", "zhihu.com"} <= {source["domain"] for source in seen["critic_sources"]}
    assert [source["domain"] for source in result["meta"]["web_sources"]] == ["history.sohu.com", "zhihu.com"]
    assert result["meta"]["web_sources"][0]["source_authority"] == "commentary_secondary"
    assert any("generic_homepage" in source["quality_tags"] for source in result["meta"]["rejected_sources"])
    assert "百度百科_全球领先的中文百科全书" not in result["chat_preview"]


def test_detailed_interpretive_plan_extracts_more_sources_and_preserves_roles(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "interpretive explanation",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "改稻为桑", "aliases": [], "type_hint": "drama_policy"},
            "answer_depth": "detailed",
            "queries": ["改稻为桑 为什么是死局"],
            "answer_claims_needed": [],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 3,
                "prefer_authoritative_sources": False,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    seen = {}

    def critic(payload):
        seen.setdefault("critic_source_counts", []).append(len(payload["sources"]))
        return {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_role": "interpretive_evidence",
                    "source_authority": "commentary_secondary",
                    "allowed_usage": ["interpretation", "background"],
                    "confidence": "medium",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "direct explanation"}],
                    "adoption": "partial",
                    "reason": "Low-risk interpretation source.",
                }
                for source in payload["sources"]
            ]
        }

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", critic)

    result = research_bridge.research_answer(
        query="为什么说改稻为桑是个死局",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": f"改稻为桑 死局 解析 {index}",
                        "url": f"https://www.zhihu.com/question/example-{index}",
                        "description": "改稻为桑 为什么是死局。",
                    }
                    for index in range(1, 7)
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "改稻为桑 为什么是死局。"} for url in urls]},
        synthesize_adapter=lambda payload: (
            seen.__setitem__("synthesis_source_count", len(payload["web_sources"]))
            or "改稻为桑是多重矛盾叠加的死局 [W1][W2][W3]"
        ),
    )

    assert sum(seen["critic_source_counts"]) == 6
    assert max(seen["critic_source_counts"]) <= 2
    assert seen["synthesis_source_count"] == 6
    assert result["meta"]["research_plan"]["answer_depth"] == "detailed"
    assert result["meta"]["research_trace"]["answer_depth"] == "detailed"
    assert result["meta"]["web_sources"][0]["evidence_role"] == "interpretive_evidence"
    assert result["meta"]["web_sources"][0]["allowed_usage"] == ["interpretation", "background"]
    assert result["meta"]["web_sources"][0]["confidence"] == "medium"


def test_low_risk_interpretive_quality_failure_still_reaches_critic():
    quality = {"reason": "no preferred or trusted source matched planner source preferences"}
    plan = {
        "mode": "web_only",
        "risk_level": "low",
        "freshness_sensitive": False,
        "answer_depth": "detailed",
        "evidence_contract": {"must_use_primary_source": False},
        "source_preferences": {"preferred_source_kinds": ["reputable_media"]},
    }

    assert research_bridge._should_let_critic_judge_quality_failure(quality, plan) is True


def test_low_risk_interpretive_summary_only_can_be_judged_by_critic(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "interpretive explanation",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "改稻为桑", "aliases": [], "type_hint": "drama_policy"},
            "answer_depth": "detailed",
            "queries": ["改稻为桑 为什么是死局"],
            "answer_claims_needed": [],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": False,
                "must_use_primary_source": False,
                "allow_summary_only": False,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": payload["sources"][0]["source_id"],
                    "subject_match": "yes",
                    "source_role": "interpretive_evidence",
                    "source_authority": "commentary_secondary",
                    "allowed_usage": ["interpretation"],
                    "confidence": "low",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "summary discusses the explanation"}],
                    "adoption": "partial",
                    "reason": "Summary can support low-risk interpretation.",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="为什么说改稻为桑是个死局",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "改稻为桑 为什么是死局",
                        "url": "https://www.zhihu.com/question/summary-only",
                        "description": "改稻为桑 为什么是死局。",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": []},
        synthesize_adapter=lambda payload: "改稻为桑是结构性矛盾导致的死局 [W1]",
    )

    assert result["meta"]["web_sources"][0]["extract_status"] == "summary"
    assert result["meta"]["web_sources"][0]["evidence_role"] == "interpretive_evidence"
    assert not any(source.get("quality_reason") == "summary_only_disallowed" for source in result["meta"]["rejected_sources"])


def test_interpretive_critic_retries_broad_claim_when_specific_claims_all_fail(monkeypatch):
    calls = []

    def critic(payload):
        calls.append(payload["answer_claims_needed"])
        if payload["answer_claims_needed"] == ["answer_user_question"]:
            return {
                "decisions": [
                    {
                        "source_id": payload["sources"][0]["source_id"],
                        "subject_match": "yes",
                        "source_role": "interpretive_evidence",
                        "source_authority": "commentary_secondary",
                        "allowed_usage": ["interpretation"],
                        "confidence": "medium",
                        "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "supports broad explanation"}],
                        "adoption": "partial",
                        "reason": "Broad explanation is supported.",
                    }
                ]
            }
        return {
            "decisions": [
                {
                    "source_id": payload["sources"][0]["source_id"],
                    "subject_match": "yes",
                    "source_role": "clue",
                    "source_authority": "commentary_secondary",
                    "allowed_usage": ["interpretation"],
                    "confidence": "low",
                    "claim_support": [{"claim_id": claim, "support": "uncertain", "reason": "too specific"} for claim in payload["answer_claims_needed"]],
                    "adoption": "clue",
                    "reason": "Specific claims are not directly supported.",
                }
            ]
        }

    sources = [
        {
            "ref": "W1",
            "url": "https://www.zhihu.com/question/retry",
            "id": "https://www.zhihu.com/question/retry",
            "title": "改稻为桑 为什么是死局",
            "domain": "zhihu.com",
            "excerpt": "改稻为桑 为什么是死局。",
            "description": "改稻为桑 为什么是死局。",
            "extract_status": "summary",
            "quality_tags": ["weak_secondary_source"],
        }
    ]
    plan = {
        "mode": "web_only",
        "risk_level": "low",
        "freshness_sensitive": False,
        "answer_depth": "detailed",
        "answer_claims_needed": ["specific_detail_one", "specific_detail_two"],
        "evidence_contract": {"must_use_primary_source": False, "allow_summary_only": True},
    }
    warnings = []

    adopted, rejected = research_bridge._apply_evidence_critic(
        "为什么说改稻为桑是个死局",
        plan,
        sources,
        warnings,
        critic_adapter=critic,
    )

    assert calls == [["specific_detail_one", "specific_detail_two"], ["answer_user_question"]]
    assert adopted[0]["evidence_role"] == "interpretive_evidence"
    assert adopted[0]["supports_claims"] == ["answer_user_question"]
    assert rejected == []
    assert any("总 claim" in warning for warning in warnings)


def test_low_risk_definition_retries_broad_claim_when_first_pass_rejects_all():
    calls = []

    def critic(payload):
        calls.append((payload["answer_claims_needed"], bool(payload.get("minimal_critic"))))
        if payload.get("minimal_critic"):
            return {
                "decisions": [
                    {
                        "source_id": payload["sources"][0]["source_id"],
                        "subject_match": "yes",
                        "source_role": "supporting_evidence",
                        "source_authority": "reputable_secondary",
                        "allowed_usage": ["fact"],
                        "confidence": "medium",
                        "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "supports broad definition"}],
                        "adoption": "adopt",
                        "reason": "Broad definition is supported.",
                    }
                ]
            }
        return {
            "decisions": [
                {
                    "source_id": payload["sources"][0]["source_id"],
                    "subject_match": "yes",
                    "source_role": "reject",
                    "source_authority": "unknown",
                    "allowed_usage": [],
                    "confidence": "low",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "uncertain", "reason": "first pass missed it"}],
                    "adoption": "reject",
                    "reason": "first pass rejected all",
                }
            ]
        }

    sources = [
        {
            "ref": "W1",
            "url": "https://www.investopedia.com/terms/v/volatility.asp",
            "id": "https://www.investopedia.com/terms/v/volatility.asp",
            "title": "Volatility Definition",
            "domain": "investopedia.com",
            "excerpt": "Volatility measures how much asset returns vary.",
            "description": "Volatility measures how much asset returns vary.",
            "extract_status": "summary",
        }
    ]
    warnings = []

    adopted, rejected = research_bridge._apply_evidence_critic(
        "什么叫波动率",
        {
            "mode": "web_only",
            "risk_level": "low",
            "freshness_sensitive": False,
            "answer_depth": "normal",
            "subject": {"name": "波动率", "type_hint": "finance_term"},
            "answer_claims_needed": ["answer_user_question"],
            "evidence_contract": {"must_use_primary_source": False, "allow_summary_only": True},
        },
        sources,
        warnings,
        critic_adapter=critic,
    )

    assert calls == [(["answer_user_question"], False), (["answer_user_question"], True)]
    assert adopted[0]["supports_claims"] == ["answer_user_question"]
    assert rejected == []
    assert any("总 claim" in warning for warning in warnings)


def test_insufficient_adoption_reasks_critic_for_remaining_independent_sources(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "interpretive explanation",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "改稻为桑", "aliases": [], "type_hint": "interpretive_topic"},
            "queries": ["改稻为桑 为什么是死局"],
            "answer_claims_needed": ["interpretive_explanation"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 2,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    calls = []

    def critic(payload):
        calls.append(payload)
        if payload.get("minimum_source_repair"):
            return {
                "decisions": [
                    {
                        "source_id": "https://analysis-two.example/topic",
                        "subject_match": "yes",
                        "source_authority": "commentary_secondary",
                        "source_role": "interpretive_evidence",
                        "allowed_usage": ["interpretation"],
                        "confidence": "medium",
                        "claim_support": [{"claim_id": "interpretive_explanation", "support": "yes", "reason": "direct explanation"}],
                        "adoption": "partial",
                        "reason": "second independent explanation",
                    }
                ]
            }
        return {
            "decisions": [
                {
                    "source_id": "https://analysis-one.example/topic",
                    "subject_match": "yes",
                    "source_authority": "commentary_secondary",
                    "source_role": "interpretive_evidence",
                    "allowed_usage": ["interpretation"],
                    "confidence": "medium",
                    "claim_support": [{"claim_id": "interpretive_explanation", "support": "yes", "reason": "direct explanation"}],
                    "adoption": "partial",
                    "reason": "first explanation",
                },
                {
                    "source_id": "https://analysis-two.example/topic",
                    "subject_match": "yes",
                    "source_authority": "commentary_secondary",
                    "source_role": "clue",
                    "allowed_usage": ["background"],
                    "confidence": "medium",
                    "claim_support": [{"claim_id": "interpretive_explanation", "support": "uncertain", "reason": "needs second pass"}],
                    "adoption": "clue",
                    "reason": "not chosen on first pass",
                },
            ]
        }

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", critic)

    result = research_bridge.research_answer(
        query="为什么说改稻为桑是个死局",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "改稻为桑分析一",
                        "url": "https://analysis-one.example/topic",
                        "description": "改稻为桑 为什么是死局。",
                    },
                    {
                        "title": "改稻为桑分析二",
                        "url": "https://analysis-two.example/topic",
                        "description": "改稻为桑 为什么是死局。",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "改稻为桑 为什么是死局。"} for url in urls]},
        synthesize_adapter=lambda payload: "改稻为桑是多重矛盾叠加的死局 [W1][W2]",
    )

    assert len(calls) == 2
    assert calls[1]["minimum_source_repair"]["minimum_independent_sources"] == 2
    assert [source["domain"] for source in result["meta"]["web_sources"]] == [
        "analysis-one.example",
        "analysis-two.example",
    ]
    assert result["meta"]["search_quality"]["evidence_status"] != "limited"
    assert "复核剩余候选" in " ".join(result["warnings"])


def test_evidence_critic_timeout_retries_sources_in_smaller_batches(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "interpretive explanation",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "改稻为桑", "aliases": [], "type_hint": "interpretive_topic"},
            "queries": ["改稻为桑 为什么是死局"],
            "answer_claims_needed": ["interpretive_explanation"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 2,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    calls = []

    def critic(payload):
        calls.append([source["source_id"] for source in payload["sources"]])
        if len(payload["sources"]) > 2:
            raise TimeoutError("critic request timed out")
        return {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "commentary_secondary",
                    "source_role": "interpretive_evidence",
                    "allowed_usage": ["interpretation"],
                    "confidence": "medium",
                    "claim_support": [{"claim_id": "interpretive_explanation", "support": "yes", "reason": "direct explanation"}],
                    "adoption": "partial",
                    "reason": "batch source supports explanation",
                }
                for source in payload["sources"]
            ]
        }

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", critic)

    result = research_bridge.research_answer(
        query="为什么说改稻为桑是个死局",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": f"改稻为桑分析{index}",
                        "url": f"https://analysis-{index}.example/topic",
                        "description": "改稻为桑 为什么是死局。",
                    }
                    for index in range(1, 6)
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "改稻为桑 为什么是死局。"} for url in urls]},
        synthesize_adapter=lambda payload: "改稻为桑是多重矛盾叠加的死局 [W1][W2]",
    )

    assert calls
    assert all(len(call) <= 2 for call in calls)
    assert len(result["meta"]["web_sources"]) >= 2
    assert "分批" in " ".join(result["warnings"])


def test_minimal_evidence_critic_timeout_retries_with_minimal_batches_only():
    calls = []

    def critic(payload):
        calls.append(payload)
        assert payload.get("minimal_critic") is True
        if len(payload["sources"]) > 2:
            raise TimeoutError("minimal critic request timed out")
        return {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "commentary_secondary",
                    "source_role": "interpretive_evidence",
                    "allowed_usage": ["interpretation"],
                    "confidence": "medium",
                    "claim_support": [{"claim_id": "interpretive_explanation", "support": "yes", "reason": "direct explanation"}],
                    "adoption": "partial",
                    "reason": "minimal batch source supports explanation",
                }
                for source in payload["sources"]
            ]
        }

    sources = [
        {
            "id": f"https://analysis-{index}.example/topic",
            "url": f"https://analysis-{index}.example/topic",
            "domain": f"analysis-{index}.example",
            "title": f"改稻为桑分析{index}",
            "excerpt": "改稻为桑 为什么是死局。",
            "extract_status": "summary",
        }
        for index in range(1, 6)
    ]
    warnings = []
    adopted, rejected = research_bridge._apply_evidence_critic(
        "为什么说改稻为桑是个死局",
        {
            "mode": "web_only",
            "answer_depth": "detailed",
            "subject": {"name": "改稻为桑"},
            "answer_claims_needed": ["interpretive_explanation"],
            "evidence_contract": {"allow_summary_only": True, "minimum_independent_sources": 2},
            "source_preferences": {"prefer_primary_sources": False},
        },
        sources,
        warnings,
        critic_adapter=critic,
    )

    assert calls
    assert all(len(call["sources"]) <= 2 for call in calls)
    assert all(call.get("minimal_critic") is True for call in calls)
    assert len(adopted) >= 2
    assert rejected == []
    assert "轻量分批" in " ".join(warnings)


def test_many_non_minimal_sources_use_full_batches_without_single_large_critic():
    calls = []

    def critic(payload):
        calls.append(payload)
        assert len(payload["sources"]) <= 2
        return {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "source_role": "supporting_evidence",
                    "allowed_usage": ["fact"],
                    "confidence": "medium",
                    "supports_claims": ["answer_user_question"],
                    "adoption": "adopt",
                    "reason": "source supports the definition",
                }
                for source in payload["sources"]
            ]
        }

    sources = [
        {
            "id": f"https://example.com/source-{index}",
            "url": f"https://example.com/source-{index}",
            "domain": "example.com",
            "title": f"Definition source {index}",
            "excerpt": "This source explains the concept.",
            "extract_status": "full",
        }
        for index in range(1, 5)
    ]

    adopted, rejected = research_bridge._apply_evidence_critic(
        "什么叫波动率",
        {
            "mode": "web_only",
            "risk_level": "low",
            "freshness_sensitive": False,
            "answer_depth": "normal",
            "subject": {"name": "波动率", "type_hint": "finance_term"},
            "answer_claims_needed": ["answer_user_question"],
            "evidence_contract": {"allow_summary_only": True, "must_use_primary_source": False},
        },
        sources,
        [],
        critic_adapter=critic,
    )

    assert calls
    assert all(len(call["sources"]) <= 2 for call in calls)
    assert len(adopted) == 4
    assert rejected == []


def test_empty_evidence_critic_retries_with_minimal_prompt():
    calls = []

    def critic(payload):
        calls.append(payload)
        if payload.get("minimal_critic"):
            return {
                "decisions": [
                    {
                        "source_id": "https://example.com/volatility",
                        "subject_match": "yes",
                        "source_authority": "reputable_secondary",
                        "source_role": "supporting_evidence",
                        "allowed_usage": ["fact"],
                        "confidence": "medium",
                        "claim_support": [{"claim_id": "definition", "support": "yes", "reason": "direct definition"}],
                        "adoption": "adopt",
                        "reason": "direct definition",
                    }
                ]
            }
        return {"decisions": []}

    warnings = []
    adopted, rejected = research_bridge._apply_evidence_critic(
        "什么叫波动率",
        {
            "subject": {"name": "波动率", "aliases": ["volatility"], "type_hint": "finance_term"},
            "answer_claims_needed": ["definition"],
            "evidence_contract": {"must_cover_subject": True, "minimum_independent_sources": 1, "allow_summary_only": True},
        },
        [
            {
                "ref": "W1",
                "title": "Volatility definition",
                "url": "https://example.com/volatility",
                "domain": "example.com",
                "excerpt": "Volatility measures how much returns vary.",
                "extract_status": "summary",
            }
        ],
        warnings,
        critic_adapter=critic,
    )

    assert [source["url"] for source in adopted] == ["https://example.com/volatility"]
    assert rejected == []
    assert any(call.get("minimal_critic") for call in calls)
    assert any("极简裁判" in warning for warning in warnings)


def test_empty_evidence_critic_minimal_retry_batches_multiple_sources():
    calls = []

    def critic(payload):
        calls.append(payload)
        if not payload.get("minimal_critic"):
            return {"decisions": []}
        return {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "commentary_secondary",
                    "source_role": "interpretive_evidence",
                    "allowed_usage": ["interpretation"],
                    "confidence": "medium",
                    "claim_support": [{"claim_id": "interpretive_explanation", "support": "yes", "reason": "direct explanation"}],
                    "adoption": "partial",
                    "reason": "minimal batch accepts topical source",
                }
                for source in payload["sources"]
            ]
        }

    sources = [
        {
            "ref": f"W{index}",
            "title": f"改稻为桑分析{index}",
            "url": f"https://analysis-{index}.example/topic",
            "domain": f"analysis-{index}.example",
            "excerpt": "改稻为桑为什么是死局的解释。",
            "extract_status": "full",
        }
        for index in range(1, 5)
    ]

    adopted, rejected = research_bridge._apply_evidence_critic(
        "为什么说改稻为桑是个死局",
        {
            "mode": "web_only",
            "risk_level": "low",
            "answer_depth": "normal",
            "subject": {"name": "改稻为桑", "aliases": [], "type_hint": "interpretive_topic"},
            "answer_claims_needed": ["interpretive_explanation"],
            "evidence_contract": {"must_cover_subject": True, "minimum_independent_sources": 2, "allow_summary_only": True},
        },
        sources,
        [],
        critic_adapter=critic,
    )

    minimal_calls = [call for call in calls if call.get("minimal_critic")]
    assert minimal_calls
    assert all(len(call["sources"]) <= 2 for call in minimal_calls)
    assert len(adopted) == 4
    assert rejected == []


def test_missing_claim_coverage_triggers_recritic_for_unadopted_candidates(monkeypatch):
    monkeypatch.setattr(research_bridge, "_research_budget_available", lambda *_args: True)
    calls = []

    def critic(payload):
        calls.append(payload)
        if payload.get("minimum_source_repair"):
            assert payload["minimum_source_repair"]["missing_claims"] == ["identity", "founder"]
            return {
                "decisions": [
                    {
                        "source_id": "https://baike.baidu.com/item/example-person",
                        "subject_match": "yes",
                        "source_authority": "reputable_secondary",
                        "source_role": "supporting_evidence",
                        "claim_support": [
                            {"claim_id": "identity", "support": "yes", "reason": "profile"},
                            {"claim_id": "founder", "support": "yes", "reason": "profile"},
                        ],
                        "adoption": "adopt",
                        "reason": "profile covers missing claims",
                    }
                ]
            }
        return {
            "decisions": [
                {
                    "source_id": "https://finance.example/person-investing",
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "source_role": "supporting_evidence",
                    "claim_support": [
                        {"claim_id": "investments", "support": "yes", "reason": "investment article"}
                    ],
                    "adoption": "adopt",
                    "reason": "investment article",
                }
            ]
        }

    adopted, rejected = research_bridge._apply_evidence_critic(
        "Example Person 是谁",
        {
            "mode": "web_only",
            "risk_level": "low",
            "freshness_sensitive": False,
            "subject": {"name": "Example Person", "aliases": [], "type_hint": "person"},
            "answer_claims_needed": ["identity", "founder", "investments"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "must_use_primary_source": False,
                "allow_summary_only": True,
            },
        },
        [
            {
                "ref": "W1",
                "title": "Investment profile",
                "url": "https://finance.example/person-investing",
                "domain": "finance.example",
                "excerpt": "Investment article.",
                "extract_status": "full",
            },
            {
                "ref": "W2",
                "title": "Example Person",
                "url": "https://baike.baidu.com/item/example-person",
                "domain": "baike.baidu.com",
                "excerpt": "Person profile.",
                "extract_status": "summary",
            },
        ],
        [],
        critic_adapter=critic,
    )

    assert len(calls) == 2
    assert [source["domain"] for source in adopted] == ["finance.example", "baike.baidu.com"]
    assert set(research_bridge._supported_claims(adopted)) == {"identity", "founder", "investments"}
    assert rejected == []


def test_source_kind_direct_probe_skips_when_search_already_has_enough_kind_sources(monkeypatch):
    calls = []
    monkeypatch.setattr(
        research_bridge,
        "_fetch_url_summary",
        lambda url: calls.append(url) or {"title": "should not be fetched", "url": url, "description": ""},
    )

    result = research_bridge._direct_source_kind_candidates(
        "volatility definition finance",
        {
            "subject": {"name": "volatility", "aliases": [], "type_hint": "finance_term"},
            "source_preferences": {"preferred_source_kinds": ["finance_glossary"]},
        },
        existing_results=[
            {
                "title": "Volatility",
                "url": "https://www.investopedia.com/terms/v/volatility.asp",
                "description": "definition",
            },
            {
                "title": "Volatility",
                "url": "https://corporatefinanceinstitute.com/resources/capital-markets/volatility-vol/",
                "description": "definition",
            },
        ],
    )

    assert result == []
    assert calls == []


def test_evidence_critic_failure_rejects_uncertified_web_sources(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "current valuation",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
            "queries": ["OpenAI latest valuation official"],
            "answer_claims_needed": ["current_valuation"],
            "claim_requirements": [{"claim_id": "current_valuation", "required_authority": "primary_or_index"}],
            "risk_level": "high",
            "freshness_sensitive": True,
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": True,
                "allow_summary_only": False,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: "not json",
    )

    result = research_bridge.research_answer(
        query="OpenAI 最新估值是多少",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {"web": [{"title": "OpenAI valuation", "url": "https://example.com/openai", "description": "OpenAI valuation"}]},
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "OpenAI valuation."}]},
        synthesize_adapter=lambda payload: "不应采用未裁判来源 [W1]",
    )

    assert result["meta"]["used_web"] is False
    assert result["meta"]["web_sources"] == []
    assert result["meta"]["rejected_sources"][0]["quality_reason"] == "evidence_critic_failed"
    assert "不应采用未裁判来源" not in result["chat_preview"]
    assert any("证据裁判失败" in warning for warning in result["warnings"])


def test_empty_evidence_critic_limited_adopts_low_risk_overview_source(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "company overview",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
            "queries": ["OpenAI official about"],
            "answer_claims_needed": ["identity"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {
                "preferred_domains": ["openai.com"],
                "preferred_source_kinds": ["official"],
            },
        },
    )
    monkeypatch.setattr(research_bridge, "_default_evidence_critic", lambda payload: {"decisions": []})

    result = research_bridge.research_answer(
        query="OpenAI 是什么",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "OpenAI",
                        "url": "https://openai.com/about/",
                        "description": "OpenAI is an AI research and deployment company.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": urls[0],
                    "content": "OpenAI is an AI research and deployment company.",
                }
            ]
        },
        synthesize_adapter=lambda payload: "OpenAI 是一家人工智能研究和部署公司 [W1]",
    )

    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["quality_reason"] == "limited_summary_after_uncertified_critic"
    assert result["meta"]["web_sources"][0]["source_authority"] == "official"
    assert "OpenAI 是一家人工智能研究和部署公司" in result["chat_preview"]
    assert "有限采用直接匹配来源" in " ".join(result["warnings"])


def test_fallback_plan_empty_critic_limited_adopts_direct_overview_source(monkeypatch):
    monkeypatch.setattr(research_bridge, "_default_query_planner", lambda payload: [])
    monkeypatch.setattr(research_bridge, "_default_evidence_critic", lambda payload: {"decisions": []})

    result = research_bridge.research_answer(
        query="OpenAI 是什么？请用联网证据简短回答，并保留引用编号。",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "openai公司是什么",
                        "url": "https://aiqicha.baidu.com/details/ugknowledge?id=266c965280acc244633d0593fc80ff2a",
                        "description": "openai公司是什么",
                    },
                    {
                        "title": "OpenAI简介-OpenAI官网-出海网",
                        "url": "https://www.chwang.com/baike/openai",
                        "description": "OpenAI 是一家人工智能研究和部署公司。",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": url,
                    "content": "OpenAI 是一家人工智能研究和部署公司。"
                    if "chwang.com" in url
                    else "",
                }
                for url in urls
            ]
        },
        synthesize_adapter=lambda payload: "OpenAI 是一家人工智能研究和部署公司 [W1]",
    )

    assert result["meta"]["research_plan"]["plan_status"] == "fallback"
    assert result["meta"]["research_plan"]["subject"]["name"] == "OpenAI"
    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["quality_reason"] == "limited_summary_after_uncertified_critic"
    assert result["meta"]["web_sources"][0]["source_authority"] == "reputable_secondary"
    assert "有限采用直接匹配来源" in " ".join(result["warnings"])


def test_low_risk_overview_critic_failure_limited_adopts_direct_source():
    warnings = []
    from bridge.research.timing import _empty_stage_timings

    stage_timings = _empty_stage_timings()

    def broken_critic(payload):
        time.sleep(0.005)
        raise ValueError("malformed critic json")

    adopted, rejected = research_bridge._apply_evidence_critic(
        "OpenAI 是什么？请用联网证据简短回答，并保留引用编号。",
        {
            "mode": "web_only",
            "plan_status": "fallback",
            "risk_level": "low",
            "freshness_sensitive": False,
            "subject": {"name": "OpenAI", "aliases": [], "type_hint": ""},
            "answer_claims_needed": [],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
            },
        },
        [
            {
                "ref": "W1",
                "title": "openai公司是什么",
                "url": "https://aiqicha.baidu.com/details/ugknowledge?id=266c965280acc244633d0593fc80ff2a",
                "domain": "aiqicha.baidu.com",
                "excerpt": "openai公司是什么",
                "extract_status": "summary",
                "quality_tags": ["low_quality_domain", "extract_failed_summary_fallback"],
            },
            {
                "ref": "W2",
                "title": "OpenAI简介-OpenAI官网-出海网",
                "url": "https://www.chwang.com/baike/openai",
                "domain": "chwang.com",
                "excerpt": "OpenAI 是一家人工智能研究和部署公司。",
                "extract_status": "full",
                "quality_tags": [],
            },
        ],
        warnings,
        critic_adapter=broken_critic,
        stage_timings=stage_timings,
    )

    assert [source["domain"] for source in adopted] == ["chwang.com"]
    assert adopted[0]["quality_reason"] == "limited_summary_after_uncertified_critic"
    assert rejected[0]["domain"] == "aiqicha.baidu.com"
    assert "有限采用直接匹配来源" in " ".join(warnings)
    assert stage_timings["initial_critic_ms"] > 0
    assert stage_timings["minimal_critic_ms"] > 0
    assert stage_timings["limited_summary_adoption_ms"] >= 0


def test_empty_evidence_critic_decisions_reject_uncertified_web_sources(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "official fact",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "Federal Reserve Chair", "aliases": [], "type_hint": "current_role"},
            "queries": ["Federal Reserve Chair current"],
            "answer_claims_needed": ["current_chair"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    monkeypatch.setattr(research_bridge, "_default_evidence_critic", lambda payload: {"decisions": []})

    result = research_bridge.research_answer(
        query="美联储主席是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Federal Reserve Board - Home",
                        "url": "https://www.federalreserve.gov/",
                        "description": "Federal Reserve home page.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "Federal Reserve home page."}]},
        synthesize_adapter=lambda payload: "不应采用未裁判来源 [W1]",
    )

    assert result["meta"]["used_web"] is False
    assert result["meta"]["web_sources"] == []
    assert result["meta"]["rejected_sources"][0]["quality_reason"] == "evidence_critic_empty"
    assert "不应采用未裁判来源" not in result["chat_preview"]


def test_evidence_critic_accepts_yaml_compatible_structured_output(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "official fact",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "Federal Reserve Chair", "aliases": [], "type_hint": "current_role"},
            "queries": ["Federal Reserve Chair current"],
            "answer_claims_needed": ["current_chair"],
            "claim_requirements": [{"claim_id": "current_chair", "required_authority": "primary_or_index"}],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": True,
                "allow_summary_only": False,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {
                "prefer_primary_sources": True,
                "preferred_source_kinds": ["official"],
                "preferred_domains": ["federalreserve.gov"],
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: """
decisions:
  - source_id: https://www.federalreserve.gov/aboutthefed/bios/board/default.htm
    subject_match: yes
    source_authority: primary
    claim_support:
      - claim_id: current_chair
        support: yes
        reason: official page
    adoption: adopt
    reason: official page
""",
    )

    result = research_bridge.research_answer(
        query="美联储主席是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Board Members - Federal Reserve",
                        "url": "https://www.federalreserve.gov/aboutthefed/bios/board/default.htm",
                        "description": "Chair.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "Jerome H. Powell, Chair."}]},
        synthesize_adapter=lambda payload: "美联储主席以官网为准 [W1]",
    )

    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["domain"] == "federalreserve.gov"
    assert result["meta"]["web_sources"][0]["source_authority"] == "primary"


def test_freshness_sensitive_claim_rejects_source_with_only_old_year(monkeypatch):
    monkeypatch.setenv("HERMES_CURRENT_DATE", "2026-06-21")
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "current official fact",
            "needs_web": True,
            "mode": "web_only",
            "freshness_sensitive": True,
            "subject": {"name": "Federal Reserve Chair", "aliases": [], "type_hint": "current_role"},
            "queries": ["Federal Reserve chair 2026 current"],
            "answer_claims_needed": ["current_chair"],
            "claim_requirements": [{"claim_id": "current_chair", "required_authority": "primary_or_index"}],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": True,
                "allow_summary_only": False,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {
                "prefer_primary_sources": True,
                "preferred_source_kinds": ["official"],
                "preferred_domains": ["federalreserve.gov"],
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://www.federalreserve.gov/monetarypolicy/fomcminutes20250129.htm",
                    "subject_match": "yes",
                    "source_authority": "primary",
                    "claim_support": [{"claim_id": "current_chair", "support": "yes", "reason": "2025 minutes say Chair"}],
                    "adoption": "adopt",
                    "reason": "official but old meeting minutes",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="美联储主席是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Minutes of the Federal Open Market Committee, January 29, 2025",
                        "url": "https://www.federalreserve.gov/monetarypolicy/fomcminutes20250129.htm",
                        "description": "Chair Jerome H. Powell.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "Chair Jerome H. Powell."}]},
        synthesize_adapter=lambda payload: "不应采用过期当前事实来源 [W1]",
    )

    assert result["meta"]["used_web"] is False
    assert result["meta"]["web_sources"] == []
    assert result["meta"]["rejected_sources"][0]["quality_reason"] == "stale_for_freshness_sensitive_claim"
    assert "不应采用过期当前事实来源" not in result["chat_preview"]


def test_missing_preferred_domain_adoption_warns_when_secondary_source_is_used(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "company overview",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
            "queries": ["OpenAI official about"],
            "answer_claims_needed": ["identity"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {
                "preferred_domains": ["openai.com"],
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://baike.baidu.com/item/OpenAI",
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "claim_support": [{"claim_id": "identity", "support": "yes", "reason": "profile"}],
                    "adoption": "adopt",
                    "reason": "secondary profile",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="OpenAI 是什么",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {"web": [{"title": "OpenAI", "url": "https://baike.baidu.com/item/OpenAI", "description": "profile"}]},
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "OpenAI profile."}]},
        synthesize_adapter=lambda payload: "OpenAI 是 AI 公司 [W1]",
    )

    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["domain"] == "baike.baidu.com"
    assert any("首选官方/文档来源" in warning for warning in result["warnings"])


def test_summary_only_source_cannot_be_adopted_when_contract_requires_full_text(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "metric check",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "Econometric Theory", "aliases": [], "type_hint": "academic_journal"},
            "queries": ["Econometric Theory impact factor"],
            "answer_claims_needed": ["impact factor and citation metrics"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": False,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://example.com/metric-summary",
                    "subject_match": "yes",
                    "supports_claims": ["impact factor and citation metrics"],
                    "adoption": "adopt",
                    "reason": "summary mentions metrics",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="这个期刊影响因子是多少",
        mode="web_only",
        selection_context={"selected_text": "《Econometric Theory》"},
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Econometric Theory metrics summary",
                        "url": "https://example.com/metric-summary",
                        "description": "Search summary says Econometric Theory has metrics.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": []},
        synthesize_adapter=lambda payload: (_ for _ in ()).throw(AssertionError("summary-only metric source should not synthesize")),
    )

    assert result["meta"]["web_sources"] == []
    assert result["meta"]["rejected_sources"][0]["quality_reason"] == "summary_only_disallowed"
    assert "足够可靠的公开资料" in result["chat_preview"]


def test_local_web_synthesizes_from_local_when_all_web_sources_rejected_by_critic(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_search_local",
        lambda *_args: (
            "本地资料只说明 Econometric Theory 是计量经济学方向的重要期刊，未提供最新影响因子。",
            [{"ref": "K1", "type": "knowledge", "id": "journal-1", "title": "经济学期刊分级笔记"}],
        ),
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "journal evaluation",
            "needs_web": True,
            "mode": "local_web",
            "subject": {"name": "Econometric Theory", "aliases": [], "type_hint": "academic_journal"},
            "queries": ["Econometric Theory impact factor"],
            "answer_claims_needed": ["latest impact factor", "journal reputation"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": False,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://example.com/rejected",
                    "subject_match": "no",
                    "supports_claims": [],
                    "adoption": "reject",
                    "reason": "wrong source",
                }
            ]
        },
    )

    seen = {}

    result = research_bridge.research_answer(
        query="这个期刊怎么样",
        mode="local_web",
        selection_context={"selected_text": "《Econometric Theory》"},
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Rejected metric source",
                        "url": "https://example.com/rejected",
                        "description": "Rejected source claims impact factor is 1.1.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "Rejected source claims impact factor is 1.1."}]},
        synthesize_adapter=lambda payload: (
            seen.setdefault("web_sources", payload["web_sources"])
            or seen.setdefault("local_answer", payload["local_answer"])
            or "本地资料只说明 Econometric Theory 是计量经济学方向的重要期刊，未提供最新影响因子 [K1]"
        ),
    )

    assert "1.1" not in result["chat_preview"]
    assert "本地资料只说明" in result["chat_preview"]
    assert seen["web_sources"] == []
    assert "Econometric Theory" in seen["local_answer"]
    assert result["meta"]["web_sources"] == []
    assert result["meta"]["search_quality"]["passed"] is False


def test_preferred_domain_without_subject_coverage_remains_a_critic_hint():
    ranked = research_bridge.rank_web_sources(
        "《American Economic Review》这个期刊怎么样",
        [
            {
                "title": "aeaweb official website",
                "url": "https://aeaweb.org/",
                "description": "Official website for aeaweb.",
            },
            {
                "title": "American Economic Review issue summary",
                "url": "https://mp.weixin.qq.com/s/example",
                "description": "American Economic Review 期刊目录摘要。",
            },
        ],
        source_preferences={"preferred_domains": ["aeaweb.org"], "preferred_source_kinds": ["academic"]},
    )

    quality = research_bridge.evaluate_search_quality(
        "《American Economic Review》这个期刊怎么样",
        ranked,
        source_preferences={"preferred_domains": ["aeaweb.org"], "preferred_source_kinds": ["academic"]},
        subject={"name": "American Economic Review", "aliases": ["AER"]},
        evidence_contract={"must_cover_subject": True, "prefer_authoritative_sources": True},
    )

    assert quality["passed"] is True
    assert quality["reason"] == "passed"
    # aeaweb.org (preferred domain) outranks mp.weixin.qq.com (weak_secondary)
    # due to +10 planner_preferred_domain bonus. Verify the weak_secondary
    # source is still visible in the ranked list for the critic to evaluate.
    assert any("weak_secondary_source" in (s.get("quality_tags") or []) for s in ranked)
    assert ranked[0]["domain"] == "aeaweb.org"
    assert "planner_preferred_domain" in ranked[0]["quality_tags"]


def test_low_quality_web_results_do_not_call_synthesizer():
    def bad_search(query, limit):
        return {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "短视频结果",
                        "url": "https://www.douyin.com/shipin/7606912622541178930",
                        "description": "个股短视频。",
                    }
                ]
            },
        }

    result = research_bridge.research_answer(
        query="四月份A股的走势和行情是什么",
        mode="web_only",
        search_adapter=bad_search,
        extract_adapter=lambda urls: {"results": []},
        synthesize_adapter=lambda payload: (_ for _ in ()).throw(AssertionError("should not synthesize low-quality web evidence")),
    )

    assert "没有找到足够可靠的公开资料" in result["chat_preview"]
    assert result["meta"]["web_sources"] == []
    assert result["meta"]["search_quality"]["passed"] is False


def test_local_web_uses_strong_local_evidence_when_web_only_has_weak_secondary_sources(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_search_local",
        lambda query, warnings: (
            "REStud 是经济学五大刊之一。",
            [{"ref": "K1", "type": "knowledge", "id": "journal-1", "title": "经济学顶刊有哪些"}],
        ),
    )
    seen = {}
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "weak_secondary",
                    "source_role": "clue",
                    "allowed_usage": [],
                    "confidence": "low",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "uncertain", "reason": "weak secondary discussion"}],
                    "adoption": "clue",
                    "reason": "weak secondary discussion only",
                }
                for source in payload["sources"]
            ]
        },
    )

    result = research_bridge.research_answer(
        query="这个期刊怎么样",
        mode="local_web",
        selection_context={"selected_text": "《Review of Economic Studies》"},
        search_adapter=lambda query, _limit: (
            seen.setdefault("query", query)
            and {
                "success": True,
                "data": {
                    "web": [
                        {
                            "title": "学术荐读 | 学术期刊分享",
                            "url": "https://mp.weixin.qq.com/s/example",
                            "description": "Review of Economic Studies 是经济学期刊。",
                        },
                        {
                            "title": "这个期刊怎么样",
                            "url": "https://www.zhihu.com/question/example",
                            "description": "Review of Economic Studies 投稿经验讨论。",
                        },
                    ]
                },
            }
        ),
        extract_adapter=lambda urls: {
            "results": [{"url": url, "content": "Review of Economic Studies 相关二手讨论。"} for url in urls]
        },
        synthesize_adapter=lambda payload: (
            seen.setdefault("web_sources", payload["web_sources"])
            or "REStud 是经济学领域顶级期刊之一 [K1]"
        ),
    )

    assert seen["query"] == "Review of Economic Studies 这个期刊怎么样"
    assert seen["web_sources"] == []
    assert result["chat_preview"] == "REStud 是经济学领域顶级期刊之一 [K1]"
    assert result["meta"]["web_sources"] == []
    assert result["meta"]["used_web"] is False
    assert {source["domain"] for source in result["meta"]["clue_sources"]} == {"mp.weixin.qq.com", "zhihu.com"}


def test_synthesis_prompt_uses_planner_answer_contract():
    prompt = research_bridge._build_synthesis_prompt(
        {
            "query": "Anthropic是什么东西？",
            "mode": "local_web",
            "research_plan": {
                "intent_label": "entity background",
                "selection_role": "context_only",
                "source_preferences": {"preferred_domains": ["anthropic.com"]},
                "answer_contract": {
                    "primary_answer_first": True,
                    "explain_selection_relation": True,
                    "avoid_unasked_side_topics": True,
                },
            },
            "article_context": {},
            "selection_context": {
                "selected_text": "Anthropic 的四层系统：从混乱数据到可信答案",
                "surrounding_after": "四层系统包括数据基础、可信来源、skills 和验证。",
            },
            "local_answer": "",
            "knowledge_sources": [],
            "web_sources": [
                {
                    "ref": "W1",
                    "title": "Introducing Claude",
                    "url": "https://www.anthropic.com/claude",
                    "excerpt": "Claude is Anthropic's family of AI assistants.",
                }
            ],
        }
    )

    assert "Planner 标签：entity background" in prompt
    assert "回答合同" in prompt
    assert "先直接回答用户的主问题" in prompt
    assert "说明选区和主问题的关系" in prompt


def test_synthesis_prompt_keeps_search_process_out_of_answer_body():
    prompt = research_bridge._build_synthesis_prompt(
        {
            "query": "Hermes 智能体是什么",
            "mode": "web_only",
            "research_plan": {
                "path": "light_web_qa",
                "answer_depth": "normal",
            },
            "article_context": {},
            "selection_context": {},
            "local_answer": "",
            "knowledge_sources": [],
            "web_sources": [
                {
                    "ref": "W1",
                    "title": "Hermes 部署教程",
                    "url": "https://blog.csdn.net/example",
                    "excerpt": "Hermes 是一款本地 AI 智能代理。",
                    "extract_status": "summary",
                }
            ],
            "search_quality": {"evidence_status": "limited"},
        }
    )

    assert "主答案不是检索报告" in prompt
    assert "从搜索结果看" in prompt
    assert "不要写免责声明" in prompt
    assert "只通过谨慎措辞体现边界" in prompt


def test_provider_configuration_error_is_user_friendly():
    warnings = []

    result = research_bridge.collect_web_search_results(
        ["test query"],
        search_adapter=lambda *_args: {"success": False, "error": "No web search provider configured. Run `hermes tools` to set one up."},
        warnings=warnings,
    )

    assert result == []
    assert "当前没有可用的联网搜索后端" in warnings[0]
    assert "No web search provider configured" not in warnings[0]


def test_web_only_search_timeout_does_not_synthesize_unsourced_answer():
    result = research_bridge.research_answer(
        query="芯片半导体的对口专业有哪些",
        mode="web_only",
        search_adapter=lambda *_args: (_ for _ in ()).throw(RuntimeError("search timeout")),
        synthesize_adapter=lambda payload: "芯片半导体相关专业主要包括微电子科学与工程、集成电路设计与集成系统、电子科学与技术等。",
    )

    assert "微电子科学与工程" not in result["chat_preview"]
    assert "没有找到足够可靠的公开资料" in result["chat_preview"]
    assert result["meta"]["used_web"] is False
    assert result["warnings"]


def test_invalid_model_citations_fall_back_to_available_sources():
    result = research_bridge.research_answer(
        query="苹果现在的市值多少",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {"web": [{"title": "Apple market cap", "url": "https://companiesmarketcap.com/apple/marketcap/", "description": "摘要"}]},
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "网页正文"}]},
        synthesize_adapter=lambda payload: "模型错误引用 [W6]",
    )

    assert "W6" not in result["chat_preview"]
    assert "[W1]" in result["chat_preview"]
    assert result["meta"]["web_sources"][0]["ref"] == "W1"
    assert any("未提供的来源编号" in warning for warning in result["warnings"])


def test_fallback_answer_uses_evidence_excerpt_when_claims_are_supported():
    answer = research_bridge._fallback_answer(
        {
            "web_sources": [
                {
                    "ref": "W1",
                    "title": "https://openai.com/about/",
                    "url": "https://openai.com/about/",
                    "description": "OpenAI is an AI research and deployment company.",
                    "excerpt": "OpenAI builds AI systems and products including ChatGPT.",
                    "claim_support": [{"claim_id": "subject_overview", "support": "yes", "reason": "official about page"}],
                }
            ],
            "knowledge_sources": [],
        }
    )

    assert "OpenAI builds AI systems and products including ChatGPT" in answer
    assert "https://openai.com/about/（可支持" not in answer


def test_answer_keeps_preferred_domain_source_even_if_model_skips_it(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "needs_web": True,
            "mode": "web_only",
            "queries": ["OpenAI是什么意思"],
            "source_preferences": {"preferred_domains": ["openai.com"]},
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda _payload: {
            "decisions": [
                {
                    "source_id": "https://openai.com/",
                    "subject_match": "yes",
                    "source_authority": "official",
                    "source_role": "primary_evidence",
                    "allowed_usage": ["fact", "background"],
                    "confidence": "high",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "official site"}],
                    "adoption": "adopt",
                    "reason": "official site",
                },
                {
                    "source_id": "https://example.com/openai",
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "source_role": "supporting_evidence",
                    "allowed_usage": ["background"],
                    "confidence": "medium",
                    "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "secondary explainer"}],
                    "adoption": "adopt",
                    "reason": "secondary support",
                },
            ]
        },
    )

    result = research_bridge.research_answer(
        query="OpenAI是什么意思",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "OpenAI official website",
                        "url": "https://openai.com/",
                        "description": "Official website for OpenAI.",
                    },
                    {
                        "title": "OpenAI explainer",
                        "url": "https://example.com/openai",
                        "description": "OpenAI is an AI research company.",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {"results": []},
        synthesize_adapter=lambda payload: "OpenAI 是一家人工智能研究公司 [W2]",
    )

    assert "[W1]" in result["chat_preview"]
    assert [source["domain"] for source in result["meta"]["web_sources"]] == ["openai.com", "example.com"]


def test_cited_web_sources_are_renumbered_after_uncited_sources_are_filtered():
    titles = ["来源一", "来源二", "未引用来源三", "来源四", "来源五"]

    result = research_bridge.research_answer(
        query="段永平是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": title,
                        "url": f"https://example.com/{index}",
                        "description": f"{title} 段永平 摘要",
                    }
                    for index, title in enumerate(titles, 1)
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [{"url": url, "content": f"{url} 正文"} for url in urls]
        },
        synthesize_adapter=lambda payload: "段永平相关信息来自四条可用资料 [W1][W2][W4][W5]",
    )

    assert "[W5]" not in result["chat_preview"]
    assert "[W4]" in result["chat_preview"]
    assert [source["ref"] for source in result["meta"]["web_sources"]] == ["W1", "W2", "W3", "W4"]
    assert [source["title"] for source in result["meta"]["web_sources"]] == ["来源一", "来源二", "来源四", "来源五"]
    assert [source["ref"] for source in result["source_map"]] == ["W1", "W2", "W3", "W4"]
    assert [source["ref"] for source in result["meta"]["research_trace"]["web_sources"]] == ["W1", "W2", "W3", "W4"]


def test_invalid_model_citation_fallback_gives_source_backed_answer():
    result = research_bridge.research_answer(
        query="公募基金是什么意思？",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "公募基金与私募基金的区别",
                        "url": "https://example.com/fund",
                        "description": "公募基金的募集对象是广大社会公众，即社会不特定的投资者。",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": urls[0],
                    "content": "公募基金的募集对象是广大社会公众，即社会不特定的投资者。公募基金通常公开发售，并由基金管理人管理。",
                }
            ]
        },
        synthesize_adapter=lambda payload: "公募基金是公开募集的基金 [K1]",
    )

    assert "没有进行完整 LLM 综合" not in result["chat_preview"]
    assert "公募基金的募集对象是广大社会公众" in result["chat_preview"]
    assert "[W1]" in result["chat_preview"]
    assert "[K1]" not in result["chat_preview"]


def test_non_tool_source_markers_are_removed_without_losing_valid_web_refs():
    result = research_bridge.research_answer(
        query="Text-to-SQL是什么意思？",
        mode="local_web",
        article_context={
            "title": "智能问数项目",
            "body": "Text-to-SQL 在智能问数中更适合作为兜底路线。核心瓶颈是 Schema Linking。",
        },
        selection_context={"selected_text": "Text-to-SQ"},
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Text-to-SQL",
                        "url": "https://example.com/text-to-sql",
                        "description": "Text-to-SQL turns natural language questions into SQL queries.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": urls[0],
                    "content": "Text-to-SQL turns natural language questions into SQL queries.",
                }
            ]
        },
        synthesize_adapter=lambda payload: (
            "Text-to-SQL 是把自然语言问题转换成 SQL 的技术。"
            "在智能问数中，它更适合作为兜底路线 [文档目录：架构决策]。"
            "核心瓶颈是 Schema Linking [文档关键内容：Schema Linking]。"
            "通用资料也说明它用于自然语言到查询的转换 [W1]。"
        ),
    )

    assert "Text-to-SQL 是把自然语言问题转换成 SQL 的技术" in result["chat_preview"]
    assert "[W1]" in result["chat_preview"]
    assert "[文档目录：架构决策]" not in result["chat_preview"]
    assert "[文档关键内容：Schema Linking]" not in result["chat_preview"]
    assert any("非工具来源标记" in warning for warning in result["warnings"])


def test_invalid_knowledge_citation_fallback_avoids_noisy_article_body():
    noisy_article = "直播开始了。" * 200

    result = research_bridge.research_answer(
        query="什么是摩尔定律",
        mode="web_only",
        article_context={"title": "直播稿", "body": noisy_article},
        selection_context={"selected_text": "摩尔定律"},
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Moore's Law",
                        "url": "https://www.intel.com/moores-law",
                        "description": "Moore's Law observes that transistor counts on integrated circuits double about every two years.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": urls[0],
                    "content": "摩尔定律是一项经验观察：集成电路上的晶体管数量大约每两年翻一番，并带来计算能力提升和单位成本下降。",
                }
            ]
        },
        synthesize_adapter=lambda payload: "摩尔定律是芯片晶体管数量周期性翻倍的经验规律 [K1]",
    )

    assert "[K1]" not in result["chat_preview"]
    assert "[W1]" in result["chat_preview"]
    assert "摩尔定律是一项经验观察" in result["chat_preview"]
    assert "当前文章依据" not in result["chat_preview"]
    assert "直播开始了" not in result["chat_preview"]
    assert result["meta"]["web_sources"][0]["ref"] == "W1"


def test_research_result_meta_exposes_trace_for_ui():
    result = research_bridge.research_answer(
        query="查一个公开资料",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {"web": [{"title": "公开资料查询指南", "url": "https://example.com/guide/public-data", "description": "公开资料的获取方法和常见来源。"}]},
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "公开资料是指政府、机构或组织公开发布的信息资源，可通过官方网站、档案馆、公共图书馆等渠道获取。"}]},
        synthesize_adapter=lambda payload: "回答 [W1]",
    )

    trace = result["meta"]["research_trace"]
    assert trace["queries"] == ["查一个公开资料"]
    assert trace["search_quality"]["passed"] is True
    assert trace["sources"][0]["ref"] == "W1"
    assert trace["web_sources"][0]["ref"] == "W1"
    assert trace["sources"][0]["extract_status"] == "full"
    provider_status = result["meta"]["provider_status"]
    stage_timings = provider_status["stage_timings"]
    assert "search_elapsed_ms" in provider_status
    assert {
        "search_ms",
        "extract_ms",
        "critic_ms",
        "initial_critic_ms",
        "minimal_critic_ms",
        "batch_critic_ms",
        "broad_retry_ms",
        "repair_critic_ms",
        "limited_summary_adoption_ms",
        "fallback_ms",
        "synthesis_ms",
    } <= set(stage_timings)
    assert trace["stage_timings"] == stage_timings
    assert trace["provider_status"]["stage_timings"] == stage_timings


def test_research_stage_timings_split_provider_trace(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "definition",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "Example", "aliases": [], "type_hint": "concept"},
            "queries": ["Example concept"],
            "answer_claims_needed": ["answer_user_question"],
            "evidence_contract": {
                "minimum_independent_sources": 1,
                "allow_summary_only": True,
            },
        },
    )

    def fake_search(query, limit):
        time.sleep(0.005)
        return {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Example concept",
                        "url": "https://example.com/concept",
                        "description": "Example is a documented concept.",
                    }
                ]
            },
        }

    def fake_extract(urls):
        time.sleep(0.005)
        return {"results": [{"url": urls[0], "content": "Example is a documented concept with readable evidence."}]}

    def fake_critic(payload):
        time.sleep(0.005)
        return {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "source_role": "supporting_evidence",
                    "allowed_usage": ["fact"],
                    "confidence": "medium",
                    "claim_support": [
                        {"claim_id": "answer_user_question", "support": "yes", "reason": "relevant"}
                    ],
                    "adoption": "adopt",
                    "reason": "relevant source",
                }
                for source in payload["sources"]
            ]
        }

    def fake_synthesize(payload):
        time.sleep(0.005)
        return "Example 是一个有公开证据支持的概念 [W1]"

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", fake_critic)

    result = research_bridge.research_answer(
        query="Example 是什么",
        mode="web_only",
        search_adapter=fake_search,
        extract_adapter=fake_extract,
        synthesize_adapter=fake_synthesize,
    )

    stage_timings = result["meta"]["provider_status"]["stage_timings"]
    assert stage_timings["search_ms"] > 0
    assert stage_timings["extract_ms"] > 0
    assert stage_timings["critic_ms"] > 0
    assert stage_timings["initial_critic_ms"] > 0
    assert stage_timings["minimal_critic_ms"] == 0
    assert stage_timings["batch_critic_ms"] == 0
    assert stage_timings["broad_retry_ms"] == 0
    assert stage_timings["repair_critic_ms"] == 0
    assert stage_timings["limited_summary_adoption_ms"] == 0
    assert stage_timings["fallback_ms"] == 0
    assert stage_timings["synthesis_ms"] > 0
    assert result["meta"]["research_trace"]["stage_timings"] == stage_timings


def test_light_web_answer_skips_evidence_critic_and_reads_selected_candidates(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "concept explainer",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "Palantir 本体论", "type_hint": "concept"},
            "queries": ["Palantir 本体论", "Palantir ontology"],
            "answer_depth": "detailed",
            "evidence_contract": {"allow_summary_only": True, "minimum_independent_sources": 1},
        },
    )
    monkeypatch.setattr(
        "bridge.research.light_web_qa._llm_selected_refs",
        lambda *_args, **_kwargs: ["W2", "W4", "W6", "W8"],
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda _payload: (_ for _ in ()).throw(AssertionError("light path must not call evidence critic")),
    )
    seen_limits: list[int] = []

    def fake_search(query, limit):
        seen_limits.append(limit)
        return {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": f"{query} source {index}",
                        "url": f"https://example{index}.com/palantir-ontology",
                        "description": f"Palantir ontology material {index}",
                    }
                    for index in range(1, 10)
                ]
            },
        }

    def fake_extract(urls):
        return {
            "results": [
                {"url": url, "content": f"正文材料 {index}: Palantir 本体论解释"}
                for index, url in enumerate(urls, 1)
            ]
        }

    result = research_bridge.light_web_answer(
        query="什么是 Palantir 的本体论？",
        mode="web_only",
        search_adapter=fake_search,
        extract_adapter=fake_extract,
        synthesize_adapter=lambda payload: "Palantir 本体论是数据语义建模方法 [W1][W2][W3][W4]",
    )

    trace = result["meta"]["research_trace"]
    assert result["meta"]["path"] == "light_web_qa"
    assert trace["path"] == "light_web_qa"
    assert trace["legacy_research"] is False
    assert trace["fallback_search"] is False
    assert trace["stage_timings"]["critic_ms"] == 0
    assert trace["candidate_count"] <= 20
    assert trace["read_pages"] == 4
    assert [source["extract_status"] for source in result["meta"]["web_sources"]] == ["full", "full", "full", "full"]
    assert seen_limits and max(seen_limits) == 8


def test_light_web_answer_keeps_summary_sources_when_extract_fails(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "relationship explainer",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "人工智能和深度学习", "type_hint": "concept"},
            "queries": ["人工智能和深度学习的关系"],
            "evidence_contract": {"allow_summary_only": True, "minimum_independent_sources": 1},
        },
    )
    monkeypatch.setattr("bridge.research.light_web_qa._llm_selected_refs", lambda *_args, **_kwargs: ["W1", "W2", "W3"])

    result = research_bridge.light_web_answer(
        query="人工智能和深度学习的关系是什么？",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "人工智能、机器学习、深度学习的关系",
                        "url": "https://example.com/ai-ml-dl",
                        "description": "深度学习是机器学习的一种，也是人工智能的重要技术路径。",
                    },
                    {
                        "title": "AI and deep learning",
                        "url": "https://example.org/deep-learning",
                        "description": "AI is the broader field; deep learning is a machine-learning approach.",
                    },
                    {
                        "title": "深度学习简介",
                        "url": "https://example.net/dl",
                        "description": "深度学习使用多层神经网络学习表示。",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "", "error": "timeout"} for url in urls]},
        synthesize_adapter=lambda payload: "深度学习是人工智能领域内的重要技术路径 [W1][W2]",
    )

    assert result["meta"]["used_web"] is True
    assert result["meta"]["research_trace"]["path"] == "light_web_qa"
    assert result["meta"]["research_trace"]["stage_timings"]["critic_ms"] == 0
    assert result["meta"]["provider_status"]["summary_only_pages"] >= 2
    assert all(source["extract_status"] == "summary" for source in result["meta"]["web_sources"])


def test_light_web_answer_times_out_extract_and_keeps_summaries(monkeypatch):
    from bridge.research import light_web_qa

    monkeypatch.setattr(light_web_qa, "LIGHT_EXTRACT_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "fact verification",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "ExampleCo", "type_hint": "company"},
            "queries": ["ExampleCo official status"],
            "risk_level": "low",
            "freshness_sensitive": True,
            "evidence_contract": {"allow_summary_only": True, "minimum_independent_sources": 1},
        },
    )
    monkeypatch.setattr(
        light_web_qa,
        "_llm_selected_refs",
        lambda *_args, **_kwargs: {"selected_refs": ["W1"], "need_extract_refs": ["W1"]},
    )

    def slow_extract(urls):
        time.sleep(2)
        return {"results": [{"url": urls[0], "content": "late content"}]}

    result = research_bridge.light_web_answer(
        query="ExampleCo 是否仍在运营？",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "ExampleCo official update",
                        "url": "https://example.com/status",
                        "description": "ExampleCo says its product remains available for customers.",
                    }
                ]
            },
        },
        extract_adapter=slow_extract,
        synthesize_adapter=lambda payload: "ExampleCo 仍在运营 [W1]",
    )

    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["extract_status"] == "summary"
    assert "抽取失败" in "；".join(result["meta"]["research_trace"]["warnings"])


def test_light_web_strong_fact_keeps_weak_sources_as_clues(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "fact verification",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "DeepSeek training hardware", "type_hint": "claim"},
            "queries": ["DeepSeek 华为芯片 训练"],
            "risk_level": "high",
            "evidence_contract": {"allow_summary_only": False, "minimum_independent_sources": 1},
        },
    )

    extracted_urls: list[str] = []

    result = research_bridge.light_web_answer(
        query="DeepSeek 是基于华为芯片训练的吗？",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "DeepSeek 训练硬件解读",
                        "url": "https://blog.csdn.net/example",
                        "description": "一篇关于 DeepSeek 和训练硬件的二手解读。",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: extracted_urls.extend(urls) or {"results": []},
        synthesize_adapter=lambda payload: "不能确认该说法。",
    )

    assert result["meta"]["web_sources"] == []
    assert result["meta"]["clue_sources"]
    assert extracted_urls == []
    assert "没有足够可靠的公开资料" in result["chat_preview"]
    assert "已尝试的搜索词" not in result["chat_preview"]


def test_light_candidate_selection_contract_separates_extract_and_summary(monkeypatch):
    from bridge.research import light_web_qa

    monkeypatch.setattr(
        light_web_qa,
        "_llm_selected_refs",
        lambda *_args, **_kwargs: {
            "selected_refs": ["W1", "W2"],
            "need_extract_refs": ["W1"],
            "answerable_from_summary_refs": ["W2"],
        },
    )

    selection = light_web_qa._candidate_selection_plan(
        "什么是 RAG？",
        [{"ref": "W1"}, {"ref": "W2"}],
        {"evidence_contract": {"allow_summary_only": True}},
        3,
    )

    assert selection["selected_refs"] == ["W1", "W2"]
    assert selection["need_extract_refs"] == ["W1"]
    assert selection["answerable_from_summary_refs"] == ["W2"]


def test_light_extract_urls_skip_llm_extract_when_low_risk_summaries_are_enough():
    from bridge.research import light_web_qa

    selected = [
        {
            "ref": "W1",
            "url": "https://example.com/rag-1",
            "description": "RAG 是检索增强生成，将外部知识检索结果提供给语言模型，再由模型结合检索材料生成更准确、可追溯的回答，常用于问答系统、企业知识库和文档助手。",
            "source_authority": "web",
        },
        {
            "ref": "W2",
            "url": "https://example.org/rag-2",
            "description": "Retrieval augmented generation combines search or retrieval with generation so answers can use relevant documents.",
            "source_authority": "web",
        },
    ]

    urls = light_web_qa._extract_urls_for_selected(
        "什么是 RAG？",
        selected,
        {"risk_level": "low", "freshness_sensitive": False, "answer_depth": "normal"},
        {"W1", "W2"},
    )

    assert urls == []


def test_light_extract_urls_keep_llm_extract_for_fresh_fact_even_with_summaries():
    from bridge.research import light_web_qa

    selected = [
        {
            "ref": "W1",
            "url": "https://example.com/news",
            "description": "ExampleCo today announced a new product update with several details in the summary.",
            "source_authority": "web",
        }
    ]

    urls = light_web_qa._extract_urls_for_selected(
        "ExampleCo 今天发布了什么？",
        selected,
        {"risk_level": "low", "freshness_sensitive": True, "answer_depth": "normal"},
        {"W1"},
    )

    assert urls == ["https://example.com/news"]


def test_light_extract_urls_skip_extract_for_single_summary_concept_explainer():
    from bridge.research import light_web_qa

    selected = [
        {
            "ref": "W1",
            "url": "https://example.com/rag",
            "description": "RAG combines retrieval with generation so answers can use relevant documents.",
            "source_authority": "web",
        }
    ]

    urls = light_web_qa._extract_urls_for_selected(
        "结合我的笔记和公开资料解释 RAG",
        selected,
        {
            "risk_level": "low",
            "freshness_sensitive": False,
            "answer_depth": "normal",
            "evidence_contract": {"allow_summary_only": True, "minimum_independent_sources": 1},
        },
        {"W1"},
    )

    assert urls == []


def test_light_path_promotes_url_mentioned_in_search_summary(monkeypatch):
    from bridge.research import light_web_qa
    import bridge.research.search_infra as search_infra

    monkeypatch.setattr(
        search_infra,
        "_fetch_url_summary",
        lambda url: {"title": "OpenAI About", "url": url, "description": "OpenAI official about page."},
    )
    results = light_web_qa._promote_mentioned_url_candidates(
        "OpenAI 是什么",
        [
            {
                "title": "openai公司是什么",
                "url": "https://aiqicha.baidu.com/details/ugknowledge?id=266",
                "description": "联网搜索结果：https://openai.com/about（OpenAI 官网 About 页面）",
            }
        ],
        {
            "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
            "source_preferences": {},
        },
    )

    assert results[0]["url"] == "https://openai.com/about"
    assert results[0]["query"] == "direct:mentioned-url"


def test_default_web_extract_falls_back_to_local_for_search_only_backend(monkeypatch):
    fake_tools_pkg = types.ModuleType("tools")
    fake_tools = types.ModuleType("tools.web_tools")
    fake_tools._get_extract_backend = lambda: "ddgs"
    fake_tools.web_extract_tool = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("search-only backend should not be called for extract")
    )
    fake_tools_pkg.web_tools = fake_tools
    monkeypatch.setattr(
        research_bridge,
        "_local_web_extract",
        lambda urls, extract_backend="": {
            "success": True,
            "results": [{"url": urls[0], "content": "本地抽取正文"}],
            "meta": {"extract_backend": extract_backend, "fallback_extract_backend": "local_http"},
        },
    )
    monkeypatch.setattr(research_bridge, "_ensure_main_repo_on_path", lambda: None)
    monkeypatch.setitem(sys.modules, "tools", fake_tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.web_tools", fake_tools)

    result = research_bridge._default_web_extract(["https://example.com"])

    assert result["success"] is True
    assert result["results"][0]["content"] == "本地抽取正文"
    assert result["meta"]["extract_backend"] == "ddgs"
    assert result["meta"]["fallback_extract_backend"] == "local_http"


def test_default_web_extract_uses_dashscope_extract_backend(monkeypatch):
    fake_tools_pkg = types.ModuleType("tools")
    fake_tools = types.ModuleType("tools.web_tools")
    fake_tools._get_extract_backend = lambda: "dashscope"
    calls = []

    def fake_web_extract_tool(urls, **kwargs):
        calls.append((urls, kwargs))
        return {
            "success": True,
            "results": [{"url": urls[0], "content": "DashScope 抽取正文"}],
            "meta": {"extract_backend": "dashscope"},
        }

    fake_tools.web_extract_tool = fake_web_extract_tool
    fake_tools_pkg.web_tools = fake_tools
    monkeypatch.setattr(
        research_bridge,
        "_local_web_extract",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dashscope should use web_extract_tool")
        ),
    )
    monkeypatch.setattr(research_bridge, "_ensure_main_repo_on_path", lambda: None)
    monkeypatch.setitem(sys.modules, "tools", fake_tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.web_tools", fake_tools)

    result = research_bridge._default_web_extract(["https://example.com"])

    assert result["success"] is True
    assert result["results"][0]["content"] == "DashScope 抽取正文"
    assert result["meta"]["extract_backend"] == "dashscope"
    assert calls == [(["https://example.com"], {"format": "markdown", "use_llm_processing": False})]


def test_default_web_extract_times_out_provider_and_allows_summary_fallback(monkeypatch):
    from bridge.research import adapters

    fake_tools_pkg = types.ModuleType("tools")
    fake_tools = types.ModuleType("tools.web_tools")
    fake_tools._get_extract_backend = lambda: "dashscope"

    def slow_web_extract_tool(urls, **kwargs):
        time.sleep(2)
        return {"success": True, "results": [{"url": urls[0], "content": "late content"}]}

    fake_tools.web_extract_tool = slow_web_extract_tool
    fake_tools_pkg.web_tools = fake_tools
    monkeypatch.setattr(adapters, "EXTRACT_STAGE_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(research_bridge, "_ensure_main_repo_on_path", lambda: None)
    monkeypatch.setitem(sys.modules, "tools", fake_tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.web_tools", fake_tools)

    result = research_bridge._default_web_extract(["https://example.com/slow"])

    assert result["success"] is True
    assert result["results"][0]["url"] == "https://example.com/slow"
    assert result["results"][0]["content"] == ""
    assert "timed out" in result["results"][0]["error"]
    assert result["meta"]["extract_backend"] == "dashscope"
    assert result["meta"]["allow_summary_fallback"] is True


def test_default_web_extract_falls_back_to_local_for_missing_extract_backend(monkeypatch):
    fake_tools_pkg = types.ModuleType("tools")
    fake_tools = types.ModuleType("tools.web_tools")
    fake_tools._get_extract_backend = lambda: ""
    fake_tools.web_extract_tool = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("missing extract backend should not call web_extract_tool")
    )
    fake_tools_pkg.web_tools = fake_tools
    monkeypatch.setattr(
        research_bridge,
        "_local_web_extract",
        lambda urls, extract_backend="": {
            "success": True,
            "results": [{"url": urls[0], "content": "本地抽取正文"}],
            "meta": {"extract_backend": extract_backend, "fallback_extract_backend": "local_http"},
        },
    )
    monkeypatch.setattr(research_bridge, "_ensure_main_repo_on_path", lambda: None)
    monkeypatch.setitem(sys.modules, "tools", fake_tools_pkg)
    monkeypatch.setitem(sys.modules, "tools.web_tools", fake_tools)

    result = research_bridge._default_web_extract(["https://example.com"])

    assert result["success"] is True
    assert result["results"][0]["content"] == "本地抽取正文"
    assert result["meta"]["extract_backend"] == ""
    assert result["meta"]["fallback_extract_backend"] == "local_http"


def test_failed_web_extract_source_is_not_used_as_summary_evidence():
    sources = research_bridge.extract_ranked_web_sources(
        [
            {
                "title": "Broken official page",
                "url": "https://example.com/missing",
                "description": "Generated search summary should not be trusted after extraction failure.",
            },
            {
                "title": "Working page",
                "url": "https://example.com/working",
                "description": "Fallback summary for working page.",
            },
        ],
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": urls[0],
                    "content": "",
                    "error": "404 Not Found",
                },
                {
                    "url": urls[1],
                    "content": "Real extracted content.",
                },
            ]
        },
        warnings=[],
    )

    assert [source["url"] for source in sources] == ["https://example.com/working"]
    assert sources[0]["ref"] == "W1"
    assert sources[0]["extract_status"] == "full"


def test_direct_probe_source_keeps_summary_when_extract_fails():
    sources = research_bridge.extract_ranked_web_sources(
        [
            {
                "title": "openai official website",
                "url": "https://openai.com/",
                "description": "Official website for openai.",
                "query": "direct:openai.com",
            }
        ],
        extract_adapter=lambda urls: {
            "results": [{"url": urls[0], "content": "", "error": "blocked"}]
        },
        warnings=[],
    )

    assert sources[0]["url"] == "https://openai.com/"
    assert sources[0]["extract_status"] == "failed"
    assert sources[0]["excerpt"] == "Official website for openai."


def test_low_risk_summary_allowed_plan_can_use_search_summary_after_extract_failure():
    sources = research_bridge.extract_ranked_web_sources(
        [
            {
                "title": "改稻为桑为什么是死局",
                "url": "https://example.com/analysis",
                "description": "改稻为桑是财政、民生和执行矛盾叠加导致的死局。",
            }
        ],
        extract_adapter=lambda urls: {
            "results": [{"url": urls[0], "content": "", "error": "blocked"}]
        },
        warnings=[],
        allow_failed_summary=True,
        allow_failed_title_summary=True,
    )

    assert sources[0]["url"] == "https://example.com/analysis"
    assert sources[0]["extract_status"] == "summary"
    assert "extract_failed_summary_fallback" in sources[0]["quality_tags"]
    assert "财政、民生和执行矛盾" in sources[0]["excerpt"]


def test_low_risk_summary_allowed_plan_can_use_title_after_extract_failure():
    sources = research_bridge.extract_ranked_web_sources(
        [
            {
                "title": "改稻为桑为什么从一开始就是死局",
                "url": "https://example.com/title-only",
                "description": "",
            }
        ],
        extract_adapter=lambda urls: {
            "results": [{"url": urls[0], "content": "", "error": "blocked"}]
        },
        warnings=[],
        allow_failed_summary=True,
        allow_failed_title_summary=True,
    )

    assert sources[0]["url"] == "https://example.com/title-only"
    assert sources[0]["extract_status"] == "summary"
    assert "extract_failed_summary_fallback" in sources[0]["quality_tags"]
    assert sources[0]["excerpt"] == "改稻为桑为什么从一开始就是死局"


def test_low_risk_summary_allowed_plan_can_use_title_when_extract_returns_no_result():
    sources = research_bridge.extract_ranked_web_sources(
        [
            {
                "title": "改稻为桑为什么从一开始就是死局",
                "url": "https://example.com/title-only-empty",
                "description": "",
            }
        ],
        extract_adapter=lambda _urls: {"results": []},
        warnings=[],
        allow_failed_summary=True,
        allow_failed_title_summary=True,
    )

    assert sources[0]["url"] == "https://example.com/title-only-empty"
    assert sources[0]["extract_status"] == "summary"
    assert sources[0]["excerpt"] == "改稻为桑为什么从一开始就是死局"


def test_summary_fallback_contract_does_not_require_detailed_answer_depth():
    assert research_bridge._allows_failed_extract_summary_fallback(
        {
            "answer_depth": "normal",
            "risk_level": "low",
            "freshness_sensitive": False,
            "claim_requirements": {},
            "evidence_contract": {
                "allow_summary_only": True,
                "must_use_primary_source": False,
            },
        }
    ) is True


def test_low_risk_summary_fallback_keeps_weak_secondary_candidates_for_critic():
    plan = {
        "mode": "web_only",
        "risk_level": "low",
        "freshness_sensitive": False,
        "answer_depth": "detailed",
        "subject": {"name": "改稻为桑"},
        "evidence_contract": {
            "must_cover_subject": True,
            "minimum_independent_sources": 2,
            "must_use_primary_source": False,
            "allow_summary_only": True,
        },
        "source_preferences": {},
    }
    ranked = [
        {
            "title": "改稻为桑为什么是死局",
            "url": "https://www.zhihu.com/question/637422702",
            "domain": "zhihu.com",
            "description": "改稻为桑的政策设计、执行和政治博弈都存在矛盾。",
            "quality_tags": ["core_term_match", "weak_secondary_source"],
        }
    ]

    sources = research_bridge._ranked_sources_for_evidence(
        "为什么说改稻为桑是个死局",
        ranked,
        {},
        research_plan=plan,
        subject=plan["subject"],
        evidence_contract=plan["evidence_contract"],
    )

    assert sources == ranked


def test_evidence_ranking_prioritizes_title_subject_match_without_hiding_candidates():
    plan = {
        "mode": "web_only",
        "risk_level": "low",
        "freshness_sensitive": False,
        "answer_depth": "detailed",
        "subject": {"name": "改稻为桑"},
        "evidence_contract": {
            "must_cover_subject": True,
            "minimum_independent_sources": 1,
            "must_use_primary_source": False,
            "allow_summary_only": True,
        },
        "source_preferences": {},
    }
    ranked = [
        {
            "title": "2026年稻谷生产补贴项目实施方案政策解读",
            "url": "https://gov.example/rice-subsidy",
            "domain": "gov.example",
            "description": "搜索摘要提到了改稻为桑，但网页标题和 URL 并不是该主题。",
            "quality_tags": ["core_term_match", "trusted_domain"],
        },
        {
            "title": "《大明王朝》影评:改稻为桑为什么从一开始就是死局?",
            "url": "https://example.com/daming-rice-mulberry",
            "domain": "example.com",
            "description": "改稻为桑为什么是死局的解释。",
            "quality_tags": ["core_term_match"],
        },
    ]

    sources = research_bridge._ranked_sources_for_evidence(
        "为什么说改稻为桑是个死局",
        ranked,
        {},
        research_plan=plan,
        subject=plan["subject"],
        evidence_contract=plan["evidence_contract"],
    )

    assert sources[0] == ranked[1]
    assert sources[1] == ranked[0]


def test_evidence_ranking_broadens_summary_candidates_to_meet_minimum_source_contract():
    plan = {
        "mode": "web_only",
        "risk_level": "low",
        "freshness_sensitive": False,
        "answer_depth": "detailed",
        "subject": {"name": "改稻为桑"},
        "evidence_contract": {
            "must_cover_subject": True,
            "minimum_independent_sources": 2,
            "must_use_primary_source": False,
            "allow_summary_only": True,
        },
        "source_preferences": {},
    }
    ranked = [
        {
            "title": "《大明王朝》影评:改稻为桑为什么从一开始就是死局?",
            "url": "https://example.com/daming-rice-mulberry",
            "domain": "example.com",
            "description": "改稻为桑为什么是死局的解释。",
            "quality_tags": ["core_term_match"],
        },
        {
            "title": "剧情深度分析",
            "url": "https://analysis.example/deadlock",
            "domain": "analysis.example",
            "description": "改稻为桑的政策设计、执行和政治博弈都存在矛盾。",
            "quality_tags": ["core_term_match"],
        },
    ]

    sources = research_bridge._ranked_sources_for_evidence(
        "为什么说改稻为桑是个死局",
        ranked,
        {},
        research_plan=plan,
        subject=plan["subject"],
        evidence_contract=plan["evidence_contract"],
    )

    assert sources == ranked


def test_evidence_ranking_keeps_semantic_hints_visible_to_critic():
    plan = {
        "mode": "web_only",
        "risk_level": "low",
        "freshness_sensitive": False,
        "subject": {"name": "波动率", "aliases": ["volatility"], "type_hint": "finance_term"},
        "evidence_contract": {
            "must_cover_subject": True,
            "minimum_independent_sources": 1,
            "must_use_primary_source": False,
            "allow_summary_only": True,
        },
        "source_preferences": {"preferred_source_kinds": ["finance_glossary"]},
    }
    ranked = [
        {
            "title": "Volatility Skew",
            "url": "https://example.com/volatility-skew",
            "domain": "example.com",
            "description": "A related volatility concept.",
            "quality_tags": ["core_term_match"],
        },
        {
            "title": "volatility",
            "url": "https://dictionary.cambridge.org/dictionary/english/volatility",
            "domain": "dictionary.cambridge.org",
            "description": "Language dictionary definition.",
            "quality_tags": ["language_dictionary_source"],
        },
        {
            "title": "Volatility Definition",
            "url": "https://www.investopedia.com/terms/v/volatility.asp",
            "domain": "investopedia.com",
            "description": "Volatility measures price variation.",
            "quality_tags": ["planner_source_kind:finance_glossary", "core_term_match"],
        },
    ]

    sources = research_bridge._ranked_sources_for_evidence(
        "什么叫波动率",
        ranked,
        plan["source_preferences"],
        research_plan=plan,
        subject=plan["subject"],
        evidence_contract=plan["evidence_contract"],
    )

    assert [source["domain"] for source in sources] == [
        "investopedia.com",
        "dictionary.cambridge.org",
        "example.com",
    ]


def test_adjacent_subject_title_is_passed_to_critic_as_hint_only():
    plan = {
        "intent_label": "definition",
        "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
        "answer_claims_needed": ["answer_user_question"],
        "evidence_contract": {"allow_summary_only": True},
    }
    annotated = research_bridge._annotate_evidence_sources(
        "OpenAI 是什么",
        [
            {
                "ref": "W1",
                "title": "OpenAI Deployment Company",
                "url": "https://example.com/openai-deployment-company",
                "domain": "example.com",
                "excerpt": "A related page.",
                "extract_status": "summary",
                "quality_tags": ["core_term_match"],
            }
        ],
        research_plan=plan,
    )

    payload = research_bridge._evidence_critic_payload(
        "OpenAI 是什么",
        plan,
        annotated,
        ["answer_user_question"],
    )

    assert annotated[0]["source_role"] == "evidence"
    assert "adjacent_subject_title" in payload["sources"][0]["retrieval_hints"]


def test_required_preferred_source_gate_rejects_secondary_only_results(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "needs_web": True,
            "mode": "web_only",
            "freshness_sensitive": True,
            "subject": {"name": "Federal Reserve Chair", "aliases": ["美联储主席"], "type_hint": "official_role"},
            "queries": ["current Federal Reserve Chair"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "must_use_primary_source": True,
            },
            "source_preferences": {
                "prefer_primary_sources": True,
                "preferred_source_kinds": ["official"],
                "preferred_domains": ["federalreserve.gov"],
                "avoid_topics": [],
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://example.com/fed-chair",
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "source_role": "supporting_evidence",
                    "allowed_usage": ["fact"],
                    "confidence": "medium",
                    "supports_claims": ["answer_user_question"],
                    "adoption": "adopt",
                    "reason": "secondary report, not a primary source",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="美联储主席是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Secondary report",
                        "url": "https://example.com/fed-chair",
                        "description": "A secondary report says someone is chair.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [{"url": urls[0], "content": "A secondary report says someone is chair."}]
        },
        synthesize_adapter=lambda payload: "不应使用这个答案 [W1]",
    )

    assert "不应使用这个答案" not in result["chat_preview"]
    assert result["meta"]["used_web"] is False
    assert result["meta"]["search_quality"]["reason"] == "missing_required_preferred_source"
    assert any("官方/原始来源" in warning for warning in result["warnings"])


def test_required_preferred_source_gate_accepts_official_summary_result(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "needs_web": True,
            "mode": "web_only",
            "freshness_sensitive": True,
            "subject": {"name": "Federal Reserve Chair", "aliases": ["美联储主席"], "type_hint": "official_role"},
            "queries": ["current Federal Reserve Chair"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "must_use_primary_source": True,
            },
            "source_preferences": {
                "prefer_primary_sources": True,
                "preferred_source_kinds": ["official"],
                "preferred_domains": ["federalreserve.gov"],
                "avoid_topics": [],
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://www.federalreserve.gov/aboutthefed/bios/board/default.htm",
                    "subject_match": "yes",
                    "source_authority": "official",
                    "source_role": "primary_evidence",
                    "allowed_usage": ["fact"],
                    "confidence": "high",
                    "supports_claims": ["answer_user_question"],
                    "adoption": "adopt",
                    "reason": "official Federal Reserve board page",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="美联储主席是谁",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Board Members - Federal Reserve Board",
                        "url": "https://www.federalreserve.gov/aboutthefed/bios/board/default.htm",
                        "description": "Jerome H. Powell, Chair of the Board of Governors.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": []},
        synthesize_adapter=lambda payload: "美联储主席以美联储官网页面为准 [W1]",
    )

    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["domain"] == "federalreserve.gov"
    assert result["meta"]["web_sources"][0]["extract_status"] == "summary"


def test_direct_preferred_domain_candidate_fills_missing_official_result(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_fetch_url_summary",
        lambda url: {
            "title": "OpenAI",
            "url": url,
            "description": "OpenAI is an AI research and deployment company.",
        },
    )

    candidates = research_bridge._direct_preferred_domain_candidates(
        "OpenAI是什么意思",
        {"preferred_domains": ["openai.com"]},
        existing_results=[{"title": "百科", "url": "https://baike.baidu.com/item/OpenAI", "description": "百科"}],
    )

    assert candidates == [
        {
            "title": "OpenAI",
            "url": "https://openai.com/",
            "description": "OpenAI is an AI research and deployment company.",
            "query": "direct:openai.com",
        }
    ]


def test_direct_preferred_domain_candidate_skips_empty_soft_official_hint(monkeypatch):
    monkeypatch.setattr(research_bridge, "_fetch_url_summary", lambda url: None)

    candidates = research_bridge._direct_preferred_domain_candidates(
        "OpenAI是什么意思",
        {"preferred_domains": ["openai.com"], "prefer_primary_sources": False},
        existing_results=[],
    )

    assert candidates == []


def test_preferred_domain_probe_urls_include_common_official_profile_paths():
    urls = research_bridge._preferred_domain_probe_urls("Claude Code Anthropic official documentation", "docs.anthropic.com")

    assert "https://docs.anthropic.com/about/" in urls
    assert "https://docs.anthropic.com/en/docs/claude-code/overview" in urls
    assert "https://docs.anthropic.com/docs/claude-code/overview" in urls


def test_preferred_domain_probe_urls_use_subject_alias_before_homepage():
    urls = research_bridge._preferred_domain_probe_urls(
        "American Economic Review 这个期刊怎么样",
        "aeaweb.org",
        subject={"name": "American Economic Review", "aliases": ["AER"], "type_hint": "academic_journal"},
    )

    assert urls[0] == "https://aeaweb.org/aer"
    assert "https://aeaweb.org/" in urls


def test_preferred_domain_probe_urls_include_marketcap_path_from_subject():
    urls = research_bridge._preferred_domain_probe_urls(
        "NVDA market cap today",
        "companiesmarketcap.com",
        subject={"name": "NVIDIA", "aliases": ["NVDA"], "type_hint": "company"},
    )

    assert "https://companiesmarketcap.com/nvidia/marketcap/" in urls
    assert "https://companiesmarketcap.com/" in urls


def test_site_query_domains_are_promoted_to_source_preferences():
    plan = research_bridge._research_plan_from_payload(
        {
            "needs_web": True,
            "mode": "web_only",
            "queries": ["Claude Code official site:docs.anthropic.com"],
            "source_preferences": {"preferred_source_kinds": ["documentation"]},
        },
        query="Claude Code 是什么",
        mode="web_only",
    )

    assert plan["source_preferences"]["preferred_domains"] == ["docs.anthropic.com"]


def test_planner_source_kind_preferences_include_finance_glossary_and_educational_domains():
    ranked = research_bridge.rank_web_sources(
        "volatility definition finance",
        [
            {
                "title": "Volatility Definition",
                "url": "https://www.investopedia.com/terms/v/volatility.asp",
                "description": "Volatility measures price variation.",
            },
            {
                "title": "Personal blog about volatility",
                "url": "https://example.com/volatility",
                "description": "Volatility notes.",
            },
        ],
        source_preferences={"preferred_source_kinds": ["finance_glossary"]},
    )

    assert ranked[0]["domain"] == "investopedia.com"


def test_finance_glossary_source_kind_can_directly_probe_known_term_pages(monkeypatch):
    def fake_fetch(url):
        if url == "https://corporatefinanceinstitute.com/resources/career-map/sell-side/capital-markets/volatility-vol/":
            return {
                "title": "Volatility - Overview, Example Calculations, and Types of Vol",
                "url": url,
                "description": "Volatility measures price fluctuations and risk.",
            }
        return None

    monkeypatch.setattr(research_bridge, "_fetch_url_summary", fake_fetch)

    candidates = research_bridge._direct_source_kind_candidates(
        "volatility definition finance",
        {
            "subject": {"name": "波动率", "aliases": ["volatility"], "type_hint": "finance_term"},
            "source_preferences": {"preferred_source_kinds": ["finance_glossary"]},
        },
        existing_results=[],
    )

    assert candidates == [
        {
            "title": "Volatility - Overview, Example Calculations, and Types of Vol",
            "url": "https://corporatefinanceinstitute.com/resources/career-map/sell-side/capital-markets/volatility-vol/",
            "description": "Volatility measures price fluctuations and risk.",
            "query": "direct:corporatefinanceinstitute.com",
        }
    ]


def test_source_identity_mismatch_is_tagged_from_mentioned_url():
    ranked = research_bridge.rank_web_sources(
        "OpenAI 是什么",
        [
            {
                "title": "OpenAI 官方关于页面",
                "url": "https://example.com/openai",
                "description": "OpenAI 官方页面位于 https://openai.com/about。",
            }
        ],
    )

    assert "source_identity_mismatch" in ranked[0]["quality_tags"]


def test_mentioned_official_url_is_promoted_to_direct_candidate(monkeypatch):
    monkeypatch.setattr(research_bridge, "_fetch_url_summary", lambda url: None)

    candidates = research_bridge._direct_mentioned_url_candidates(
        [
            {
                "title": "OpenAI 官网入口",
                "url": "https://m.php.cn/faq/openai.html",
                "description": "OpenAI official website is https://openai.com/.",
            }
        ],
        {
            "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
            "source_preferences": {"preferred_source_kinds": ["official", "encyclopedia"]},
        },
    )

    assert candidates == [
        {
            "title": "https://openai.com/",
            "url": "https://openai.com/",
            "description": "OpenAI official website is https://openai.com/.",
            "query": "direct:mentioned-url",
            "quality_tags": ["mentioned_url_candidate", "mentioned_subject_domain"],
        }
    ]


def test_mentioned_url_candidate_strips_chinese_parenthetical_tail(monkeypatch):
    monkeypatch.setattr(research_bridge, "_fetch_url_summary", lambda url: None)

    candidates = research_bridge._direct_mentioned_url_candidates(
        [
            {
                "title": "OpenAI官方网址入口",
                "url": "https://m.php.cn/faq/1713494.html",
                "description": "来源：https://www.openai.com/（依据联网搜索 `OpenAI 是什么 site:openai.com`）",
            }
        ],
        {
            "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
            "source_preferences": {"preferred_domains": ["openai.com"]},
        },
    )

    assert candidates == [
        {
            "title": "https://www.openai.com/",
            "url": "https://www.openai.com/",
            "description": "来源：https://www.openai.com/（依据联网搜索 `OpenAI 是什么 site:openai.com`）",
            "query": "direct:mentioned-url",
            "quality_tags": ["mentioned_url_candidate", "mentioned_subject_domain"],
        }
    ]


def test_ranker_preserves_direct_mentioned_url_candidate_tag():
    ranked = research_bridge.rank_web_sources(
        "OpenAI 是什么",
        [
            {
                "title": "https://openai.com/",
                "url": "https://openai.com/",
                "description": "OpenAI official website.",
                "quality_tags": ["mentioned_url_candidate"],
            }
        ],
    )

    assert "mentioned_url_candidate" in ranked[0]["quality_tags"]


def test_professional_definition_hint_does_not_override_critic_adoption():
    adopted, rejected = research_bridge._apply_evidence_decisions_to_sources(
        {
            "intent_label": "definition",
            "subject": {"name": "波动率", "aliases": ["volatility"], "type_hint": "finance_term"},
            "answer_claims_needed": ["answer_user_question"],
            "evidence_contract": {"allow_summary_only": True},
        },
        [
            {
                "ref": "W1",
                "title": "volatility",
                "url": "https://dictionary.cambridge.org/dictionary/english-chinese-traditional/volatility",
                "domain": "dictionary.cambridge.org",
                "excerpt": "Translation of volatility.",
                "quality_tags": ["language_dictionary_source"],
                "extract_status": "full",
            }
        ],
        [
            {
                "source_id": "https://dictionary.cambridge.org/dictionary/english-chinese-traditional/volatility",
                "subject_match": "yes",
                "source_authority": "reputable_secondary",
                "source_role": "supporting_evidence",
                "allowed_usage": ["fact"],
                "confidence": "medium",
                "supports_claims": ["answer_user_question"],
                "adoption": "adopt",
            }
        ],
    )

    assert adopted[0]["quality_reason"] == "evidence_critic_adopted"
    assert adopted[0]["domain"] == "dictionary.cambridge.org"
    assert rejected == []


def test_source_identity_hint_does_not_override_critic_adoption():
    adopted, rejected = research_bridge._apply_evidence_decisions_to_sources(
        {
            "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
            "answer_claims_needed": ["answer_user_question"],
            "evidence_contract": {"allow_summary_only": True},
        },
        [
            {
                "ref": "W1",
                "title": "OpenAI 官方页面",
                "url": "https://example.com/openai",
                "domain": "example.com",
                "excerpt": "OpenAI 官方页面位于 https://openai.com/about。",
                "quality_tags": ["source_identity_mismatch"],
                "extract_status": "summary",
            }
        ],
        [
            {
                "source_id": "https://example.com/openai",
                "subject_match": "yes",
                "source_authority": "reputable_secondary",
                "source_role": "supporting_evidence",
                "allowed_usage": ["fact"],
                "confidence": "medium",
                "supports_claims": ["answer_user_question"],
                "adoption": "adopt",
            }
        ],
    )

    assert adopted[0]["quality_reason"] == "evidence_critic_adopted"
    assert adopted[0]["domain"] == "example.com"
    assert rejected == []


def test_explicit_claim_requirements_reject_generic_claim_support():
    adopted, rejected = research_bridge._apply_evidence_decisions_to_sources(
        {
            "risk_level": "high",
            "subject": {"name": "某某药", "aliases": [], "type_hint": "medication"},
            "answer_claims_needed": ["indication", "contraindication"],
            "claim_requirements": [
                {"claim_id": "indication", "required_authority": "primary_or_index"},
                {"claim_id": "contraindication", "required_authority": "primary_or_index"},
            ],
            "evidence_contract": {
                "must_cover_subject": True,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": True,
                "allow_summary_only": False,
            },
        },
        [
            {
                "ref": "W1",
                "title": "中成药品种使用说明书",
                "url": "https://www.nmpa.gov.cn/example",
                "domain": "nmpa.gov.cn",
                "excerpt": "A drug label page.",
                "extract_status": "full",
            }
        ],
        [
            {
                "source_id": "https://www.nmpa.gov.cn/example",
                "subject_match": "yes",
                "source_authority": "official",
                "source_role": "primary_evidence",
                "allowed_usage": ["fact"],
                "confidence": "medium",
                "supports_claims": ["answer_user_question"],
                "claim_support": [{"claim_id": "answer_user_question", "support": "yes", "reason": "generic support"}],
                "adoption": "adopt",
            }
        ],
    )

    assert adopted == []
    assert rejected[0]["quality_reason"] == "evidence_critic_rejected"
    assert rejected[0]["unsupported_claims"] == ["answer_user_question"]


def test_definition_hint_detects_adjacent_term_titles_without_topic_blacklist():
    plan = {
        "subject": {"name": "波动率", "aliases": ["volatility"], "type_hint": "finance_term"},
        "intent_label": "definition",
        "answer_claims_needed": ["answer_user_question"],
    }

    assert research_bridge._source_is_adjacent_for_definition_claim(plan, {"title": "Volatility Skew"}) is True
    assert research_bridge._source_is_adjacent_for_definition_claim(plan, {"title": "波动率微笑"}) is True
    assert research_bridge._source_is_adjacent_for_definition_claim(plan, {"title": "Volatility (finance) - Wikipedia"}) is False
    assert research_bridge._source_is_adjacent_for_definition_claim(plan, {"title": "Volatility Definition"}) is False


def test_evidence_critic_payload_exposes_retrieval_hints_as_hints_only():
    payload = research_bridge._evidence_critic_payload(
        "OpenAI 是什么",
        {
            "subject": {"name": "OpenAI", "type_hint": "company"},
            "answer_claims_needed": ["answer_user_question"],
            "evidence_contract": {"allow_summary_only": True},
        },
        [
            {
                "ref": "W1",
                "title": "OpenAI 官方关于页面",
                "url": "https://example.com/openai",
                "domain": "example.com",
                "description": "OpenAI 官方页面位于 https://openai.com/about。",
                "quality_tags": ["source_identity_mismatch"],
                "matched_terms": ["openai"],
                "domain_roles": ["official"],
                "domain_scores": {"trusted": 9, "official": 9},
                "extract_status": "summary",
            }
        ],
        ["answer_user_question"],
    )

    assert payload["sources"][0]["retrieval_hints"] == ["source_identity_mismatch"]
    assert payload["sources"][0]["matched_terms"] == ["openai"]
    assert payload["sources"][0]["domain_roles"] == ["official"]
    assert payload["sources"][0]["domain_scores"] == {"trusted": 9, "official": 9}
    prompt = research_bridge._build_evidence_critic_prompt(payload)
    assert "retrieval_hints 只是检索排序线索，不是判决" in prompt


def test_subject_title_priority_promotes_direct_pages_before_generic_cards():
    sources = [
        {"title": "联网搜索结果：", "rank_score": 20},
        {"title": "波动性分析", "rank_score": 30},
        {"title": "波动率", "rank_score": 1},
    ]

    sorted_sources = research_bridge._sort_sources_by_subject_title_priority(
        sources,
        {"name": "波动率", "aliases": ["volatility"]},
    )

    assert [source["title"] for source in sorted_sources] == ["波动率", "波动性分析", "联网搜索结果："]


def test_direct_preferred_domain_candidate_probes_specific_slug_before_homepage(monkeypatch):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if url == "https://www.anthropic.com/":
            return {"title": "Home \\ Anthropic", "url": url, "description": "Anthropic homepage"}
        if url == "https://www.anthropic.com/claude-code":
            return {
                "title": "Claude Code by Anthropic",
                "url": url,
                "description": "Anthropic's agentic coding tool for developers.",
            }
        return None

    monkeypatch.setattr(research_bridge, "_fetch_url_summary", fake_fetch)

    candidates = research_bridge._direct_preferred_domain_candidates(
        "Claude Code Anthropic official documentation",
        {"preferred_domains": ["www.anthropic.com"]},
        existing_results=[],
    )

    assert calls == ["https://www.anthropic.com/claude-code"]
    assert candidates == [
        {
            "title": "Claude Code by Anthropic",
            "url": "https://www.anthropic.com/claude-code",
            "description": "Anthropic's agentic coding tool for developers.",
            "query": "direct:www.anthropic.com",
        }
    ]


def test_direct_preferred_domain_candidate_does_not_semantically_filter_fetchable_domain(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_fetch_url_summary",
        lambda url: {
            "title": "Company",
            "url": url,
            "description": "Generic official landing page without query words.",
        },
    )

    candidates = research_bridge._direct_preferred_domain_candidates(
        "Claude Code Anthropic official documentation",
        {"preferred_domains": ["www.anthropic.com"]},
        existing_results=[],
    )

    assert candidates == [
        {
            "title": "Company",
            "url": "https://www.anthropic.com/claude-code",
            "description": "Generic official landing page without query words.",
            "query": "direct:www.anthropic.com",
        }
    ]


def test_direct_preferred_domain_candidate_returns_empty_when_all_probes_fail(monkeypatch):
    monkeypatch.setattr(research_bridge, "_fetch_url_summary", lambda url: None)

    candidates = research_bridge._direct_preferred_domain_candidates(
        "美联储主席是谁",
        {"preferred_domains": ["federalreserve.gov"], "prefer_primary_sources": True},
        existing_results=[],
    )

    assert candidates == []


def test_run_web_research_prefetches_direct_candidates_before_default_search(monkeypatch):
    calls = []

    def fake_fetch(url):
        calls.append(("direct", url))
        return {"title": "Example official", "url": url, "description": "Example official profile."}

    def fake_search(query, limit):
        assert calls and calls[0][0] == "direct"
        calls.append(("search", query))
        return {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Example mirror",
                        "url": "https://mirror.example/example",
                        "description": "Mirror profile.",
                    }
                ]
            },
        }

    monkeypatch.setattr(research_bridge, "_fetch_url_summary", fake_fetch)
    monkeypatch.setattr(research_bridge, "_default_web_search", fake_search)
    monkeypatch.setattr(research_bridge, "_default_search_backend", lambda: "dashscope")
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "official" if source["domain"] == "example.com" else "weak_secondary",
                    "claim_support": [
                        {
                            "claim_id": "answer_user_question",
                            "support": "yes" if source["domain"] == "example.com" else "no",
                            "reason": "official" if source["domain"] == "example.com" else "mirror",
                        }
                    ],
                    "adoption": "adopt" if source["domain"] == "example.com" else "clue",
                    "reason": "official" if source["domain"] == "example.com" else "mirror",
                }
                for source in payload["sources"]
            ]
        },
    )

    result = research_bridge._run_web_research(
        "Example 是什么",
        [],
        research_plan={
            "intent_label": "company overview",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "Example", "aliases": [], "type_hint": "company"},
            "queries": ["Example official"],
            "answer_claims_needed": ["answer_user_question"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {"preferred_domains": ["example.com"]},
        },
        search_adapter=None,
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "Example official profile."} for url in urls]},
    )

    assert calls[0] == ("direct", "https://example.com/example")
    assert calls[1][0] == "search"
    assert result["sources"][0]["domain"] == "example.com"


def test_wrong_citation_without_lexical_support_falls_back_to_source_summary(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": s["source_id"],
                    "subject_match": "yes",
                    "source_authority": "official",
                    "source_role": "primary_evidence",
                    "evidence_role": "interpretive_evidence",
                    "allowed_usage": ["fact", "background", "interpretation"],
                    "confidence": "high",
                    "claim_support": [
                        {"claim_id": c, "support": "yes", "reason": "directly relevant"}
                        for c in (payload.get("answer_claims_needed") or ["answer_user_question"])
                    ],
                    "adoption": "adopt",
                    "reason": "official source for company query",
                }
                for s in payload["sources"]
            ]
        },
    )

    result = research_bridge.research_answer(
        query="OpenAI是什么意思",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "OpenAI | Research & Deployment",
                        "url": "https://openai.com/",
                        "description": "OpenAI is an AI research and deployment company.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": urls[0],
                    "content": "OpenAI is an AI research and deployment company that builds AI systems and products.",
                }
            ]
        },
        synthesize_adapter=lambda payload: "OpenAI 是一家人工智能研究和产品部署公司，主要开发 AI 模型与相关产品 [K1]",
    )

    assert "[K1]" not in result["chat_preview"]
    assert "[W1]" in result["chat_preview"]
    assert "OpenAI is an AI research and deployment company" in result["chat_preview"]
    assert any("可验证检索摘要" in warning for warning in result["warnings"])


def test_wrong_citation_without_source_overlap_uses_fallback_summary():
    result = research_bridge.research_answer(
        query="什么是 coding agent",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Coding agents explained",
                        "url": "https://example.com/coding-agent",
                        "description": "AI coding agents plan and complete software tasks.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "AI coding agents plan and complete software tasks."}]},
        synthesize_adapter=lambda payload: "这是一段和来源完全无关的长回答，不能被强行绑定到第一个网页来源 [K1]",
    )

    assert "完全无关的长回答" not in result["chat_preview"]
    assert "[K1]" not in result["chat_preview"]
    assert "[W1]" in result["chat_preview"]
    assert any("可验证检索摘要" in warning for warning in result["warnings"])


def test_uncited_web_answer_gets_first_valid_web_citation():
    result = research_bridge.research_answer(
        query="什么叫波动率",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Volatility definition",
                        "url": "https://example.com/volatility",
                        "description": "Volatility measures how much returns vary.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "Volatility measures how much returns vary."}]},
        synthesize_adapter=lambda payload: "波动率是衡量资产收益或价格变化幅度的指标。",
    )

    assert "[W1]" in result["chat_preview"]
    assert result["meta"]["web_sources"][0]["url"] == "https://example.com/volatility"
    assert any("未引用已采用" in warning for warning in result["warnings"])


def test_answer_mentioning_unadopted_source_name_falls_back_to_source_summary():
    warnings = []
    payload = {
        "web_sources": [
            {
                "ref": "W1",
                "title": "volatility",
                "url": "https://baike.baidu.com/item/volatility/111",
                "domain": "baike.baidu.com",
                "excerpt": "volatility 可译为波动率。",
            }
        ],
        "clue_sources": [
            {
                "title": "联网搜索结果：",
                "url": "https://www.investopedia.com/terms/v/volatility.asp",
                "domain": "investopedia.com",
            }
        ],
        "rejected_sources": [],
    }

    answer = research_bridge._ensure_no_unavailable_source_mentions(
        "当前证据主要来自 Investopedia，可确认波动率含义 [W1]",
        payload,
        warnings,
    )

    assert "Investopedia" not in answer
    assert "volatility 可译为波动率" in answer
    assert any("未采用来源" in warning for warning in warnings)


def test_uncited_web_refusal_uses_source_backed_fallback():
    result = research_bridge.research_answer(
        query="什么叫波动率",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Volatility definition",
                        "url": "https://example.com/volatility",
                        "description": "Volatility measures how much returns vary.",
                    }
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "Volatility measures how much returns vary."}]},
        synthesize_adapter=lambda payload: "本次联网搜索没有找到足够可靠的公开资料来回答这个问题。",
    )

    assert "可确认的是" in result["chat_preview"]
    assert "[W1]" in result["chat_preview"]
    assert "本次联网搜索" not in result["chat_preview"]
    assert result["meta"]["web_sources"][0]["url"] == "https://example.com/volatility"


def test_fullwidth_tool_citations_are_normalized_before_source_filtering():
    result = research_bridge.research_answer(
        query="什么叫波动率",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Source one",
                        "url": "https://example.com/one",
                        "description": "First source.",
                    },
                    {
                        "title": "Source two",
                        "url": "https://example.org/two",
                        "description": "Second source.",
                    },
                ]
            },
        },
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "Volatility definition evidence."} for url in urls]},
        synthesize_adapter=lambda payload: "波动率是风险指标［W2］。",
    )

    assert "［W2］" not in result["chat_preview"]
    assert "[W1]" in result["chat_preview"]
    assert [source["ref"] for source in result["meta"]["web_sources"]] == ["W1"]
    assert result["meta"]["web_sources"][0]["url"] == "https://example.org/two"


def test_async_research_answer_awaits_extract_adapter():
    async def fake_extract(urls):
        return {"results": [{"url": urls[0], "content": "网页正文"}]}

    result = asyncio.run(
        research_bridge.async_research_answer(
            query="苹果现在的市值多少",
            mode="web_only",
            search_adapter=lambda *_args: {
                "success": True,
                "data": {"web": [{"title": "Apple market cap", "url": "https://companiesmarketcap.com/apple/marketcap/", "description": "摘要"}]},
            },
            extract_adapter=fake_extract,
            synthesize_adapter=lambda payload: "回答 [W1]",
        )
    )

    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["extract_status"] == "full"
    assert result["meta"]["web_sources"][0]["excerpt"] == "网页正文"


def test_async_research_answer_streams_research_progress_events():
    events = []

    result = asyncio.run(
        research_bridge.async_research_answer(
            query="what is a coding agent",
            mode="web_only",
            search_adapter=lambda *_args: {
                "success": True,
                "data": {
                    "web": [
                        {
                            "title": "What Is an AI Coding Agent?",
                            "url": "https://example.com/coding-agent",
                            "description": "AI coding agents plan, edit code, run checks, and iterate.",
                        }
                    ]
                },
            },
            extract_adapter=lambda urls: {
                "results": [
                    {
                        "url": urls[0],
                        "content": "AI coding agents plan, edit code, run checks, and iterate on software tasks.",
                    }
                ]
            },
            synthesize_adapter=lambda payload: "Coding agent 是能规划、修改代码并运行检查的 AI 系统 [W1]",
            event_callback=lambda event: events.append(event),
        )
    )

    event_types = [event["type"] for event in events]
    assert event_types.index("plan") < event_types.index("web_query_start") < event_types.index("web_query_done")
    assert event_types.index("web_query_done") < event_types.index("web_critic_start") < event_types.index("web_critic_done")
    assert event_types.index("web_critic_done") < event_types.index("web_extract_done") < event_types.index("synthesis_start")
    assert event_types.index("web_classified") < event_types.index("synthesis_start")
    assert any(event.get("candidate_sources") for event in events if event["type"] == "web_classified")
    assert any(event.get("web_sources") for event in events if event["type"] == "web_critic_start")
    assert any(
        source.get("title") == "What Is an AI Coding Agent?"
        for event in events
        for source in event.get("web_sources", [])
    )
    assert result["chat_preview"].startswith("Coding agent")


def test_web_only_synthesis_prompt_uses_web_as_primary_evidence():
    prompt = research_bridge._build_synthesis_prompt(
        {
            "query": "什么是 coding agent",
            "mode": "web_only",
            "research_plan": {"answer_contract": {"primary_answer_first": True, "avoid_unasked_side_topics": True}},
            "article_context": {"title": "Data Agent 文章", "body": "这里大量讨论 Data Agent 和 SQL 口径。"},
            "selection_context": {"selected_text": "Coding agent", "surrounding_after": "Data Agent 不应混入答案。"},
            "local_answer": "",
            "knowledge_sources": [],
            "web_sources": [
                {
                    "ref": "W1",
                    "title": "Coding agents explained",
                    "url": "https://example.com/coding-agent",
                    "excerpt": "AI coding agents autonomously plan and complete software engineering tasks.",
                }
            ],
        }
    )

    assert "当前是纯联网模式" in prompt
    assert "这里大量讨论 Data Agent" not in prompt
    assert "Data Agent 不应混入答案" not in prompt
    assert "Coding agents explained" in prompt


def test_normal_depth_synthesis_prompt_requires_more_than_one_sentence():
    prompt = research_bridge._build_synthesis_prompt(
        {
            "query": "什么叫波动率",
            "mode": "web_only",
            "research_plan": {"answer_depth": "normal"},
            "article_context": {},
            "selection_context": {},
            "local_answer": "",
            "knowledge_sources": [],
            "web_sources": [
                {
                    "ref": "W1",
                    "title": "Volatility definition",
                    "url": "https://example.com/volatility",
                    "excerpt": "Volatility measures how much returns vary.",
                }
            ],
            "search_quality": {"evidence_status": "sufficient"},
        }
    )

    assert "回答不要只写一句话" in prompt
    assert "2-4 个简洁要点" in prompt


def test_local_only_does_not_call_web(monkeypatch):
    called = {"web": False}

    monkeypatch.setattr(
        research_bridge,
        "_search_local",
        lambda query, warnings: ("本地答案", [{"ref": "K1", "type": "knowledge", "id": "cap-1", "title": "本地材料"}]),
    )

    result = research_bridge.research_answer(
        query="我的笔记里怎么说",
        mode="local_only",
        search_adapter=lambda *_args: called.__setitem__("web", True),
        synthesize_adapter=lambda payload: "回答 [K1]",
    )

    assert called["web"] is False
    assert result["meta"]["used_local"] is True
    assert result["meta"]["used_web"] is False
    assert result["meta"]["knowledge_sources"][0]["ref"] == "K1"


def test_web_only_does_not_call_local(monkeypatch):
    called = {"local": False}

    monkeypatch.setattr(
        research_bridge,
        "_search_local",
        lambda *_args: called.__setitem__("local", True),
    )

    def _adopt_all_critic(payload):
        return {"decisions": [
            {
                "source_id": s["source_id"],
                "subject_match": "yes",
                "source_authority": "reputable_secondary",
                "source_role": "supporting_evidence",
                "allowed_usage": ["fact"],
                "confidence": "medium",
                "claim_support": [{"claim_id": c, "support": "yes", "reason": "relevant"} for c in (payload.get("answer_claims_needed") or ["answer_user_question"])],
                "adoption": "adopt",
                "reason": "relevant source",
            }
            for s in payload["sources"]
        ]}

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", _adopt_all_critic)

    result = research_bridge.research_answer(
        query="查一下最新市场信息",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {"web": [{"title": "2026年最新市场行情分析", "url": "https://finance-example.com/market-2026", "description": "最新市场走势与行情分析。"}]},
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "2026年市场行情持续向好，科技板块表现突出。"}]},
        synthesize_adapter=lambda payload: "回答 [W1]",
    )

    assert called["local"] is False
    assert result["meta"]["used_local"] is False
    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["ref"] == "W1"


def test_local_web_merges_sources(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_search_local",
        lambda query, warnings: ("本地答案", [{"ref": "K1", "type": "knowledge", "id": "cap-1", "title": "本地材料"}]),
    )

    def _adopt_all_critic(payload):
        return {"decisions": [
            {
                "source_id": s["source_id"],
                "subject_match": "yes",
                "source_authority": "reputable_secondary",
                "source_role": "supporting_evidence",
                "allowed_usage": ["fact"],
                "confidence": "medium",
                "claim_support": [{"claim_id": c, "support": "yes", "reason": "relevant"} for c in (payload.get("answer_claims_needed") or ["answer_user_question"])],
                "adoption": "adopt",
                "reason": "relevant source",
            }
            for s in payload["sources"]
        ]}

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", _adopt_all_critic)

    result = research_bridge.research_answer(
        query="结合我的知识库和公开信息判断",
        mode="local_web",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {"web": [{"title": "公开信息分析报告", "url": "https://research-example.com/report", "description": "综合公开信息的分析结论。"}]},
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "根据公开信息综合分析，当前趋势积极。"}]},
        synthesize_adapter=lambda payload: "回答 [K1][W1]",
    )

    assert result["meta"]["knowledge_sources"][0]["ref"] == "K1"
    assert result["meta"]["web_sources"][0]["ref"] == "W1"
    assert [source["ref"] for source in result["source_map"]] == ["K1", "W1"]


def test_uncited_sources_are_not_returned_to_ui(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_search_local",
        lambda query, warnings: (
            "本地答案",
            [
                {"ref": "K1", "type": "knowledge", "id": "cap-1", "title": "被引用材料"},
                {"ref": "K2", "type": "knowledge", "id": "cap-2", "title": "未引用材料"},
            ],
        ),
    )

    result = research_bridge.research_answer(
        query="解释一下这段话",
        mode="local_only",
        synthesize_adapter=lambda payload: "回答只使用第一条来源 [K1]",
    )

    assert [source["ref"] for source in result["source_map"]] == ["K1"]
    assert [source["ref"] for source in result["meta"]["knowledge_sources"]] == ["K1"]
    assert [source["ref"] for source in result["meta"]["retrieved_knowledge_sources"]] == ["K1", "K2"]


def test_web_search_failure_falls_back_with_warning(monkeypatch):
    monkeypatch.setattr(research_bridge, "_search_local", lambda *_args: ("本地答案", []))

    result = research_bridge.research_answer(
        query="最近有什么变化",
        mode="local_web",
        search_adapter=lambda *_args: (_ for _ in ()).throw(RuntimeError("no backend")),
        synthesize_adapter=lambda payload: "本地回答",
    )

    assert result["status"] == "success"
    assert result["meta"]["used_web"] is False
    assert any("联网搜索失败" in warning for warning in result["warnings"])


def test_low_risk_healthy_default_results_skip_recall_fallback(monkeypatch):
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "simple overview",
            "needs_web": True,
            "mode": "web_only",
            "risk_level": "low",
            "freshness_sensitive": False,
            "answer_depth": "normal",
            "subject": {"name": "example", "aliases": [], "type_hint": "concept"},
            "queries": ["example q1", "example q2", "example q3", "example q4"],
            "answer_claims_needed": [],
            "evidence_contract": {
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_available_fallback_search_backends",
        lambda primary: (_ for _ in ()).throw(AssertionError("healthy simple retrieval should not fallback")),
    )

    def _adopt_all_critic(payload):
        return {"decisions": [
            {
                "source_id": s["source_id"],
                "subject_match": "yes",
                "source_authority": "reputable_secondary",
                "source_role": "supporting_evidence",
                "allowed_usage": ["fact"],
                "confidence": "medium",
                "claim_support": [{"claim_id": c, "support": "yes", "reason": "relevant"} for c in (payload.get("answer_claims_needed") or ["answer_user_question"])],
                "adoption": "adopt",
                "reason": "relevant source",
            }
            for s in payload["sources"]
        ]}

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", _adopt_all_critic)
    calls = []

    def fake_default_search(query, limit):
        calls.append(query)
        slug = query.replace(" ", "-")
        return {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": f"{query} overview",
                        "url": f"https://example-{slug}.org/overview",
                        "description": f"{query} has readable evidence.",
                    }
                ]
            },
        }

    monkeypatch.setattr(research_bridge, "_default_web_search", fake_default_search)

    result = research_bridge.research_answer(
        query="什么是 example",
        mode="web_only",
        extract_adapter=lambda urls: {"results": [{"url": url, "content": "Readable extracted evidence."} for url in urls]},
        synthesize_adapter=lambda payload: "Example 是一个说明性概念 [W1]",
    )

    assert sorted(calls) == ["example q1", "example q2"]
    assert result["meta"]["provider_status"]["fallback_attempts"] == []
    assert result["meta"]["research_trace"]["retrieval_health"]["needs_recall_repair"] is False


def test_weak_retrieval_uses_available_fallback_backend_and_still_runs_critic(monkeypatch):
    monkeypatch.setattr(research_bridge, "_available_fallback_search_backends", lambda primary: ["ddgs"])
    monkeypatch.setattr(
        research_bridge,
        "_default_web_search",
        lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "低质量泛讨论",
                        "url": "https://zhuanlan.zhihu.com/p/weak",
                        "description": "没有直接解释 coding agent 的泛讨论。",
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_web_search_with_backend",
        lambda query, limit, backend: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Coding agents explained",
                        "url": "https://example.org/coding-agent",
                        "description": "AI coding agents plan code changes, run checks, and iterate.",
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://example.org/coding-agent",
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "source_role": "supporting_evidence",
                    "allowed_usage": ["fact", "background"],
                    "confidence": "medium",
                    "supports_claims": ["answer_user_question"],
                    "adoption": "adopt",
                    "reason": "directly explains coding agents",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="什么是 coding agent",
        mode="web_only",
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": url,
                    "content": "AI coding agents plan code changes, run checks, and iterate."
                    if "example.org" in url
                    else "泛讨论内容",
                }
                for url in urls
            ]
        },
        synthesize_adapter=lambda payload: "Coding agent 是能规划、改代码并运行检查的 AI 系统 [W1]",
    )

    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["url"] == "https://example.org/coding-agent"
    assert result["meta"]["web_sources"][0]["search_backend"] == "ddgs"
    assert result["meta"]["provider_status"]["fallback_attempts"][0]["backend"] == "ddgs"
    assert result["meta"]["research_trace"]["retrieval_health"]["needs_recall_repair"] is True


def test_missing_preferred_domain_triggers_domain_limited_fallback(monkeypatch):
    monkeypatch.setattr(research_bridge, "_available_fallback_search_backends", lambda primary: ["ddgs"])
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda _payload: {
            "intent_label": "company overview",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "OpenAI", "aliases": [], "type_hint": "company"},
            "queries": ["OpenAI official about"],
            "answer_claims_needed": ["identity"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {
                "preferred_domains": ["openai.com"],
                "preferred_source_kinds": ["official"],
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_web_search",
        lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "OpenAI 中文二手讨论",
                        "url": "https://example.cn/openai",
                        "description": "二手讨论 OpenAI。",
                    }
                ]
            },
        },
    )

    fallback_queries = []

    def fake_fallback_search(query, limit, backend):
        fallback_queries.append(query)
        return {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "About | OpenAI",
                        "url": "https://openai.com/about/",
                        "description": "OpenAI consists of the nonprofit OpenAI Foundation and the for-profit OpenAI Group.",
                    }
                ]
            },
        }

    monkeypatch.setattr(research_bridge, "_web_search_with_backend", fake_fallback_search)
    monkeypatch.setattr(research_bridge, "_fetch_url_summary", lambda _url: None)
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda _payload: {
            "decisions": [
                {
                    "source_id": "https://openai.com/about/",
                    "subject_match": "yes",
                    "source_authority": "official",
                    "source_role": "primary_evidence",
                    "allowed_usage": ["fact", "background"],
                    "confidence": "high",
                    "claim_support": [{"claim_id": "identity", "support": "yes", "reason": "official about page"}],
                    "adoption": "adopt",
                    "reason": "official preferred domain",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="OpenAI 是什么",
        mode="web_only",
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": url,
                    "content": "OpenAI consists of the nonprofit OpenAI Foundation and the for-profit OpenAI Group."
                    if "openai.com" in url
                    else "二手讨论",
                }
                for url in urls
            ]
        },
        synthesize_adapter=lambda _payload: "OpenAI 是 AI 研究与产品机构 [W1]",
    )

    assert any("site:openai.com" in query for query in fallback_queries)
    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["domain"] == "openai.com"
    assert result["meta"]["provider_status"]["fallback_attempts"][0]["trigger"] == "preferred_domain_missing"


def test_authoritative_web_plan_without_explicit_claims_still_runs_evidence_critic():
    claims = research_bridge._evidence_claims_for_plan(
        "OpenAI 是什么",
        {
            "evidence_contract": {
                "must_cover_subject": True,
                "prefer_authoritative_sources": True,
                "allow_summary_only": True,
            },
            "source_preferences": {
                "preferred_domains": ["openai.com"],
                "preferred_source_kinds": ["official"],
            },
        },
        [
            {
                "title": "OpenAI 二手教程",
                "url": "https://blog.csdn.net/example",
                "description": "二手教程",
            }
        ],
    )

    assert claims == ["answer_user_question"]


def test_evidence_claim_ids_are_normalized_to_schema_tokens():
    claims = research_bridge._evidence_claims_for_plan(
        "什么叫波动率",
        {
            "answer_claims_needed": ["波动率是衡量金融资产价格波动程度的指标", "current_metric"],
            "evidence_contract": {"must_cover_subject": True},
        },
        [],
    )

    assert claims == ["answer_user_question", "current_metric"]


def test_fallback_backend_sources_cannot_bypass_evidence_critic(monkeypatch):
    monkeypatch.setattr(research_bridge, "_available_fallback_search_backends", lambda primary: ["ddgs"])
    monkeypatch.setattr(research_bridge, "_default_web_search", lambda *_args: {"success": True, "data": {"web": []}})
    monkeypatch.setattr(
        research_bridge,
        "_web_search_with_backend",
        lambda query, limit, backend: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Unverified coding agent note",
                        "url": "https://example.org/coding-agent",
                        "description": "A note about coding agents.",
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_evidence_critic",
        lambda payload: {
            "decisions": [
                {
                    "source_id": "https://example.org/coding-agent",
                    "subject_match": "uncertain",
                    "source_authority": "unknown",
                    "source_role": "clue",
                    "allowed_usage": ["background"],
                    "confidence": "low",
                    "supports_claims": [],
                    "adoption": "clue",
                    "reason": "not reliable enough",
                }
            ]
        },
    )

    result = research_bridge.research_answer(
        query="什么是 coding agent",
        mode="web_only",
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "A note about coding agents."}]},
        synthesize_adapter=lambda payload: (_ for _ in ()).throw(AssertionError("critic-rejected fallback source should not synthesize")),
    )

    assert result["meta"]["web_sources"] == []
    assert result["meta"]["provider_status"]["fallback_attempts"][0]["backend"] == "ddgs"
    assert result["meta"]["search_quality"]["reason"] == "evidence_critic_rejected_all"
    assert "没有足够可靠的公开资料" in result["chat_preview"]
    assert "已尝试的搜索词" not in result["chat_preview"]
    assert "搜索结果主要问题" not in result["chat_preview"]


def test_critic_rejected_primary_results_trigger_post_critic_fallback(monkeypatch):
    monkeypatch.setattr(research_bridge, "_available_fallback_search_backends", lambda primary: ["ddgs"])
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "definition_explanation",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "波动率", "aliases": ["volatility"], "type_hint": "concept"},
            "queries": ["什么叫波动率"],
            "answer_claims_needed": ["answer_user_question"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 1,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_web_search",
        lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Volatility overview",
                        "url": "https://primary.example/volatility",
                        "description": "A page that appears relevant but is rejected by critic.",
                    },
                    {
                        "title": "Volatility explainer",
                        "url": "https://second.example/volatility",
                        "description": "Another relevant-looking page rejected by critic.",
                    },
                    {
                        "title": "Volatility guide",
                        "url": "https://third.example/volatility",
                        "description": "A third relevant-looking page rejected by critic.",
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_web_search_with_backend",
        lambda query, limit, backend: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Volatility definition",
                        "url": "https://fallback.example/volatility",
                        "description": "Volatility measures how much returns vary.",
                    }
                ]
            },
        },
    )

    def fake_critic(payload):
        source_ids = [source["source_id"] for source in payload["sources"]]
        if "https://fallback.example/volatility" in source_ids:
            return {
                "decisions": [
                    {
                        "source_id": "https://fallback.example/volatility",
                        "subject_match": "yes",
                        "source_authority": "reputable_secondary",
                        "source_role": "supporting_evidence",
                        "allowed_usage": ["fact"],
                        "confidence": "medium",
                        "supports_claims": ["answer_user_question"],
                        "adoption": "adopt",
                        "reason": "fallback source supports the claim",
                    }
                ]
            }
        return {
            "decisions": [
                    {
                        "source_id": "https://primary.example/volatility",
                        "subject_match": "uncertain",
                    "source_authority": "unknown",
                    "source_role": "reject",
                    "allowed_usage": [],
                    "confidence": "low",
                    "supports_claims": [],
                        "adoption": "reject",
                        "reason": "primary result is not usable evidence",
                    },
                    {
                        "source_id": "https://second.example/volatility",
                        "subject_match": "uncertain",
                        "source_authority": "unknown",
                        "source_role": "reject",
                        "allowed_usage": [],
                        "confidence": "low",
                        "supports_claims": [],
                        "adoption": "reject",
                        "reason": "primary result is not usable evidence",
                    },
                    {
                        "source_id": "https://third.example/volatility",
                        "subject_match": "uncertain",
                        "source_authority": "unknown",
                        "source_role": "reject",
                        "allowed_usage": [],
                        "confidence": "low",
                        "supports_claims": [],
                        "adoption": "reject",
                        "reason": "primary result is not usable evidence",
                    },
                ]
            }

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", fake_critic)

    result = research_bridge.research_answer(
        query="什么叫波动率",
        mode="web_only",
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": url,
                    "content": "Volatility measures how much returns vary."
                    if "fallback.example" in url
                    else "A page that appears relevant but is rejected by critic.",
                }
                for url in urls
            ]
        },
        synthesize_adapter=lambda payload: "波动率衡量收益变化幅度 [W1]",
    )

    assert result["meta"]["web_sources"][0]["url"] == "https://fallback.example/volatility"
    assert result["meta"]["provider_status"]["fallback_attempts"][0]["trigger"] == "evidence_critic_rejected_all"
    assert "补充搜索取得可采用来源" in " ".join(result["warnings"])


def test_insufficient_adopted_sources_trigger_fallback_and_recritic(monkeypatch):
    monkeypatch.setattr(research_bridge, "_available_fallback_search_backends", lambda primary: ["ddgs"])
    monkeypatch.setattr(
        research_bridge,
        "_default_query_planner",
        lambda payload: {
            "intent_label": "definition_explanation",
            "needs_web": True,
            "mode": "web_only",
            "subject": {"name": "波动率", "aliases": ["volatility"], "type_hint": "concept"},
            "queries": ["volatility definition financial term"],
            "answer_claims_needed": ["answer_user_question"],
            "evidence_contract": {
                "must_cover_subject": True,
                "minimum_independent_sources": 2,
                "prefer_authoritative_sources": True,
                "must_use_primary_source": False,
                "allow_summary_only": True,
                "answer_policy": "answer_only_supported",
            },
            "source_preferences": {},
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_default_web_search",
        lambda *_args: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Volatility Definition",
                        "url": "https://www.investopedia.com/terms/v/volatility.asp",
                        "description": "Volatility measures how much returns vary.",
                    },
                    {
                        "title": "General volatility note",
                        "url": "https://example.com/volatility",
                        "description": "General note about volatility.",
                    },
                    {
                        "title": "Volatility article",
                        "url": "https://sample.example/volatility",
                        "description": "Another volatility article.",
                    },
                ]
            },
        },
    )
    monkeypatch.setattr(
        research_bridge,
        "_web_search_with_backend",
        lambda query, limit, backend: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Volatility",
                        "url": "https://corporatefinanceinstitute.com/resources/career-map/sell-side/capital-markets/volatility-vol/",
                        "description": "Volatility is a statistical measure of dispersion of returns.",
                    }
                ]
            },
        },
    )

    def fake_critic(payload):
        decisions = []
        for source in payload["sources"]:
            if "volatility" not in source["source_id"]:
                continue
            if "investopedia.com" not in source["source_id"] and "corporatefinanceinstitute.com" not in source["source_id"]:
                decisions.append(
                    {
                        "source_id": source["source_id"],
                        "subject_match": "yes",
                        "source_authority": "unknown",
                        "source_role": "reject",
                        "allowed_usage": [],
                        "confidence": "low",
                        "supports_claims": [],
                        "adoption": "reject",
                        "reason": "critic did not accept this source for the definition",
                    }
                )
                continue
            decisions.append(
                {
                    "source_id": source["source_id"],
                    "subject_match": "yes",
                    "source_authority": "reputable_secondary",
                    "source_role": "supporting_evidence",
                    "allowed_usage": ["fact"],
                    "confidence": "medium",
                    "supports_claims": ["answer_user_question"],
                    "adoption": "adopt",
                    "reason": "source supports the definition",
                }
            )
        return {"decisions": decisions}

    monkeypatch.setattr(research_bridge, "_default_evidence_critic", fake_critic)

    result = research_bridge.research_answer(
        query="什么叫波动率",
        mode="web_only",
        extract_adapter=lambda urls: {
            "results": [
                {
                    "url": url,
                    "content": "Volatility is a statistical measure of dispersion of returns.",
                }
                for url in urls
            ]
        },
        synthesize_adapter=lambda payload: "波动率衡量收益变化幅度 [W1][W2]",
    )

    assert len(result["meta"]["web_sources"]) == 2
    assert result["meta"]["provider_status"]["fallback_attempts"][0]["trigger"] == "insufficient_adopted_sources"
    assert "补充搜索增加独立来源" in " ".join(result["warnings"])


def test_async_research_stream_reports_fallback_search_round(monkeypatch):
    events = []
    monkeypatch.setattr(research_bridge, "_available_fallback_search_backends", lambda primary: ["ddgs"])
    monkeypatch.setattr(research_bridge, "_default_web_search", lambda *_args: {"success": True, "data": {"web": []}})
    monkeypatch.setattr(
        research_bridge,
        "_web_search_with_backend",
        lambda query, limit, backend: {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Coding agents explained",
                        "url": "https://example.org/coding-agent",
                        "description": "AI coding agents plan code changes.",
                    }
                ]
            },
        },
    )

    result = asyncio.run(
        research_bridge.async_research_answer(
            query="什么是 coding agent",
            mode="web_only",
            extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "AI coding agents plan code changes."}]},
            synthesize_adapter=lambda payload: "Coding agent 是能规划代码任务的 AI 系统 [W1]",
            event_callback=lambda event: events.append(event),
        )
    )

    event_types = [event["type"] for event in events]
    assert "web_fallback_start" in event_types
    assert "web_fallback_done" in event_types
    assert event_types.index("web_fallback_start") < event_types.index("web_fallback_done")
    assert any(
        source.get("search_backend") == "ddgs"
        for event in events
        for source in event.get("web_sources", [])
    )
    assert result["meta"]["provider_status"]["fallback_attempts"][0]["backend"] == "ddgs"


@pytest.mark.parametrize(
    "case",
    [
        {"query": "OpenAI是什么意思", "mode": "web_only", "answer": "OpenAI 是人工智能研究和产品部署公司 [W1]", "expected_refs": ["W1"]},
        {"query": "SQL是什么意思", "mode": "web_only", "answer": "SQL 是结构化查询语言 [W1]", "expected_refs": ["W1"]},
        {"query": "AAPL market cap today", "mode": "web_only", "answer": "苹果市值需要以公开财经页为准 [W1]", "expected_refs": ["W1"]},
        {"query": "OpenAI 最近发布了什么新模型", "mode": "web_only", "answer": "公开资料显示最近发布信息见官方页面 [W1]", "expected_refs": ["W1"]},
        {"query": "Claude Code 是什么", "mode": "web_only", "answer": "Claude Code 是面向编码任务的 AI 工具 [W1]", "expected_refs": ["W1"]},
        {"query": "结合我的笔记和公开资料解释 RAG", "mode": "local_web", "answer": "RAG 需要结合本地资料和网页资料 [K1][W1]", "expected_refs": ["K1", "W1"]},
        {"query": "只查我的知识库", "mode": "local_only", "answer": "本地知识库已有说明 [K1]", "expected_refs": ["K1"], "no_web": True},
        {"query": "只看当前文章", "mode": "current_article_only", "answer": "当前文章讨论的是联网检索体验。", "expected_refs": [], "no_web": True},
        {"query": "摘要降级也要可引用", "mode": "web_only", "answer": "即使没有正文抽取，也可以依据搜索摘要回答 [W1]", "expected_refs": ["W1"], "summary_only": True},
        {"query": "模型引用错编号但正文有效", "mode": "web_only", "answer": "这是一段足够长的回答，应该保留正文并修正引用编号 [K1]", "expected_refs": ["W1"]},
        {"query": "什么是 coding agent", "mode": "web_only", "answer": "Coding agent 能规划、改代码并运行检查 [W1]", "expected_refs": ["W1"]},
        {"query": "段永平是谁", "mode": "web_only", "answer": "证据有限：段永平是企业家 [W1]", "expected_refs": ["W1"], "url": "https://baike.baidu.com/item/段永平/6041736"},
        {"query": "美联储主席是谁", "mode": "web_only", "answer": "美联储主席应以官方页面为准 [W1]", "expected_refs": ["W1"], "url": "https://www.federalreserve.gov/aboutthefed/bios/board/default.htm"},
        {"query": "英伟达现在市值是多少", "mode": "web_only", "answer": "英伟达市值需要以实时财经页面为准 [W1]", "expected_refs": ["W1"], "url": "https://companiesmarketcap.com/nvidia/marketcap/"},
        {"query": "A股四月份走势", "mode": "web_only", "answer": "A股走势需要结合市场数据页面判断 [W1]", "expected_refs": ["W1"], "url": "https://finance.eastmoney.com/a/cmarket.html"},
        {"query": "Anthropic 是什么公司", "mode": "web_only", "answer": "Anthropic 是 AI 公司 [W1]", "expected_refs": ["W1"], "url": "https://www.anthropic.com/company"},
        {"query": "什么是 RAG", "mode": "local_web", "answer": "RAG 结合检索与生成 [K1][W1]", "expected_refs": ["K1", "W1"]},
        {"query": "只用联网回答 RAG", "mode": "web_only", "answer": "RAG 是检索增强生成方法 [W1]", "expected_refs": ["W1"]},
        {"query": "联网搜索失败但本地可答", "mode": "local_web", "answer": "本地材料可以回答这个问题 [K1]", "expected_refs": ["K1"], "search_error": True},
        {"query": "抽取失败且无摘要不能采用", "mode": "web_only", "answer": "不应采用失败抽取源 [W1]", "expected_refs": [], "extract_failed_empty": True},
    ],
)
def test_research_quality_matrix_covers_twenty_modes_and_source_contracts(monkeypatch, case):
    local_called = {"value": False}
    web_called = {"value": False}

    def fake_local(_query, _warnings):
        local_called["value"] = True
        return "本地答案", [{"ref": "K1", "type": "knowledge", "id": "cap-1", "title": "本地材料"}]

    def fake_search(_query, _limit):
        web_called["value"] = True
        if case.get("search_error"):
            raise RuntimeError("search timeout")
        return {
            "success": True,
            "data": {
                "web": [
                        {
                            "title": f"Official source for {case['query']}",
                            "url": case.get("url") or "https://example.com/source",
                            "description": "" if case.get("extract_failed_empty") else f"Search summary with enough evidence about {case['query']}.",
                        }
                ]
            },
        }

    def fake_extract(urls):
        if case.get("extract_failed_empty"):
            return {"results": [{"url": urls[0], "content": "", "error": "extract failed"}]}
        if case.get("summary_only"):
            return {"results": []}
        return {"results": [{"url": urls[0], "content": "Full extracted page content with enough evidence."}]}

    monkeypatch.setattr(research_bridge, "_search_local", fake_local)
    result = research_bridge.research_answer(
        query=case["query"],
        mode=case["mode"],
        article_context={"title": "当前文章", "body": "当前文章讨论的是联网检索体验。"},
        search_adapter=fake_search,
        extract_adapter=fake_extract,
        synthesize_adapter=lambda payload: case["answer"],
    )

    content = result["chat_preview"]
    for ref in case["expected_refs"]:
        assert f"[{ref}]" in content
    assert not any(ref in content for ref in ["[K1]", "[W2]", "[W6]"] if ref.strip("[]") not in case["expected_refs"])
    assert result["meta"]["web_search_attempted"] is (case["mode"] in {"local_web", "web_only"})
    assert web_called["value"] is (case["mode"] in {"local_web", "web_only"} and not case.get("no_web", False))
    if case["mode"] == "local_only":
        assert local_called["value"] is True
    if case["mode"] == "web_only":
        assert result["meta"]["used_local"] is False
    if case.get("extract_failed_empty"):
        assert result["meta"]["web_sources"] == []
        assert "不应采用失败抽取源" not in content
        assert result["meta"]["search_quality"]["reason"] == "no_extractable_sources"
    if case.get("search_error"):
        assert result["meta"]["used_web"] is False
    for source in result["source_map"]:
        assert source["ref"] in case["expected_refs"]

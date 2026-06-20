import asyncio

import pytest

from bridge import research_bridge


@pytest.fixture(autouse=True)
def disable_default_query_planner(monkeypatch):
    monkeypatch.setattr(research_bridge, "_default_query_planner", lambda payload: [])


def test_query_planner_expands_market_cap_query():
    queries = research_bridge.plan_web_queries("苹果现在的市值多少")

    assert "Apple AAPL market cap today" in queries
    assert "Apple AAPL market capitalization current" in queries
    assert any("苹果" in query and "市值" in query for query in queries)


def test_query_planner_preserves_a_share_token():
    queries = research_bridge.plan_web_queries("四月份A股的走势和行情是什么")

    assert any("A股" in query for query in queries)
    assert all("股的走势" not in query for query in queries)
    assert any("2026年4月 A股" in query for query in queries)


def test_query_planner_does_not_pollute_independent_question_with_selection():
    queries = research_bridge.plan_web_queries(
        "现任美联储主席是谁",
        selection_context={"selected_text": "4月反弹以来"},
    )

    assert all("4月反弹以来" not in query for query in queries)
    assert all("月反弹以来" not in query for query in queries)


def test_query_planner_uses_official_fed_query():
    queries = research_bridge.plan_web_queries("现任美联储主席是谁")

    assert any("federalreserve.gov" in query for query in queries)
    assert any("Federal Reserve Chair" in query for query in queries)


def test_detect_query_intent_covers_quality_recovery_cases():
    assert research_bridge.detect_query_intent("现任美联储主席是谁") == "official_fact"
    assert research_bridge.detect_query_intent("四月份A股的走势和行情是什么") == "market_recap"
    assert research_bridge.detect_query_intent("苹果现在的市值多少") == "finance_fact"
    assert research_bridge.detect_query_intent("Anthropic是什么东西？") == "entity_explainer"
    assert research_bridge.detect_query_intent("这句话现在还成立吗") == "article_check"


def test_local_web_market_recap_triggers_web_search():
    assert research_bridge.should_search_web("四月份A股的走势和行情是什么", "local_web") is True


def test_query_planner_uses_llm_planner_for_symbolic_law_aliases():
    queries = research_bridge.plan_web_queries(
        "什么是τ定律",
        selection_context={
            "selected_text": "τ定律",
            "surrounding_before": "华为在论坛上提到",
            "surrounding_after": "和芯片半导体 3D 封装有关",
        },
        query_planner_adapter=lambda payload: {
            "queries": [
                "τ定律 华为 芯片半导体 3D封装",
                "涛定律 华为 3D封装",
                "Tao law semiconductor packaging",
            ]
        },
    )

    assert any("τ定律" in query for query in queries)
    assert any("涛定律" in query for query in queries)
    assert any("tao law" in query.lower() for query in queries)


def test_query_planner_uses_llm_planner_for_chip_major_query():
    queries = research_bridge.plan_web_queries(
        "芯片半导体的对口专业有哪些",
        query_planner_adapter=lambda payload: [
            "芯片半导体 对口专业 微电子 集成电路",
            "半导体行业 本科专业 电子科学与技术",
            "芯片行业 大学专业 材料科学 电子信息",
        ],
    )

    assert len(queries) == 3
    assert any("微电子" in query for query in queries)
    assert any("电子科学与技术" in query for query in queries)
    assert research_bridge.should_search_web("芯片半导体的对口专业有哪些", "local_web") is True


def test_query_planner_deterministic_fallback_does_not_invent_aliases():
    queries = research_bridge.plan_web_queries(
        "什么是τ定律",
        selection_context={
            "selected_text": "τ定律",
            "surrounding_before": "华为在论坛上提到",
            "surrounding_after": "和芯片半导体 3D 封装有关",
        },
        query_planner_adapter=lambda payload: [],
    )

    assert any("τ定律" in query for query in queries)
    assert any("tau law" in query.lower() for query in queries)
    assert all("涛定律" not in query and "掏定律" not in query for query in queries)


def test_query_planner_entity_fallback_does_not_hardcode_semantic_queries():
    queries = research_bridge.plan_web_queries(
        "Anthropic是什么东西？",
        selection_context={
            "selected_text": "Anthropic 的四层系统：从混乱数据到可信答案",
            "surrounding_after": "四层系统包括数据基础、可信来源、skills 和验证。",
        },
        query_planner_adapter=lambda payload: [],
    )

    assert queries == ["Anthropic是什么东西"]
    assert all(
        suffix not in query.lower()
        for query in queries
        for suffix in ["official", "about", "product", "company"]
    )


def test_source_ranker_for_entity_explainer_prefers_official_over_valuation_news():
    ranked = research_bridge.rank_web_sources(
        "Anthropic是什么东西？",
        [
            {
                "title": "估值已达615亿美元！Anthropic为何能吸引顶级资本疯狂追逐",
                "url": "https://wallstreetcn.com/articles/3742347",
                "description": "Anthropic 估值、融资和投资者消息。",
            },
            {
                "title": "Introducing Claude",
                "url": "https://www.anthropic.com/claude",
                "description": "Claude is Anthropic's family of AI assistants.",
            },
            {
                "title": "大模型公司对标：Anthropic公司",
                "url": "https://m.c114.com.cn/w5339-1264411.html",
                "description": "中文二手介绍。",
            },
        ],
    )

    assert ranked[0]["domain"] == "anthropic.com"
    assert ranked[0]["query_type"] == "entity_explainer"
    assert any("official_source" in tag for tag in ranked[0]["quality_tags"])
    assert ranked[-1]["domain"] == "wallstreetcn.com"


def test_synthesis_prompt_for_entity_explainer_limits_selection_and_timely_extras():
    prompt = research_bridge._build_synthesis_prompt(
        {
            "query": "Anthropic是什么东西？",
            "mode": "local_web",
            "query_intent": "entity_explainer",
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

    assert "先直接回答用户问题" in prompt
    assert "选区只用于消歧和补充" in prompt
    assert "不要主动展开融资、估值、股价、投资" in prompt


def test_general_knowledge_web_timeout_can_still_synthesize_answer():
    result = research_bridge.research_answer(
        query="芯片半导体的对口专业有哪些",
        mode="web_only",
        search_adapter=lambda *_args: (_ for _ in ()).throw(RuntimeError("search timeout")),
        synthesize_adapter=lambda payload: "芯片半导体相关专业主要包括微电子科学与工程、集成电路设计与集成系统、电子科学与技术等。",
    )

    assert "微电子科学与工程" in result["chat_preview"]
    assert "没有找到足够可靠的公开资料" not in result["chat_preview"]
    assert result["meta"]["used_web"] is False
    assert result["warnings"]


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


def test_source_ranker_prioritizes_finance_domains():
    ranked = research_bridge.rank_web_sources(
        "苹果现在的市值多少",
        [
            {
                "title": "个人博客谈苹果历史",
                "url": "https://blog.example.com/apple",
                "description": "history of Apple",
                "query": "苹果 市值",
            },
            {
                "title": "Apple market cap today",
                "url": "https://companiesmarketcap.com/apple/marketcap/",
                "description": "Apple market capitalization current",
                "query": "Apple market cap today",
            },
        ],
    )

    assert ranked[0]["domain"] == "companiesmarketcap.com"


def test_source_ranker_prioritizes_official_fed_domain():
    ranked = research_bridge.rank_web_sources(
        "现任美联储主席是谁",
        [
            {
                "title": "新任美联储主席要来啦，他是谁",
                "url": "https://h5.ifeng.com/article",
                "description": "中文二手新闻提到美联储主席候选人。",
                "query": "现任 美联储 主席",
            },
            {
                "title": "Board of Governors of the Federal Reserve System",
                "url": "https://www.federalreserve.gov/aboutthefed.htm",
                "description": "Official Federal Reserve Board page listing the Chair.",
                "query": "site:federalreserve.gov Federal Reserve Chair current",
            },
        ],
    )

    assert ranked[0]["domain"] == "federalreserve.gov"
    assert ranked[0]["query_type"] == "official_fact"


def test_source_ranker_prioritizes_china_market_recap_sources():
    ranked = research_bridge.rank_web_sources(
        "四月份A股的走势和行情是什么",
        [
            {
                "title": "天际股份明天走势怎么看-抖音",
                "url": "https://www.douyin.com/shipin/7606912622541178930",
                "description": "个股短视频。",
                "query": "四月份A股走势",
            },
            {
                "title": "2026年4月A股市场行情回顾 上证指数创业板指",
                "url": "https://finance.eastmoney.com/a/20260430.html",
                "description": "4月A股市场表现回顾，包含上证指数、创业板指和沪深300。",
                "query": "2026年4月 A股 行情 回顾 上证指数 创业板指",
            },
        ],
    )

    assert ranked[0]["domain"] == "finance.eastmoney.com"
    assert ranked[0]["query_type"] == "market_recap"


def test_source_ranker_penalizes_historical_only_results_for_current_query():
    ranked = research_bridge.rank_web_sources(
        "AAPL current market cap",
        [
            {
                "title": "Apple all-time high market cap history",
                "url": "https://finance.yahoo.com/history",
                "description": "historical market cap",
                "query": "AAPL market cap",
            },
            {
                "title": "AAPL market cap today",
                "url": "https://finance.yahoo.com/quote/AAPL/",
                "description": "current market capitalization",
                "query": "AAPL market cap today",
            },
        ],
    )

    assert ranked[0]["title"] == "AAPL market cap today"


def test_quality_gate_rejects_low_relevance_market_results():
    ranked = research_bridge.rank_web_sources(
        "四月份A股的走势和行情是什么",
        [
            {
                "title": "斐波那契是什么？为什么在交易中它的成功率这么高？",
                "url": "https://blog.tangly1024.com/article/fibonacci",
                "description": "行情走势和技术分析。",
                "query": "四月份 股的走势和行情是什么",
            },
            {
                "title": "天际股份明天走势怎么看-抖音",
                "url": "https://www.douyin.com/shipin/7606912622541178930",
                "description": "个股短视频。",
                "query": "四月份 股的走势和行情是什么",
            },
        ],
    )

    quality = research_bridge.evaluate_search_quality(
        "四月份A股的走势和行情是什么",
        "market_recap",
        ranked,
    )

    assert quality["passed"] is False
    assert quality["trusted_sources"] == 0


def test_low_quality_web_results_do_not_call_synthesizer():
    def bad_search(query, limit):
        return {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "天际股份明天走势怎么看-抖音",
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


def test_invalid_model_citations_fall_back_to_available_sources():
    result = research_bridge.research_answer(
        query="苹果现在的市值多少",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {"web": [{"title": "Apple market cap today", "url": "https://companiesmarketcap.com/apple/marketcap/", "description": "摘要"}]},
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "网页正文"}]},
        synthesize_adapter=lambda payload: "模型错误引用 [W6]",
    )

    assert "W6" not in result["chat_preview"]
    assert "[W1]" in result["chat_preview"]
    assert result["meta"]["web_sources"][0]["ref"] == "W1"
    assert any("未提供的来源编号" in warning for warning in result["warnings"])


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


def test_async_research_answer_awaits_extract_adapter():
    async def fake_extract(urls):
        return {"results": [{"url": urls[0], "content": "网页正文"}]}

    result = asyncio.run(
        research_bridge.async_research_answer(
            query="苹果现在的市值多少",
            mode="web_only",
            search_adapter=lambda *_args: {
                "success": True,
                "data": {"web": [{"title": "Apple market cap today", "url": "https://companiesmarketcap.com/apple/marketcap/", "description": "摘要"}]},
            },
            extract_adapter=fake_extract,
            synthesize_adapter=lambda payload: "回答 [W1]",
        )
    )

    assert result["meta"]["used_web"] is True
    assert result["meta"]["web_sources"][0]["extract_status"] == "full"
    assert result["meta"]["web_sources"][0]["excerpt"] == "网页正文"


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

    result = research_bridge.research_answer(
        query="查一下最新市场信息",
        mode="web_only",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {"web": [{"title": "网页", "url": "https://example.com", "description": "摘要"}]},
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "网页正文"}]},
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

    result = research_bridge.research_answer(
        query="结合我的知识库和最新公开信息判断",
        mode="local_web",
        search_adapter=lambda *_args: {
            "success": True,
            "data": {"web": [{"title": "网页", "url": "https://example.com", "description": "摘要"}]},
        },
        extract_adapter=lambda urls: {"results": [{"url": urls[0], "content": "网页正文"}]},
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

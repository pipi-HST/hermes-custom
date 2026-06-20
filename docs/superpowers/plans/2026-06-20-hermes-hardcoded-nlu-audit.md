# Hermes 自然语言硬编码审计

日期：2026-06-20

范围：
- `/Users/pipi/hermes-custom`
- `/Users/pipi/Downloads/hermes-agent-main`，当前分支：`codex/garden-draft-lifecycle`

本次只做扫描和方案整理，没有修改业务代码。

## 结论

当前 Hermes 的知识研究链路还没有真正做到“LLM 负责语义判断，代码负责确定性合同”。尤其是 `/Users/pipi/hermes-custom/bridge/research_bridge.py`，里面同时存在：

- 用正则/关键词判断用户意图。
- 用固定实体表和关键词表扩展 query。
- 用 intent 分支拼接 web search query。
- 用关键词对搜索结果加减分。
- 用固定建议词做 fallback。

这些都属于用户已经明确反对的方向：不是单纯的边界校验，而是代码在替 LLM 做语义规划。

更合理的边界应该是：

- LLM planner：判断 intent、选区是否相关、是否需要联网、query 怎么写、是否需要官方/时效来源、哪些旁支不要展开。
- 代码：校验 JSON schema、限制 query 数量和长度、裁剪隐私上下文、去重、执行搜索、超时处理、source URL 去重、citation 校验、来源编号不可编造、fallback 只保留原问题或返回“planner 不可用”状态。

## 审计标准

判定为问题的模式：

- 代码中出现中文/英文自然语言关键词，用来判断用户意图。
- 代码中维护实体同义词、股票 ticker、概念 alias、行业词表。
- 代码按 intent 拼搜索 query 或重写 query。
- 代码按语义关键词给来源排序，例如“融资/估值降权”“官方/现任加权”“行情/走势加权”。
- 测试把这些硬编码行为锁死。

不列为问题的模式：

- URL、JSON、Markdown、citation、文件路径解析。
- `MAX_WEB_QUERIES`、长度限制、去重、timeout、schema 校验。
- 来源编号 `[K1]` / `[W1]` 校验。
- 低质量域名黑名单这类非语义安全/质量 guardrail，前提是它不参与自然语言意图判断。

## P0 问题：research_bridge 语义判断硬编码

### 1. Web 触发和风险识别由关键词决定

文件：`/Users/pipi/hermes-custom/bridge/research_bridge.py`

位置：
- `WEB_INTENT_RE`：约第 29-33 行
- `LOCAL_ONLY_RE`：约第 34 行
- `HIGH_RISK_RE`：约第 35 行

问题：
- `最新/最近/市场/行情/芯片/半导体/定律/概念` 等词直接决定是否联网。
- `我的笔记/知识库里/这篇文章什么意思` 直接决定 local-only 倾向。
- `市场/股票/法律/医疗` 直接决定高风险。

为什么不对：
- 这会不断逼迫代码维护自然语言词表。
- 对新说法、错别字、上下文省略、跨语言表达不稳。
- 它和“LLM 负责语义判断”的边界冲突。

建议：
- 改为 `research_planner` 输出：
  - `needs_web: boolean`
  - `privacy_scope: current_article_only | local_only | local_web | web_only`
  - `risk_level: low | high`
  - `reason`
- 代码只校验这些字段是否合法；planner 不可用时用保守 fallback：默认不泄露私有上下文，只用原始问题或本地模式。

### 2. 实体和关键词扩展表

文件：`/Users/pipi/hermes-custom/bridge/research_bridge.py`

位置：
- `ENTITY_QUERY_EXPANSIONS`：约第 43-53 行
- `QUERY_KEYWORD_EXPANSIONS`：约第 55-60 行

问题：
- `苹果 -> Apple AAPL`
- `市值 -> market cap today`
- `股价 -> stock price today`

为什么不对：
- 这是典型硬编码 synonym/entity expansion。
- 会继续引出更多维护项，例如 Anthropic、Claude、半导体专业、τ定律等。

建议：
- 删除实体扩展表和关键词扩展表。
- 由 LLM planner 根据上下文生成 query。
- 代码只限制 query 条数、长度、隐私裁剪，不生成语义扩展。

### 3. 正则 intent 分类

文件：`/Users/pipi/hermes-custom/bridge/research_bridge.py`

位置：
- `SELECTION_DEPENDENT_RE` / `SELECTION_INDEPENDENT_RE`：约第 126-128 行
- `ENTITY_EXPLAINER_RE`：约第 128 行
- `_is_finance_fact_query`：约第 407-408 行
- `_is_recent_news_query`：约第 411-412 行
- `detect_query_intent`：约第 415-432 行

问题：
- `什么是/是谁/最近/最新/现任/主席/CEO/市值/股价` 等词直接映射 intent。
- `ENTITY_EXPLAINER_RE` 是最近新增的硬编码，应该撤掉。

为什么不对：
- 这是语义理解，不是确定性合同。
- 新 intent 会不断以正则形式增殖。

建议：
- 删除 `detect_query_intent` 的关键词分类职责。
- 建立 LLM planner 输出 `intent_label`，但代码不要围绕具体 label 写语义分支。
- 如果必须保留 intent，代码只能把它透传到日志、UI、prompt，不应用它拼 query 或语义排序。

### 4. plan_web_queries 继续拼语义 query

文件：`/Users/pipi/hermes-custom/bridge/research_bridge.py`

位置：
- `plan_web_queries`：约第 670-751 行

问题：
- LLM planner 空结果后，代码按 `official_fact / market_recap / article_check / entity_hint` 拼 query。
- 包含 `site:federalreserve.gov`、`A股 行情 回顾 上证指数`、`最新 公开信息`、`latest news` 等。

为什么不对：
- 这是代码层 query planner。
- 当前虽然“LLM planner 有结果时先返回”，但 planner 失败时会回到硬编码语义分支。

建议：
- planner 有效：使用 planner queries。
- planner 无效：只返回机械 fallback：
  - 原始用户问题的安全裁剪版。
  - 选区不可直接泄露；只在 planner 输出允许时使用。
  - 符号的机械转写可以保留，例如希腊字母 `τ -> tau`，但不能发明语义 alias。
- 删除按 intent 拼 query 的分支。

### 5. 低质量重写仍按 intent 拼 query

文件：`/Users/pipi/hermes-custom/bridge/research_bridge.py`

位置：
- `rewrite_queries_for_low_quality_results`：约第 995-1035 行

问题：
- 搜索质量差时，代码再次按 intent 生成 Fed、A股、财务类 query。

为什么不对：
- 这是第二套 query planner，且完全由代码维护。

建议：
- 改为调用同一个 LLM planner，传入：
  - 原始 query
  - 已尝试 queries
  - 搜索结果摘要
  - 失败原因
  - 隐私裁剪后的上下文
- planner 输出 revised_queries。
- 代码只校验 revised queries。

### 6. rank_web_sources 混入语义关键词排序

文件：`/Users/pipi/hermes-custom/bridge/research_bridge.py`

位置：
- `rank_web_sources`：约第 843-930 行

问题：
- `official_fact` 分支识别 `federal reserve/chair/主席/现任`。
- `market_recap` 分支识别 A股和走势行情。
- `finance_fact` 分支识别 market cap、AAPL、市值、股价。
- `recent_news` 分支识别 latest、recent、最新、公告。
- `entity_explainer` 分支识别 about/company/product/docs、融资、估值、投资。

为什么不对：
- 来源排序可以是 deterministic contract，但这里混入了语义理解。
- “融资估值是否旁支”也应该由 planner 或 synthesis contract 判断，而不是代码关键词。

建议：
- 保留非语义排序：
  - 搜索 provider 原始 rank。
  - URL 去重。
  - 抽取是否成功。
  - 域名基础信誉表，可作为通用质量 prior。
  - 低质量域名/短视频垃圾源惩罚。
- 移除按自然语言关键词加减分。
- 如果需要“source preferences”，由 planner 输出结构化偏好，例如：
  - `preferred_source_kinds: ["official", "documentation", "primary_source"]`
  - `avoid_topics: ["valuation", "funding"]`
  代码只把它作为可审计的过滤/排序配置执行，不自己从文本推断。

### 7. 概念定义 query 生成

文件：`/Users/pipi/hermes-custom/bridge/research_bridge.py`

位置：
- `_concept_definition_queries`：约第 1279-1307 行
- `_clean_concept_term`：约第 1310-1313 行

问题：
- 用 `什么是/是什么/定律/law` 识别概念定义问题。
- 拼 `xxx定律 是什么`、`xxx law meaning`。

为什么不对：
- 这就是固定概念 query planner。
- 用户已经明确反对为 `τ定律` 这类问题继续靠代码补词。

建议：
- 删除概念 query 生成。
- 如果 planner 不可用，只保留原始问题和机械符号转写。

### 8. core term/entity hint/keyword expansion

文件：`/Users/pipi/hermes-custom/bridge/research_bridge.py`

位置：
- `_extract_core_terms`：约第 1350-1381 行
- `_entity_hint`：约第 1384-1390 行
- `_keyword_expansions`：约第 1393-1400 行

问题：
- 内置 A股、ticker、Apple、Fed、美联储、市值、走势等保护词和停用词。
- `_entity_hint` 使用固定实体表。
- `_keyword_expansions` 按 `市值/现在/最新` 扩展英文 query。

建议：
- `_extract_core_terms` 只保留非常机械的 tokenization，用于粗略匹配和隐私裁剪，不参与 query 语义生成。
- 删除 `_entity_hint` 和 `_keyword_expansions`。
- 让 planner 输出 entities / query_terms。

### 9. 新增 entity_explainer helper 也应撤掉

文件：`/Users/pipi/hermes-custom/bridge/research_bridge.py`

位置：
- `_entity_name_for_explainer`：约第 1428-1440 行
- `_looks_like_entity_intro_page`：约第 1451-1453 行
- `_is_financing_or_valuation_side_topic`：约第 1456-1457 行

问题：
- 这些是上一轮新增的硬编码。
- `/about|/company|/product|/docs`、`融资|估值|投资` 都属于关键词策略。

建议：
- 删除。
- 若需要 official/about/product 这种偏好，由 planner 输出，不由代码从 URL/title 猜。

### 10. synthesis prompt 里也写死了具体语义

文件：`/Users/pipi/hermes-custom/bridge/research_bridge.py`

位置：
- `_build_synthesis_prompt`：约第 1637-1648 行

问题：
- `是什么/什么是/什么意思`、`融资/估值/股价/投资` 被写入固定回答合同。

为什么不对：
- prompt 可以表达通用回答原则，但不应把单类问题和旁支词表硬编码。

建议：
- prompt 改成使用 planner 输出的 `answer_contract`：
  - `primary_answer_first: true`
  - `selection_role: disambiguation | evidence | irrelevant`
  - `avoid_topics: [...]`
  - `freshness_sensitive: boolean`
- synthesis prompt 只引用这些结构化字段，不自己列关键词。

### 11. fallback suggestions 固定 query

文件：`/Users/pipi/hermes-custom/bridge/research_bridge.py`

位置：
- `_query_suggestions`：约第 1775-1787 行

问题：
- 返回 Fed、AAPL、CompaniesMarketCap 等固定建议。

建议：
- 删除。
- 如果需要建议，也由 planner 或 re-planner 输出。

## P1 问题：测试锁死硬编码行为

文件：`/Users/pipi/hermes-custom/tests/test_research_bridge.py`

位置：
- 约第 46-184 行，以及 market/Fed/finance 相关测试。

问题：
- 测试断言 `detect_query_intent("Anthropic是什么东西？") == "entity_explainer"`。
- 测试断言代码 fallback 不拼 `official/about/product/company`，但仍然把 Anthropic 和这些词写进测试。
- 测试断言 Fed/A股/Apple 的固定 query。

建议：
- 测试重写方向：
  - mock planner 返回结构化 plan，断言代码只执行 schema/长度/去重/隐私裁剪。
  - mock planner 失败，断言 fallback 只使用原始问题，不做语义扩展。
  - source ranking 测试改为非语义合同，例如 URL 去重、低质量域名惩罚、citation 不造假。
  - 不再断言具体 query 词。

## P1 问题：hermes-agent-main 工具描述和路由

### 12. research_with_web tool 描述把触发语义写给主 Agent

文件：`/Users/pipi/Downloads/hermes-agent-main/tools/research_with_web_tool.py`

位置：
- 约第 16-25 行

问题：
- tool schema description 写了“用户要求最新、联网、外部公开资料、市场/政策/价格/新闻/版本变化时，优先使用本工具”。
- 这不是 Python 分支，但它仍然是在 prompt/tool 描述层维护一组触发关键词。

建议：
- 改成能力描述，而不是关键词触发：
  - 本工具用于需要结合本地知识、当前文章和公开网页证据的研究回答。
  - 是否调用由主模型根据用户意图判断。
  - mode 由用户显式选择或 planner 决定。

### 13. Dashboard 前端注入研究模式提示

文件：`/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx`

位置：
- `withResearchModeInstruction`：约第 1475-1486 行

问题：
- 前端把“涉及知识研究、最新事实或外部资料时，优先调用 research_with_web”注入用户消息。
- 这会让主 Agent 的工具选择依赖硬编码提示。

建议：
- 前端只传结构化 mode 和用户问题。
- 不把工具调用策略拼进自然语言消息。
- 工具选择应由 agent/tool router 或 planner 完成。

### 14. Dashboard selection-chat 直接透传到 custom research_bridge

文件：`/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py`

位置：
- selection chat 调用 custom `async_research_answer`：约第 5889-5913 行

问题：
- main 这边本身没有重写语义，但它把 selection-chat 全部交给 custom 的 `research_bridge`。
- 因此 custom 的硬编码会直接影响 dashboard 知识库选区问答。

建议：
- main 不需要重写语义。
- 但 API contract 应改成接收/返回 planner plan：
  - request: question, mode, article_context, selection_context
  - bridge 内部先调用 planner
  - response meta 包含 `plan`, `plan_status`, `plan_warnings`

### 15. selection-chat 旧 prompt 路径仍有行为约束

文件：`/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py`

位置：
- `_knowledge_selection_chat_messages`：约第 5283-5341 行

问题：
- 这段 prompt 写死“有选区就优先解释选区，无选区就基于文章整体回答”。
- 它不是关键词硬编码，但属于固定回答策略。

建议：
- 如果这条路径仍被使用，应改为结构化 planner 输出 `selection_role`：
  - `primary_subject`
  - `context_only`
  - `irrelevant`
- prompt 根据 `selection_role` 执行，而不是简单按是否有 selected_text 判断。

### 16. 前端确认入库命令硬编码

文件：`/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx`

位置：
- `isCommitDraftCommand`：约第 1508-1512 行

问题：
- `存库|入库|保存|确认入库...` 由前端正则决定动作。

影响：
- 这不是 web search 语义，但属于同类“自然语言意图由代码判断”。

建议：
- 前端确认按钮优先。
- 聊天里的自然语言确认由主 Agent 或 dedicated planner 判断。
- 前端可以只识别显式机器命令，不识别自然语言。

## P2 问题：capture / answer 也有同类 fallback

### 17. Capture task_type fallback 用关键词判断

文件：`/Users/pipi/hermes-custom/agents/capture_agent.py`

位置：
- `_build_capture_intent`：约第 400-430 行

问题：
- `翻译/改写/润色/总结/整理` 决定 `task_type`。
- 该文件已有 `_build_capture_intent_with_planning` 和 LLM planner，但 fallback 仍会用关键词。

建议：
- fallback 不应做自然语言分类。
- planner 不可用时只生成保守默认：`task_type=capture`，并保留用户原话给后续 LLM prompt。

### 18. Capture material/detail/compression 用关键词判断

文件：`/Users/pipi/hermes-custom/agents/capture_agent.py`

位置：
- `_detect_material_intent`：约第 701-708 行
- `_detect_detail_level`：约第 710-720 行
- `_detect_compression`：约第 722-730 行
- `_detect_preserve_items`：约第 732-743 行

问题：
- `原文增强版/一句话/详细整理/原文质感/数字/案例` 等词决定整理契约。

建议：
- 让 capture planner 输出这些字段。
- fallback 只使用固定安全默认，不猜用户语义。

### 19. Answer save output type 用关键词判断

文件：`/Users/pipi/hermes-custom/bridge/answer_bridge.py`

位置：
- `choose_answer_output_type`：约第 80-83 行

问题：
- `卡片/知识卡/可复用` 决定 `knowledge_card`。
- `补进/合并/主笔记/原文增强` 决定 `enhance_capture`。

建议：
- 前端按钮或 tool 参数明确时按参数。
- 自然语言 `auto` 情况交给 planner，不在 bridge 用关键词猜。

### 20. Answer source selection 用关键词判断

文件：`/Users/pipi/hermes-custom/bridge/answer_bridge.py`

位置：
- `_should_use_latest_full_answer`：约第 501 行

问题：
- `回答/刚才/这段/保存/全文/完整/原样/不要修改` 决定是否使用 latest full answer。

建议：
- 由调用方传 `answer_source`。
- 如果 `auto`，使用 planner 或要求用户确认，不在 bridge 猜。

## 建议目标架构

### A. 新增统一 Research Planner contract

建议 planner 输出 JSON：

```json
{
  "intent_label": "free_text_label_for_logs_only",
  "needs_web": true,
  "mode": "local_web",
  "selection_role": "context_only",
  "risk_level": "low",
  "freshness_sensitive": false,
  "queries": ["..."],
  "source_preferences": {
    "prefer_primary_sources": true,
    "preferred_source_kinds": ["official", "documentation"],
    "avoid_topics": ["valuation", "funding"]
  },
  "answer_contract": {
    "primary_answer_first": true,
    "explain_selection_relation": true,
    "avoid_unasked_side_topics": true
  },
  "privacy": {
    "allowed_context_terms": ["..."],
    "may_use_selection_text": false
  }
}
```

关键点：
- `intent_label` 只用于日志和 UI，不驱动代码语义分支。
- `queries` 是 planner 的产物，不由代码拼。
- `source_preferences` 是 planner 明确输出，不由代码从 query/title 关键词推断。
- `answer_contract` 控制 synthesis prompt，不在 prompt 里写死“什么是/融资/估值”等词表。

### B. 代码保留的确定性职责

代码可以保留：

- JSON schema 校验。
- query 数量上限。
- query 长度上限。
- 去重。
- 私有上下文裁剪。
- search timeout 和 provider error。
- URL 去重。
- extract fallback。
- citation 校验。
- source_map 只展示被引用来源。
- low quality domain 安全过滤。
- planner 不可用时的保守 fallback。

代码不应该做：

- 用关键词识别 intent。
- 用固定实体表扩展 query。
- 用固定 query 模板重写 query。
- 用自然语言关键词判断来源是否相关。
- 用自然语言关键词判断回答要不要展开某个旁支。

### C. fallback 原则

当 planner 不可用：

- 不要切回关键词语义逻辑。
- 可以做：
  - 原始 query 安全裁剪。
  - 不泄露选区长文本。
  - 机械符号转写，例如希腊字母名。
  - 明确标记 `plan_status=unavailable`。
- 不可以做：
  - `苹果 -> AAPL`
  - `市值 -> market cap`
  - `什么是 -> entity_explainer`
  - `定律 -> law meaning`
  - `现任主席 -> official_fact`

## 推荐迁移顺序

### Phase 1：撤掉最近新增硬编码

目标：
- 删除 `ENTITY_EXPLAINER_RE` 和相关 helper。
- 删除对应测试。
- 保留已有测试里能证明“planner adapter 输出优先”的部分。

验证：
- `tests/test_research_bridge.py` 应改为断言 planner contract，而不是断言关键词 intent。

### Phase 2：Research Planner 结构化

目标：
- `_planned_web_queries` 升级为 `_planned_research_plan`。
- planner 输出完整 plan，不只是 queries。
- `detect_query_intent` 逐步退化为兼容字段，最终删除。

验证：
- mock planner 返回 plan，代码按 plan 执行。
- mock planner 空/异常，代码只做原始 query fallback。

### Phase 3：删除 deterministic semantic query builders

目标：
- 删除 `ENTITY_QUERY_EXPANSIONS`。
- 删除 `QUERY_KEYWORD_EXPANSIONS`。
- 删除 `_concept_definition_queries`。
- 删除 `rewrite_queries_for_low_quality_results` 的 intent 分支。
- 删除 `_query_suggestions` 固定建议。

验证：
- 所有语义 query 都来自 planner mock。
- fallback 不出现任何新增语义词。

### Phase 4：source ranking 去语义化

目标：
- 移除 `rank_web_sources` 中按自然语言关键词加减分的逻辑。
- 保留 provider rank、通用域名质量、低质量域名、抽取成功率。
- planner 输出 source preferences，代码只执行偏好，不自行推断。

验证：
- source ranking 测试只覆盖 deterministic source contract。
- 语义相关排序由 planner/retrieval query 本身解决。

### Phase 5：Capture / Answer auto intent 后移

目标：
- `capture_agent` fallback 不再通过关键词决定 task/material/detail。
- `answer_bridge` 的 `auto` output/source 判断改为 planner 或用户确认。
- 前端自然语言确认命令减少到显式按钮或机器命令。

验证：
- `auto` 场景下 planner mock 决策。
- planner 不可用时返回需要确认，而不是猜。

## 验收标准

完成后应满足：

- 搜索 `detect_query_intent`，不再有关键词分类。
- 搜索 `ENTITY_QUERY_EXPANSIONS` / `QUERY_KEYWORD_EXPANSIONS`，不存在。
- 搜索 `什么是|是什么|最近|最新|市值|融资|估值|定律`，不应出现在 query planning、source ranking、answer routing 代码中；只允许出现在测试样例、prompt 示例或用户可见文案中。
- planner adapter 测试覆盖：
  - planner 返回 plan。
  - planner 返回非法 JSON。
  - planner 超时。
  - planner 空结果。
  - planner 试图输出过长 query。
  - planner 试图泄露长选区。
- fallback 测试覆盖：
  - 只保留原始 query。
  - 不发明别名。
  - 不拼固定英文 query。
  - 不把选区长文本发给 web search。


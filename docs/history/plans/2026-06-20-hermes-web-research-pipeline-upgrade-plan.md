---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes Knowledge 通用联网研究 Pipeline 升级方案

日期：2026-06-20

执行状态：主执行方案。后续联网搜索修复和升级以本文档为准。

本文档定义 Hermes Knowledge 的联网能力升级方案。目标不是接入专门的实时行情、汇率、天气、日程等结构化数据工具，而是把当前较浅的“搜索摘要问答”升级为类似网页版 DeepSeek / Codex 的通用联网研究能力：

```text
问题理解 -> 多 query 搜索 -> 来源排序 -> 网页读取 -> 证据整理 -> 带引用回答
```

这套能力必须同时覆盖两个入口：

- `/knowledge-library` 知识库右侧 AI 对话。
- `/knowledge-chat` 工作台 Hermes 对话。

相关草案 `/Users/pipi/hermes-custom/docs/superpowers/plans/2026-06-20-hermes-knowledge-web-research-tool-plan.md` 作为背景参考，里面的工具接入内容已被本文档吸收。实际实现、验收和回归测试只对照本文档，避免两个方案并行导致口径冲突。

## 0. 完成定义

这次升级完成必须同时满足下面条件，不能只完成其中一个页面或只完成搜索 provider 配置。

1. `/knowledge-library` 右侧 AI 支持 `仅知识库`、`知识库+联网搜索`、`联网搜索` 三种模式。
2. `/knowledge-chat` 工作台 Hermes 对话支持同一套联网研究能力，不单独复制一份搜索逻辑。
3. 两个入口都调用共享后端 pipeline：query planner、多 query 搜索、来源排序、网页读取、证据综合、引用输出。
4. 系统不再把 `No web search provider configured` 直接暴露成最终体验；如果 provider 失败，要给出可理解的失败原因和本地 fallback。
5. 回答正文只引用实际使用过的 `[K]` 和 `[W]` 来源，底部只展示实际引用来源，不重复输出两块来源清单。
6. `复制`、`存笔记`、`做卡片` 继续可用，并保留联网来源 metadata，不能把网页来源误写成本地知识来源。
7. 至少通过后端单测、工作台前端 smoke、知识库右侧 AI 前端 smoke 三类验证。

## 1. 背景

当前联网能力已经初步接入，但实际体验仍有明显问题。

以“苹果现在的市值多少”为例，用户选择“仅联网”后，Hermes 可能能发起网页搜索，但最终回答仍然是：

> 根据您提供的联网资料，我无法给出苹果公司“现在”的精确市值。

这说明问题不只是“有没有联网 provider”，而是联网研究链路不够完整。

当前链路近似为：

```text
用户问题
-> 单次搜索
-> 读取搜索摘要
-> LLM 综合回答
```

这个链路适合简单事实，但不适合需要更准检索、更好来源、更完整网页证据的问题。

## 2. 非目标

本方案明确不做以下事情：

- 不做金融级实时行情 API。
- 不保证秒级准确的股价、市值、汇率、利率。
- 不做 Bloomberg / Wind / TradingView 一类专业数据终端。
- 不为每类结构化时效数据单独设计专用工具。

如果用户问“苹果现在市值多少”，Hermes 只需要像网页版 DeepSeek / Codex 一样，通过公开网页搜索和网页读取找到可靠页面，给出带来源的近似答案，并明确时效边界。

## 3. 目标

### 3.1 产品目标

升级后，用户在知识库右侧 AI 或工作台中提问：

```text
苹果现在的市值多少
段永平最近关于茅台说了什么
OpenAI 最近发布了什么新模型
这篇文章里的判断现在还成立吗
```

Hermes 应能：

- 自动生成更好的搜索 query。
- 搜索多个关键词组合。
- 优先选择更可靠的来源。
- 尝试读取网页正文或关键片段。
- 用 `[W1] [W2]` 形式标注网页来源。
- 在回答底部只展示实际引用过的来源。
- 搜不到时说明“本次搜索未找到足够证据”，而不是说“联网工具不可用”。

### 3.2 工程目标

联网研究逻辑必须收敛在共享后端层，不允许两个页面各写一套。

推荐核心位置：

```text
/Users/pipi/hermes-custom/bridge/research_bridge.py
/Users/pipi/Downloads/hermes-agent-main/tools/research_with_web_tool.py
/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx
```

其中：

- `research_bridge.py` 负责通用联网研究 pipeline。
- `selection-chat` route 调用同一套 pipeline。
- `research_with_web_tool.py` 也调用同一套 pipeline。
- 前端只负责模式选择、状态展示和来源渲染。

## 4. 当前问题拆解

### 4.1 搜索 query 太弱

用户问：

```text
苹果现在的市值多少
```

如果直接搜索中文原句，很容易拿到：

- 历史市值新闻。
- “曾突破万亿美元”的旧文章。
- 没有当前数值的网页摘要。
- 中文聚合站内容。

更合理的 query 应包括：

```text
Apple AAPL market cap today
Apple market capitalization current
AAPL market cap Yahoo Finance
Apple market cap CompaniesMarketCap
苹果 AAPL 市值
```

### 4.2 只看搜索摘要

搜索摘要通常很短，不一定包含答案。很多可靠网页会把关键值放在页面正文、表格或 meta 内容里。

如果只看摘要，LLM 很容易认为“没有当前数据”。

### 4.3 没有多 query 搜索

DeepSeek / Codex 类体验通常不是只搜一次，而是会根据问题生成多个搜索表达，然后合并证据。

当前 Hermes 应升级为：

```text
原问题
-> query planner
-> 2 到 4 个搜索 query
-> 搜索结果合并去重
```

### 4.4 来源没有排序

不同问题应有不同来源优先级。

例如市值类问题，优先：

```text
companiesmarketcap.com
finance.yahoo.com
nasdaq.com
macrotrends.net
investing.com
marketwatch.com
reuters.com
bloomberg.com
cnbc.com
```

观点、新闻类问题，优先：

```text
reuters.com
bloomberg.com
cnbc.com
wsj.com
ft.com
sina.com.cn
stcn.com
21jingji.com
xueqiu.com
```

没有排序时，模型可能读到低质量聚合页，或者把历史新闻当当前事实。

### 4.5 网页读取链路不完整

当前为了避免 FastAPI async route 中 `asyncio.run()` 报错，网页抽取在运行中的 event loop 下可能被降级为空结果。

这会导致：

```text
搜索能返回 URL
但网页正文没有读到
LLM 只能看摘要
```

这也是“联网搜索看起来可用，但回答能力很弱”的核心原因之一。

## 5. 总体架构

新增通用 Web Research Pipeline：

```text
Research Request
  -> Query Planner
  -> Web Search Collector
  -> Source Ranker
  -> Web Page Extractor
  -> Evidence Synthesizer
  -> Cited Answer
```

### 5.1 Query Planner

职责：把用户问题改写成多个搜索 query。

输入：

```python
{
  "query": "苹果现在的市值多少",
  "mode": "web_only",
  "article_context": {...},
  "selection_context": {"selected_text": "苹果"}
}
```

输出：

```python
[
  "Apple AAPL market cap today",
  "Apple market capitalization current",
  "AAPL market cap Yahoo Finance",
  "Apple market cap CompaniesMarketCap",
]
```

第一版用规则实现，不依赖 LLM。

实体映射示例：

| 中文 | 英文 / ticker |
| --- | --- |
| 苹果 | Apple AAPL |
| 英伟达 | Nvidia NVDA |
| 特斯拉 | Tesla TSLA |
| 微软 | Microsoft MSFT |
| 亚马逊 | Amazon AMZN |
| Meta | Meta META |
| 谷歌 | Alphabet Google GOOGL |
| 贵州茅台 | Kweichow Moutai 600519 |
| 腾讯 | Tencent 0700.HK |

关键词映射示例：

| 中文 | 英文 |
| --- | --- |
| 市值 | market cap, market capitalization |
| 股价 | stock price, share price |
| 现在 | current, today |
| 最新 | latest |
| 财报 | earnings, financial results |
| 发布 | release, announcement |

控制规则：

- 最多生成 4 个 query。
- 保留一个原始中文 query 作为兜底。
- 如果用户已经输入英文 ticker，不重复扩展过多 query。
- 不把用户整篇文章塞进搜索 query，只取问题、选区和少量关键词。

### 5.2 Web Search Collector

职责：对多个 query 调用现有 `web_search_tool`，合并结果。

第一版继续复用：

```text
/Users/pipi/Downloads/hermes-agent-main/tools/web_tools.py
web_search_tool(query, limit=...)
```

合并规则：

- URL 去重。
- 标题近似重复去重。
- 每个 query 最多取 5 条。
- 合并后最多保留 12 条候选。
- 记录每条结果来自哪个 query，便于调试。

输出结构：

```python
{
  "title": "...",
  "url": "...",
  "description": "...",
  "query": "Apple AAPL market cap today",
}
```

### 5.3 Source Ranker

职责：根据问题类型排序来源。

问题类型可以先分为：

```python
"finance_fact"
"recent_news"
"general_web"
"official_doc"
```

市值、股价、财报等归为 `finance_fact`。

排序维度：

```text
score =
  domain_score
  + title_match_score
  + snippet_match_score
  + freshness_score
  - low_quality_penalty
```

评分规则：

- domain 命中可信来源，加分。
- title 同时包含实体和核心关键词，加分。
- snippet 包含 market cap / current / today / AAPL 等，加分。
- 明显历史词如 `once reached`, `history`, `all-time high`，如果用户问当前值则降权。
- 无 URL、无标题、无摘要，降权。

第一版无需复杂模型，只做 deterministic ranking。

### 5.4 Web Page Extractor

职责：读取前 3 个高分来源的正文或关键片段。

第一版继续复用：

```text
web_extract_tool(urls, format="markdown")
```

但必须修复 async 调用问题。

推荐新增：

```python
async def async_research_answer(...):
    ...
```

FastAPI route 使用：

```python
result = await async_research_answer(...)
```

同步工具仍保留：

```python
def research_answer(...):
    ...
```

如果 `web_extract_tool` 返回 coroutine：

- 在 async path 中 `await result`。
- 在 sync path 中 `asyncio.run(result)`。

不要在已运行 event loop 中调用 `asyncio.run()`。

抽取结果结构：

```python
{
  "ref": "W1",
  "type": "web",
  "title": "...",
  "url": "...",
  "domain": "...",
  "query": "...",
  "excerpt": "...",
  "extract_status": "full" | "summary" | "failed",
  "rank_score": 12.5,
}
```

如果网页抽取失败：

- 保留搜索摘要。
- `extract_status = "summary"`。
- 不让整个回答失败。

### 5.5 Evidence Synthesizer

职责：把网页证据、本地知识、当前文章和选区交给 LLM 综合。

Prompt 必须明确：

- 网页来源只能引用 `[W1] [W2] ...`。
- 本地知识来源只能引用 `[K1] [K2] ...`。
- 不允许编造来源。
- 不要在正文末尾生成来源清单，来源由前端统一展示。
- 如果问题涉及“现在/当前/最新”，优先使用包含当前语义的网页证据。
- 如果只有历史数据，必须说明这是历史数据，不能冒充当前值。
- 如果多个来源数值不一致，说明差异。
- 可以回答“约为”，但必须说明公开网页信息可能延迟。

示例 prompt 片段：

```text
如果问题涉及当前价格、市值、汇率等时效信息：
- 优先使用网页证据中明确包含 current/today/latest 或等价含义的来源。
- 如果只找到历史信息，必须明确说“本次搜索只找到历史信息”。
- 不要把历史最高值当作当前值。
- 可以给出“约为”，但必须标注来源和时效边界。
- 不要说“联网工具不可用”，除非搜索调用失败。
```

## 6. 前端交互调整

### 6.1 模式文案

当前模式：

```text
当前文章
仅知识库
知识库+联网
仅联网
```

建议改为：

```text
当前文章
仅知识库
知识库+联网搜索
联网搜索
```

这样用户不会误以为“仅联网”等于专业实时数据终端。

### 6.2 Loading 文案

当前：

```text
AI 正在回答...
```

建议改成：

```text
正在搜索并读取网页...
```

第一版可以不做精细阶段状态。后续可升级为：

```text
正在生成搜索词...
正在搜索网页...
正在读取来源...
正在综合回答...
```

### 6.3 重新联网检索

当前按钮“重新联网检索”应该：

- 使用用户原始问题重新发起请求。
- 不再把问题改写成 `请刷新联网资料，并重新回答：...`。
- 保留当前模式，或明确切到 `local_web` / `web_only`。

### 6.4 来源展示

继续沿用已经修正过的策略：

- 前端只展示正文实际引用过的来源。
- 底部只出现一块“参考来源”。
- 不显示未被引用的候选来源。
- 不重复展示模型正文里的来源清单。

## 7. 后端接口影响

### 7.1 `research_bridge.py`

新增或调整函数：

```python
def plan_web_queries(query, article_context=None, selection_context=None) -> list[str]

def collect_web_search_results(queries, limit_per_query=5) -> list[dict]

def rank_web_sources(query, candidates) -> list[dict]

def extract_ranked_web_sources(sources) -> list[dict]

async def async_research_answer(...)
```

现有 `research_answer()` 保持兼容。

### 7.2 `web_server.py`

`selection-chat` route 从：

```python
result = research_answer(...)
```

调整为：

```python
result = await async_research_answer(...)
```

如果暂时不改整个 bridge，也可以先只让 route 使用 async extractor helper，但长期应收敛成 `async_research_answer`。

### 7.3 `research_with_web_tool.py`

工作台工具继续调用共享 bridge。

如果工具本身是同步工具，仍可调用 `research_answer()`；但它必须获得同样的 query planner、source ranker 和 extractor 能力。

## 8. 测试计划

### 8.1 单元测试

新增测试文件建议：

```text
/Users/pipi/hermes-custom/tests/test_research_web_pipeline.py
```

测试项：

1. `test_query_planner_expands_market_cap_query`

输入：

```text
苹果现在的市值多少
```

断言 query 包含：

```text
Apple AAPL market cap today
Apple market capitalization current
```

2. `test_query_planner_preserves_original_chinese_query`

断言保留中文兜底 query。

3. `test_search_collector_dedupes_urls`

多个 query 返回同一 URL 时，只保留一条。

4. `test_source_ranker_prioritizes_finance_domains`

CompaniesMarketCap / Yahoo Finance 排在普通博客前面。

5. `test_source_ranker_penalizes_historical_only_results_for_current_query`

用户问当前市值时，历史最高市值文章降权。

6. `test_async_research_answer_awaits_extract_adapter`

模拟 async extract adapter，确认不会出现 event loop 错误。

7. `test_uncited_sources_are_not_returned_to_ui`

保持引用过滤逻辑。

### 8.2 前端验证

必须用 Playwright 或浏览器真实操作验证。

用例 1：知识库右侧 AI

```text
选区：苹果
模式：联网搜索
问题：苹果现在的市值多少
```

期望：

- 回答给出近似值。
- 回答包含 `[W1]` 等引用。
- 底部显示实际引用来源。
- 不说“联网工具不可用”。

用例 2：知识库右侧 AI 普通联网问题

```text
问题：段永平最近关于茅台说了什么
```

期望：

- 能搜索公开网页。
- 能引用新闻/雪球/财经来源。
- 不出现无关 K/W 来源。

用例 3：工作台

```text
联网查一下苹果现在市值
```

期望：

- 工作台也走同样增强后的联网研究链路。
- 可保存为学习笔记。

用例 4：失败兜底

模拟 search provider 失败。

期望：

- 明确显示失败原因。
- 不把失败伪装成正常回答。

## 9. 实施顺序

### Phase 1：Query Planner 和 Source Ranker

改动范围：

```text
bridge/research_bridge.py
tests/test_research_web_pipeline.py
```

验收：

- query 扩展单测通过。
- source ranking 单测通过。
- 不影响现有前端。

### Phase 2：多 query 搜索和结果合并

改动范围：

```text
bridge/research_bridge.py
```

验收：

- 同一问题可以触发多个 query。
- URL 去重。
- `meta.retrieved_web_sources` 可看见候选来源。

### Phase 3：修复网页抽取 async 链路

改动范围：

```text
bridge/research_bridge.py
/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py
```

验收：

- 前端 route 不再触发 `asyncio.run()` 相关 warning。
- 至少能读取部分网页正文或保留明确 fallback。

### Phase 4：Prompt 改造

改动范围：

```text
bridge/research_bridge.py
```

验收：

- 当前/最新类问题不会轻易拒答。
- 能区分当前信息和历史信息。
- 回答有时效说明。

### Phase 5：前端文案和 loading 状态

改动范围：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/knowledge-chat/GatewayChat.tsx
```

验收：

- `仅联网` 改为 `联网搜索`。
- loading 文案改成 `正在搜索并读取网页...`。
- 来源展示仍只显示实际引用来源。

### Phase 6：端到端验证

执行：

```text
pytest tests/test_research_bridge.py tests/test_research_web_pipeline.py
npm run build
Playwright 前端验证知识库右侧 AI
Playwright 前端验证工作台 AI
```

验收后重启 `9119` dashboard。

## 10. 风险和边界

### 10.1 免费搜索 provider 不稳定

当前 `ddgs` 属于免费搜索方案，可能遇到：

- 搜索引擎限流。
- 某些 query 超时。
- 结果质量不稳定。

因此系统应保留 provider 错误提示，并允许未来切换 Exa / Tavily / Brave / Firecrawl。

### 10.2 网页抽取不保证成功

部分网站有：

- 反爬。
- 动态渲染。
- 付费墙。
- 地区限制。

抽取失败时，应退回搜索摘要，不应直接失败。

### 10.3 当前值不是实时值

对“现在市值/股价/汇率”等问题，回答必须说明：

```text
这是基于公开网页搜索结果的近似信息，可能有延迟。
```

不能包装成专业实时数据。

### 10.4 不扩大为复杂 Agent

第一版不要引入复杂 agent loop。保持 deterministic pipeline：

```text
最多 4 个 query
最多 12 个候选来源
最多读取 3 个网页
最多传 5 个 W 来源给 LLM
```

这样可控、可测、可回滚。

## 11. 最终验收标准

本方案完成后，以下结果必须成立：

- 知识库右侧 AI 问“苹果现在的市值多少”，能给出带来源的近似回答。
- 工作台问同样问题，也能给出带来源的近似回答。
- 普通联网问题仍能正常搜索和引用。
- 回答不会再因为只拿到摘要就轻易说“无法给出”。
- 前端不再显示技术性 warning。
- 来源只显示实际引用过的来源。
- 失败时说清楚是搜索失败、抽取失败还是证据不足。

## 12. 结论

Hermes 不需要在这一阶段做专门实时数据工具。更合理的升级是把联网能力做成通用 Web Research Pipeline：

```text
多 query 搜索
+ 来源排序
+ 网页读取
+ 证据综合
+ 引用展示
```

这能更贴近网页版 DeepSeek / Codex 的联网体验，也更符合 Hermes Knowledge 的定位：不是替代专业数据终端，而是帮助用户把公开网页信息、本地知识库和当前阅读上下文结合起来，形成可追溯、可保存、可复用的知识回答。

---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes 联网搜索质量恢复与可验证升级方案

日期：2026-06-20

执行状态：已按本文档完成第一轮实现和验证。本文档是对既有联网研究 pipeline 的二阶段质量修复方案，重点解决“搜不到用户真正想要的答案”和“低质量来源被模型综合成错误结论”的问题。

实施记录：

- 已在 `/Users/pipi/hermes-custom/bridge/research_bridge.py` 实现 intent detection、选区依赖判断、实体保留 query planner、来源排序升级、搜索质量门禁、低质量 fallback、二次检索 query rewrite 和当前日期综合约束。
- 已在 `/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py` 的 selection-chat response 中暴露轻量 `meta`，包含 `web_queries`、`query_intent`、`search_quality`、`rewritten_web_queries`、`provider_status`、`web_search_attempted`，便于 API smoke 和后续 UI 调试。
- 已在 `/Users/pipi/hermes-custom/tests/test_research_bridge.py` 增加 A 股 tokenizer、选区污染、Fed 官方源、A 股来源排序、质量门禁、低质量 fallback、`local_web` 触发联网等回归测试。
- 验证通过：`tests/test_research_bridge.py` 21 passed；主 repo web tool/selection-chat 回归 4 passed；`bridge/research_bridge.py` 与 `hermes_cli/web_server.py` py_compile 通过；`web` 前端 `npm run build` 通过；`/knowledge-library` 和 `/knowledge-chat` Playwright 快照确认联网模式按钮仍可用；真实 selection-chat API smoke 覆盖 A 股与美联储两个错例。

关联文档：

- `/Users/pipi/hermes-custom/docs/superpowers/plans/2026-06-20-hermes-web-research-pipeline-upgrade-plan.md`
- `/Users/pipi/hermes-custom/docs/superpowers/plans/2026-06-20-hermes-knowledge-web-research-tool-plan.md`

## 0. 背景与结论

当前 Hermes 已经具备基础联网研究链路：

```text
用户问题 -> query planner -> 多 query 搜索 -> 来源排序 -> 网页抽取 -> LLM 综合 -> 引用输出
```

但实测体验仍然明显弱于预期。典型问题：

1. 用户问“四月份 A 股的走势和行情是什么”，实际搜索 query 变成了“四月份 股的走势和行情是什么”，`A股` 被清洗坏了。
2. 用户选区是“4月反弹以来”，再问“现任美联储主席是谁”，选区被拼进联网搜索，产生“月反弹以来 现任美联储主席是谁”这类污染 query。
3. “现任美联储主席是谁”这类官方事实问题，没有优先检索 `federalreserve.gov`，反而使用中文二手新闻和聚合站，导致结论不可靠。
4. 搜索结果相关性很低时，系统仍然把它们交给 LLM 综合，没有触发重写 query、降级拒答或提示用户结果质量不足。
5. 免费 DDGS 后端不稳定，存在超时和结果质量波动；当正文抽取失败时，系统退化到搜索摘要，答案质量会进一步下降。

判断：这不是单一 prompt 问题，而是检索工程问题。需要从 query 生成、搜索后端、来源排序、质量门禁、正文抽取和评测集六层一起修复。

## 1. 本次目标

### 1.1 产品目标

联网模式下，Hermes 应该像一个可审计的研究助手：

- 理解用户真正想查什么，而不是机械拼接选区和问题。
- 对不同问题选择不同检索策略。
- 优先使用权威源、原始源、主流财经/新闻源。
- 发现搜索结果质量不足时主动重搜或明确失败。
- 不把低相关来源包装成确定答案。
- 每个结论都能追溯到实际引用的 `[W]` 来源。

### 1.2 工程目标

把联网搜索质量控制收敛在共享后端 pipeline：

```text
/Users/pipi/hermes-custom/bridge/research_bridge.py
```

两个入口继续共用同一套逻辑：

```text
/knowledge-library
/knowledge-chat
```

前端只展示模式、状态、引用和 warning，不在页面内复制检索逻辑。

### 1.3 非目标

本阶段不做以下内容：

- 不接 Wind、Bloomberg、TradingView 等专业行情终端。
- 不承诺金融数据秒级实时准确。
- 不做每个垂类的完整专用 agent。
- 不重构整个 Hermes 知识库架构。
- 不删除当前 DDGS fallback，但不能继续只依赖它支撑质量。

## 2. 成功标准

### 2.1 必须修复的错例

#### Case A：A 股月度行情

输入：

```text
选区：4月反弹以来
问题：四月份A股的走势和行情是什么
模式：知识库+联网搜索 或 联网搜索
```

期望行为：

- 搜索 query 必须保留 `A股`。
- 至少生成一个结构化 query，例如：

```text
2026年4月 A股 行情 回顾 上证指数 创业板指
2026年4月 A股 走势 复盘 沪深300
```

- 搜索结果不能主要来自抖音、泛博客、无关个股页面。
- 回答应区分：
  - 4 月 A 股整体走势。
  - “4月反弹以来”在当前文章中的上下文含义。
  - 如果公开资料不足，应说明不足，而不是引用无关页面硬答。

#### Case B：现任美联储主席

输入：

```text
选区：4月反弹以来
问题：现任美联储主席是谁
模式：联网搜索
```

期望行为：

- 搜索 query 不应混入“4月反弹以来”。
- 至少生成一个官方源 query，例如：

```text
site:federalreserve.gov current Federal Reserve Chair
Federal Reserve Board Chair current
```

- 来源优先级应把 `federalreserve.gov` 排在中文二手新闻之前。
- 如果官方来源与二手来源冲突，应以官方来源为准，并说明时间点。

#### Case C：苹果当前市值

输入：

```text
问题：苹果现在的市值多少
模式：联网搜索
```

期望行为：

- query 应包含英文实体和 ticker：

```text
Apple AAPL market cap today
Apple market capitalization current
AAPL market cap CompaniesMarketCap
```

- 优先来源应包括 `companiesmarketcap.com`、`finance.yahoo.com`、`nasdaq.com` 等。
- 回答必须明确“公开网页显示值可能延迟”，不能冒充实时行情终端。

### 2.2 质量门槛

一次联网回答进入 LLM 综合前，必须满足至少一个条件：

- top 3 搜索结果中至少 1 条来源域名匹配该问题类型的权威域名规则。
- top 5 搜索结果中至少 2 条标题或摘要命中核心实体与核心意图。
- 正文抽取成功获得至少 1 条非空页面内容，且内容包含核心实体。

如果不满足，pipeline 必须执行：

```text
重写 query -> 再搜一次 -> 重新排序
```

如果第二轮仍不满足，回答应进入“证据不足”路径，而不是让 LLM 基于垃圾来源自由发挥。

## 3. 当前根因分析

### 3.1 中文金融实体被 tokenizer 破坏

当前 `_search_query_from_private_context` 用正则提取：

```python
[A-Za-z0-9][A-Za-z0-9._-]{1,}|[\u4e00-\u9fff]{2,}
```

这会把 `A股` 拆坏，因为它是英文字母和中文的混合实体。实际复现中：

```text
四月份A股的走势和行情是什么
-> 四月份 股的走势和行情是什么
```

影响：

- 搜索引擎失去关键实体。
- “A股”退化成普通“股”，召回大量个股、博客、短视频页面。

修复方向：

- 增加混合实体 token 支持。
- 明确保留常见金融实体：
  - `A股`
  - `港股`
  - `美股`
  - `上证指数`
  - `创业板指`
  - `沪深300`
  - `中证500`

### 3.2 选区被错误当成搜索关键词

选区的职责应该是“解释用户意图和当前文章上下文”，不应该默认进入联网 query。

当前问题：

```text
选区：4月反弹以来
问题：现任美联储主席是谁
```

实际 query：

```text
月反弹以来 现任美联储主席是谁
```

这会污染检索，尤其当用户只是连续在同一选区附近追问一个独立问题时。

修复方向：

- 将 query planner 改成两阶段：

```text
1. 判断问题是否依赖选区
2. 只有依赖时才把选区压缩成 query hint
```

依赖选区的例子：

```text
这句话现在还成立吗
这个判断有没有新证据
文中提到的公司最近怎么样
```

不依赖选区的例子：

```text
现任美联储主席是谁
苹果现在市值多少
今天人民币汇率是多少
```

### 3.3 问题类型识别过粗

当前基本只有：

- finance fact
- recent news
- general web

这不够。至少需要增加：

| 类型 | 例子 | 检索策略 |
| --- | --- | --- |
| official_fact | 现任美联储主席是谁 | 官方域名优先 |
| market_recap | 4月A股走势 | 时间 + 市场 + 指数 + 复盘 |
| finance_fact | 苹果市值多少 | ticker + 英文财务关键词 |
| recent_news | 最近发生了什么 | 主流新闻源 + 时间词 |
| article_check | 文中判断现在还成立吗 | 选区实体 + 最新公开证据 |
| general_web | 普通知识查询 | 常规多 query |

### 3.4 来源排序缺少权威域名体系

不同问题要有不同域名权重。

官方事实：

```text
federalreserve.gov
sec.gov
whitehouse.gov
congress.gov
gov.cn
pbc.gov.cn
csrc.gov.cn
sse.com.cn
szse.cn
```

A 股行情与财经复盘：

```text
eastmoney.com
finance.sina.com.cn
stcn.com
cs.com.cn
cnstock.com
cls.cn
21jingji.com
sse.com.cn
szse.cn
```

美股/公司财务：

```text
companiesmarketcap.com
finance.yahoo.com
nasdaq.com
macrotrends.net
marketwatch.com
reuters.com
bloomberg.com
cnbc.com
```

排序不仅要加分，还要对低质量域名降权：

```text
douyin.com
toutiao.com/w
泛个人博客
无标题聚合页
明显短视频页
```

注意：降权不等于禁止。某些问题可能确实需要社媒来源，但财经事实和官方事实不应优先使用这些来源。

### 3.5 搜索后端不稳定

DDGS 的优点：

- 免费。
- 无 API key。
- 适合做 fallback。

DDGS 的问题：

- 不稳定，可能超时。
- 中文财经搜索质量波动大。
- search-only，不能负责网页正文抽取。
- 报错里可能出现 Startpage/DuckDuckGo 后端超时。

修复方向：

- 保留 DDGS 为免费 fallback。
- 增加可配置的高质量 provider 优先级：

```text
Brave Search -> Tavily/Exa -> DDGS
```

第一阶段可以不立即接新 provider，但要把 provider 质量状态暴露到 meta/warnings，避免用户以为是“AI 没能力”。

### 3.6 缺少搜索质量门禁

当前只要有搜索结果，就会进入综合。

正确行为应该是：

```text
搜索结果 -> 相关性评分 -> 是否达标？
  是 -> 网页抽取 -> 综合
  否 -> query rewrite -> 第二轮搜索
    仍否 -> 证据不足回答
```

这个门禁必须在 LLM 之前执行。

## 4. 总体方案

新增或强化 6 个模块，仍然放在 `research_bridge.py`，避免过早拆文件：

```text
detect_query_intent()
plan_web_queries()
search_quality_gate()
rewrite_queries_for_low_quality_results()
rank_web_sources()
extract_ranked_web_sources()
```

整体流程：

```text
用户问题 + 选区 + 当前文章
  -> 意图识别
  -> query 规划
  -> 第一轮并发搜索
  -> 结果去重
  -> 来源排序
  -> 质量门禁
      -> 通过：抽取正文
      -> 不通过：重写 query 并第二轮搜索
  -> 抽取正文
  -> 引用校验
  -> LLM 综合或证据不足 fallback
```

## 5. 详细实施步骤

### Phase 1：建立可复现问题集

目标：先把当前坏例子固定成测试，避免凭感觉调 prompt。

新增测试文件或扩展现有文件：

```text
/Users/pipi/hermes-custom/tests/test_research_bridge.py
```

新增测试：

1. `test_query_planner_preserves_a_share_token`
   - 输入：`四月份A股的走势和行情是什么`
   - 断言：至少一个 query 包含 `A股`
   - 断言：不能出现只剩 `股的走势`

2. `test_query_planner_does_not_pollute_independent_question_with_selection`
   - 输入问题：`现任美联储主席是谁`
   - 选区：`4月反弹以来`
   - 断言：query 不包含 `4月反弹以来`

3. `test_query_planner_uses_official_fed_query`
   - 输入：`现任美联储主席是谁`
   - 断言：query 包含 `federalreserve.gov` 或 `Federal Reserve Chair`

4. `test_source_ranker_prioritizes_official_fed_domain`
   - 给定 `federalreserve.gov` 和中文二手新闻
   - 断言：官方源排第一

5. `test_quality_gate_rejects_low_relevance_market_results`
   - 给定抖音、斐波那契博客、个股页面
   - 断言：质量门禁不通过

验证：

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest tests/test_research_bridge.py -q
```

预期：先红后绿。

### Phase 2：修复 query 清洗与实体保留

目标：不再把用户核心实体清洗坏。

修改点：

```text
_search_query_from_private_context()
```

建议规则：

- 先用短语替换保护常见实体。
- 支持英文/数字/中文混合 token。
- 保留市场类固定词。
- 对用户原问题做最小清洗，不做过度分词。

候选实现思路：

```text
1. normalize whitespace
2. extract protected phrases
3. extract mixed tokens:
   - A股
   - 港股
   - 美股
   - AAPL
   - 600519
   - 0700.HK
4. merge Chinese terms
5. unique + length cap
```

验收：

- `A股` 不丢。
- `AAPL` 不丢。
- `0700.HK` 不丢。
- 不能把私有文章大段原文泄漏进搜索 query。

### Phase 3：选区依赖判断

目标：选区只在需要时影响 query。

新增函数：

```text
_question_depends_on_selection(query: str, selection: str) -> bool
```

规则优先用确定性判断：

依赖选区：

```text
这句话
这段
文中
上面
这个判断
这里说的
还成立吗
有没有更新
```

不依赖选区：

```text
是谁
是什么
多少
现在的
最新的
今天的
```

注意：这不是完美语义理解，但足够修复当前主要污染问题。

修改 `plan_web_queries()`：

```text
if selection and question_depends_on_selection:
    add selection-based query
else:
    do not add selection to web query
```

验收：

- “现任美联储主席是谁”不带选区。
- “这句话现在还成立吗”可以带选区实体。

### Phase 4：增加问题类型识别

新增函数：

```text
detect_query_intent(query: str, selection_context: dict | None = None) -> str
```

第一版用规则即可：

```text
official_fact:
  美联储主席 / Federal Reserve Chair / 总统 / CEO / 任期 / 现任

market_recap:
  A股 / 港股 / 美股 / 上证指数 / 创业板 / 沪深300
  + 走势 / 行情 / 复盘 / 反弹 / 下跌 / 上涨

finance_fact:
  市值 / 股价 / 财报 / EPS / PE / 估值

recent_news:
  最新 / 最近 / 今天 / 新闻 / 发布 / 公告

article_check:
  这句话 / 这个判断 / 文中 / 还成立吗
```

将 intent 写入 source meta：

```json
{
  "query_intent": "market_recap"
}
```

验收：

- 测试覆盖上述三类关键错例。
- 前端无需立刻展示 intent，但 API meta 中可见，方便调试。

### Phase 5：重写 query planner

不同 intent 生成不同 query。

#### official_fact

例：`现任美联储主席是谁`

生成：

```text
site:federalreserve.gov Federal Reserve Chair current
Federal Reserve Board Chair current
Federal Reserve Board Governors Chair
```

中文问题可以保留一个中文 query，但不能只搜中文：

```text
现任 美联储 主席
```

#### market_recap

例：`四月份A股的走势和行情是什么`

生成：

```text
2026年4月 A股 行情 回顾 上证指数 创业板指
2026年4月 A股 走势 复盘 沪深300
4月 A股 市场表现 上证指数 创业板指
```

如果问题没有年份：

- 默认使用当前年份 `2026`。
- 同时保留一个不带年份 query，避免历史资料缺失。

#### finance_fact

沿用已有实体扩展，但增加更多中文资产实体：

```text
苹果 -> Apple AAPL
英伟达 -> Nvidia NVDA
特斯拉 -> Tesla TSLA
茅台 -> Kweichow Moutai 600519
腾讯 -> Tencent 0700.HK
```

#### article_check

从选区和上下文提取实体，不直接整段拼接：

```text
选区实体 + 最新 / 近况 / 公开信息
```

验收：

- 每类 intent query 数量不超过 `MAX_WEB_QUERIES`。
- query 长度不超过既有限制。
- 不泄漏长篇私有文章内容。

### Phase 6：来源排序升级

新增域名分组：

```text
OFFICIAL_DOMAINS
CHINA_MARKET_DOMAINS
FINANCE_DOMAINS
NEWS_DOMAINS
LOW_QUALITY_DOMAINS
```

排序因子：

1. intent-domain 匹配加分。
2. title/description 命中核心实体加分。
3. title/description 命中核心意图加分。
4. 低质量域名降权。
5. 无标题、无摘要、短视频页降权。
6. 历史/旧闻对“当前”问题降权。

核心实体提取：

```text
query_terms = extract_core_terms(query)
```

不要把“是什么”“多少”“现在”当核心实体。

验收：

- Fed 官方源高于中文二手源。
- A 股 market recap 中，东方财富/证券时报/中证报高于抖音/泛博客。
- 苹果市值中，CompaniesMarketCap/Yahoo Finance 高于历史新闻。

### Phase 7：搜索质量门禁与二次检索

新增函数：

```text
evaluate_search_quality(query, intent, ranked_sources) -> SearchQuality
```

返回结构：

```python
{
    "passed": bool,
    "score": float,
    "reason": str,
    "matched_terms": [...],
    "trusted_sources": int,
}
```

门禁规则第一版：

```text
passed if:
  top source score >= threshold
  OR trusted domain count >= 1 and core entity matched
  OR at least 2 top-5 sources match core terms
```

如果不通过：

```text
rewrite_queries_for_low_quality_results()
```

重写策略：

- official_fact：强制 `site:` 官方域。
- market_recap：增加年份、指数名、`回顾`、`复盘`。
- finance_fact：增加 ticker 和英文关键词。
- general：去掉选区噪声，保留实体和动词。

最多二次检索一次，避免无限循环。

验收：

- A 股坏结果集触发重搜。
- 重搜后如果仍然坏，answer 进入证据不足 fallback。
- warnings 中展示简洁原因：

```text
联网搜索结果相关性不足，已尝试重写搜索词；仍未找到足够可靠来源。
```

### Phase 8：证据不足 fallback

当前 fallback 偏向“把可用材料摘要出来”。联网搜索质量差时，这仍可能误导。

新增低质量 fallback：

```text
本次联网搜索没有找到足够可靠的公开资料来回答这个问题。

已尝试的搜索词：
- ...

搜索结果主要问题：
- 未命中核心实体
- 来源权威性不足
- 页面只涉及个股/历史文章/短视频，与问题不匹配

建议改问：
- 2026年4月A股行情回顾
- 4月A股上证指数创业板走势
```

要求：

- 不引用低质量 `[W]` 来源当依据。
- 可以在 debug meta 中保留 retrieved sources。
- UI 底部不要展示未被引用的来源。

### Phase 9：搜索 provider 策略

第一阶段最小改动：

- 保留 DDGS。
- warnings/meta 中明确 provider failure。
- 增加 provider latency 和 failure 信息。

第二阶段建议接入：

```text
Brave Search API
Tavily
Exa
```

优先级建议：

```text
Brave Search: 通用搜索质量和稳定性较好
Tavily: research-style answer and extract 方便
Exa: 语义检索强，但需要控制成本
DDGS: 免费 fallback
```

provider 选择原则：

- 不把 provider key 写死进代码。
- 走 Hermes 现有 tools/provider 配置体系。
- provider 不可用时降级，但必须给出可理解 warning。

### Phase 10：UI 调试可见性

不做复杂 UI，但建议在开发/调试模式下显示：

```text
检索模式：联网搜索
搜索词：...
搜索状态：已重写 / 已降级 / 抽取失败
来源质量：通过 / 不足
```

第一版可只放入 API meta，不改 UI；后续再决定是否显示给用户。

## 6. 验证计划

### 6.1 单元测试

命令：

```bash
cd /Users/pipi/hermes-custom
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest tests/test_research_bridge.py -q
```

必须覆盖：

- query planner。
- intent detection。
- source ranking。
- quality gate。
- citation filtering。
- low-quality fallback。

### 6.2 API smoke

启动 dashboard：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -c 'from hermes_cli.web_server import start_server; start_server(host="127.0.0.1", port=9119, open_browser=False, embedded_chat=False)'
```

测试 selection-chat：

```bash
POST /api/knowledge/library/items/capture/{id}/selection-chat
```

用例：

```json
{
  "question": "四月份A股的走势和行情是什么",
  "selected_text": "4月反弹以来",
  "mode": "local_web"
}
```

检查：

- `meta.web_queries` 包含 `A股`。
- `meta.web_queries` 不出现被破坏的 `股的走势`。
- `meta.query_intent == "market_recap"`。
- 如果低质量，warnings 说明重搜或证据不足。

用例：

```json
{
  "question": "现任美联储主席是谁",
  "selected_text": "4月反弹以来",
  "mode": "web_only"
}
```

检查：

- `meta.web_queries` 不包含 `4月反弹以来`。
- retrieved sources 中官方源优先。
- 回答引用的来源真实存在。

### 6.3 前端 smoke

路径：

```text
http://127.0.0.1:9119/knowledge-library
http://127.0.0.1:9119/knowledge-chat
```

检查：

- 模式按钮仍存在。
- loading 状态正常。
- 复制、存笔记、做卡片仍可用。
- 来源卡片不展示未引用来源。
- warnings 不撑爆布局。

### 6.4 回归测试

主 repo：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest \
  /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_research_with_web_tool.py \
  /Users/pipi/Downloads/hermes-agent-main/tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_knowledge_library_selection_chat_uses_async_research_bridge \
  -q
```

前端：

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm run build
```

## 7. 实施顺序

推荐顺序：

1. 写红测试：固定 A 股 tokenizer、选区污染、Fed 官方源、低质量门禁。
2. 修 tokenizer：保证核心实体不丢。
3. 加选区依赖判断：防止 query 污染。
4. 加 intent detection：先覆盖 official_fact、market_recap、finance_fact。
5. 重写 query planner：按 intent 生成 query。
6. 升级 source ranking：加入官方源、A 股财经源、低质量降权。
7. 加 quality gate：低质量结果触发二次检索。
8. 加 low-quality fallback：不再硬答。
9. 跑单测、API smoke、前端 build。
10. 如仍不稳定，再评估接入 Brave/Tavily/Exa。

每一步完成后都跑局部测试，不等全部写完再验证。

## 8. 风险与处理

### 风险 1：规则越来越多，变成不可维护的 if/else

处理：

- 只为稳定高频 intent 写规则。
- 每个规则必须有对应测试。
- 没有测试的规则不加。

### 风险 2：权威域名表过拟合

处理：

- 域名表只做排序加权，不做硬过滤。
- 搜不到官方源时仍可用主流新闻源。
- low-quality 只降权，不全局封杀。

### 风险 3：二次检索增加延迟

处理：

- 只有 quality gate 不通过才二次检索。
- 二次检索最多一次。
- 保留全局 timeout。
- meta 中记录是否发生 rewrite，方便后续优化。

### 风险 4：DDGS 超时拖慢请求

处理：

- 保持 per-query timeout。
- 记录 provider failure。
- 后续接入更稳定 provider。
- 如果搜索后端不可用，快速返回可理解 warning。

### 风险 5：官方事实随时间变化

处理：

- 当前日期进入 prompt/meta。
- 官方源优先。
- 回答必须说明“截至资料发布时间/检索时间”。
- 避免用旧新闻推断当前职位。

## 9. 建议的数据结构

### 9.1 Query Plan

```python
{
    "intent": "market_recap",
    "queries": [
        "2026年4月 A股 行情 回顾 上证指数 创业板指",
        "2026年4月 A股 走势 复盘 沪深300"
    ],
    "used_selection": False,
    "selection_reason": "independent_question"
}
```

### 9.2 Search Quality

```python
{
    "passed": False,
    "score": 0.2,
    "reason": "top results did not match core entity A股 and came from low-quality domains",
    "trusted_sources": 0,
    "matched_core_terms": ["走势"],
    "rewrite_attempted": True
}
```

### 9.3 Web Source Meta

```python
{
    "ref": "W1",
    "type": "web",
    "title": "...",
    "url": "...",
    "domain": "...",
    "query": "...",
    "intent": "market_recap",
    "rank_score": 8.4,
    "quality_tags": ["trusted_domain", "core_entity_match"],
    "extract_status": "full"
}
```

## 10. 完成定义

本方案完成必须满足：

1. 三个关键错例都有自动化测试。
2. `A股` 不再被 query 清洗破坏。
3. 独立问题不再被选区污染。
4. 官方事实问题优先官方源。
5. A 股行情问题优先财经/交易所/主流证券媒体源。
6. 低质量搜索结果不会直接进入 LLM 硬答。
7. 二次检索和证据不足 fallback 可观测。
8. `/knowledge-library` 和 `/knowledge-chat` 都继续可用。
9. 后端测试、API smoke、前端 build 通过。

## 11. 后续增强

这些不阻塞本次质量恢复：

- 接入 Brave Search。
- 接入 Tavily/Exa 做更强网页抽取。
- 增加 UI 上的“检索过程”折叠面板。
- 为用户提供“重新搜索”按钮。
- 建立 20-50 条 Hermes 联网搜索评测集。
- 对不同 provider 做延迟、成功率、来源质量统计。

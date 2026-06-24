# Hermes Contract-Lite 联网研究设计方案

日期：2026-06-21

执行状态：已实现并完成核心验证。本文档用于替代“按问题类型写死来源策略”的思路，把 Hermes 联网研究收敛为一套轻量证据合同机制。

当前实现锚点：

- 后端核心：`/Users/pipi/hermes-custom/bridge/research_bridge.py`
- 后端测试：`/Users/pipi/hermes-custom/tests/test_research_bridge.py`
- 入口测试：`/Users/pipi/hermes-custom/tests/test_api_routes.py`
- 流式接口：`/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py`
- 前端展示：`/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx`

当前验证结果：

- `./.venv/bin/python -m pytest tests/test_research_bridge.py tests/test_api_routes.py -q`：99 passed。
- `./.venv/bin/python -m pytest tests/hermes_cli/test_web_server.py -q -k 'selection_chat'`：4 passed。
- `npm run build` in `/Users/pipi/Downloads/hermes-agent-main/web`：passed。
- live DashScope smoke：AER 只采用覆盖 subject 的 `aeaweb.org` 作为 limited evidence；百科、LetPub、Wikipedia 首页等未进入 W evidence。

## 0. 核心判断

Hermes 的联网研究不应该继续靠代码判断“这是人物、期刊、公司、政策、论文，所以应该查某些固定站点”。这会把语义规划塞进代码，越修越碎。

更合理的分工是：

```text
LLM planner：判断用户问题的主题、别名、证据门槛、是否需要联网。
确定性代码：执行检索、抽取、去重、主题覆盖检查、独立来源计数、编号、引用修复。
Synthesizer：只能基于通过证据门槛的 K/W 来源回答。
UI：实时展示搜索过程和证据状态，但不暴露私有思考链。
```

本方案采用 Contract-Lite，不做完整 claim engine。先解决三个当前最痛的问题：

1. 无关或低质来源被采用。
2. 证据不足时答案仍然过度展开。
3. 检索过程和来源编号混乱。

## 1. 四种模式合同

Hermes 当前研究入口需要同时支持四种模式。模式是硬合同，不是 planner 建议。

| 模式 | 目标 | 可用来源 | 是否联网 | 答案可引用 |
|---|---|---|---|---|
| `current_article_only` | 只解释当前文章或选区 | 当前文章、选区上下文 | 否 | 不出现 `W`，必要时说明“当前文章依据” |
| `local_only` | 只查本地知识库 | 本地知识库 | 否 | 只允许 `K` |
| `web_only` | 只查公开网页 | 联网资料 | 是，必须发起 | 只允许 `W` |
| `local_web` | 本地 + 公网综合 | 本地知识库 + 联网资料 | 是，必须发起 | 允许 `K` + `W` |

### 1.1 current_article_only

行为：

- 不调用 web search。
- 不调用本地知识库，除非后续明确设计“当前文章属于本地来源”。
- 只基于当前文章、选区、前后文回答。
- 不显示联网过程面板。

适合：

- “这段话是什么意思？”
- “这篇文章的这个判断怎么理解？”
- “帮我解释选区。”

### 1.2 local_only

行为：

- 只查本地知识库。
- 不发起联网。
- 答案只允许引用 `K1/K2/...`。
- 如果本地没有证据，明确说本地知识库未找到支撑，不用网页补。

适合：

- “我的笔记里怎么说？”
- “我之前保存过相关内容吗？”

### 1.3 web_only

行为：

- 必须发起联网。
- 不把当前文章正文当主依据。
- 选区只用于理解指代或生成 subject，不得污染 query。
- 答案只允许引用 `W1/W2/...`。

适合：

- “OpenAI 是什么意思？”
- “美联储主席是谁？”
- “这个期刊怎么样？”

### 1.4 local_web

行为：

- 必须发起联网，同时可查本地知识库。
- 本地来源用于用户历史上下文，网页来源用于公开事实与更新。
- 如果 K 和 W 冲突，答案必须说明冲突，不强行合并。
- 答案允许引用 `K` 和 `W`。

适合：

- “结合我的笔记和公开资料判断这个观点还成立吗？”
- “这篇文章里的说法现在有没有更新？”

## 2. Planner 输出结构

Planner 输出轻量合同，不输出复杂逐 claim 证据图。

推荐 schema：

```json
{
  "intent_label": "journal_evaluation",
  "needs_web": true,
  "mode": "web_only",
  "subject": {
    "name": "American Economic Review",
    "aliases": ["AER"],
    "type_hint": "academic_journal"
  },
  "queries": [
    "American Economic Review journal overview",
    "American Economic Review ranking impact factor"
  ],
  "evidence_contract": {
    "must_cover_subject": true,
    "minimum_independent_sources": 2,
    "prefer_authoritative_sources": true,
    "must_use_primary_source": false,
    "allow_summary_only": true,
    "answer_policy": "answer_only_supported"
  },
  "answer_contract": {
    "primary_answer_first": true,
    "state_evidence_limits": true,
    "avoid_unsupported_metrics": true,
    "avoid_unasked_side_topics": true
  },
  "privacy": {
    "may_use_selection_text": false,
    "allowed_context_terms": ["American Economic Review", "AER"]
  }
}
```

### 2.1 字段含义

`subject`

- `name`：用户真正询问的主体。
- `aliases`：常见简称、英文名、中文名、ticker、缩写。
- `type_hint`：仅用于日志和 prompt，不用于代码分支 hardcode。

`evidence_contract`

- `must_cover_subject`：来源必须覆盖主体。
- `minimum_independent_sources`：最少独立来源数，同 domain 只算一个。
- `prefer_authoritative_sources`：权威来源优先，但不是硬拒绝二级来源。
- `must_use_primary_source`：强事实或高风险问题必须有官方/原始来源。
- `allow_summary_only`：是否允许只用搜索摘要支撑有限结论。
- `answer_policy`：当前固定为 `answer_only_supported`。

`answer_contract`

- 控制回答风格和边界，不决定检索规则。

### 2.2 缺字段 fallback

Planner 输出缺字段时，代码使用保守默认：

```json
{
  "subject": {"name": "用户原问题中最短可用主题", "aliases": []},
  "evidence_contract": {
    "must_cover_subject": true,
    "minimum_independent_sources": 1,
    "prefer_authoritative_sources": true,
    "must_use_primary_source": false,
    "allow_summary_only": true,
    "answer_policy": "answer_only_supported"
  }
}
```

代码不能因为 planner 失败就回到领域 hardcode。

## 3. 搜索与检索流程

统一 pipeline：

```text
normalize mode
-> planner contract
-> apply mode contract
-> local search if enabled
-> web search if enabled
-> candidate normalization
-> source classification
-> extraction
-> evidence gate
-> synthesis input
-> citation validation
-> final trace
```

### 3.1 查询词生成

Planner 负责 query 语义。代码只做：

- 去重。
- 长度裁剪。
- 私有选区脱敏。
- 按模式强制联网。
- 对 planner 给出的 preferred/authoritative hints 做 site query 扩展，但不猜领域。

禁止：

- 代码根据 `journal_evaluation` 自动加 AEA/JCR/RePEc。
- 代码根据 `person_profile` 自动加百科/人民网。
- 代码根据 `finance` 自动展开 ticker，除非 planner 给出。

### 3.2 搜索结果归一化

每个候选结果归一成：

```json
{
  "candidate_ref": "C1",
  "title": "",
  "url": "",
  "domain": "",
  "query": "",
  "snippet": "",
  "rank_score": 0,
  "extract_status": "pending"
}
```

确定性清洗：

- 清洗百炼返回中混入搜索时间、关键词的脏 URL。
- 同 URL 去重。
- 空 URL 或无法解析 domain 的候选保留但降权。
- 搜索摘要保留为 `snippet`，不得伪装成正文。

## 4. 来源分层

内部使用四类角色：

```ts
type SourceRole = "candidate" | "evidence" | "clue" | "rejected";
```

### 4.1 candidate

搜索召回结果。所有网页先进入 candidate。

显示编号：`C1/C2/C3`

### 4.2 evidence

可以进入答案引用的来源。

显示编号：`W1/W2/W3`

必须满足：

- 覆盖 subject name 或 alias。
- 有可用摘要或正文。
- 没有被判定为无关首页、错实体、抽取失败无摘要、重复、低质 SEO。
- 满足 `must_use_primary_source` 时，必须是官方/原始来源或 planner 明确认可的 primary source。

### 4.3 clue

相关但不足以支撑正式结论的来源。

显示编号：`L1/L2`

典型情况：

- 摘要很薄，只能提示方向。
- 百科/媒体/博客能说明背景，但不足以支撑强结论。
- 同主题但不满足权威门槛。
- 与 subject 相关但正文抽取不完整。

clue 不允许被答案正文引用。

### 4.4 rejected

不能用于回答的来源。

显示编号：`R1/R2`

常见原因：

- `wrong_subject`：不覆盖主题或错实体。
- `unrelated_homepage`：泛首页，不含主体。
- `extract_failed`：抽取失败且无摘要。
- `duplicate`：重复 URL 或重复内容。
- `low_quality`：SEO 聚合、论坛、低质量自媒体。
- `not_contract_compliant`：不满足 evidence_contract。

## 5. 确定性质量门槛

### 5.1 主题覆盖

如果 `must_cover_subject = true`：

来源必须在以下任一字段命中 `subject.name` 或 aliases：

- title
- snippet
- extracted excerpt
- canonical URL 中的可读 slug

如果只在 query 中命中，不算覆盖。

例：

```text
问题：American Economic Review 这个期刊怎么样
来源：people.com.cn 首页
title：人民网_网上的人民日报
snippet：无 AER
结果：rejected / wrong_subject
```

### 5.2 独立来源计数

独立来源按 domain 计数：

```text
baike.baidu.com/page1
baike.baidu.com/page2
```

只算 1 个 independent source。

如果 evidence 来源数大于 1，但 independent source 不足：

```json
{
  "evidence_status": "limited",
  "reason": "insufficient_independent_sources"
}
```

### 5.3 抽取状态

`full`

- 正文抽取成功，可支撑较完整回答。

`summary`

- 只可支撑摘要中明确出现的内容。
- prompt 必须要求模型不要展开摘要没有支撑的细节。

`failed`

- 默认不能作为 evidence。
- 例外：官方直探卡可以作为 very thin evidence，但必须标记 `thin_official_card`，答案必须保守。

### 5.4 软权威与硬权威

软权威：

```json
"prefer_authoritative_sources": true,
"must_use_primary_source": false
```

行为：

- 官方/权威来源优先。
- 可靠二级来源可辅助。
- 如果官方来源很薄，答案要说明证据有限。

硬权威：

```json
"must_use_primary_source": true
```

行为：

- 没有官方/原始来源，不给确定结论。
- 二级来源只能进入 clue。

适用：

- 现任职位。
- 最新政策。
- 法律/医疗/金融高风险事实。
- 用户明确要求“官方说法”。

## 6. Synthesis 输入合同

Synthesizer 不能直接看所有 candidates。只能看：

```json
{
  "evidence_status": "sufficient | limited | insufficient",
  "knowledge_sources": ["K..."],
  "web_sources": ["W..."],
  "clue_sources": ["L..."],
  "rejected_sources": ["R..."],
  "answer_constraints": [
    "Only cite K/W sources.",
    "Do not cite C/L/R sources.",
    "If evidence_status is limited, state evidence is limited.",
    "Do not mention rankings, metrics, latest facts, or precise values unless W/K evidence directly supports them."
  ]
}
```

### 6.1 sufficient

条件：

- 满足 minimum independent sources。
- required evidence contract 满足。
- 至少一个 evidence 有正文或明确摘要。

回答：

- 可以直接回答。
- 每个重要结论都要引用 K/W。

### 6.2 limited

条件：

- 有 evidence，但独立来源不足。
- 或 evidence 只有摘要。
- 或官方来源很薄但相关。

回答：

- 可以给简短结论。
- 必须说明“证据有限”。
- 不展开未被来源直接支持的细节。

### 6.3 insufficient

条件：

- 没有 evidence。
- 硬权威问题缺 primary source。
- 来源不覆盖 subject。

回答：

- 不给确定结论。
- 说明搜了什么、缺什么、为什么不能确定。

## 7. UI 展示设计

UI 采用渐进披露，不把工程调试面板直接摊给用户。

### 7.1 默认展示

默认显示：

- 模式。
- 查询词。
- 已查看网页数量。
- 采用依据。
- 证据状态：充分 / 有限 / 不足。
- 当前阶段：搜索中 / 筛选来源 / 读取网页 / 生成答案。

### 7.2 展开展示

展开后显示：

- 已发现网页 `C`
- 相关但未引用 `L`
- 未采用 `R`
- 未采用原因
- provider 状态
- warnings

### 7.3 编号规范

```text
C = candidate
W = adopted web evidence
L = clue
R = rejected
K = adopted local knowledge
```

答案正文只允许：

```text
[K1] [K2] [W1] [W2]
```

不允许：

```text
[C1] [L1] [R1]
```

底部“参考来源”只显示 K/W。

## 8. 实时事件设计

不展示 chain-of-thought，只展示事实过程。

建议事件：

```text
plan_created
local_search_started
local_source_found
web_query_started
web_candidate_found
source_classified
web_extract_started
web_extract_done
evidence_status_updated
synthesis_started
final_answer
```

### 8.1 事件样例

`plan_created`

```json
{
  "type": "plan_created",
  "mode": "web_only",
  "intent_label": "journal_evaluation",
  "subject": {"name": "American Economic Review", "aliases": ["AER"]},
  "evidence_contract": {"minimum_independent_sources": 2}
}
```

`web_candidate_found`

```json
{
  "type": "web_candidate_found",
  "sources": [
    {"candidate_ref": "C1", "title": "American Economic Review", "domain": "aeaweb.org"}
  ]
}
```

`source_classified`

```json
{
  "type": "source_classified",
  "evidence_sources": [{"ref": "W1", "from_candidate": "C1"}],
  "clue_sources": [{"ref": "L1", "from_candidate": "C2", "reason": "summary_only"}],
  "rejected_sources": [{"ref": "R1", "from_candidate": "C3", "reason": "wrong_subject"}]
}
```

## 9. 四种模式下的 trace 字段

统一 `research_trace`：

```json
{
  "mode": "local_web",
  "subject": {},
  "evidence_contract": {},
  "plan_status": "planned",
  "queries": [],
  "local_search_attempted": true,
  "web_search_attempted": true,
  "candidate_sources": [],
  "knowledge_sources": [],
  "web_sources": [],
  "clue_sources": [],
  "rejected_sources": [],
  "source_counts": {
    "candidate": 0,
    "knowledge": 0,
    "web": 0,
    "clue": 0,
    "rejected": 0,
    "independent_web": 0
  },
  "evidence_status": "sufficient",
  "warnings": []
}
```

`current_article_only`：

- `web_search_attempted = false`
- `local_search_attempted = false`
- candidate/web/clue/rejected 为空

`local_only`：

- `local_search_attempted = true`
- `web_search_attempted = false`

`web_only`：

- `local_search_attempted = false`
- `web_search_attempted = true`

`local_web`：

- 两者都为 true。

## 10. 回归测试矩阵

至少覆盖以下 24 个场景。

### 模式合同

1. `current_article_only` 不联网。
2. `local_only` 不联网，只返回 K。
3. `web_only` 不混入当前文章正文。
4. `local_web` 同时返回 K/W。

### 来源编号

5. C/W/L/R 编号不冲突。
6. 答案正文不能引用 C/L/R。
7. 底部参考来源只展示 K/W。
8. W 编号连续，无 W3 缺失。

### 主题覆盖

9. 无关首页 rejected。
10. title 命中但 excerpt 不命中，降级 clue。
11. alias 命中可采用，例如 AER = American Economic Review。
12. 错实体 rejected。

### 抽取状态

13. full extraction 可作为 evidence。
14. summary-only 只允许有限回答。
15. failed 且无摘要 rejected。
16. thin official card 允许 limited evidence。

### 证据门槛

17. 同域名多来源只算一个 independent source。
18. independent source 不足时 evidence_status = limited。
19. must_use_primary_source 缺失时 evidence_status = insufficient。
20. 软权威允许官方 + 二级来源组合。

### 模型输出修复

21. 模型引用不存在 W 编号，修复或 fallback。
22. 模型引用 C/L/R 编号，移除或 fallback。
23. 模型生成 unsupported metrics，prompt/后处理阻断。
24. 流式事件 `source_classified` 必须早于 `final_answer`。

## 11. 实施顺序

### Phase 1：Schema 与兼容

文件：

```text
/Users/pipi/hermes-custom/bridge/research_bridge.py
```

任务：

- 扩展 planner schema：`subject`、`evidence_contract`。
- 保留旧 `source_preferences` 兼容。
- 增加 schema sanitizer。
- 增加缺字段保守 fallback。

验收：

- 旧测试不破。
- planner 输出旧格式时仍可运行。

### Phase 2：Source Role Classifier

任务：

- candidate 使用 C 编号。
- evidence 使用 W 编号。
- clue 使用 L 编号。
- rejected 使用 R 编号。
- 增加 subject coverage、independent source count、homepage rejection。

验收：

- 无关首页不再进入 evidence。
- AER 问题中 people.com.cn 首页不能采用。
- 答案正文只引用 W/K。

### Phase 3：Synthesis Contract

任务：

- synthesis input 增加 `evidence_status`、`answer_constraints`。
- prompt 强制 limited/insufficient 行为。
- citation validator 禁止 C/L/R。

验收：

- 证据不足时不展开指标、排名、最新事实。
- invalid citation 仍能修复。

### Phase 4：UI 渐进披露

文件：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/lib/api.ts
```

任务：

- 默认展示：查询词、已查看数量、采用依据、证据状态。
- 展开展示：C/L/R 和原因。
- 底部参考来源只展示 K/W。

验收：

- 不再出现多个 `[W1]` 指不同网页。
- 用户能看到实时过程，但界面不显得像调试面板。

### Phase 5：真实 smoke

必测问题：

```text
《American Economic Review》这个期刊怎么样
OpenAI 是什么意思
什么是 coding agent
段永平是谁
美联储主席是谁
结合我的笔记和公开资料解释 RAG
```

验收：

- AER 不采用无关首页。
- OpenAI 至少优先显示官方/权威来源，证据薄时说明有限。
- Coding agent 证据不足时不硬展开。
- 美联储主席硬官方事实缺官方确认时不采信二手传闻。
- local_web 同时保留本地和网页来源。

## 12. 明确不做

V1 不做：

- 完整 claim-level evidence graph。
- 每个结论逐条自动事实核验。
- 大规模 source ontology。
- 每个垂类的站点白名单 hardcode。
- 复杂多轮 autonomous research。

这些等 Contract-Lite 稳定后再评估。

## 13. 成功标准

Contract-Lite 完成后，Hermes 联网研究应满足：

1. 模式合同稳定，不偷联网、不偷混本地。
2. 来源采用更保守，无关首页、错实体、低质来源不进 evidence。
3. 证据不足时答案会收缩。
4. 用户能实时看到搜索、筛选、采用过程。
5. 编号体系清楚，答案正文只引用 K/W。
6. 新问题类型不需要新增领域 if-else。

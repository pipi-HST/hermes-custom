---
status: historical
execution_status: pending
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes 联网研究边界合同与评估协议

日期：2026-06-23

执行状态：待确认后执行。本文档不是实现完成记录，而是对当前 Hermes 联网研究链路的重新收敛方案。后续继续改 `bridge/research_bridge.py` 前，应先按本文档统一边界、审计语义门，并建立固定 eval。

关联背景文档：

- `docs/superpowers/plans/2026-06-20-hermes-web-research-pipeline-upgrade-plan.md`
- `docs/superpowers/plans/2026-06-20-hermes-web-search-quality-recovery-plan.md`
- `docs/superpowers/plans/2026-06-21-hermes-contract-lite-web-research-design.md`

## 0. 当前暂停点

本轮暂停时的事实状态：

- `bridge/research_bridge.py` 和 `tests/test_research_bridge.py` 存在半途改动，不能视为完成方案。
- 已经尝试拆掉部分代码侧语义拒绝，例如 critic 后的相邻概念、身份不匹配、词典类硬拒绝。
- 已补过一批边界测试，证明方向包括：retrieval hints 进入 critic、critic adoption 不被代码二次语义否决、primary-source 合同依赖 critic 的 `source_authority`。
- 最后一轮完整 research 测试没有在最终状态下完成验证；最近一次可靠 targeted 测试是局部通过，不足以声明质量达标。
- 当前工作树很脏，包含大量与本任务无关或前序任务产生的改动。继续实现前必须先只锁定本任务文件，不能顺手清理无关 diff。

这意味着后续不能直接从“继续修一个失败测试”开始，而应先做一次边界重构。

## 1. 根因判断

7 小时没有完全修好的根因不是单个 bug，而是架构边界不清：

```text
同一个语义问题被分散在 planner、ranker、quality gate、candidate filter、critic、post-critic gate、fallback、synthesis 多处判断。
```

具体表现：

1. `rank_web_sources` 既排序，也给来源贴可信/低质/弱二级等标签。
2. `evaluate_search_quality` 既判断是否有候选，也判断 top result 是否匹配 subject、是否满足 preferred source。
3. `_ranked_sources_for_evidence` 在送入 critic 前会隐藏一部分候选。
4. `_apply_evidence_decisions_to_sources` 在 critic 采纳后还会用代码二次语义拒绝。
5. `_should_use_low_quality_fallback` 同时承担失败兜底、是否绕过 synthesis、本地证据是否可用等职责。
6. 同步和异步路径有重复链路，容易出现测试路径和 UI 路径不一致。

只修某个函数，会被另一个函数里的旧语义门抵消，所以会出现“旧题过了，新题失败”的现象。

## 2. 核心原则

后续实现必须真正遵守：

```text
LLM 负责语义判断，代码负责确定性合同。
```

这不是口号，而是边界约束。

LLM planner / evidence critic 可以判断：

- 用户到底问的主体是什么。
- selection 是否只是上下文，还是查询主体的一部分。
- 某个来源是否直接讨论研究主体。
- 某个来源是否是相邻概念、错主体、泛门户、首页、论坛闲聊。
- 某个来源是否足够权威。
- 某个来源能支持哪些 claim。
- 某个来源应该是 `adopt`、`partial`、`clue` 还是 `reject`。
- 某个 claim 是否必须有 primary / official / index source。

确定性代码只能判断：

- 模式合同：四种模式是否应发起本地/联网检索。
- 搜索预算、超时、最大 query 数、最大 candidate 数、最大抽取 URL 数。
- URL 和 domain 去重。
- 来源编号是否连续、引用编号是否合法。
- 搜索/抽取是否成功，正文是否为空。
- summary-only 是否被合同允许。
- critic 输出是否符合 schema。
- adopted 来源是否达到最低独立 domain 数。
- model 是否引用了未提供的 `K/W` 编号。
- metadata 是否完整传给 UI。

确定性代码禁止判断：

- 某个标题“语义上”是否匹配用户主体。
- 某个 domain 是否因为题型而必须采用。
- 某个来源是否是“权威解释”。
- 某个来源是否为相邻概念并因此不能采用。
- 某个百科、论坛、公众号、媒体页在当前问题中是否可作为解释证据。
- 某个 planner `preferred_domain` 未采用时是否应强制失败，除非 critic 或 claim contract 明确要求 primary source。

## 3. 四种模式合同

模式是硬合同，不是 planner 建议。

| 模式 | 本地检索 | 联网检索 | 可引用来源 | 失败策略 |
|---|---:|---:|---|---|
| `current_article_only` | 否 | 否 | 当前文章/选区 | 只基于文章回答，不补联网 |
| `local_only` | 是 | 否 | `K` | 本地无证据就说明无本地依据 |
| `web_only` | 否 | 是 | `W` | 无 adopted web source 就 source-backed fallback / 拒答 |
| `local_web` | 是 | 是 | `K` + `W` | web 失败时仍可基于 K 回答，但必须说明联网未提供可用证据 |

`local_web` 的关键约束：

- 联网必须发起，不能因为本地有答案而跳过。
- 如果 web source 被 critic 全部拒绝，不能把被拒网页混进答案。
- 如果本地证据足够，synthesis 可以只用 `K` 回答。
- 如果本地和网页冲突，答案必须显式区分，不强行合并。

## 4. 推荐链路

后端应收敛成一条核心链路，同步和异步入口复用同一套核心函数：

```text
normalize request
-> planner
-> apply deterministic mode contract
-> local retrieval if mode allows
-> web search candidate collection
-> candidate normalization and deterministic annotations
-> extraction / summary fallback decision
-> evidence critic
-> deterministic contract executor
-> synthesis
-> citation validator
-> UI trace metadata
```

### 4.1 Planner

职责：

- 输出 subject、aliases、intent label、queries、answer claims、source preferences、evidence contract。
- 判断是否需要 primary source。
- 判断是否 freshness sensitive / high risk。
- 判断 selection 是否允许进入 query。

代码职责：

- 校验 planner 输出 schema。
- 对 query 去重、裁剪、脱敏。
- 强制模式合同，例如 `web_only` / `local_web` 必须 `needs_web=true`。

代码禁止：

- 用 regex 判断“这是期刊/人物/金融术语，所以写死某类来源”。
- 用题型给出最终采用策略。

### 4.2 Search Candidate Collection

职责：

- 执行 planner queries。
- 对 planner 明确给出的 `preferred_domains` 添加 site query 或 direct probe。
- 合并多后端结果。
- URL 去重。

允许的确定性 annotation：

- `retrieval_rank`
- `domain`
- `search_backend`
- `extract_status`
- `matched_terms`
- `retrieval_hints`
- `planner_preferred_domain`
- `planner_source_kind:<kind>`
- `low_quality_domain_hint`
- `weak_secondary_hint`
- `generic_homepage_hint`

注意：这些字段只能是 hints，不能直接决定 adopt/reject。

### 4.3 Extraction

代码可以决定：

- 哪些 URL 进入抽取。
- 是否允许 summary-only fallback。
- 抽取失败时是否保留 title/description 给 critic。
- 抽取正文为空时不能进入 synthesis adopted sources。

代码不可以决定：

- 摘要内容是否“足够解释问题”。
- 正文是否“语义上支持结论”。

### 4.4 Evidence Critic

critic 是唯一的语义证据裁判。输入必须包含：

- query
- current date
- subject
- answer claims
- claim authority requirements
- evidence contract
- candidate sources
- title / url / domain / extract_status / description / excerpt
- retrieval_hints
- matched_terms

critic 输出 schema：

```json
{
  "decisions": [
    {
      "source_id": "https://example.com/source",
      "subject_match": "yes",
      "source_authority": "reputable_secondary",
      "source_role": "supporting_evidence",
      "allowed_usage": ["fact", "background"],
      "confidence": "medium",
      "claim_support": [
        {
          "claim_id": "answer_user_question",
          "support": "yes",
          "reason": "..."
        }
      ],
      "adoption": "partial",
      "reason": "..."
    }
  ]
}
```

允许值：

- `subject_match`: `yes` / `no` / `uncertain`
- `source_authority`: `primary` / `index_database` / `official` / `reputable_secondary` / `commentary_secondary` / `weak_secondary` / `unknown`
- `source_role`: `primary_evidence` / `supporting_evidence` / `interpretive_evidence` / `clue` / `reject`
- `adoption`: `adopt` / `partial` / `clue` / `reject`

代码只执行 schema：

- `adopt` / `partial` 且 `subject_match=yes` 且支持至少一个 claim，才可进入 adopted。
- `clue` 进入线索来源。
- `reject` 进入未采用来源。
- `weak_secondary` / `unknown` 是否可 adopt 由 schema contract 决定；如果需要禁止，应写成 contract，而不是散落在 domain rule 里。

### 4.5 Deterministic Contract Executor

代码可以在 critic 后执行这些硬规则：

- `summary_only_disallowed`
- `evidence_critic_failed`
- `evidence_critic_empty`
- `schema_invalid`
- `stale_for_freshness_sensitive_claim`，仅限 critic reason/claim 中明确含有年份且合同要求 current；更稳妥做法是把 freshness 也交给 critic。
- `missing_minimum_independent_sources`，只产生 warning 或触发补充搜索，不应自动采用弱源。
- `missing_required_primary_source`，只在 evidence contract 明确 `must_use_primary_source=true` 且 critic 没有任何 `primary/index_database/official` adopted source 时触发。

代码禁止在 critic 后做这些事：

- 因为 title 看起来相邻而拒绝 adopted source。
- 因为 domain 在弱来源列表而拒绝 adopted source。
- 因为 preferred domain 没有被采用而拒绝其它 adopted source。
- 因为 source_identity hint 而拒绝 adopted source。
- 因为 language dictionary hint 而拒绝 adopted source。

## 5. 语义门审计清单

继续实现前，应逐项审计 `bridge/research_bridge.py`。

### 5.1 必须保留的确定性函数

- URL/domain 去重。
- 引用编号重写。
- citation validation。
- source map 生成。
- extract status 归一化。
- schema parse / enum validation。
- independent domain count。
- mode normalization。
- timeout / budget。

### 5.2 必须降级成 hint 的函数或逻辑

这些函数可以继续存在，但只能产出 `retrieval_hints`，不能直接 reject/adopt：

- `rank_web_sources`
- `_classification_reason`
- `_ranked_source_matches_subject`
- `_source_identity_quality_tags`
- `_is_language_dictionary_source`
- `_source_is_adjacent_for_definition_claim`
- `_source_satisfies_preferences`
- low-quality / weak-secondary domain lists
- trusted domain score
- preferred source kind score

### 5.3 必须删除或重写的语义门

这些逻辑不能继续作为硬门：

- `_ranked_sources_for_evidence` 在 critic 前过滤相邻概念或弱二级来源。
- `_ranked_sources_for_evidence` 因 preferred source 存在而只返回 preferred candidates。
- `_apply_evidence_decisions_to_sources` 在 critic 采纳后又调用 `_source_is_adjacent_for_definition_claim`。
- `_apply_evidence_decisions_to_sources` 在 critic 采纳后又调用 `_source_violates_identity_contract`。
- `_apply_evidence_decisions_to_sources` 在 critic 采纳后又调用 `_source_violates_professional_definition_contract`。
- `_missing_preferred_domain_adoption` 把 planner preferred domain 未采用当成质量失败。
- `evaluate_search_quality` 把 top result subject mismatch 当成拒绝进入 critic 的理由。

### 5.4 需要统一的双路径

必须避免同步/异步分叉造成行为不一致：

- `_run_web_research`
- `_run_web_research_async`

推荐做法：

- 抽出纯函数：candidate ranking、candidate selection、critic apply、contract executor。
- 同步和异步只差 I/O 调用方式，不复制业务判断。

## 6. UI Trace 合同

UI 要展示事实过程，不展示隐藏思考链。

允许展示：

- `planning_start`
- `plan`
- `local_search_start/done`
- `web_query_start/done/error`
- `web_fallback_start/done`
- `web_extract_start/done`
- `web_critic_start/done`
- `synthesis_start`
- `warnings`

每个事件应尽早流式发出，而不是最后一次性展示。

禁止展示：

- LLM chain-of-thought。
- prompt 原文。
- 用户 API key、provider secret。
- 未脱敏的私有长选区。

UI 分组建议：

- `采用来源`：critic adopt/partial 且最终引用。
- `候选来源`：检索到并送入 critic 的来源。
- `线索来源`：critic 标为 clue。
- `未采用`：critic reject 或确定性合同拒绝。

编号展示要求：

- adopted web sources 最终编号必须连续：`W1, W2, W3...`
- 如果中间候选失败，UI 可以显示内部候选序号，但最终“参考来源”不能跳号。

## 7. 固定 20 题 eval

单测只验证合同，不能替代搜索质量。必须建立固定 20 题 eval，并保存每次运行结果。

### 7.1 场景覆盖

| 编号 | 场景 | 示例问题 | 模式 |
|---:|---|---|---|
| 1 | 公司/组织定义 | OpenAI 是什么意思 | web_only |
| 2 | 工具概念 | 什么是 coding agent | web_only |
| 3 | 人物简介 | 段永平是谁 | web_only |
| 4 | 官方当前事实 | 美联储主席是谁 | web_only |
| 5 | 金融术语 | 什么叫波动率 | web_only |
| 6 | 解释/剧情机制 | 为什么说改稻为桑是个死局 | web_only |
| 7 | 顶级期刊 | American Economic Review 这个期刊怎么样 | local_web |
| 8 | 顶级期刊 | Review of Economic Studies 这个期刊怎么样 | local_web |
| 9 | 专业期刊 | Econometric Theory 这个期刊怎么样 | local_web |
| 10 | 综述期刊 | Journal of Economic Literature 这个期刊怎么样 | local_web |
| 11 | 当前市值 | 苹果现在市值多少 | web_only |
| 12 | 中文市场回顾 | 四月份 A股的走势和行情是什么 | web_only |
| 13 | 产品/公司官网 | Anthropic 是什么 | web_only |
| 14 | 开源/技术概念 | RAG 是什么 | local_web |
| 15 | 最新发布 | OpenAI 最近发布了什么模型 | web_only |
| 16 | 当前文章解释 | 这段话是什么意思 | current_article_only |
| 17 | 仅知识库 | 我的笔记里怎么解释 RAG | local_only |
| 18 | 本地+联网冲突 | 结合我的笔记和公开资料判断这个观点还成立吗 | local_web |
| 19 | 搜索失败兜底 | 联网 provider 超时但本地有证据 | local_web |
| 20 | 抽取失败 | 搜索有 URL 但正文抽取失败 | web_only |

### 7.2 每题记录字段

eval 输出必须记录：

```json
{
  "case_id": "web_company_definition_openai",
  "query": "OpenAI 是什么意思",
  "mode": "web_only",
  "planner": {
    "subject": {},
    "queries": [],
    "evidence_contract": {}
  },
  "retrieval": {
    "candidate_count": 0,
    "candidate_domains": [],
    "query_count": 0,
    "search_backend": "",
    "extract_backend": "",
    "extract_full_count": 0,
    "extract_summary_count": 0,
    "extract_failed_count": 0
  },
  "critic": {
    "seen_source_count": 0,
    "adopted_count": 0,
    "clue_count": 0,
    "rejected_count": 0,
    "adopted_independent_domains": 0
  },
  "answer": {
    "length": 0,
    "cited_refs": [],
    "invalid_refs": [],
    "uses_unadopted_source": false,
    "has_direct_answer": false,
    "states_limits_when_limited": false
  },
  "result": "pass | weak_pass | fail",
  "failure_stage": "planner | search | extraction | critic | contract | synthesis | citation | ui"
}
```

### 7.3 通过标准

每题最低标准：

- `web_only` 和 `local_web` 必须发起 web search。
- candidate sources 不得为 0，除非专门测试 provider failure。
- critic 必须看到候选来源。
- 最终答案不得引用未 adopted 的 `W` 来源。
- adopted 来源编号必须连续。
- 如果 adopted 独立来源低于合同要求，必须有 warning。
- 如果没有 adopted web source，答案不能伪装成“联网资料显示”。

质量标准：

- 简单定义类：至少 2 个候选来源，至少 1 个 adopted source。
- 当前官方事实：必须有 `primary/index_database/official` adopted source。
- 金融/法律/医疗/强时效问题：必须说明时效或风险边界。
- 解释/观点类：可以采用 commentary_secondary，但必须直接讨论用户问题。
- 期刊评价类：稳定背景可用二级来源，影响因子/JCR/排名等数值 claim 需要 primary/index_database/official。
- `local_web` 中如果 web 全部 rejected，本地 K 可回答，但必须说明联网未提供可用证据。

整体发布门槛：

- 20 题中 `pass >= 16`。
- 不允许出现 citation contract fail。
- 不允许出现 UI 最终来源跳号。
- 不允许出现“未采用来源被答案引用”。
- 官方当前事实类必须全部 pass。
- 任何 fail 都必须归因到明确 stage，不能只写“模型没答好”。

## 8. 单测策略

单测目标不是模拟所有话题，而是锁死边界。

必须补的结构性测试：

1. semantic hint reaches critic
   - 输入带 `source_identity_mismatch`、`language_dictionary_source`、`weak_secondary_hint` 的候选。
   - 断言这些候选进入 critic payload。

2. critic adoption is final semantic decision
   - critic adopt 后，代码不得因为相邻标题、词典 domain、source identity hint 二次 reject。

3. critic rejection is executed
   - critic reject 后，source 不进入 synthesis web_sources。

4. primary-source contract is deterministic
   - `must_use_primary_source=true` 时，只有 critic 输出 `primary/index_database/official` adopted source 才满足。
   - preferred domain 命中但 critic 标为 secondary，不满足 primary contract。

5. preferred domains are routing hints
   - planner 给出 `preferred_domains` 时，代码生成 site/direct probe。
   - 但其它候选仍进入 critic，不能被隐藏。

6. minimum independent sources is count-only
   - 代码只按 adopted domains 计数。
   - 不为了凑数自动采用 clue/reject 来源。

7. local_web fallback
   - web 全 rejected 时，本地 K 仍可进入 synthesis。
   - 被 rejected W 不得被引用。

8. sync/async parity
   - 同一 fake search/extract/critic，在 sync 和 async 下得到同一 source adoption 结果。

9. citation normalization
   - 模型输出不存在的 `K1/W9` 时，代码清理或改用可用引用。
   - 最终 source map 只包含实际引用来源。

10. streaming trace
   - event 顺序至少包含 plan -> search -> extract -> critic -> synthesis。
   - 不等到 final 后才一次性产生 trace。

## 9. 实施顺序

不要继续按失败案例修。推荐顺序：

1. 冻结当前 diff
   - 记录当前文件、未完成测试、已知失败。
   - 只保留和联网研究相关的 diff 进入下一步。

2. 写 contract tests
   - 先写上面 10 类边界测试。
   - 允许测试先失败。

3. 重构 core pipeline
   - 提取 sync/async 共享纯函数。
   - 把 semantic filters 改成 hints。
   - 把 critic 后处理收敛成 deterministic executor。

4. 建 eval runner
   - 扩展或重写 `scripts/eval_research_quality.py`。
   - 结果输出 JSONL + Markdown summary。

5. 跑自动验证
   - `./.venv/bin/python -m py_compile bridge/research_bridge.py`
   - `./.venv/bin/python -m pytest tests/test_research_bridge.py tests/test_research_quality_eval.py -q`
   - `./.venv/bin/python scripts/eval_research_quality.py`

6. 跑 live probes
   - 至少覆盖 20 题中的 8 个高风险代表。
   - 记录 trace，不只看最终回答。

7. 再改 UI
   - 后端 trace 稳定后，再改右侧检索过程展示。
   - 不让 UI 为后端语义混乱兜底。

## 10. 不做事项

本轮收敛不做：

- 不新增垂类硬编码，例如“期刊必须百度百科/AEA/Cambridge”。
- 不把知乎/公众号/Sohu 全局封死；它们是否可用由 critic 判断具体问题。
- 不因为一个 live case 成功就声明搜索质量达标。
- 不把 answer length 当质量指标；短答可能正确，长答可能幻觉。
- 不接新的付费搜索 API 来掩盖链路问题。API 可以提升召回，但不能替代 evidence contract。

## 11. 完成定义

只有同时满足以下条件，才能说“联网研究质量方案完成”：

1. 文档中的 LLM/代码边界在实现中可追踪。
2. 语义门审计清单全部处理：保留、降级或删除。
3. sync 和 async 路径共享核心合同，不再各写一套语义判断。
4. 结构性单测覆盖第 8 节全部类别。
5. 固定 20 题 eval 可运行，并输出可复查报告。
6. 20 题达到第 7.3 节发布门槛。
7. UI 实时 trace 能逐步展示 plan/search/extract/critic/synthesis 事实事件。
8. 最终答案和参考来源无未采用引用、无跳号、无 invented source id。

未满足这些条件时，即使某几个例子回答变好，也只能算阶段性进展，不能算完成。

---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes 联网搜索质量评测执行方案

日期：2026-06-23

目标：把联网搜索质量验证从“反复调用 live 搜索 API”改成“离线黄金集为主、live canary 为辅”的低成本可复现流程。

## 1. 当前原则

默认不跑 live 搜索。

每次搜索质量改动后，先跑免费验证：

```bash
.venv/bin/python -m py_compile bridge/research_bridge.py scripts/eval_research_quality.py
.venv/bin/python -m pytest tests/test_research_bridge.py tests/test_research_quality_eval.py -q
.venv/bin/python scripts/eval_research_quality.py \
  --output-jsonl output/research-eval/latest.jsonl \
  --output-md output/research-eval/latest.md
```

只有在明确要检查真实后端健康时，才跑 live canary：

```bash
.venv/bin/python scripts/eval_research_quality.py \
  --live \
  --canary \
  --case-timeout 240 \
  --output-jsonl output/research-eval/live-canary.jsonl \
  --output-md output/research-eval/live-canary.md
```

脚本默认限制 live 最多 5 个 case。超过上限必须显式传 `--allow-live-over-budget`，这应只用于人工确认后的专项排查。

## 2. 三层评测

### L0 单测

覆盖确定性合同：

- 四种模式是否正确发起本地/联网检索。
- 来源编号是否连续。
- 模型引用不存在的 `K/W` 编号时是否修复或回退。
- 无 adopted source 时是否拒答。
- 无权威支撑的指标数值是否剥离。
- `web_only` 不得引用本地 `K` 来源。

### L1 离线黄金集

文件：`tests/fixtures/research_eval_cases.jsonl`

用途：固定 planner/search/extract/critic 输入，验证 Hermes 在给定材料下能否正确采用、拒绝、引用和回答。

当前覆盖：

- 学术期刊评价。
- 期刊指标和二手指标剥离。
- 人物简介和近名误采。
- 公司/产品官方来源。
- 概念解释。
- 知识库 + 联网混合模式。
- 当前文章/仅本地/仅联网模式合同。
- 金融指标、政策、医疗高风险、论文/期刊/书名近似混淆。
- 论坛、公众号、门户首页等弱来源处理。

新增 bug 的正确处理方式：

1. 先把失败时的搜索候选、抽取正文、critic 决策沉淀成 fixture。
2. 用 fixture 复现。
3. 修确定性合同或 prompt/schema。
4. 跑 L0 + L1。

不要因为一次 live 失败直接连续重跑 live。

### L2 Live Canary

固定 5 个代表场景：

- `company_openai_official`
- `person_duan_profile_reliable`
- `concept_coding_agent`
- `finance_market_cap_requires_index`
- `concept_rag_local_web`

用途：检查真实搜索后端是否整体可用。它不是主回归门。

live canary 失败后的处理：

- 如果是 timeout 或搜索后端没有返回好候选，记录为后端波动或检索召回问题。
- 如果是错误采用、错误引用、无证据残句，转成离线 fixture，再修代码合同。
- 如果是期望过严，调整 `expect_live`，但不得降低离线 fixture 的严格度。

## 3. 责任边界

LLM 负责语义判断：

- 用户问的主体是什么。
- 候选网页是否讨论该主体。
- 候选网页支持哪些 claim。
- 来源是 adopt、partial、clue 还是 reject。

代码负责确定性合同：

- schema 校验。
- 模式合同。
- 成本、轮数、超时。
- URL 去重。
- 来源编号。
- 引用合法性。
- adopted 来源为空时拒答。
- live case 数量上限。

代码不应通过关键词或题型硬编码决定某个网页是否“语义上可采用”。

## 4. 后续搜索质量建设

下一步真正提升质量，不是继续堆 guard，而是做缺口驱动二次检索：

1. 首轮搜索。
2. critic 判断证据缺口。
3. LLM 生成补充查询。
4. 代码限制最多 1-2 轮、去重、超时和预算。
5. 仍无 adopted source 时稳定拒答。
6. live 失败样本自动保存，转为 L1 fixture。

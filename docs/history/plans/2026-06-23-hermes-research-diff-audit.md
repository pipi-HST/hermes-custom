---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes 联网研究 diff 审计

日期：2026-06-23

目的：收口当前 `research_bridge.py` 大 diff，明确哪些改动可保留，哪些属于待重做风险，避免继续在 live 搜索上打补丁。

## 1. 当前 diff 规模

本轮审计只看联网研究链路相关文件：

- `bridge/research_bridge.py`：约 `+5035 / -610`
- `tests/test_research_bridge.py`：约 `+5622 / -335`
- 新增离线评测工具和 fixtures：
  - `scripts/eval_research_quality.py`
  - `tests/test_research_quality_eval.py`
  - `tests/fixtures/research_eval_cases.jsonl`

结论：`research_bridge.py` 已经不是“小修小补”级别，不能继续按单个 live 失败点追加规则。后续必须拆成合同层、检索召回层、证据裁判层、UI trace 层逐步收口。

## 2. 应保留的确定性合同

这些改动符合“代码负责确定性合同”：

1. 模式合同
   - `web_only` / `local_web` 必须尝试联网。
   - `local_only` / `current_article_only` 不应误联网。

2. 引用合同
   - 未提供的 `K/W` 编号不能出现在最终答案。
   - 最终展示来源必须连续编号。
   - 没有可引用来源时，`web_only` 不能残留无引用答案。

3. 指标合同
   - 影响因子、市值、排名、价格等指标 claim 需要可观察数值和足够权威来源。
   - 无支撑的指标数值从最终答案中剥离。

4. 预算合同
   - planner、search、critic、synthesis 都受统一 research budget 约束。
   - live eval 现在默认最多 5 个 case。

5. 过程 trace
   - `research_trace`、候选来源、采用来源、线索来源、拒绝来源、warnings、source_counts 都是 UI 流式展示的必要基础。

## 3. 高风险待重做区域

这些区域容易违反“LLM 负责语义判断，代码负责确定性合同”。

### 3.1 代码注入题型默认来源

涉及：

- `PROFILE_DOMAINS`
- `GLOSSARY_DOMAINS`
- `SOURCE_KIND_QUERY_DOMAINS`
- `_source_preferences_with_subject_defaults`
- `_source_matches_kind`
- `_finance_glossary_probe_urls`

问题：

这些逻辑会把 `finance_term`、`profile`、`glossary` 等语义类型映射到固定 domain 或固定 URL 模板。作为检索 hint 可以接受，但不能成为采用/拒绝依据。当前代码里这些 hint 和召回修复、direct probe、quality gate 混在一起，边界不够清楚。

处理建议：

- 保留为 retrieval hints。
- 不允许直接决定 adopted/rejected。
- 中期应把这些配置移到可审计的 provider/ranking config，而不是散落在 bridge 代码里。

本轮补充约束：

- 已增加离线合同测试，锁住 preferred domain / source-kind 命中不能覆盖 evidence critic 的 reject。
- 当前允许的行为是：source-kind/domain 可以影响召回、排序、direct probe 和 `retrieval_hints`；最终是否进入 synthesis 必须由 critic adoption 加确定性合同共同决定。

### 3.2 post-critic 语义二次拒绝

涉及：

- `_source_quality_disallowed_by_contract`
- `_source_title_conflicts_with_subject`
- `_source_is_adjacent_for_definition_claim`
- `_plan_is_definition_like`
- `_title_is_adjacent_to_subject`

原问题：

这些函数在 evidence critic 已经做完语义判断后，仍可能根据标题、弱来源域名、相邻概念等代码规则覆盖 critic 的 adopt/partial 决策。这是当前最大架构风险。

本轮处理：

- `_source_quality_disallowed_by_contract` 已不再因为低质域名、弱二级来源、近名标题或相邻概念直接覆盖 critic 的 `adopt/partial`。
- 近名标题冲突、相邻概念、弱来源等信号保留为 `retrieval_hints` / `quality_tags`，传给 evidence critic 判断。
- 代码侧只保留确定性合同：summary-only 是否允许、critic 明确给出的 authority 是否满足合同、时效敏感来源是否过期、指标值是否有可观察支撑、引用编号是否合法。
- 已用测试反向约束：可疑信号必须进入 critic 输入；critic 采纳且 authority 合同允许时，代码不得再以 `source_quality_below_contract` 拒绝。

剩余边界：

- `commentary_secondary` 在权威优先合同下仍会被代码拒绝，除非 plan 明确允许解释性评论。这属于合同判断，不是代码自行判断网页语义。
- `_source_title_conflicts_with_subject`、`_source_is_adjacent_for_definition_claim` 仍存在，但职责已降级为 hint 生成；后续如继续改，应优先移动到更清晰的 retrieval hint 层。

### 3.3 quality gate 与 critic 职责混杂

涉及：

- `rank_web_sources`
- `evaluate_search_quality`
- `_ranked_sources_for_evidence`
- `_should_let_critic_judge_quality_failure`

问题：

ranker 和 quality gate 既做召回排序，也会提前判断 trusted/preferred/low quality。它们现在有些地方只是 hint，有些地方会阻断后续 critic，容易导致“搜索到了但没让 LLM 判断”。

处理建议：

- ranker 只负责排序和打 hint。
- quality gate 只判断机械失败：无结果、抽取全空、预算耗尽。
- 是否语义相关、是否足以作为解释证据，交给 critic。

### 3.4 direct probe 容易扩大 live 成本

涉及：

- `_direct_preferred_domain_candidates`
- `_direct_source_kind_candidates`
- `_preferred_domain_probe_urls`
- `_fetch_url_summary`

问题：

direct probe 能改善召回，但它绕过 search backend，可能增加隐性 HTTP 请求成本和延迟。它还会把代码生成的 URL 模板引入检索策略。

处理建议：

- 保留预算保护。
- 在 trace 中明确标记 direct probe。
- 后续 live canary 失败时优先看 direct probe 是否有效，而不是继续追加 URL 模板。

## 4. 暂不应回滚的改动

不建议现在整块回滚，因为它们已经被离线测试覆盖，且解决真实问题：

- planner schema 化。
- evidence critic schema 化。
- `research_trace` 元数据。
- 引用修复与 source renumber。
- 无来源最终拒答。
- unsupported metric stripping。
- 离线 eval harness 和 36 个 fixture。
- live canary 预算保护。

## 5. 下一步真正要做

按优先级：

1. 已完成：把 post-critic 语义二次拒绝改成 critic hints，并用离线测试锁住边界。
2. 继续收口：把 domain/source-kind 常量从“采用判断”降级为“召回排序 hint”，避免它们绕过 critic。
3. 已完成：增加 eval 失败落盘机制，保存 planner/search/extract/critic 输入输出，并生成 fixture 草稿。
4. 再做 UI 流式 trace：逐步显示计划、查询、候选、抽取、采用/线索/拒绝。

失败落盘使用方式：

```bash
.venv/bin/python scripts/eval_research_quality.py \
  --live --canary \
  --capture-failures-dir output/research-eval/failures \
  --output-jsonl output/research-eval/live-canary.jsonl \
  --output-md output/research-eval/live-canary.md
```

只有失败 case 会写入：

- `<case>.io.json`：planner/search/extract/critic/synthesis 的输入输出。
- `<case>.fixture.json`：可人工审查后追加到 `tests/fixtures/research_eval_cases.jsonl` 的 fixture 草稿。

## 6. 当前验证基线

本次审计前已经通过免费验证：

- `py_compile`：通过。
- `tests/test_research_quality_eval.py`：`9 passed`。
- 离线 canary：`5/5 passed`。
- 离线黄金集：`36/36 passed`。
- focused pytest：`178 passed`。

本轮收口后新增验证：

- `py_compile bridge/research_bridge.py scripts/eval_research_quality.py`：通过。
- focused critic-contract pytest：`6 passed, 164 deselected`。
- `pytest tests/test_research_bridge.py tests/test_research_quality_eval.py -q`：`181 passed`。
- `pytest tests/test_research_quality_eval.py -q`：`11 passed`。
- 离线 golden eval：`36/36 passed`。
- capture 参数 smoke：单 fixture case 通过时不产生失败落盘。

未跑 live 搜索。

## 7. 工具进度流式链路补充

本轮继续项完成了 `research_with_web` 的运行时稳定性收口：

- 网关已只转发 `research.progress` 自定义事件为 `hermes.tool.progress`，避免普通工具 start/complete 生命周期和研究阶段事件重复展示。
- `research_with_web_tool.py` 现在不再直接裸用 `asyncio.run(...)`。当工具 handler 在已有 event loop 中被调用时，会在线程内运行研究 coroutine，避免 `asyncio.run() cannot be called from a running event loop` 打断进度流。
- 新增回归测试覆盖“已有 event loop 内同步调用研究工具”的路径，使用 fake `async_research_answer`，不触发真实搜索/API。

本轮验证：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m py_compile model_tools.py agent/agent_runtime_helpers.py agent/tool_executor.py gateway/platforms/api_server.py tools/research_with_web_tool.py
.venv/bin/python -m pytest tests/tools/test_research_with_web_tool.py -q
```

结果：

- `py_compile`：通过。
- `tests/tools/test_research_with_web_tool.py`：`5 passed`。

仍未跑 live 搜索。原因是当前目标是修复流式事件链路和运行时桥接，验证可由本地 fake coroutine 覆盖；继续用真实联网搜索验证会浪费搜索 API 预算，且无法稳定定位该类运行时问题。

## 8. Runtime Web 前端流式展示补充

继续排查发现 `/Users/pipi/hermes-custom/dashboard` 的 `package.json` 已标注自定义 Dashboard 废弃，实际可见的 Hermes Web 前端在 `/Users/pipi/Downloads/hermes-agent-main/web`。因此本轮补充修正落在 runtime web 链路，而不是继续扩展旧 dashboard。

本轮发现的真实问题：

- `research_with_web_tool.py` 已经发出 `research.progress`。
- OpenAI-compatible API Server SSE 已能把该事件转成 `hermes.tool.progress`。
- 但 TUI/WebSocket runtime 链路的 `tui_gateway/server.py::_on_tool_progress` 只转发 `tool.started`，没有转发 `research.progress`，导致 Web 前端侧栏收不到研究阶段事件。
- Web 前端收到普通 `tool.progress` 后只覆盖 `preview`，没有保存阶段历史；研究工具默认折叠，用户容易看到“最后一条”或需要手动展开。

本轮修正：

- `tui_gateway/server.py`：将 `research.progress` 转发为 `tool.progress`，并保留 `stage/status/queries/web_sources/candidate_sources/clue_sources/rejected_sources/warnings` 等结构化字段。
- `web/src/lib/knowledgeChatState.ts`：`tool_progress` 不再只覆盖 preview，而是追加 `progressEvents` 阶段历史。
- `web/src/components/ChatSidebar.tsx`：终端侧栏的 `tool.progress` 也追加阶段历史。
- `web/src/components/ToolCall.tsx`：`research_with_web` 有阶段历史时默认展开，显示最近 10 个步骤；普通工具仍保持简洁。
- `web/src/pages/KnowledgeChatPage.tsx`：把 `stage/status` 从 WebSocket payload 传入 reducer。

本轮验证：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m py_compile tui_gateway/server.py tools/research_with_web_tool.py
.venv/bin/python -m pytest tests/tui_gateway/test_protocol.py::test_research_progress_forwarded_as_tool_progress tests/tools/test_research_with_web_tool.py -q

cd /Users/pipi/Downloads/hermes-agent-main/web
npx tsc -b
npm run build
```

结果：

- `py_compile`：通过。
- focused pytest：`6 passed`。
- `npx tsc -b`：通过。
- `npm run build`：通过，仅提示 bundle 体积超过 Vite 默认 warning 阈值。

仍未跑 live 搜索；这轮验证的是事件转发和 UI 展示合同，不需要消耗搜索 API。

## 9. 持续工程计划：从补丁模式切到闸门模式

后续不再按单个失败 query 现场追加规则。联网研究能力按三层闸门推进：

### 9.1 第一层：合成链路闸门

目标：不消耗搜索 API，也能证明“工具产生的过程事件会被页面实时看到”。

已完成：

- `tests/tui_gateway/test_research_progress_chain.py`
  - fake `async_research_answer` 发出 3 条 `research.progress`。
  - 经过 `research_with_web_tool._handler`、`tool_progress_callback`、`tui_gateway.server._on_tool_progress`。
  - 最终断言 Web 会收到的事件顺序是：`tool.start -> tool.progress -> tool.progress -> tool.progress -> tool.complete`。
- `web/scripts/check-research-progress-ui-contract.mjs`
  - 直接执行 `knowledgeChatReducer` 的合成事件。
  - 断言研究进度会追加为 `progressEvents`，不会被最后一条覆盖。
  - 静态锁住 `ToolCall` 对 `research_with_web` 的默认展开和步骤展示合同。
- `web/package.json`
  - 新增 `npm run check:research-progress`。

验证命令：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/tui_gateway/test_research_progress_chain.py tests/tui_gateway/test_protocol.py::test_research_progress_forwarded_as_tool_progress tests/tools/test_research_with_web_tool.py -q

cd /Users/pipi/Downloads/hermes-agent-main/web
npm run check:research-progress
```

结果：

- focused pytest：`7 passed`。
- `npm run check:research-progress`：通过。

### 9.2 第二层：离线质量闸门

目标：不用 live 搜索也能防止“按领域硬编码”“来源编号错乱”“只有一个低质来源也装作高质量”等回归。

现有基础：

- `scripts/eval_research_quality.py`
- `tests/fixtures/research_eval_cases.jsonl`
- `tests/test_research_quality_eval.py`
- `--capture-failures-dir` 失败落盘机制

后续执行策略：

- 所有 live 失败先转成 fixture，再进离线黄金集。
- 代码只允许修确定性合同：编号、引用、预算、模式、字段完整性、summary-only 标记、过期时效、指标可观察性。
- 语义采用、是否相关、是否足够支撑解释，继续由 LLM critic 的结构化输出负责。
- 禁止新增按题型、站点、关键词直接采用/拒绝的规则。

### 9.3 第三层：受控 live canary

目标：少量真实联网只用于验收 provider 和真实网页抽取，不用于无限调参。

执行条件：

- 第一层合成链路通过。
- 第二层离线质量闸门通过。
- 用户明确允许消耗搜索/API 预算。

建议 live canary 规模：

- 每轮最多 6 个 case。
- 覆盖 `web_only`、`local_web`、`local_only`、`current_article_only`。
- 覆盖定义解释、人物/机构、学术期刊、金融概念、影视/历史解释、近期事实。
- 失败只落盘为 `.io.json` 和 `.fixture.json`，不现场继续烧 API。

### 9.4 下一步执行顺序

1. 已完成：建立一个一键免费验收脚本，串起 `py_compile`、focused pytest、离线黄金集、Web UI contract、`npx tsc -b`。
2. 重启 dashboard/gateway，做一次不联网的 runtime smoke：确认 `http://127.0.0.1:9119` 使用的是最新 build。
3. 用 fake/sandbox 事件确认 WebSocket 事件在真实页面里逐步出现。
4. 再回到搜索质量：只处理离线黄金集暴露的合同问题。
5. 最后才申请跑受控 live canary。

一键免费验收入口：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
scripts/check_research_pipeline_free.sh
scripts/check_research_pipeline_free.sh --runtime
scripts/check_research_pipeline_free.sh --full
```

`scripts/check_research_pipeline_free.sh` 默认执行：

- Python syntax：`model_tools.py`、agent runtime、API server、TUI gateway、`research_with_web_tool.py`。
- research progress chain pytest。
- Web research progress UI contract。
- `npx tsc -b`。

`--full` 额外执行：

- Web production build。
- `/Users/pipi/hermes-custom` 的 `research_bridge.py` / `eval_research_quality.py` 语法检查。
- `scripts/promote_research_fixture.py` 语法检查。
- `tests/test_research_bridge.py tests/test_research_quality_eval.py tests/test_promote_research_fixture.py`。
- 离线黄金集 `scripts/eval_research_quality.py`。

`--runtime` 额外执行：

- 要求 `http://127.0.0.1:9119` dashboard 已运行。
- Playwright 打开真实 `/knowledge-chat` 页面。
- 拦截真实页面创建的 `/api/ws` WebSocket 实例。
- 派发合成 `tool.start/tool.progress/tool.complete` message event。
- 断言页面在“详情 -> 工具”里展示：
  - `检索过程`
  - `规划检索任务`
  - `搜索：OpenAI 是什么`
  - `证据裁判完成：采用 1，拒绝 0`

本轮 `--full` 结果：

- runtime focused pytest：`7 passed`。
- Web UI contract：通过。
- Web production build：通过，仅有 Vite bundle size warning。
- hermes-custom focused pytest：`184 passed`。
- 离线黄金集：`36/36 passed`。

本轮 runtime 重启与 smoke：

- 已备份 sessions：`~/.hermes/session-backups/sessions.research-progress.20260623T205454`。
- Gateway 已重启，新 PID：`75459`，`http://127.0.0.1:8642/health` 返回 `{"status": "ok", "platform": "hermes-agent"}`。
- Dashboard 已重启，新 PID：`76502`，`http://127.0.0.1:9119/` 引用当前 build 产物 `index-BJma9HM2.js` / `index-C2SKkTqt.css`。
- `scripts/check_research_pipeline_free.sh --runtime`：通过。

### 9.5 Live 失败转离线 fixture 的固定流程

后续如果用户批准跑 live canary，失败不再现场继续烧 API，而是落盘、审查、提升为离线 fixture。

1. 运行受控 live canary，并只捕获失败：

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python scripts/eval_research_quality.py \
  --live \
  --canary \
  --capture-failures-dir output/research-eval/failures \
  --output-jsonl output/research-eval/live-canary.jsonl \
  --output-md output/research-eval/live-canary.md
```

2. 人工打开失败目录里的 `<case>.io.json`，看失败阶段是 planner/search/extract/critic/citation/synthesis/contract。

3. 只在确认 fixture 草稿本身合理后提升：

```bash
.venv/bin/python scripts/promote_research_fixture.py \
  output/research-eval/failures/<case>.fixture.json \
  --dry-run

.venv/bin/python scripts/promote_research_fixture.py \
  output/research-eval/failures/<case>.fixture.json
```

4. 提升后必须跑免费闸门：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
scripts/check_research_pipeline_free.sh --full
```

本轮新增：

- `scripts/promote_research_fixture.py`
  - 校验 fixture draft。
  - 拒绝重复 ID。
  - 规范字段顺序后追加到 `tests/fixtures/research_eval_cases.jsonl`。
- `tests/test_promote_research_fixture.py`
  - dry-run 不写入。
  - 正式追加 JSONL。
  - 重复 ID 拒绝。

本轮验证：

- `py_compile scripts/promote_research_fixture.py scripts/eval_research_quality.py bridge/research_bridge.py`：通过。
- `pytest tests/test_promote_research_fixture.py tests/test_research_quality_eval.py -q`：`14 passed`。
- `scripts/check_research_pipeline_free.sh --runtime`：通过。
- `scripts/check_research_pipeline_free.sh --full`：通过，hermes-custom focused pytest 为 `184 passed`，离线黄金集为 `36/36 passed`。

# Life Memory Influence Trace Design

Date: 2026-06-01

## 定位

Life Memory Influence Trace 是 Life Assistant 的“记忆影响解释层”。它不负责新增健康记录类型，也不急着让记忆大幅改变提醒策略。它先回答一个更基础的问题：

- 哪条记忆被系统用到了？
- 它影响了哪一次提醒或复盘？
- 它只是作为上下文，还是改变了候选排序、提醒强度、文案或跳过逻辑？
- 如果系统没有提醒，是规则、限流、冷却、用户计划，还是记忆导致的？

第一版目标是让记忆从“看板里存在的一组条目”变成“Life 运转中可解释的一部分”。

## 背景问题

目前 Life Assistant 已经有记录、提醒、久坐检测、日报、周报、长期决定、记忆、看板和 runtime 状态。但这些模块的关系还不够顺滑：

- 看板能看到数据，但不一定能看懂系统当前为什么这么判断。
- 记忆已经能形成并记录 `life_memory_hits`，但 hit 只说明“用过”，不说明“怎么影响”。
- `life_tick` 里候选提醒、限流、冷却、记忆和最终选择混在一起，调试时需要读代码或查底层日志。
- 记忆如果直接开始改变提醒策略，容易变成新的黑盒。

因此 P1 不做强自动化策略调整，而是先做影响链路透明化。

## 非目标

- 不接入 Knowledge、Garden、Obsidian 或 RAG。
- 不让记忆直接覆盖硬规则，例如久坐强提醒、离座阈值、通知限流。
- 不做医疗判断、诊断或治疗建议。
- 不把每分钟 tick 都变成大量冗长日志。
- 不要求用户审批每条记忆。
- 不把看板变成 Agent 内部原始日志浏览器。

## 推荐方案

采用 `LifeContext -> Candidate -> Memory Influence -> Decision Trace` 的分层。

### 1. LifeContext

每次 `life_tick` 构建一个当前状态快照，聚合已有 Life 数据：

- 今日睡眠、饮食、喝水、运动记录。
- 当前坐姿窗口、Mac idle、最近离座证据。
- 今日已触发提醒、最近失败、限流冷却状态。
- 当前可用记忆：`active`、`temporary`、`weak`。
- 待补信息：缺睡眠、缺饮食、待补长期决定复盘时间等。

LifeContext 是 tick 的输入事实包，不直接决定提醒。

### 2. Candidate

现有 `_sedentary_candidate`、`_exercise_candidate`、`_water_candidate`、`_sleep_record_candidate`、`_meal_record_candidate`、`_daily_review_candidate`、`_weekly_review_candidate` 继续提出候选动作。

候选动作需要补充更明确的解释字段：

- `reason`: 为什么这个候选成立。
- `blockers`: 当前阻止它被选择的因素。
- `rule_basis`: 触发它的硬规则依据。
- `base_priority`: 未使用记忆前的优先级。

这一步仍然是规则层，避免把硬规则藏进 LLM。

### 3. Memory Influence

在候选生成后，读取相关 Life memories，并对每个候选产生影响记录。

第一版允许的 influence effect：

- `context_only`: 只作为解释上下文，不改变行为。
- `copy_hint`: 影响文案方向，但不改变触发时机。
- `risk_hint`: 提醒强度解释，例如久坐、睡眠不足等。
- `strategy_hint`: 给后续策略参考，但 P1 不自动执行。

P1 默认大部分记忆都是 `context_only` 或 `copy_hint`。只有明确安全的文案偏好可以进入 `copy_hint`，不做自动延后、压制或重排。

后续 P2 才开放：

- `priority_adjusted`
- `time_shifted`
- `suppressed`
- `strength_adjusted`

### 4. Decision Trace

每次 tick 写入一条轻量决策解释。它回答：

- 当前有哪些候选？
- 哪些候选被过滤或延后？
- 哪些记忆命中？
- 每条命中的记忆产生了什么 effect？
- 最终选中哪个提醒，或为什么没有提醒？

这条 trace 是给调试和看板使用的，不作为用户长期生活记录。

## 数据模型

新增独立表：`life_decision_traces`。

字段建议：

- `id`
- `tick_at`
- `status`: `selected`、`idle`、`locked`、`suppressed`
- `selected_reminder_type`
- `selected_reminder_id`
- `context_json`
- `candidates_json`
- `memory_influences_json`
- `decision_reason`
- `suppression_reason`
- `warnings_json`
- `created_at`

保留现有 `life_memory_hits`。两者分工：

- `life_memory_hits`: 某条记忆被哪次 tick/review 用过。
- `life_decision_traces`: 这次 tick 整体怎么判断，记忆产生了什么影响。

`life_memory_hits.target_id` 可以指向 reminder log id；decision trace 里也保存 selected reminder id，方便看板关联。

## 运行流程

1. `life_tick` 获取锁。
2. 刷新 Life Memory Analyzer。
3. 构建 LifeContext。
4. 规则层生成候选 Candidate。
5. Memory Influence 层给候选附加记忆影响。
6. Policy 层按现有优先级、限流、冷却、用户计划选择最终动作。
7. 写 reminder log、memory hits、decision trace。
8. Dashboard 读取最近 traces，展示为“记忆影响”模块。

P1 的 Policy 不改变现有提醒结果，只把原因解释清楚。这样能保证上线风险小。

## 看板展示

日视图新增“记忆影响”模块，放在提醒调度状态附近。

展示内容以最近 5 次 tick 为主：

- 最终动作：例如“运动提醒”“本次无提醒”。
- 判断原因：一句中文解释。
- 候选数量：例如“候选 3 个，选中 1 个”。
- 命中记忆：列出 1-3 条最相关记忆。
- 影响类型：`仅作上下文`、`影响文案`、`风险提示`。
- 跳过原因：限流、冷却、已有记录、用户计划、规则未到点。

看板不展示完整 JSON。原始 JSON 只放在诊断折叠区，避免变成内部日志堆叠。

## LLM 和硬规则边界

硬规则负责：

- 是否到提醒窗口。
- 久坐阈值、离座阈值、重复提醒间隔。
- 通知限流和发送冷却。
- 已记录就不重复提醒。
- 用户明确跳过或改时间。

记忆负责：

- 提供生活模式、提醒响应、文案偏好、阶段目标。
- 给候选动作增加解释和轻量提示。
- 记录是否参与了某次判断。

LLM 负责：

- 解析模糊生活记录意图。
- 在已知 fact pack 和 memory pack 范围内生成短文案。
- 周报中解释趋势，但不能编造事实。

P1 不让 LLM 独立决定是否提醒。

## 错误和降级

- trace 写入失败不阻塞 reminder log。
- memory refresh 失败时，trace 写 `warnings=["life_memory_refresh_failed"]`，但 tick 继续按规则运行。
- 记忆为空时，trace 仍然记录候选和决策原因。
- API 读 trace 失败时，看板显示“暂无决策解释”，不影响其他数据展示。
- 如果候选生成异常，应记录 warning 并维持现有错误处理，不吞掉真实异常。

## 测试策略

后端：

- schema migration 创建 `life_decision_traces`。
- repo 可以写入、读取最近 traces。
- tick 无记忆时仍写 trace。
- tick 命中记忆时同时写 memory hit 和 decision trace。
- 发送限流时 trace 说明候选被 suppress。
- 现有 Life tests 保持不改变提醒选择结果。

API：

- `/api/life/decision-traces` 返回稳定 shape。
- 空库返回空列表。
- trace JSON 字段能正确解析。

前端：

- TypeScript 类型检查通过。
- 日视图显示最近决策解释。
- 无 trace 时显示空状态。
- 记忆影响模块不出现在周视图。

## P1 验收标准

完成 P1 后，用户应该能在看板上回答：

1. 系统刚才为什么提醒或没提醒？
2. 哪些记忆参与了这次判断？
3. 这些记忆只是作为上下文，还是影响了文案？
4. 如果没有提醒，是因为规则未到、已有记录、冷却、限流，还是用户计划？
5. 记忆层有没有真的接入 Life 运转，而不是只在看板上静态展示？

## P2 方向

P2 再让记忆小范围改变策略：

- 文案偏好影响提醒话术。
- 提醒失败记忆影响同类提醒的短期降噪。
- 饮食/睡眠规律影响补记提醒的解释和时机建议。
- 周报生成下周 temporary strategy memory。

P2 的每一种行为改变都必须能在 decision trace 中解释。

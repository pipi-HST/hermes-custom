# Life Assistant Pipeline 验收清单

生成时间：2026-06-02

## 结论

当前 Life Assistant 的 P0/P1 主链路处于可用状态。

- 真实库验收：通过
- 记录链路：通过
- 提醒调度：通过
- 发送回写：通过
- review 落库：通过
- API：通过
- 看板：通过
- 数据修复 dry-run：当前无安全修复项

## 已闭环

### 记录

- 睡眠记录：已有 `sleep_end` 记录，可计算睡眠时长。
- 饮食记录：已有 `meal` 记录，当前无脏饮食记录。
- 喝水记录：已有 `water` 记录。
- 运动记录：已有 `exercise` 记录。
- 久坐记录：已有 `sedentary_start` / `sedentary_break` 相关记录。

### 提醒

- `life-assistant-tick` 以 1 分钟间隔运行。
- `/api/life/runtime` 当前显示 `cron=ok`。
- 发送通道当前 `delivery=ok`。
- 最近 6 小时无卡住的 `sending` 提醒。
- 最近 6 小时无提醒风暴。

### Review

- 日复盘已落库。
- 周复盘已落库。
- 长期决定记录和复盘提醒链路已具备基础能力。

### 看板

- 日视图可显示运行态、今日闭环、久坐实时计时、复盘、记忆、提醒判断和生活明细。
- 周视图可显示本周节律、坐姿压力、睡眠/运动和记录覆盖。
- 刷新后可保留周视图。

## 已新增工具

### `scripts/life_acceptance.py`

只读验收脚本，用于同时检查：

- Life 数据库记录覆盖
- `life_audit.py` 运行态
- API 可达性
- 看板可达性
- pipeline 覆盖度

常用命令：

```bash
.venv/bin/python scripts/life_acceptance.py --output-md docs/superpowers/reports/2026-06-02-life-acceptance.md
```

### `scripts/life_repair.py`

dry-run 优先的数据修复入口。默认只列出候选修复，不改库。

常用命令：

```bash
.venv/bin/python scripts/life_repair.py
```

需要执行安全修复时：

```bash
.venv/bin/python scripts/life_repair.py --apply
```

当前支持的安全修复：

- 把已完成投递但仍卡在 `sending` 的提醒，按 cron 的上次运行结果回写为 `sent` 或 `failed`。

当前仅列出、不会自动修复的项目：

- 重复睡眠记录。
- 可疑饮食记录。

## 当前差距

- 真实端到端对话仍需要人工抽样验证，因为自动脚本不能替代微信/Hermes 的真实入口体验。
- 历史失败提醒仍保留在库中，但不会影响最近 6 小时健康状态。
- P2 的“智能健康分析、长期目标/决定管理、记忆驱动个性化”还没有系统化完成。

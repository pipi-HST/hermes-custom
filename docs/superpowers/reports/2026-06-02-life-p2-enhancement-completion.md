# Life Assistant P2 增强完成报告

## 已落地

- 智能周报：周报控制在 600 字以内，覆盖睡眠、饮食/喝水、运动、久坐、记忆参考、数据缺口和下周一个重点建议。
- 记忆驱动提醒：喝水、饮食、久坐等提醒会把相关 Life 记忆写入提醒文案，并在 decision trace 与 memory hit 中记录 `message_context`。
- 长期决定复盘：支持创建、改期、复盘、关闭；复盘结论写入 `life.decision` 长期记忆；看板显示活跃决定、下次复盘、最近复盘和逾期提示。
- 自动补记追问：睡眠/饮食缺口保留规则底线；睡眠已有醒来但缺入睡时，只追问入睡时间；饮食无记录时追问内容和时间，已有记录不重复打扰。
- 数据修复工作台：`life_repair.py` 默认 dry-run；支持安全修复卡住的 `sending` 提醒和明确被覆盖的重复提醒；看板展示候选数量、安全可修数量和高风险建议。

## P2+ 建议

- 周报分析可以继续接入更强 LLM，总体仍保持 600 字以内，优先输出一条关键行动而不是长篇建议。
- 修复工作台前端可以增加手动 apply 按钮，但必须继续保持 dry-run 优先，并只允许安全项自动执行。
- 长期决定可以继续扩展 outcome 为完成、放弃、暂停等更细状态，但当前先通过复盘/关闭链路保证可用。
- 补记追问可以继续强化上下文窗口，让“一句话补多顿饭/一段睡眠”更稳定地结构化。

## 验收证据

- Life 全量测试：`194 passed`
- Dashboard TypeScript：`npx tsc --noEmit` 通过
- `scripts/life_acceptance.py`：`status=passed`
- `scripts/life_audit.py`：`status=ok`
- `scripts/life_repair.py`：`action_count=0`
- 浏览器渲染检查：日视图包含数据修复、提醒判断、定期复盘，无 Life API 错误。

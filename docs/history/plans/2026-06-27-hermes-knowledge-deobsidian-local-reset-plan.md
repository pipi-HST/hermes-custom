---
status: historical
execution_status: partial
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes Knowledge 去 Obsidian 化 + 本地数据重置执行计划

## 计划关系

“Hermes 本地数据总重置 + 去 Obsidian 化规划”和“Hermes Knowledge 去 Obsidian 化 + 本地数据重置规划”不是两个互相竞争的项目。它们描述的是同一次迁移，只是视角不同：

- **Knowledge 去 Obsidian 化**是产品和存储架构决策：Obsidian 不再是主载体；Hermes 以 Web 工作台、SQLite 结构化对象和少量 Knowledge File 作为主模型。
- **本地数据重置**是落地这次架构决策的操作步骤：清空本机历史 Knowledge 内容和旧 Obsidian store，让新模型从空库开始。

以后统一使用这个 canonical 名称：**Hermes Knowledge 去 Obsidian 化 + 本地数据重置执行计划**。

## 目标

- 不迁移旧知识内容，从空库重新开始。
- 保留 Hermes Knowledge Workflow：Capture、Draft、用户确认、Knowledge File 写入、结构化 Knowledge Card、Theme View、Graph、RAG。
- Obsidian 退出主载体；`~/Obsidian/hermes-vault` 定义为 Legacy Knowledge Store，验收后删除。
- 新主模型是 Web 工作台 + SQLite 结构化对象 + Knowledge File。
- 常驻 Markdown Knowledge File 只包括 Capture Note 和 Raw Evidence。
- Knowledge Card、Theme View、Graph、Review state、draft、source map 是结构化 Web/DB 对象。

## Reset 边界

清理：

- `/Users/pipi/hermes-custom/data/captures.db` 中的 Knowledge 内容表。
- `/Users/pipi/hermes-custom/data/captures.db` 中拆仓前残留的 `life_*` legacy residual tables。
- Legacy Knowledge Store：`~/Obsidian/hermes-vault`。
- reset 脚本 allowlist 内的 Knowledge 内容备份目录。
- 新 Knowledge File storage root 中 apply 前已经存在的文件；apply 后只保留空目录骨架。

保留：

- `/Users/pipi/hermes-custom/config.yaml`。
- API key、模型配置、LaunchAgent 配置、普通运行日志。
- 独立 Life store：`/Users/pipi/hermes-life/data/life.db`。

## 执行顺序

1. 架构和术语先落地：`CONTEXT.md`、`CONTEXT-MAP.md`、ADR、runtime/Web 文案都必须指向 Knowledge File + Web/DB 主模型。
2. reset 脚本先 dry-run：输出表计数、旧目录文件数、新 storage 骨架、新 storage 现有文件、automation blockers。
3. 只有在用户明确授权后，才执行 destructive apply。
4. apply 前必须停 watcher、pause cron、确认 dry-run blockers 为空。
5. apply 后跑 `--verify-empty`，再跑 isolated smoke，确认空库仍能完成 capture -> commit -> index -> search/RAG -> optional structured card。
6. 验收后保持真实本地库为空，不保留 smoke 样本。

## 当前配套文档

- `CONTEXT.md`：定义 Knowledge File、Theme View、Knowledge Card、Legacy Obsidian language。
- `CONTEXT-MAP.md`：定义 Knowledge 和 Life Assistant 的 reset 边界。
- `docs/adr/0003-knowledge-file-web-db-primary-model.md`：记录 Obsidian 退出主载体的架构决策。
- `docs/adr/0004-local-personal-data-reset-boundary.md`：记录本地个人数据 reset 的保留/清理边界。
- `docs/local-data-reset-runbook.md`：记录 destructive reset 的人工执行流程。

## 非目标

- 不把旧 `~/Obsidian/hermes-vault` 内容迁移进新库。
- 不删除 `/Users/pipi/hermes-life/data/life.db`。
- 不把 Knowledge Card、Theme View、Graph 重新持久化成默认 Markdown 文件。
- 不把 Obsidian 当作用户必须使用的产品界面；以后最多作为可选外部 Markdown 编辑器。

---
status: current
owner: knowledge-service
last_verified: 2026-11-17
verified_against: "e4cfdda (hermes-custom) / ceac73d (hermes-agent-main) + 2026-11-17 working tree"
---

# Hermes Knowledge Architecture

> 状态：current  
> 最后核对：2026-11-17  
> 对应代码：`hermes-custom` `37f6a17`，`hermes-agent-main` `c4b3b23`

## 1. 职责边界

```text
用户
  -> Hermes Runtime：消息入口、主 Agent、工具注册、Dashboard、投递
       -> Hermes Knowledge Service：Capture / Library / Reading / RAG / Theme / Card / Review
            -> SQLite + Knowledge Files + Topic registry
       -> Hermes Life（可选）：生活记录、提醒
```

**Runtime 不拥有 Knowledge 业务逻辑。** Runtime 只做三件事：

1. 把用户消息路由给主 Agent；
2. 调用 Knowledge Service 的 HTTP 接口；
3. 展示预览、来源、草稿和确认动作。

**Knowledge Service 拥有：**

- Knowledge 数据的读写；
- Markdown / SQLite 的一致性；
- 确认令牌、写入任务、运行记录；
- 对 Runtime 的 HTTP 契约。

Life Assistant 是第三个、可选的能力项目；本仓库不读写 Life 数据。

## 2. 两种运行模式

| 模式 | 开关 | 用途 | 写路径 |
|---|---|---|---|
| HTTP（生产推荐） | `HERMES_KNOWLEDGE_URL` + `HERMES_KNOWLEDGE_TOKEN` | 正常运行 | Runtime → Knowledge Service → 存储 |
| Local（仅开发） | `HERMES_KNOWLEDGE_LOCAL_ROOT` | 离线调试、老脚本 | Runtime 直接 import 本仓库 |

规则：

- **不要同时配置 URL 和 LOCAL_ROOT。** URL 优先。
- HTTP 模式必须能在没有本地 checkout 的机器上启动。
- Local 模式不是生产部署方案；启动日志会打印 `local` 并给出警告。
- `HERMES_CUSTOM_ROOT` 是 `HERMES_KNOWLEDGE_LOCAL_ROOT` 的旧别名，只用于兼容。

## 3. 数据所有权

| 对象 | 唯一真相源 | 派生/缓存 | 不要做 |
|---|---|---|---|
| Capture Note | `captures/*.md` frontmatter + body | `capture_metadata` 的查询索引 | 在 DB 里再存一份 title/summary/tags |
| Raw Evidence | `_archive/raw/*.md` | 无 | 把 raw 当默认 RAG 证据 |
| Topic / Folder | frontmatter `folder_path` + `<vault>/.hermes/knowledge_folders.json` | `capture_metadata.folder_path` | 三处各写各的 |
| Knowledge Card | `garden_cards`（SQLite） | 可选 Markdown export | 把 `garden/*.md` 当主存储 |
| Theme View | `theme_*` / `knowledge_edges`（SQLite） | 可选 Markdown export | 默认写 `themes/*.md` |
| Review | `review_items` | 无 | 再给 `captures` 留一套复习字段 |
| Embedding | `capture_embeddings` / `garden_cards.embedding` | 无 | 再从 `captures` 读旧向量 |
| Run / audit | `agent_runs`、`agent_run_events` | 无 | 用日志替代台账 |

**Capture 文本的规则**：Markdown 是正文和 frontmatter 的真相；SQLite 只做路由、索引和状态。新增字段前先问：它能不能从 Markdown 重建？能，就不要在 DB 再存一份。

**Card / Theme 的规则**：它们是结构化对象，SQLite 是真相；Markdown 只是可选导出。删除或重建导出文件不能影响卡片本身。

## 4. 写路径规则（单写入口）

1. **所有 Knowledge 写操作必须经过 Knowledge Service。**
   - HTTP 模式下，Runtime 使用 `integrations.hermes_knowledge.client.KnowledgeClient`；
   - 不允许 Runtime 在 HTTP 模式下直接 `UPDATE captures`、写 vault 文件或调用 `_knowledge_custom_module`。

2. **每个对象只有一个写服务：**
   - Capture：`bridge/capture_bridge.py`
   - Library / Topic：`bridge/library_view.py`、`bridge/folder_store.py`
   - Knowledge Card：`bridge/garden_bridge.py`
   - Reading：`bridge/reading_bridge.py`
   - Answer：`bridge/answer_bridge.py`
   - Review：`agents/review_agent.py` + `bridge/review_bridge.py`

3. **写操作必须可审计：**
   - 使用 `knowledge_write_jobs` 记录阶段；
   - 使用 `agent_runs` 记录运行；
   - 修改已有 Markdown 时使用备份（`_archive/write_backups/`）。

4. **用户确认优先：**
   - AI 可以生成草稿；
   - 不能在没有确认动作的情况下覆盖、修复或删除持久知识。

## 5. Topic / Folder 模型

当前产品里的“专题”就是文件夹，不是旧的 `Theme View`，也不是 `Knowledge Area`。

- 用户词：**专题 / Topic**
- 代码字段：`folder_path`
- 卡片上的派生字段：`garden_cards.source_theme`
- 注册表：`<vault>/.hermes/knowledge_folders.json`
- SQL 镜像：`capture_metadata.folder_path`

重命名必须走 `agents/folder_service.py:rename_folder()`（HTTP 端点的
`bridge/library_view.py:rename_topic()` 和 worker 都委托到它），一次更新：

1. `captures/*.md` frontmatter；
2. `capture_metadata.folder_path`；
3. `garden_cards.source_theme`；
4. `capture_drafts.capture_intent.folder_path`；
5. folder registry。

修复历史漂移：`scripts/repair_topics.py`（默认 dry-run）。

## 6. 当前拆分状态（诚实版）

已完成：

- Knowledge Service 有完整 FastAPI 读写接口；
- Runtime 的 42 条 knowledge 路由中，40 条有 HTTP 委托；仅 `open-local`
  和 `runtime-check` 是显式 local-only（见 `docs/INVENTORY.md`）；
- dashboard 的草稿、卡片详情、时间线、依赖读路由已改为服务端形状并委托；
- `synthesize_garden` 已改走统一 `call_custom_bridge`；
- HTTP-only 模式下 `_knowledge_custom_root()` 不再直接 503；
- Topic 重命名统一到 `agents.folder_service.rename_folder()`，Markdown、
  SQL 镜像、卡片专题和 registry 一次提交；
- 提供 `repair_topics.py`。

仍未完成：

- `open-local` 仍然只能是 local-only：它的语义是打开用户本机文件；
- `web_server.py` 仍保留 local 模式的直接 SQL / vault 回退（仅 dev-only）；
- UI 仍混用“知识库/资料库”等旧词，术语清扫未覆盖前端全部文案。

2026-11-17 已补齐：selection chat、capture revise、runtime-check、AI cancel 的 HTTP 路径；
schema v4 删除了 `captures.embedding`、`captures.review_*` 和 garden v1 镜像列。
复习调度只读 `review_items`，Capture 归档只读 `captures.archived_at`，卡片内容只读
`garden_cards.payload_json`。

HTTP 模式的 selection-chat stream 目前返回 `start` + 完整 `complete`，不转发逐阶段 progress；逐阶段 progress 仍属于本地回退路径。

这些是已知技术债，不是“设计如此”。新代码不要继续扩大它们。

## 7. 失败模式

| 现象 | 先检查 |
|---|---|
| Dashboard 知识页 503 | `HERMES_KNOWLEDGE_URL` 是否设置；`HERMES_KNOWLEDGE_TOKEN` 是否与服务 `HERMES_API_TOKEN` 一致 |
| Runtime 工具报 “not configured” | 是否只配了 URL 但工具仍走 local import；检查该工具是否使用 `call_custom_bridge` |
| 专题筛选不对 | `capture_metadata.folder_path` 是否过期；跑 `repair_topics.py --apply` |
| 卡片所属专题不对 | `garden_cards.source_theme` 是否过期；跑 `repair_topics.py --apply --fix-mismatched` |
| 检索结果旧 | `capture_embeddings` 是否为空；重建索引后再试，旧 `captures.embedding` 已不存在 |
| Git 视图报 “不是仓库” | 当前会话目录不是 Git 仓库；切到具体项目目录 |

## 8. 不要做

- 不要为了统一文案而全库重命名表 / 目录 / 函数。先写映射，再做兼容迁移。
- 不要新增第四套存储（例如再写一份 `topic.json`）。
- 不要在 Runtime 里新增直接 SQL 写 Knowledge 的代码。
- 不要把 `Knowledge Map / Knowledge Area` 当已实现功能；ADR 0005 仍是 deferred。
- 不要让 `HERMES_KNOWLEDGE_LOCAL_ROOT` 进入生产部署。

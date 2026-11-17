---
status: current
owner: knowledge-service
last_verified: 2026-11-18
verified_against: "3e6b974 (hermes-custom) + 2026-11-18 并行修复分支"
---

# Hermes Knowledge Architecture

> 状态：current
>
> 最后核对：2026-11-18
>
> 对应代码：`hermes-custom` `3e6b974`（2026-11-18 并行修复分支）

## 1. 职责边界

```text
用户
  -> Hermes Runtime：消息入口、主 Agent、工具注册、原生 Dashboard、投递
       -> Hermes Knowledge Service：Capture / Library & Topic / Reading / RAG /
            Theme / Knowledge Card / Review / Canvas / Research / Librarian /
            Answer / Knowledge Graph / Folder write jobs / Run ledger
            -> SQLite + Knowledge Files + Topic registry
       -> Hermes Life（可选）：生活记录、提醒
```

**Runtime 不拥有 Knowledge 业务逻辑。** Runtime 只做三件事：

1. 把用户消息路由给主 Agent；
2. 调用 Knowledge Service 的 HTTP 接口，或通过 `POST /api/tools/{module}/{function}` 调用白名单 bridge；
3. 展示预览、来源、草稿和确认动作。

**Knowledge Service 拥有：**

- Knowledge 数据的读写；
- Markdown / SQLite 的一致性；
- 确认令牌、写入任务、运行记录；
- 对 Runtime 的 HTTP 契约。

Research 是独立研究子系统，不是 RAG 的一个模式：入口是 `bridge/research_bridge.py`，实现放在 `agents/research/`（planner、synthesis、evidence、orchestration 等，约 8800 行）。`bridge/research/` 只保留兼容薄壳，让旧 import 返回同一个模块对象；新研究逻辑只写 `agents/research/`，不要再往兼容壳里加业务代码。

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

## 3. 子系统地图

| 子系统 | 入口 | 存储 / 真相源 |
|---|---|---|
| Capture | `bridge/capture_bridge.py`、`agents/capture_agent.py`、`agents/extractors/`；`/api/capture-drafts`、`/api/captures` | `captures/*.md`（正文 + frontmatter）、`_archive/raw/*.md`、`capture_drafts`、`capture_metadata` |
| Library / Topic | `bridge/library_view.py`、`agents/library_agent.py`、`bridge/folder_store.py`、`agents/folder_service.py`；`/api/library/*` | frontmatter `folder_path`、`<vault>/.hermes/knowledge_folders.json`、`capture_metadata.folder_path`、`garden_cards.source_theme` |
| Reading | `bridge/reading_bridge.py`、`db/reading_repo.py`；`/api/reading/*` | `reading_sessions`、`reading_turns`、`reading_evidence`、`reading_drafts` |
| RAG | `bridge/rag_bridge.py`、`bridge/rag_continue_bridge.py`、`bridge/knowledge_search.py`、`agents/rag_agent.py`；`GET /api/knowledge/search` | `capture_embeddings`、`capture_search_fts`、`citation_events`；回答附 Source Map |
| Theme | `bridge/theme_bridge.py`、`agents/theme_agent.py`、`agents/theme_graph.py`；工具 `theme_scan`（含候选查看） | `captures.theme_scanned_at`、`pending_questions`、`knowledge_edges`；可选 `themes/*.md` 导出 |
| Knowledge Card | `bridge/garden_bridge.py`、`agents/garden_agent.py`、`agents/garden_card_payload.py`；`/api/garden/*`、`/api/knowledge/cards` | `garden_cards`、`garden_drafts`（内容在 `payload_json`） |
| Review | `agents/review_agent.py`、`bridge/review_bridge.py`；工具 `daily_review` / `review_followup` / `review_feedback` | `review_items` |
| Canvas | `bridge/canvas_bridge.py`、`agents/canvas_agent.py`；工具 `generate_canvas` | 可选 `.canvas` 导出文件；没有独立表 |
| Research | `bridge/research_bridge.py`、`bridge/research_bridge_facade.py`、`agents/research/`（`bridge/research/` 仅为兼容壳）；工具 `research_with_web` | `agent_runs` / `agent_run_events` 运行记录；答案经 Answer 落为 `reading_drafts` |
| Librarian | `bridge/librarian_bridge.py`、`agents/library_agent.py`；工具 `audit_library` | `data/repair_reports/librarian-*.json`；修复写回 Capture / 索引 |
| Answer | `bridge/answer_bridge.py`、`bridge/web_context_answer.py`；`/api/knowledge/answer-drafts` | `reading_sessions` / `reading_turns` / `reading_evidence` / `reading_drafts`；确认后写 Capture Note 或 Knowledge Card |
| Knowledge Graph | `agents/knowledge_graph.py`；`/api/knowledge/graph`、`/api/knowledge/graph/refresh` | `knowledge_edges` |
| Folder write jobs | `workers/knowledge_write_worker.py`、`db/write_job_repo.py`、`bridge/knowledge_job_submit.py`、`agents/folder_service.py` | `knowledge_write_jobs`；opt-in，未接入 HTTP API |
| Run ledger | `bridge/run_ledger.py`、`bridge/agent_run_bridge.py`、`db/agent_run_repo.py`、`agents/run_evidence.py`；读 `GET /api/knowledge/object-events` | `agent_runs`、`agent_run_events`、`agent_run_objects` |

Runtime 的 envelope 工具统一通过 `bridge/result_envelope.py` 返回结果；HTTP 模式下由 `api/routes/tools.py` 的白名单执行，请求不能自选模块或函数。

## 4. 数据所有权

| 对象 | 唯一真相源 | 派生/缓存 | 不要做 |
|---|---|---|---|
| Capture Note | `captures/*.md` frontmatter + body | `capture_metadata` 的查询索引 | 在 DB 里再存一份 title/summary/tags |
| Raw Evidence | `_archive/raw/*.md` | 无 | 把 raw 当默认 RAG 证据 |
| Topic / Folder | frontmatter `folder_path` + `<vault>/.hermes/knowledge_folders.json` | `capture_metadata.folder_path` | 三处各写各的 |
| Knowledge Card | `garden_cards`（SQLite） | 可选 Markdown export | 把 `garden/*.md` 当主存储 |
| Theme View | `pending_questions`、`knowledge_edges`、`captures.theme_scanned_at`（SQLite） | 可选 `themes/*.md` export | 创建不存在的主题表；把 Theme View 当 Topic |
| Review | `review_items` | 无 | 再给 `captures` 留一套复习字段 |
| Research Run | `agent_runs` / `agent_run_events` | `reading_drafts`（可保存草稿） | 把网页结果直接写进 `captures` |
| Embedding | `capture_embeddings` / `garden_cards.embedding` | 无 | 再从 `captures` 读旧向量 |
| Run / audit | `agent_runs`、`agent_run_events`、`agent_run_objects` | 无 | 用日志替代台账 |

**Capture 文本的规则**：Markdown 是正文和 frontmatter 的真相；SQLite 只做路由、索引和状态。新增字段前先问：它能不能从 Markdown 重建？能，就不要在 DB 再存一份。

**Card / Theme 的规则**：它们是结构化对象，SQLite 是真相；Markdown 只是可选导出。删除或重建导出文件不能影响卡片本身。

**命名的规则**：`garden_cards.source_theme` 是历史列名，值就是专题（`folder_path`），不是一张独立主题表。真实主题数据只在 `pending_questions`、`knowledge_edges` 和 capture 的 `theme_scanned_at` 等既有列里。

## 5. 写路径规则（单写入口）

1. **所有 Knowledge 写操作必须经过 Knowledge Service。**
   - HTTP 模式下，Runtime 使用 `integrations.hermes_knowledge.client.KnowledgeClient`；
   - 不允许 Runtime 在 HTTP 模式下直接 `UPDATE captures`、写 vault 文件或调用 `_knowledge_custom_module`。

2. **每个对象只有一个写服务：**
   - Capture：`bridge/capture_bridge.py`
   - Library / Topic：`bridge/library_view.py`（条目写）、`bridge/folder_store.py`（注册表）、`agents/folder_service.py`（重命名唯一入口）
   - Reading / Answer：`bridge/reading_bridge.py`、`bridge/answer_bridge.py`
   - Knowledge Card：`bridge/garden_bridge.py`
   - Theme：`bridge/theme_bridge.py` + `agents/theme_agent.py`
   - Review：`agents/review_agent.py` + `bridge/review_bridge.py`
   - Research：`bridge/research_bridge.py` + `agents/research`
   - Canvas：`bridge/canvas_bridge.py`（只导出）
   - Librarian：`bridge/librarian_bridge.py`（审计；修复委托 `agents/library_agent.py`）

3. **写操作必须可审计：**
   - 使用 `knowledge_write_jobs` 记录阶段；
   - 使用 `agent_runs` 记录运行；
   - 修改已有 Markdown 时使用备份（`_archive/write_backups/`）。

4. **用户确认优先：**
   - AI 可以生成草稿；
   - 不能在没有确认动作的情况下覆盖、修复或删除持久知识。

### 5.1 一次 Capture commit 实际做什么

用户确认 `commit_capture_draft` 后，`bridge/capture_bridge.py` 会同步完成：

1. 写 `_archive/raw/<slug>.md` 和 `captures/<slug>.md`；
2. 写 `captures` 行与 identity embedding；
3. 对长文运行 Library 后处理：生成/重写 `## 目录纲要`、重建 section embedding、刷新 `capture_metadata` 和 FTS；
4. 返回一个 `next_action` “生成知识卡片”，但**不会**自动创建 Garden Card、Theme View 或 pending question。

所以“存一条笔记”仍会同步改写正文目录和索引，但不会连锁创建别的持久知识对象。卡片/主题必须由后续独立动作确认。后处理失败会让 commit 返回错误，不会留下“文件已写、索引静默缺失”的成功假象。

### 5.2 Agent Result Envelope 的适用范围

- **Agent/工具入口**：`capture_bridge`、`garden_bridge`、`reading_bridge`、`research_bridge`、`answer_bridge`、`theme_bridge`、`review_bridge`、`canvas_bridge`、`librarian_bridge`、`rag_bridge` 的公开入口返回 `bridge/result_envelope.py` 定义的 envelope。
- **View / retrieval 入口**：`bridge/library_view.py` 返回列表/详情 view model，`rag_bridge.retrieve_knowledge_sources()` 返回检索 bundle；它们不是 agent 结果，不套 envelope。
- Runtime 的 envelope 适配器要求 10 个字段完全匹配；旧格式的 `json.dumps(payload)` 和顶层 `draft_id` 回退仍在，用于兼容历史工具，不得作为新工具的返回形状。

## 6. Topic / Folder 模型

当前产品里的“专题”就是文件夹，不是 `Theme View`，也不是 deferred 的 `Knowledge Area`。

- 用户词：**专题 / Topic**
- 代码字段：`folder_path`
- 卡片上的派生字段：`garden_cards.source_theme`（列名是历史名，值是专题）
- 注册表：`<vault>/.hermes/knowledge_folders.json`
- SQL 镜像：`capture_metadata.folder_path`

Theme View 是按主题聚合的高级视图/扫描结果，不是专题本身；它使用 `pending_questions`、`knowledge_edges` 和 capture 的 `theme_scanned_at`，不会创建独立主题表。

重命名必须走 `agents/folder_service.py:rename_folder()`（HTTP 端点的
`bridge/library_view.py:rename_topic()` 和 worker 都委托到它），一次更新：

1. `captures/*.md` frontmatter；
2. `capture_metadata.folder_path`；
3. `garden_cards.source_theme`；
4. `capture_drafts.capture_intent.folder_path`；
5. folder registry。

修复历史漂移：`scripts/repair_topics.py`（默认 dry-run）。

## 7. Dashboard 边界

- 用户侧 Dashboard 只有 Hermes Runtime 原生 Dashboard（默认 `127.0.0.1:9119`）。
- 本仓库旧的 Next.js 自定义 Dashboard（`dashboard/`，端口 3000）已于 2026-06-16 废弃、2026-11-18 删除；`npm run dev/build/start` 不再是本仓库入口。
- `api/routes/dashboard_read.py` 保留，它服务的是原生 Dashboard 的草稿、卡片详情、依赖和时间线读形状，不是已删除的 Next.js 项目。
- `api/routes/captures.py` 的 `GET /api/captures` 保留，它是服务 API；Runtime client 的部分 capture 详情/修改仍会用到。

## 8. 当前拆分状态（诚实版）

已完成：

- Knowledge Service 有完整 FastAPI 读写接口，当前生成清单为 61 条服务路由（见 `docs/INVENTORY.md`）。
- Runtime 的 42 条 knowledge 路由中，40 条在 HTTP 模式下有服务端路径（38 条直接 `get_client()` 委托；`garden-drafts/preview` 转调；`automation-boundaries` 是只读观测端点），只有 `open-local` 和 `runtime-check` 是显式 local-only；这 40 条里有 36 条仍保留 local SQL/module 回退。
- Dashboard 的草稿、卡片详情、时间线、依赖读形状已在 Service 实现，Runtime 路由优先走 HTTP 委托。
- `synthesize_garden` 已改走统一 `call_custom_bridge`。
- HTTP-only 模式下 `_knowledge_custom_root()` 不再直接 503。
- Topic 重命名统一到 `agents.folder_service.rename_folder()`，Markdown、SQL 镜像、卡片专题和 registry 一次提交。
- 提供 `repair_topics.py`。

仍未完成（已知技术债，不是“设计如此”）：

- `open-local` 仍然只能是 local-only：它的语义是打开用户本机文件；`runtime-check` 也只检查本地 checkout。
- `web_server.py` 仍有约 32 处 `_knowledge_custom_module`，以及 9 条直接 SQL 写语句（草稿 intent、capture 时间戳、`capture_metadata` upsert、`garden_cards.source_theme`、`review_items` 删除、`garden_drafts` 丢弃等）。
- HTTP 模式跑工具不需要本地 checkout，但 Dashboard 的草稿、卡片详情、时间线读路由仍会读 `HERMES_KNOWLEDGE_DB` / `HERMES_KNOWLEDGE_VAULT`。这是已知例外，不是推荐生产形态。
- **local fallback 仅 dev-only，不是生产部署方案。** 不要把 `HERMES_KNOWLEDGE_LOCAL_ROOT` 当成部署开关。
- UI 仍混用“知识库/资料库”等旧词，术语清扫未覆盖前端全部文案。

2026-11-17 已补齐：selection chat、capture revise、runtime-check、AI cancel 的 HTTP 路径；schema v4 删除了 `captures.embedding`、`captures.review_*` 和 garden v1 镜像列。复习调度只读 `review_items`，Capture 归档只读 `captures.archived_at`，卡片内容只读 `garden_cards.payload_json`。

HTTP 模式的 selection-chat stream 目前返回 `start` + 完整 `complete`，不转发逐阶段 progress；逐阶段 progress 仍属于本地回退路径。

新代码不要继续扩大这些例外。

## 9. 失败模式

| 现象 | 先检查 |
|---|---|
| Dashboard 知识页 503 | `HERMES_KNOWLEDGE_URL` 是否设置；`HERMES_KNOWLEDGE_TOKEN` 是否与服务 `HERMES_API_TOKEN` 一致 |
| Runtime 工具报 “not configured” | 是否只配了 URL 但工具仍走 local import；检查该工具是否使用 `call_custom_bridge` |
| 专题筛选不对 | `capture_metadata.folder_path` 是否过期；跑 `repair_topics.py --apply` |
| 卡片所属专题不对 | `garden_cards.source_theme` 是否过期；跑 `repair_topics.py --apply --fix-mismatched` |
| 主题候选为空 | `captures.theme_scanned_at`、`pending_questions` 是否有数据；先跑一次 `theme_scan` |
| 检索结果旧 | `capture_embeddings` 是否为空；重建索引后再试，旧 `captures.embedding` 已不存在 |
| 联网研究没有结果 | 看 `agent_runs` 的 research run 与 warnings；再查搜索/抓取后端和网络 |
| Git 视图报 “不是仓库” | 当前会话目录不是 Git 仓库；切到具体项目目录 |

## 10. 不要做

- 不要为了统一文案而全库重命名表 / 目录 / 函数。先写映射，再做兼容迁移。
- 不要新增第四套存储（例如再写一份 `topic.json`，或再造主题表）。
- 不要在 Runtime 里新增直接 SQL 写 Knowledge 的代码。
- 不要把 `Knowledge Map / Knowledge Area / Map Proposal` 当已实现功能；ADR 0005 仍是 deferred。
- 不要让 `HERMES_KNOWLEDGE_LOCAL_ROOT` 进入生产部署。
- **不要把新研究逻辑写回 `bridge/research` 兼容壳；只写 `agents/research`。**
- 不要把已删除的 Next.js `dashboard/` 恢复成产品入口；用户侧只维护原生 Dashboard。

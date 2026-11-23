---
status: current
owner: knowledge-service
last_verified: 2026-11-22
verified_against: "9e44c73 (hermes-custom) + 2026-11-22 working tree"
---

# Hermes Knowledge Glossary

> 状态：current  
> 规则：**一个概念，一个用户词。** 代码名可以保留，但文档和 UI 不再混用旧名。

## 1. 规范术语

| 用户词 | 英文 | 代码 / 存储 | 含义 | 不要叫 |
|---|---|---|---|---|
| 收集 | Capture | `capture_*`、`captures` | 用户把材料交进来的动作 | 抓取（作名词）、入库 |
| 保存 | Commit | `capture_drafts.status=committed`、`captures` | 用户确认草稿并写入资料库的动作 | 入库、提交（作产品词时） |
| 待确认草稿 | Capture Draft | `capture_drafts` | 保存前可修改的草稿 | 笔记、原文 |
| 资料笔记 | Capture Note | `captures/*.md` | 已确认的加工后资料 | capture（指文件时） |
| 原始证据 | Raw Evidence | `_archive/raw/*.md` | 为追溯保留的来源原文 | raw、缓存 |
| 资料库 | Library | `library_*`、`capture_metadata` | 浏览、筛选、编辑资料的界面/能力 | 知识库（泛称） |
| 伴读会话 | Reading Session | `reading_*` | 围绕一份或多份材料的临时讨论 | 阅读、聊天 |
| 知识卡片 | Knowledge Card | `garden_cards`、`garden_drafts` | 可复用的观点/事实/方法/风险 | Garden Card、花园卡片 |
| 专题 | Topic | `folder_path`、`garden_cards.source_theme`、`.hermes/knowledge_folders.json` | 资料和卡片的归属主题 | Theme View、知识领域、文件夹 |
| 主题视图 | Theme View | `pending_questions`、`knowledge_edges`、`captures.theme_scanned_at` | 按主题聚合的高级视图/扫描结果；不是专题本身 | MOC、主题页（作产品词时） |
| 知识图谱 | Knowledge Graph | `knowledge_edges` | 卡片、资料、专题之间的关系浏览 | 知识地图 |
| 知识地图 | Knowledge Map | **deferred 设计稿，无表、无接口** | 未来的导航结构；ADR 0005 为 deferred | 图谱、目录树、当前功能 |
| 联网研究 | Research | `bridge/research_bridge.py`、`agents/research/*`（`bridge/research/*` 仅为兼容壳） | 结合本地资料与公开网页回答 | 联网、web QA |
| 复习 | Review | `review_items` | 按复习计划回看知识点 | 每日回顾（可作功能名） |
| 来源表 | Source Map | `source_map`（结果信封字段，不是表） | 回答附带的分类来源列表 | 引用列表（泛称） |
| 结果信封 | Agent Result Envelope | `bridge/result_envelope.py` | Agent 返回给 Runtime 的统一结构 | 工具返回、JSON |

## 2. 旧名与处置

| 旧名 | 现状 | 处置 |
|---|---|---|
| `Garden Card` | 代码/DB 仍用 `garden_*` | 只允许出现在代码、迁移和内部注释；用户词统一“知识卡片” |
| `Garden / 知识花园` | 早期产品名 | 只允许出现在 `docs/history/` |
| `MOC` | 早期 Obsidian 页面结构 | 只允许历史/导出语境；不作用户词 |
| `Canvas` | 早期可视化/导出 | 降级为可选导出；不作用户主流程 |
| `Theme` / `Theme Page` | 早期主题聚类 | 用户说“专题”；内部高级能力说“主题视图” |
| `Knowledge Map` / `Knowledge Area` / `Map Proposal` / `Health Report` | deferred 设计稿词汇，代码未实现 | 在实现前只放 `docs/history/` 或 ADR，不放入当前术语表，也不作用户默认词 |
| `Obsidian vault` | 历史存储主载体 | 现在只是可选 Markdown 编辑器/导出；主存储是 Knowledge Files + SQLite |
| `Life` 表 / `life_*` | 已拆到独立项目 | 不得在 Knowledge 里新增或读取 |

## 3. 代码名映射（不要改代码名，先改文案）

| 用户看到 | 代码里可能看到 |
|---|---|
| 收集 | `capture_content`、`create_capture_draft` |
| 保存 | `commit_capture_draft`、`committed_capture_id` |
| 待确认草稿 | `capture_drafts`、`draft_ready`、`raw_extracted` |
| 资料笔记 | `captures.file_path`、`Capture Note` |
| 资料库 | `library_items`、`library_view` |
| 伴读会话 | `reading_sessions`、`reading_turns` |
| 知识卡片 | `garden_cards`、`garden_drafts` |
| 专题 | `folder_path`、`source_theme`、`knowledge_folders.json` |
| 主题视图 | `theme_scan`（含候选查看，工具名不是表）、`pending_questions`、`knowledge_edges` |
| 知识图谱 | `knowledge_edges`、`graph_report` |
| 联网研究 | `research_with_web`、`research_bridge`、`agents/research/*`（`bridge/research/*` 仅为兼容壳） |
| 复习 | `review_items`、`daily_review` |

### 旧工具名映射

用户词统一，代码名可以保留；旧工具名只用于兼容，不再出现在新文案里。

| 旧工具名 | 现在的入口 | 说明 |
|---|---|---|
| `search_my_notes` | `search_knowledge_base`（`bridge/rag_bridge.py`）/ RAG 检索 | 工具名暂时保留，内部已委托到统一的 RAG 检索 |
| `refine_capture` | `revise_capture_draft`（`bridge/capture_bridge.py`） | 兼容旧名；新代码和新提示词用 `revise_capture_draft` |

## 4. 一句话规则

- 用户界面只用“用户词”一列。
- 代码名可以保留；旧工具名只做兼容，用户词统一。
- 历史文档可以用旧名，但必须放在 `docs/history/`。
- 新增术语前，先改本表；没有表，不准进代码。
- 不为了改名而改表名；先做兼容层，再迁移。

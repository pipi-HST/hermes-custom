---
status: historical
owner: knowledge-service
last_verified: 2026-11-17
verified_against: snapshot; kept for context, not a current runbook
---

# Hermes Knowledge 文档 / 代码 / 术语一致性审计

> **状态：2026-11-17 审计快照。** 第 1 周止血和部分第 2-3 周修复已开始落地；当前入口看 `README.md` / `ARCHITECTURE.md` / `RUNBOOK.md`。

- 日期：2026-11-17
- 范围：`hermes-custom`、`hermes-agent-main`
- 方式：只读审计；读文档、读代码、跑测试、查真实 SQLite / vault 数据；**没有改任何代码和业务数据**
- 基线：`hermes-custom` HEAD `37f6a17`，`hermes-agent-main` HEAD `c4b3b23`

## 0. 一句话结论

不是“代码写得烂”。当前 `hermes-custom` 全量测试 **1287 个用例，退出码 0**；真正的问题是：

> **一套系统，三张地图，两条写路径，四种叫法。**

- **三张地图**：HANDOFF 的 5 月地图、CONTEXT 的 6 月地图、2026-11 产品故事里的 Web-native 地图，互相不是替代关系，而是同时存在。
- **两条写路径**：Knowledge Service 和 Hermes Runtime 都能直接改同一个 SQLite / vault。
- **四种叫法**：代码叫 `garden/theme/capture/folder`，UI 叫“资料/卡片/专题”，CONTEXT 叫 `Knowledge Card/Theme View/Knowledge Map`，旧文档叫“知识花园/主题/MOC”。

这不是“未来三个月会出问题”，而是**现在已经有数据不一致的实例**（见 §2.4）。

---

## 1. 审计结果总览

| 级别 | 问题 | 影响 |
|---|---|---|
| P0 | HTTP 模式下 `synthesize_garden` 直接失败 | 推荐部署模式下“生成知识卡片”核心流程不可用 |
| P0 | Runtime 标称 HTTP 模式，实际仍要求本地 checkout | 只配 `HERMES_KNOWLEDGE_URL` 时 dashboard 知识页 503 |
| P0 | Runtime 与 Service 双写同一个 DB / vault | 同一对象两种写法，迟早不一致 |
| P1 | Topic / folder 有三份存储，重命名不全量同步 | 当前 6/6 篇资料的 `capture_metadata.folder_path` 已是 `Inbox` |
| P1 | 入口文档停在 5 月/6 月 | 新人按 HANDOFF / SETUP 操作会对不上代码 |
| P1 | 术语四套并存，且 `Knowledge Map` 没有实现 | 无法判断哪个词是“当前产品词” |
| P1 | Fork 治理文档自己已被 CI 判定过期 | `fork_delta.py --check` 退出码 1 |
| P1 | 没有 README / AGENTS / 架构图 / 术语表 / 文档索引 | 新人和新 Agent 没有“总图” |
| P1 | 文档里的数字全部手写 | 112 / 600 / 1247 / 1287 四个测试数同时存在 |
| P2 | 旧字段、旧表、旧脚本继续留在主路径 | `captures.embedding`、garden v1 列、根目录 `_cron_*` |
| P2 | 历史代码快照带“恢复”命令 | 会把 5 月旧版覆盖到当前 runtime |
| P2 | 模型名、Python 版本、端口、路径多处不一致 | 换机器/换人必踩 |

---

## 2. 关键发现与证据

### 2.1 P0：HTTP 模式下知识卡片生成是坏的

架构文档和 `.env.example` 都说 HTTP 模式是推荐模式：

- `.env.example`：`# HTTP mode (recommended): the runtime talks to the Knowledge service...`
- `integrations/hermes_knowledge/client.py`：已有完整 HTTP client
- `tools/hermes_custom_envelope.py`：`call_custom_bridge` 已支持“本地 checkout 或 HTTP 服务”二选一

但 `tools/synthesize_garden_tool.py` 绕过了这条统一入口：

```python
# tools/synthesize_garden_tool.py:55-58
def do_synthesize(**kwargs):
    ensure_hermes_custom_path()          # 只认 HERMES_KNOWLEDGE_LOCAL_ROOT
    from bridge.garden_bridge import synthesize_garden
    return synthesize_garden(**kwargs)
```

实测：即使设置了 `HERMES_KNOWLEDGE_URL`，只要没有本地 checkout，调用就返回：

```text
KnowledgeUnavailable: Hermes Knowledge is not configured:
set HERMES_KNOWLEDGE_URL (HTTP mode) or HERMES_KNOWLEDGE_LOCAL_ROOT (local checkout)
```

而 `commit_garden_draft` 走的是 `call_custom_bridge`，HTTP 模式正常。结果是：

- 草稿能确认、能入库；
- 但“从材料生成卡片草稿”这一步失败；
- 用户看到的是“工具执行失败”，不是“服务没配置”。

**修法**：改成

```python
return call_custom_bridge("garden_bridge", "synthesize_garden", **kwargs)
```

并补一条“HTTP 模式下 personal-assistant 每个工具都能调用”的矩阵测试。

---

### 2.2 P0：HTTP 模式是半成品，Runtime 仍然离不开本地 checkout

`hermes_cli/web_server.py` 里：

- `_knowledge_custom_root()` 只读取 `HERMES_KNOWLEDGE_LOCAL_ROOT` / `HERMES_CUSTOM_ROOT`，**不读取 `HERMES_KNOWLEDGE_URL`**；
- 没有本地 root 时直接抛 503，错误信息却写着“Set HERMES_KNOWLEDGE_URL (HTTP mode) or HERMES_KNOWLEDGE_LOCAL_ROOT”。

实测：

```text
HERMES_KNOWLEDGE_URL=http://127.0.0.1:8000
HERMES_KNOWLEDGE_LOCAL_ROOT 未设置
-> HTTPException 503 feature_disabled
```

更严重的是，`web_server.py` 里有大量直接依赖本地 checkout 的调用：

- `_knowledge_conn()` 约 26 处；
- `_knowledge_vault_path()` 约 24 处；
- `_knowledge_custom_module()` 约 31 处；
- 直接 `UPDATE captures`、`DELETE FROM ...`、读写 vault 文件的路径仍然存在。

**结论**：现在的“仓库拆分”只完成了“多了一个 HTTP client”，没有完成“Runtime 不再拥有 Knowledge 业务逻辑”。这就是典型的半拆状态。

---

### 2.3 P0：同一个数据库 / vault，有两个写入口

两条路径都能写：

1. **Knowledge Service**：`api/routes/*` → `bridge/*` → SQLite / Markdown；
2. **Hermes Runtime**：`hermes_cli/web_server.py` 内部直接 SQL / 直接文件写入。

例如文件夹重命名：

- Service 版：`bridge/library_view.py:rename_folder_everywhere()`
- Runtime 版：`hermes_cli/web_server.py:6328-6383` 又实现了一遍

两边都只更新：

- `capture_drafts.capture_intent.folder_path`
- `captures/*.md` 的 frontmatter
- `captures.updated_at`

两边都**没有**更新：

- `capture_metadata.folder_path`（SQL 镜像）
- `garden_cards.source_theme`（卡片所属专题）

也就是说：**同一个 bug 被复制了两份**。

---

### 2.4 P1：Topic / folder 有三份存储，而且当前数据已经不一致

同一篇资料的“专题/文件夹”至少存在三处：

| 位置 | 作用 | 谁写 |
|---|---|---|
| `captures/*.md` frontmatter `folder_path` | 文档里的真相 | Capture / Library / rename |
| `capture_metadata.folder_path` | SQL 查询镜像 | `LibraryAgent._sync_capture_folder()` |
| `capture_drafts.capture_intent.folder_path` | 草稿期的意图 | Capture / rename |

实际数据检查（当前 `data/captures.db` + `data/vault`）：

```text
6/6 篇 capture 的 frontmatter 有正确专题
6/6 篇 capture_metadata.folder_path 仍然是 Inbox
```

也就是说，按专题筛选时 SQL 镜像和 Markdown 真相已经对不上。另有一张卡片的 `source_theme` 与其来源 capture 当前专题不一致：

```text
card_episodic_memory_fact: card=Agent 与大模型, capture=行业与产品拆解
```

这不是“未来风险”，是**现在就能查到的脏数据**。

**修法二选一**：

- A. 保留 frontmatter 为唯一真相，`capture_metadata.folder_path` 只做派生缓存；所有写路径必须调用同一个 `set_capture_folder()`，并在重命名时同步卡片 `source_theme`。
- B. 去掉 SQL 镜像，列表筛选时实时读 frontmatter（性能不够再加缓存，但缓存必须有失效机制）。

不要继续让三处各写各的。

---

### 2.5 P1：入口文档与代码的硬矛盾

`HANDOFF.md` 头部还写着“最后更新 2026-05-27”，正文甚至包含 2026-05-28 的内容；而仓库第一次 git commit 是 2026-05-28。也就是说，**这份“交接文档”描述的是仓库建立前的状态**，之后 300+ 次提交它没有跟着走。

它自己贴了 2026-06-19 的免责声明，但免责声明不能替代重写：文件仍在根目录，仍是新人第一眼看到的“交接文档”，正文里的具体命令和表结构仍会被照做。

| 文档说 | 实际 | 证据 |
|---|---|---|
| 5 个 Agent | `agents/` 有 22 个模块；runtime 的 personal-assistant 是 14 个核心工具 | `HANDOFF.md:18`、`toolsets.py:493` |
| 5 张表 | 当前库 24 张业务表（含 1 张 FTS 虚拟表，不含 5 张 FTS 影子表） | `HANDOFF.md:53/193`、DB `sqlite_master` |
| 112 个测试 | 1287 个用例，全量 exit 0（含平台相关 skip） | `HANDOFF.md:59/281`、本次实测 |
| Dashboard 端口 3000 | `dashboard/package.json` 三个脚本全部 `exit 1`；当前 dashboard 是 9119 | `HANDOFF.md:57/244`、`dashboard/DEPRECATED.md` |
| `raw/` 存原文 | 实际是 `_archive/raw/` | `HANDOFF.md:81`、`agents/markdown_writer.py:134` |
| `garden/` 存卡片 | vault 里 0 个 garden 文件；9 张卡全在 SQLite | 实际 vault 目录、`bridge/garden_bridge.py:713` |
| `themes/` 存主题页 | 默认扫描不再写 `themes/*.md`，只有显式 export 才写 | `bridge/theme_bridge.py:590-592` |
| DB 不存标题/摘要/标签 | `capture_drafts` 存 title/summary/tags；`garden_cards` 存 title/content_markdown；`capture_metadata` 存 metadata_json | `db/schema.py:163-180/103-124/206-216` |
| 全量 1290+ 用例 | 1287 | `HANDOFF.md:232` |
| Python 3.11 | 当前 `.venv` 是 3.12.0；CI 用 3.11 | 本次实测、`.github/workflows/tests.yml` |
| SETUP：三个仓库，Life 在 `/Users/pipi/hermes-life` | 当前工作区只有两个仓库，Life checkout 不存在；工具调用会返回 `LifeUnavailable` | `SETUP.md:7-14`、`integrations/hermes_life.py` |
| SETUP：API 只挂 `/api/captures` | 实际 8 个 router、51 个路由装饰器 | `SETUP.md:37-41`、`api/main.py` |
| SETUP：runtime 从两个本地 checkout 加载能力 | runtime 已支持并推荐 HTTP 模式；本地 checkout 只是回退 | `SETUP.md:88-91`、`.env.example:487-498` |
| SETUP：服务端 `HERMES_API_TOKEN` | runtime 侧用的是 `HERMES_KNOWLEDGE_TOKEN`；文档没写“两边填同一个值” | `api/main.py:53`、`integrations/hermes_knowledge/client.py:137` |

注意：`HANDOFF.md` 关于 `captures` 表“12 列”的描述仍然是对的；错的是“只有 5 张表”和“所有文本元数据都在 frontmatter”。

---

### 2.6 P1：四套术语同时存在

| 层 | 用的词 |
|---|---|
| 旧文档 / HANDOFF | 知识花园、主题聚类、MOC、Obsidian vault |
| CONTEXT / ADR（6 月） | Capture、Capture Note、Knowledge Card、Theme View、Knowledge Map、Knowledge Area、Map Proposal |
| 当前 UI（2026-11） | 资料伴读、知识卡片、专题输出、所属专题、知识图谱 |
| 代码 / 数据库 | `capture_*`、`garden_cards`、`garden_drafts`、`theme_*`、`folder_path`、`source_theme`、`knowledge_edges` |

最典型的冲突：

- CONTEXT 把 **Theme View** 定义为当前 Web 产品对象；
- 当前 UI 的主词是 **专题（Topic）**；
- 代码里“专题”实际是 `folder_path` / `source_theme`，不是 `theme_*`；
- CONTEXT 里的 **Knowledge Map / Knowledge Area / Map Proposal / Health Report** 在代码中 **0 处实现**；
- 旧文档把 **Garden Card** 当产品词，CONTEXT 把它降级为内部名，UI 又叫 **知识卡片**。

这不是“术语多”，是**没有一张对照表告诉人和 Agent：哪个是当前词，哪个是历史词，哪个只是代码名**。

---

### 2.7 P1：Fork 治理文档自己已经过期

`docs/fork-upstream.md` 是 runtime 仓库的“哪些 upstream 文件被改过”的治理文档，表格由 `scripts/fork_delta.py` 生成。

实测：

```text
python scripts/fork_delta.py --check
docs/fork-upstream.md is stale — run `python scripts/fork_delta.py --write`
EXIT=1
```

数字也对不上：

| 文档 | 实际 |
|---|---|
| 100 个 upstream 文件被改 | 142 个 upstream-original 文件被改（另有 105 个 fork-only 新增文件） |
| `web_server.py` +3,477 / -21 | +4,121 / -21 |
| 表格是“generated, do not edit” | 没有接进 CI，所以没人发现它过期 |

同时，`AGENTS.md` 没有提到 `fork-upstream.md` 或 `fork_delta.py`，CI 也没有跑这个检查。治理规则存在，但没有门禁。

---

### 2.8 P1：没有“总图”，只有一堆局部图

`hermes-custom` 根目录缺少：

- `README.md`
- `AGENTS.md`
- `ARCHITECTURE.md`
- `GLOSSARY.md`
- `docs/README.md`（文档索引）

现有文档规模：

- 90 个 Markdown 文件；
- 50 个 plans、10 个 specs、5 个 ADR；
- 1,144 处 `/Users/pipi/...` 绝对路径，分布在 48 个文档里；
- `Garden` 出现 1,514 次、`Obsidian` 323 次、`MOC` 203 次、`Canvas` 375 次、`Research` 397 次、`Knowledge Map` 27 次。

这些不是“文档多”，是**没有分层**：当前运行手册、历史设计、一次性施工单、面试稿、代码评审、benchmark 快照全混在一起。新人和新 Agent 无法判断该信哪一份。

---

### 2.9 P2：几个会直接咬人的小问题

1. **根目录 `_cron_check_new.py` 查 `captures.title`**  
   `captures` 早就没有 `title` 列了，这个脚本一跑就报 `no such column: title`。根目录还有 5 个同类 `_cron_*` / `_check_state.py`，全是硬编码 Mac 路径的一次性脚本。

2. **`docs/checkpoints/2026-05-29-.../` 的恢复命令会覆盖当前 runtime**  
   12 个被保存的 `.py` 文件与当前 runtime **全部不同**（例如 `capture_tool.py` 2,903 vs 6,053 字节）。README 里的 `cp -R ...` 会把 5 月旧版直接盖到今天的 runtime 上。

3. **`theme_candidates` 是一张不存在的表**  
   `hermes_cli/web_server.py:2953/3109` 的自动化清单声称 `theme_scan` 会写 `theme_candidates`，实际数据库里没有这张表；候选数据存在 `pending_questions`。这会让“自动化边界”接口给出错误的写表清单。

4. **模型名不统一**  
   代码/配置里出现：`gpt-4o`、`deepseek-v4-pro`、`deepseek-v4-flash`、`deepseek-flash`。至少说明没有单一模型配置源；哪个是可用名、哪个是笔误，文档没有说。

5. **`config.yaml` 里有真实 API key**  
   已确认 `config.yaml` 被 `.gitignore` 忽略，没有进 Git；但如果这个工作区被复制、打包或上传过，建议轮换 key。文档里不要再出现任何 `sk-` 值。

---

## 3. 文档分层与处置建议

### 3.1 当前应保留并重写的（Root / Current）

| 文件 | 处置 |
|---|---|
| `SETUP.md` | 重写为跨平台 quickstart；补 HTTP 模式、token 配对、8 个 router、Windows 命令 |
| `CONTEXT.md` | 保留“产品工作流”，删掉未实现的 Knowledge Map 大段，补 Library/Research/Topic/Folder/Presentation |
| `CONTEXT-MAP.md` | 保留 Knowledge / Life 边界；注明 Life 仓库是可选的、缺失时工具会返回什么 |
| `docs/adr/0001-0004` | 保留；补“实现状态”一行 |
| `docs/adr/0005` | 改为 `Status: Proposed / Deferred`，或明确“尚未实现” |
| `docs/PRODUCT-REFACTOR-STORY.md` | 保留为产品叙事；但它不是运行手册，文件头要写清楚 |
| `docs/CODE-REVIEW-V3.md` | 保留为 2026-11-16 的评审快照；头部已注明历史数字，保持 |
| `docs/BENCHMARK-*` | 保留；已经有“不要引用”的警告，做得对 |
| `docs/PLAN-P10-T9-T26.md` | 保留为当前技术债执行单 |
| `docs/local-data-reset-runbook.md` | 重写为跨平台；删掉硬编码 cron id / launchctl / Mac 路径 |

### 3.2 应移到 `docs/history/` 的（Historical）

- `HANDOFF.md`（改名为 `HANDOFF-2026-05.md`）
- `docs/hermes-function-tool-map-2026-06-01.md`
- `docs/hermes-knowledge-v0.1-acceptance.md`
- `docs/superpowers/plans/*`（50 个）
- `docs/superpowers/specs/*`（10 个）
- `docs/superpowers/reports/*`
- `docs/checkpoints/*`（只留 README 指向 commit，不要留可执行旧代码）

### 3.3 应删除或合并的

- `docs/checkpoints/.../agent/*.py`、`.../tools/*.py`：删掉代码副本和 `cp` 恢复命令；保留一行“checkpoint commit = e01abb0”。
- 根目录 `_cron_check_new.py`、`_cron_db_stats.py`、`_cron_pipeline.py`、`_cron_schema.py`、`_check_state.py`：移到 `scripts/debug/` 或删除；它们已经和 schema 脱节。
- `docs/HERMES_IMPROVEMENT_REPORT_v2_VERIFIED.md`：与 `EXECUTION-PLAN-V3.3.md` 内容不同但主题重叠，合并成一份“V3 施工总单”，或明确一个是历史评审、一个是执行单。
- `docs/BENCHMARK-RAG-V3.md`、`benchmark-rag-v3.json`：已被 post-fix / DashScope 文档取代；保留但只放在 `docs/history/benchmarks/`。

### 3.4 必须新增的（Missing）

```text
README.md                     # 1 页：是什么、两个仓库、5 分钟启动、下一步读什么
ARCHITECTURE.md               # 当前架构、所有权、写路径、数据真相矩阵
GLOSSARY.md                   # 中英术语 + 代码名 + 禁用旧名 + 状态
RUNBOOK.md                    # 启动/停止/排障/重置/备份，Windows + macOS
AGENTS.md                     # 给 AI Agent 的硬规则：改功能必须同步哪些文档/测试
docs/README.md                # 文档索引：Current / Historical / Superseded
docs/CURRENT-STATE.md         # 由脚本生成的测试数/表数/工具数/路由数/commit
docs/adr/README.md            # ADR 索引与状态
```

---

## 4. 术语收敛方案（建议）

**原则：不搞大重命名。** 数据库表、目录、函数名先冻结；先把“用户词”和“代码词”的映射写清楚，再决定是否迁移。

| 概念 | 建议用户词 | 代码/存储 | 旧名/禁用 | 处置 |
|---|---|---|---|---|
| Capture | 收集 | `capture_*`、`captures` | 捕获、抓取（作名词时） | 保留 |
| Capture Draft | 待确认草稿 | `capture_drafts` | 草稿（泛称） | 保留 |
| Capture Note | 资料笔记 | `captures/*.md` | capture（指文件时） | 保留 |
| Raw Evidence | 原始证据 | `_archive/raw/` | raw、原文库 | 保留 |
| Library | 资料库 | `library_*`、`capture_metadata` | 知识库（泛称） | 保留，补定义 |
| Reading Session | 伴读会话 | `reading_*` | 阅读、讨论 | 保留，补定义 |
| Knowledge Card | 知识卡片 | `garden_cards`、`garden_drafts` | Garden Card、花园卡片 | 用户词统一；代码名暂留 |
| Topic | 专题 | `folder_path`、`source_theme` | Theme View、知识领域 | **当前 UI 主词，建议定为规范词** |
| Theme View | 主题视图（高级） | `theme_*`、`pending_questions` | MOC、主题页 | 降级为高级/内部词，或映射到 Topic |
| Knowledge Map | 知识地图（未实现） | 无 | Knowledge Area、Map Proposal | **先标记 Deferred，不要当现状写** |
| Knowledge Graph | 知识图谱 | `knowledge_edges` | 关系图 | 保留 |
| Research | 联网研究 | `bridge/research/*` | 联网、web QA | 保留，补定义 |
| Review | 复习 | `review_items` | 每日回顾 | 保留 |
| Canvas | 画布（导出） | `canvas_*` | Obsidian Canvas | 降级为导出/历史 |
| Source Map | 来源表 | `source_map` | 引用列表 | 保留 |
| Agent Result Envelope | 结果信封 | `result_envelope` | 工具返回 | 保留 |

**硬规则**：

- 用户可见文案只使用“建议用户词”一列；
- 代码名可以继续用 `garden_*` / `theme_*`，但注释和文档必须写“内部名，对应 Knowledge Card / Topic”；
- `MOC`、`Garden Card`、`Canvas` 不再出现在“当前产品说明”里，只允许出现在历史文档和代码注释；
- `Knowledge Map` 在实现前不得出现在 CONTEXT 的“当前术语”表里。

---

## 5. 功能与文档覆盖矩阵

符号：✅ 有且文档覆盖；⚠ 有但文档不完整/只在历史文档；❌ 文档有但代码没有。

| 功能 | 代码 | 当前 UI | CONTEXT | SETUP | 结论 |
|---|---|---|---|---|---|
| Capture | ✅ | ✅ | ✅ | ✅ | 保留 |
| Capture Draft | ✅ | ✅ | ✅ | ⚠ | 补状态机 |
| Capture Note / Raw Evidence | ✅ | ✅ | ✅ | ⚠ | 补实际路径 |
| Library（浏览/编辑/文件夹） | ✅ | ✅ | ⚠ | ❌ | 补定义与 API |
| Reading Session | ✅ | ✅ | ✅ | ✅ | 保留 |
| RAG / Source Map | ✅ | ✅ | ✅ | ⚠ | 补引用契约 |
| Research（本地+联网） | ✅ | ✅ | ❌ | ❌ | **补一整节** |
| Review | ✅ | ⚠ | ✅ | ✅ | 补状态机 |
| Knowledge Card | ✅ | ✅ | ✅ | ⚠ | 用户词统一 |
| Topic / Folder | ✅ | ✅ | ⚠ | ❌ | **补定义与存储边界** |
| Theme View / theme_scan | ✅ | ⚠ | ✅ | ⚠ | 决定保留还是降级 |
| Knowledge Graph | ✅ | ✅ | ⚠ | ✅ | 补“不是 Knowledge Map” |
| Knowledge Map / Area / Proposal | ❌ | ❌ | ✅ | ❌ | **删除或标 Deferred** |
| Canvas | ✅ | ⚠ | ⚠ | ❌ | 标为导出/历史 |
| Selection Chat / 侧栏 AI | ✅ | ✅ | ❌ | ❌ | **补设计文档入口** |
| Agent Result Envelope | ✅ | — | ✅ | ❌ | 补到架构文档 |

统计对照：

- `Research` 在 50 个 plans 里出现 397 次，在 `CONTEXT.md` 里 0 次；
- `Knowledge Map` 在 CONTEXT/ADR/设计稿里 27 次，在代码里 0 次；
- `Library` 在代码里有 88k 的 `library_agent.py`，CONTEXT 只有 2 次泛泛提及。

---

## 6. 架构整改：把“两条写路径”收敛成一条

### 6.1 目标形态

```text
微信 / 飞书 / Web
        |
        v
Hermes Runtime（gateway / tools / dashboard）
        |
        |  HTTP（生产唯一写路径）
        v
Hermes Knowledge Service（FastAPI）
        |
        +-- SQLite（结构化对象：卡片、主题、复习、运行记录）
        +-- Knowledge Files（Capture Note / Raw Evidence）
        +-- folder registry（专题列表）
```

### 6.2 规则

1. **生产环境只允许 Service 写 Knowledge 数据。**
   - Runtime 的 dashboard 写操作全部走 `KnowledgeClient`；
   - `HERMES_KNOWLEDGE_LOCAL_ROOT` 只用于开发/离线调试，启动时打印“DEV MODE”；
   - 删除或隔离 `web_server.py` 里的直接 SQL 写路径。

2. **HTTP 模式必须真正无 checkout 运行。**
   - `_knowledge_custom_root()` 在 `HERMES_KNOWLEDGE_URL` 存在时不得抛 503；
   - `_knowledge_custom_module()` 的调用点改成 service API；
   - 加一条 smoke：只设置 `HERMES_KNOWLEDGE_URL` + `HERMES_KNOWLEDGE_TOKEN`，跑 capture → draft → commit → card → RAG。

3. **每个对象只有一个写服务。**
   - Topic/folder：`set_capture_folder()` / `rename_topic()` 一个入口；
   - Knowledge Card：`garden_bridge` 一个入口；
   - Capture：`capture_bridge` 一个入口；
   - Review：`review_items` 一个状态机。

4. **数据真相矩阵**（建议写进 `ARCHITECTURE.md`）：

| 对象 | 真相源 | 派生/缓存 | 禁止 |
|---|---|---|---|
| Capture Note | Markdown frontmatter + body | `capture_metadata` 查询镜像 | 在 DB 再存一份 title/summary |
| Raw Evidence | `_archive/raw/*.md` | 无 | 当默认 RAG 证据 |
| Topic / Folder | frontmatter `folder_path` + folder registry | `capture_metadata.folder_path` | 三处各写各的 |
| Knowledge Card | `garden_cards`（SQLite） | 可选 Markdown export | 把 `garden/*.md` 当主存储 |
| Theme View | `theme_*` / `knowledge_edges`（SQLite） | 可选 Markdown export | 默认写 `themes/*.md` |
| Review | `review_items` | `captures.review_*`（旧） | 两套都当权威 |
| Embedding | `capture_embeddings` + `garden_cards.embedding` | `captures.embedding`（旧） | 无写入方却继续读 |

5. **Fork 治理**：
   - `python scripts/fork_delta.py --write` 重新生成；
   - 在 `.github/workflows/lint.yml` 加 `--check`；
   - 在 `AGENTS.md` 写“改 frozen 文件必须更新 fork-upstream 表”；
   - 记录 upstream 对应版本（当前 `pyproject.toml` 是 0.14.0）。

---

## 7. 30 / 60 / 90 天行动清单

### 第 1 周：止血 + 门面（不做大重构）

- [ ] 冻结新功能（只修 P0），发一条“本周只做一致性”的公告。
- [ ] 新增 `README.md`：两仓库、架构图、5 分钟启动、当前状态、下一步读什么。
- [ ] 新增 `GLOSSARY.md`：按 §4 的表；明确禁用词。
- [ ] 新增 `ARCHITECTURE.md`：按 §6；写清“HTTP 生产 / 本地开发”两种模式。
- [ ] 新增 `AGENTS.md`：定义“完成”必须包含代码、迁移、测试、文档、术语、feature registry。
- [ ] 把 `HANDOFF.md` 移到 `docs/history/`，根目录留一行链接。
- [ ] 修 `synthesize_garden` 的 HTTP 模式（一行）+ 补测试。
- [ ] 修 `_knowledge_custom_root()` 的 URL-only 503 + 补 smoke。
- [ ] 跑 `fork_delta.py --write`，把 `--check` 接进 CI。
- [ ] 把 `config.yaml` 的 key 视为已暴露，必要时轮换。

**验收**：新人在 Windows 上只看 README + SETUP，能在 10 分钟内启动 API、跑通一条 capture。

### 第 2-3 周：单写入口

- [ ] Runtime dashboard 的 Knowledge 写操作全部走 `KnowledgeClient`。
- [ ] `HERMES_KNOWLEDGE_LOCAL_ROOT` 标记为 dev-only；生产启动日志打印模式。
- [ ] 删除/隔离 `web_server.py` 里的直接 SQL 写路径；读路径逐步迁移。
- [ ] 文件夹重命名统一到 `rename_topic()`：frontmatter + `capture_metadata` + `garden_cards.source_theme` 一次完成。
- [ ] 写一个 `repair_topics.py`：扫描 frontmatter，修复 metadata 镜像和卡片 topic；先 dry-run。
- [ ] 加契约测试：同一操作分别走 Service / Runtime，DB 和文件结果必须一致。

**验收**：只设置 `HERMES_KNOWLEDGE_URL` 也能跑完整 dashboard；文件夹重命名后筛选、卡片、RAG 三处都一致。

### 第 4 周：存储收口

- [ ] 决定 `captures.embedding`：删掉，或补写入方 + 版本号；不能继续“无写入方但 fallback 会读”。
- [ ] 决定 `captures.review_*`：删掉，或明确它是 capture 级汇总，`review_items` 是唯一调度源。
- [ ] `garden_cards`：`CREATE TABLE` 补齐真实列；默认 schema 改 v2；v1 列只读兼容或删除。
- [ ] `SCHEMA_VERSION` 从 1 升到真实版本，并写迁移说明。
- [ ] 修复 `theme_candidates` 自动化清单（要么建表，要么改清单）。
- [ ] 修复根目录 `_cron_*` 或删除。

**验收**：`docs/CURRENT-STATE.md` 自动生成的表数/列数与 `db/schema.py` 一致；没有“没有写入方的列”。

### 第 2 个月：文档生命周期

- [ ] 所有 plans/specs 移到 `docs/history/`，每份加 `status / supersedes / last_verified`。
- [ ] `docs/README.md` 只列 8-12 份“当前文档”。
- [ ] 文档 CI：
  - 当前文档禁止 `/Users/...` 绝对路径；
  - 链接检查；
  - 测试数/表数/工具数从脚本生成；
  - 术语表禁用词扫描；
  - 每份 plan 必须有 status。
- [ ] 删除 `docs/checkpoints` 的可执行旧代码，只留 commit 指针。
- [ ] 合并 `HERMES_IMPROVEMENT_REPORT` 与 `EXECUTION-PLAN-V3.3` 的重叠部分。

### 第 3 个月：验收与防复发

- [ ] 让一个没参与过项目的人/Agent 按 README 从零跑通 capture → card → RAG，记录卡点。
- [ ] 每周生成一次“架构健康报告”：
  - 过期文档数；
  - 断链数；
  - 每个概念的名字数；
  - 每个对象的写路径数；
  - 无写入方/无读取方的列；
  - HTTP-only smoke 是否通过。
- [ ] 把“新增功能”模板化：feature registry + owner + ADR + docs + tests，缺一不合并。

---

## 8. 防复发的 6 条硬规则

1. **没有术语表，不准加新词。** PR 里出现新名词，必须同时更新 `GLOSSARY.md`。
2. **没有迁移计划，不准改表名/目录名。** 先写兼容层，再迁移，再删旧名。
3. **没有 owner 的文档，不算当前文档。** 当前文档必须有 `owner`、`last_verified`、`verified_against`。
4. **没有 HTTP 契约测试，不准加 dashboard Knowledge 路由。**
5. **没有写入方的列，不准留在 schema 里。** 要么删，要么补 writer + invariant test。
6. **没有跑 `fork_delta --check` 和文档检查，不准合并 runtime 变更。**

---

## 9. 附录：本次审计的硬数字

| 指标 | 数值 |
|---|---|
| `hermes-custom` Markdown 文件 | 90 |
| plans / specs / ADR | 50 / 10 / 5 |
| `/Users/pipi` 绝对路径引用 | 1,144 次，48 个文件 |
| 测试收集 | 1,287 |
| 全量测试 | 1,287 collected，exit 0（含平台相关 skip） |
| SQLite 业务表 | 24（含 1 张 FTS 虚拟表，不含 5 张 FTS 影子表） |
| API router | 8 |
| API 路由装饰器 | 51 |
| 当前 captures / cards / edges | 6 / 9 / 32 |
| vault 目录 | `captures/`、`_archive/raw/`、`.hermes/`（无 `raw/`、`themes/`、`garden/`） |
| `capture_metadata.folder_path` 与 frontmatter 不一致 | 6/6 |
| `garden_cards.source_theme` 与来源 capture 不一致 | 1/9 |
| fork 文档声称 | 100 files，web_server +3,477 |
| fork 实际 | 142 个 upstream-original 文件被改 + 105 个 fork-only 新增；`web_server.py` +4,121 |
| `fork_delta.py --check` | exit 1（stale） |

### 复现命令

```bash
# 测试
cd hermes-custom
.venv/Scripts/python.exe -m pytest tests --collect-only   # 1287 tests collected
.venv/Scripts/python.exe -m pytest tests -q               # exit 0

# fork 治理
cd hermes-agent-main
.venv/Scripts/python.exe scripts/fork_delta.py --check    # exit 1

# 数据一致性（只读）
# 见本报告 §2.4 的 SQL / frontmatter 对比脚本
```

---
status: current
owner: knowledge-service
last_verified: 2026-11-18
verified_against: "3e6b974 (hermes-custom) + 2026-11-18 并行修复分支"
---

# Hermes Knowledge

Hermes Knowledge 是 Hermes 的**知识库能力层**：把微信、飞书、网页、文件里的材料，变成可确认的草稿、可检索的资料笔记、可复用的知识卡片，以及可追溯的来源。

它不是一个独立的聊天机器人。用户始终从 Hermes Runtime 进入；Runtime 负责消息、工具注册和 Dashboard，Knowledge 负责知识库业务和存储。

## 两个仓库，一个入口

| 仓库 | 作用 |
|---|---|
| `hermes-custom`（本仓库） | Capture、Library / Topic、Reading、RAG、Theme、Knowledge Card、Review、Canvas、Research、Librarian、Answer、Knowledge Graph、Folder write jobs、Run ledger、Knowledge API、SQLite / Knowledge File 存储 |
| `hermes-agent-main` | 微信 / 飞书网关、工具注册、主 Agent、用户侧原生 Dashboard（默认 `127.0.0.1:9119`） |
| `hermes-life`（可选） | Life Assistant；不在本仓库，未配置时 Life 工具会返回明确的错误信封 |

```text
微信 / 飞书 / Web
        |
        v
Hermes Runtime（gateway / tools / dashboard）
        |
        | HTTP（生产推荐）
        v
Hermes Knowledge Service（FastAPI，默认 127.0.0.1:8000）
        |
        +-- Capture / Library & Topic / Reading / RAG / Theme / Card / Review
        +-- Canvas / Research / Librarian / Answer / Knowledge Graph
        +-- Folder write jobs / Run ledger
        +-- SQLite：卡片、主题、复习、运行记录
        +-- Knowledge Files：captures/*.md、_archive/raw/*.md
        +-- 专题注册表：<vault>/.hermes/knowledge_folders.json
```

Research 是独立研究子系统：入口为 `bridge/research_bridge.py`，实现代码在 `agents/research/`（planner / synthesis / evidence / orchestration，约 8800 行；基线 `3e6b974` 仍位于 `bridge/research/`，迁移在并行分支进行）。

## 当前状态

- Knowledge 全量测试：收集数见 `docs/CURRENT-STATE.md`；维护者环境最近一次全量运行 exit 0。
- 推荐部署：Runtime 通过 HTTP 调 Knowledge Service。
- 本地 checkout 模式（`HERMES_KNOWLEDGE_LOCAL_ROOT`）只用于开发和离线调试。
- 用户侧只有 Hermes Runtime 原生 Dashboard（`127.0.0.1:9119`）。本仓库旧的 Next.js 自定义 Dashboard 已删除，不再提供 `npm run dev` 入口。
- 当前仍有一部分 Runtime Dashboard 读路由保留本地回退；见 `ARCHITECTURE.md` 的“当前拆分状态”。

## 5 分钟启动（Windows）

### 1. Knowledge Service

```powershell
cd <repos>\hermes-custom
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy config.example.yaml config.yaml   # 仅首次；已有 config.yaml 不要覆盖
# 编辑 config.yaml：填 LLM / DashScope key、vault、database 路径
.venv\Scripts\python.exe -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
```

探活：`GET http://127.0.0.1:8000/api/health`。

### 2. Hermes Runtime

首次运行先装依赖（Runtime 不在本仓库，需要单独 checkout）：

```powershell
cd <repos>\hermes-agent-main
uv sync                      # 或：py -3.12 -m venv .venv; .venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m hermes_cli.main dashboard --no-open
# 打开 http://127.0.0.1:9119
```

HTTP 模式是推荐部署方式，要在**启动 Dashboard 之前**设置：

```text
HERMES_KNOWLEDGE_URL=http://127.0.0.1:8000
HERMES_KNOWLEDGE_TOKEN=<与服务的 HERMES_API_TOKEN 相同的高强度随机值>
```

服务侧设置 `HERMES_API_TOKEN=<同一个值>`；改完 token 要重启服务。两边值必须一致。

还需要告诉 Runtime 服务端的 vault / DB 位置（HTTP 模式下这两个变量不是可选的，
Dashboard 的草稿、卡片详情、时间线读路由仍会用到）：

```text
HERMES_KNOWLEDGE_VAULT=<repos>\hermes-custom\data\vault
HERMES_KNOWLEDGE_DB=<repos>\hermes-custom\data\captures.db
```

### 3. 本地开发模式（可选）

不启动 HTTP 服务，让 Runtime 直接 import 本仓库：

```text
HERMES_KNOWLEDGE_LOCAL_ROOT=<repos>\hermes-custom
# 不要同时设置 HERMES_KNOWLEDGE_URL
```

这是开发回退路径，不是生产推荐路径。

## 第一条 capture → card → RAG

服务起来后（HTTP 模式需带 `Authorization: Bearer <token>`）：

```bash
# 1. 建 capture 草稿（content 换成你的材料）
curl -X POST http://127.0.0.1:8000/api/capture-drafts \
  -H "Authorization: Bearer $HERMES_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"MCP 把工具发现与调用契约标准化。","source_type":"text"}'

# 2. 生成确认 token，再用它提交
curl -X POST http://127.0.0.1:8000/api/capture-drafts/<draft_id>/confirmation \
  -H "Authorization: Bearer $HERMES_API_TOKEN"
curl -X POST http://127.0.0.1:8000/api/capture-drafts/<draft_id>/commit \
  -H "Authorization: Bearer $HERMES_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"confirmation_token":"<token>"}'

# 3. 从资料生成卡片（auto_commit=true，直接落库）
curl -X POST http://127.0.0.1:8000/api/garden/drafts \
  -H "Authorization: Bearer $HERMES_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"capture_ids":["<capture_id>"]}'

# 4. RAG 检索（应能命中刚才的资料）
curl "http://127.0.0.1:8000/api/knowledge/search?q=MCP" \
  -H "Authorization: Bearer $HERMES_API_TOKEN"
```

不想用 curl 就在 Dashboard 里操作：资料伴读 → 新建/提交 → 知识卡片 → 专题输出。

## 测试

```powershell
# 全量
cd <repos>\hermes-custom
.venv\Scripts\python.exe -m pytest tests -q

# HTTP-only smoke（临时 DB/vault；需要同级 hermes-agent-main；建议指定 work-dir）
.venv\Scripts\python.exe scripts\http_only_smoke.py --work-dir .tmp\hermes-http-only

# 跨进程 pipeline（会启动真实 API；外部 LLM 用本地假服务替代）
# 需要同级 hermes-agent-main checkout；不在同级时设置 HERMES_RUNTIME_ROOT
$env:HERMES_RUNTIME_ROOT = "<repos>\hermes-agent-main"
.venv\Scripts\python.exe scripts\knowledge_pipeline_e2e.py --work-dir .tmp\hermes-pipeline-e2e

# Runtime 侧 Knowledge 相关
cd <repos>\hermes-agent-main
.venv\Scripts\python.exe -m pytest tests\tools tests\hermes_cli\test_web_server.py -q
```

## 文档入口

| 文档 | 用途 |
|---|---|
| `README.md` | 本页：是什么、怎么启动 |
| `ARCHITECTURE.md` | 当前架构、数据所有权、写路径规则 |
| `GLOSSARY.md` | 中英术语、代码名、历史名 |
| `RUNBOOK.md` | 启停、排障、重置、修复 |
| `AGENTS.md` | 给 AI Agent / 开发者的硬规则 |
| `CONTEXT.md` | 产品工作流与领域语言 |
| `docs/adr/` | 已确认的架构决策 |
| `docs/PRODUCT-REFACTOR-STORY.md` | 产品叙事与面试口径 |
| `docs/CODE-REVIEW-V3.md` | 2026-11-16 的代码评审快照 |
| `docs/CURRENT-STATE.md` | 自动生成的测试数/表数/commit |
| `docs/README.md` | 文档索引：Current / Historical |
| `docs/history/` | 历史交接、旧计划、旧设计；**不要当运行手册** |

历史 `HANDOFF.md` 已移到 `docs/history/HANDOFF-2026-05.md`。根目录的 `HANDOFF.md` 只是指向本页的占位。

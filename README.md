---
status: current
owner: knowledge-service
last_verified: 2026-11-23
verified_against: "9e5c752 (hermes-custom) / 115bd27 (hermes-agent-main)"
---

# Hermes Knowledge

> **快速上手（3 步一键启动）**：请先看 **[USER-GUIDE.md](USER-GUIDE.md)** —— 只需克隆本仓库、填一份 `.env`、运行 `.\start.ps1`（Windows）或 `./start.sh`（macOS/Linux），脚本会自动拉取配套网页运行时并一键启动。

Hermes Knowledge 是 Hermes 的**知识库能力层**：把微信、飞书、网页、文件里的材料，变成可确认的草稿、可检索的资料笔记、可复用的知识卡片，以及可追溯的来源。配套运行时基于 Nous Research 开源的 Hermes Agent（MIT License）定制。

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

Research 是独立研究子系统：入口为 `bridge/research_bridge.py`，实现代码在 `agents/research/`（planner / synthesis / evidence / orchestration，约 8800 行）。`bridge/research/` 只保留兼容薄壳，新研究逻辑只写 `agents/research/`。

## 当前状态

- Knowledge 全量测试：收集数见 `docs/CURRENT-STATE.md`；维护者环境最近一次全量运行 exit 0。
- 推荐部署：Runtime 通过 HTTP 调 Knowledge Service。
- 本地 checkout 模式（`HERMES_KNOWLEDGE_LOCAL_ROOT`）只用于开发和离线调试。
- 用户侧只有 Hermes Runtime 原生 Dashboard（`127.0.0.1:9119`）。本仓库旧的 Next.js 自定义 Dashboard 已删除，不再提供 `npm run dev` 入口。
- 资料伴读右栏「伴读助手」的回答是流式输出：服务端在合成时推送 `answer_delta`，Runtime 通过 `/api/knowledge/library/items/{kind}/{item_id}/selection-chat/stream` 转发，长回答不必等到最后一次性出现。
- 联网与研究双轨分流：微信端按日常快答 / 资料检索 / DeepSeek 托管极速联网三档分流；Web 专题工作台走瘦身研究管线，保留完整 `[K]`/`[W]` 证据追踪与可归档成稿。
- 知识卡片采用低认知负担的三要素自然人话结构（短标题 + 一句话结论 + 易扫读 Markdown 要点正文），支持草稿与已存卡片统一全文查看、内联编辑及多卡选题篮送入「专题输出」。已存卡片用 `GET/PATCH /api/garden/cards/{id}` 查看和编辑。草稿用 `PATCH /api/garden/drafts/{id}` 直接编辑，用 `POST /api/garden/drafts/{id}/revise` 让 AI 按指令改写。
- Web Dashboard 已统一为去 AI 化的编辑部纸白视觉体系（`资料伴读` / `知识卡片` / `专题输出`），并在 Runtime 侧通过连接池与异步线程卸载优化了高频读接口响应。
- 当前仍有一部分 Runtime Dashboard 读路由保留本地回退；见 `ARCHITECTURE.md` 的“当前拆分状态”。

## 5 分钟启动（Windows）

### 1. Knowledge Service

把示例中的 `<repos>` 换成两个仓库的共同父目录。以下命令使用 PowerShell。

```powershell
cd "<repos>\hermes-custom"
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path config.yaml)) { Copy-Item config.example.yaml config.yaml }
# 编辑 config.yaml：填 LLM / DashScope key、vault、database 路径
$env:HERMES_API_TOKEN = "<替换为高强度随机值>"
.venv\Scripts\python.exe -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
```

在另一个终端探活时，请带上同一个 Bearer 令牌。查看响应里的 `status`，正常为 `ok`。完整命令见 [运行手册](RUNBOOK.md)。

### 2. Hermes Runtime

Runtime 在独立仓库。首次从源码构建 Dashboard 需要 PATH 上的 Node.js 和 npm，已有前端构建产物时可用 `--skip-build` 启动。在另一个 PowerShell 窗口安装依赖，再设置 HTTP 地址和令牌，最后启动 Dashboard。

```powershell
cd "<repos>\hermes-agent-main"
uv sync --extra web          # 或：py -3.12 -m venv .venv; .venv\Scripts\python.exe -m pip install -e ".[web]"
$env:HERMES_KNOWLEDGE_URL = "http://127.0.0.1:8000"
$env:HERMES_KNOWLEDGE_TOKEN = "<替换为与服务 HERMES_API_TOKEN 相同的值>"
.venv\Scripts\python.exe -m hermes_cli.main dashboard --no-open
# 打开 http://127.0.0.1:9119
```

修改服务令牌后，更新 Runtime 的令牌并重启对应进程。

分批升级时，新 Runtime 可从旧服务预览知识卡片草稿，但通过 Dashboard 保存卡片要求 Service 契约 v2。请先升级 Service，再升级 Runtime。

Dashboard 的草稿、卡片详情、时间线和伴读流式转发走 HTTP。使用「打开本地文件（`open-local`）」时，Runtime 需要能访问服务实际使用的 SQLite 和 Vault。按 `config.yaml` 的 `database.path` 和 `vault.path` 填写，以下示例对应 `config.example.yaml` 的默认值。

```powershell
$env:HERMES_KNOWLEDGE_VAULT = "$env:USERPROFILE\Hermes\knowledge"
$env:HERMES_KNOWLEDGE_DB = "<repos>\hermes-custom\data\captures.db"
```

### 3. 本地开发模式（可选）

不启动 HTTP 服务，让 Runtime 直接 import 本仓库：

```text
HERMES_KNOWLEDGE_LOCAL_ROOT=<repos>\hermes-custom
# 不要同时设置 HERMES_KNOWLEDGE_URL
```

这是开发回退路径，不是生产推荐路径。

## 第一条 capture → card → RAG

以下示例使用 Bash。先在当前终端设置与服务一致的 `HERMES_API_TOKEN`，并在每一步用上一步返回的实际 ID 或确认令牌替换占位值。

```bash
# 1. 建 capture 草稿（content 换成你的材料）
curl -X POST http://127.0.0.1:8000/api/capture-drafts \
  -H "Authorization: Bearer $HERMES_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"MCP 把工具发现与调用契约标准化。","source_type":"text"}'

# 2. 生成确认 token，再用它提交
curl -X POST "http://127.0.0.1:8000/api/capture-drafts/<draft_id>/confirmation" \
  -H "Authorization: Bearer $HERMES_API_TOKEN"
curl -X POST "http://127.0.0.1:8000/api/capture-drafts/<draft_id>/commit" \
  -H "Authorization: Bearer $HERMES_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"confirmation_token":"<token>"}'

# 3. 从资料生成卡片草稿（默认不落库）
curl -X POST http://127.0.0.1:8000/api/garden/drafts \
  -H "Authorization: Bearer $HERMES_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"capture_ids":["<capture_id>"]}'

# 4. 确认卡片草稿后再提交
curl -X POST "http://127.0.0.1:8000/api/garden/drafts/<draft_id>/confirmation" \
  -H "Authorization: Bearer $HERMES_API_TOKEN"
curl -X POST "http://127.0.0.1:8000/api/garden/drafts/<draft_id>/commit" \
  -H "Authorization: Bearer $HERMES_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"confirmation_token":"<token>"}'

# 5. RAG 检索（应能命中刚才的资料）
curl "http://127.0.0.1:8000/api/knowledge/search?q=MCP" \
  -H "Authorization: Bearer $HERMES_API_TOKEN"
```

不想用 curl 就在 Dashboard 里操作：资料伴读 → 新建/提交 → 知识卡片 → 专题输出。

## 测试

```powershell
# 全量
cd "<repos>\hermes-custom"
.venv\Scripts\python.exe -m pytest tests -q

# HTTP-only smoke（临时 DB/vault；需要同级 hermes-agent-main；建议指定 work-dir）
.venv\Scripts\python.exe scripts\http_only_smoke.py --work-dir .tmp\hermes-http-only

# 跨进程 pipeline（会启动真实 API；外部 LLM 用本地假服务替代）
# 需要同级 hermes-agent-main checkout；不在同级时设置 HERMES_RUNTIME_ROOT
$env:HERMES_RUNTIME_ROOT = "<repos>\hermes-agent-main"
.venv\Scripts\python.exe scripts\knowledge_pipeline_e2e.py --work-dir .tmp\hermes-pipeline-e2e

# 架构清单（--strict 需要 Runtime checkout；缺失会失败而不是跳过）
.venv\Scripts\python.exe scripts\architecture_inventory.py --check --strict

# Runtime 侧 Knowledge 相关
cd "<repos>\hermes-agent-main"
.venv\Scripts\python.exe -m pytest tests\tools tests\hermes_cli\test_web_server.py -q
```

## 文档入口

| 文档 | 用途 |
|---|---|
| `USER-GUIDE.md` | 面向用户的 3 步一键安装与日常使用指南 |
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

## License

本项目采用 [MIT License](LICENSE) 开源（Copyright (c) 2026 pipi-HST）。
配套运行时仓库 `hermes-agent-main` 基于 [Nous Research / Hermes Agent](https://github.com/NousResearch/hermes-agent)（MIT License）定制，已完整保留原协议与版权声明。

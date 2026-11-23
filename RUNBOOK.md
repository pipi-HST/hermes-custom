---
status: current
owner: knowledge-service
last_verified: 2026-11-23
verified_against: "9e5c752 (hermes-custom) / 115bd27 (hermes-agent-main)"
---

# Hermes Knowledge Runbook

> 状态：current  
> 命令示例使用 Windows PowerShell。macOS/Linux 使用 `.venv/bin/python`、正斜杠路径和对应 shell 的环境变量语法。
> 路径约定：`<repos>` 是同时包含 `hermes-custom` 和 `hermes-agent-main` 的目录。

## 1. 端口与进程

| 服务 | 默认地址 | 启动方式 |
|---|---|---|
| Knowledge API | `http://127.0.0.1:8000` | `uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000` |
| Hermes Dashboard | `http://127.0.0.1:9119` | `.venv\Scripts\python.exe -m hermes_cli.main dashboard` |
| Hermes Gateway | 由平台适配器决定 | `.venv\Scripts\python.exe -m hermes_cli.main gateway run` |

本仓库旧的 `dashboard/`（Next.js，端口 3000）已删除，不再提供 `npm run dev`。用户侧 Dashboard 只用 Hermes Runtime 原生 Dashboard（9119）。

## 2. 启动 Knowledge API

```powershell
cd "<repos>\hermes-custom"
# 推荐在启动前设置令牌
$env:HERMES_API_TOKEN = "<替换为高强度随机值>"
.venv\Scripts\python.exe -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
```

在另一个 PowerShell 窗口为 `$env:HERMES_API_TOKEN` 设置相同的值，再执行：

```powershell
curl.exe --fail-with-body -H "Authorization: Bearer $env:HERMES_API_TOKEN" "http://127.0.0.1:8000/api/health"
```

响应里的 `status` 应为 `ok`。服务在依赖异常时可能返回 HTTP 200 和 `status: degraded`，此时查看响应中的 `checks`。

未配置 `HERMES_API_TOKEN` 时，只有回环请求可访问服务，此时探活可以省略 Authorization 请求头。配置令牌后，可用 `HERMES_HEALTH_PUBLIC=1` 允许无凭证的 `GET /api/health`。

远程访问需要配置高强度随机令牌。通过自定义域名访问时，还要在服务侧设置 `HERMES_API_ALLOWED_HOSTS`，列出允许的主机名，用逗号分隔，省略协议和端口。

## 3. 启动 Runtime

先按下一节设置 Knowledge HTTP 环境变量，再启动 Gateway 或 Dashboard。首次构建 Dashboard 需要 Node.js 和 npm。

```powershell
cd "<repos>\hermes-agent-main"
uv run hermes gateway run
# 或
.venv\Scripts\python.exe -m hermes_cli.main gateway run --replace
```

Dashboard：

```powershell
.venv\Scripts\python.exe -m hermes_cli.main dashboard --no-open
# http://127.0.0.1:9119
```

## 4. HTTP 模式（生产推荐）

在启动 Runtime 的 PowerShell 窗口设置：

```powershell
$env:HERMES_KNOWLEDGE_URL = "http://127.0.0.1:8000"
$env:HERMES_KNOWLEDGE_TOKEN = "<替换为与服务 HERMES_API_TOKEN 相同的值>"
```

纯 HTTP 模式只需要上述两个变量（工具调用与 Dashboard 的草稿、卡片详情、时间线、伴读流式转发均已走 HTTP 委托，不需要本地 Knowledge checkout）。

Dashboard「打开本地文件（`open-local`）」需要 Runtime 能访问服务实际使用的 SQLite 和 Vault。以下示例对应 `config.example.yaml`，使用不同路径时以服务配置为准。

```powershell
$env:HERMES_KNOWLEDGE_DB = "<repos>\hermes-custom\data\captures.db"
$env:HERMES_KNOWLEDGE_VAULT = "$env:USERPROFILE\Hermes\knowledge"
```

服务启用令牌后，Runtime 缺少令牌或令牌不匹配都会导致上游鉴权失败。仅在回环开发模式且服务未配置令牌时，Runtime 才可省略令牌。

如果预览知识卡片草稿正常、保存提示契约不兼容，检查 Service `/api/health` 的 `contract_version`。Dashboard 保存要求 v2 或以上；未升级的旧服务仍可用于预览。

Dashboard 保存资料笔记要求 Service 契约 v3 或以上，升级时先更新 Knowledge Service。
如果提示笔记已在别处修改，当前编辑会留在页面里；可在另一个标签页打开最新版，
在新页面合并后再保存，不要直接刷新有未保存内容的页面。

如果专题改名中途失败，修复报错后用相同的旧名和新名重试。不要手工清理 `rename_folder_journal` 记录：它记录了待补齐索引的资料，以及数据库是否已经提交。启用队列时，重启 worker 会自动接手上一进程未完成的作业。

## 5. 本地开发模式

```text
HERMES_KNOWLEDGE_LOCAL_ROOT=<repos>/hermes-custom
# 不要设置 HERMES_KNOWLEDGE_URL
```

这是 dev-only。启动日志会打印：

```text
Hermes Knowledge transport: local
Hermes Knowledge local-checkout mode is for development only
```

生产环境不要使用这个模式。

## 6. 测试

```powershell
cd "<repos>\hermes-custom"
.venv\Scripts\python.exe -m pytest tests -q
```

HTTP-only smoke（真实 API + Runtime 工具，不需要本地 Knowledge checkout；需要同级 `hermes-agent-main`）：

```powershell
cd "<repos>\hermes-custom"
.venv\Scripts\python.exe scripts\http_only_smoke.py --work-dir .tmp\hermes-http-only
.venv\Scripts\python.exe scripts\check_docs.py
.venv\Scripts\python.exe scripts\update_current_state.py --check
.venv\Scripts\python.exe scripts\architecture_inventory.py --check --strict
```

`--strict` 需要同级 `hermes-agent-main`（或 `HERMES_RUNTIME_ROOT`）；缺失时直接失败，不跳过。

跨进程 pipeline（需要同级 `hermes-agent-main`；否则设置 `HERMES_RUNTIME_ROOT`）：

```powershell
.venv\Scripts\python.exe scripts\knowledge_pipeline_e2e.py --work-dir .tmp\hermes-pipeline-e2e
```

真实浏览器验收：默认使用本地假模型、临时 Vault/SQLite，Runtime 只通过本次启动的 HTTP Service 访问资料库。Windows 默认使用已安装的 Chrome；Linux CI 使用 Playwright 安装的 Chromium。

```powershell
.venv\Scripts\python.exe scripts\knowledge_product_e2e.py --work-dir .tmp\hermes-product-e2e
```

Runtime 侧 Knowledge 测试：

```powershell
cd "<repos>\hermes-agent-main"
.venv\Scripts\python.exe -m pytest tests/tools tests/hermes_cli/test_web_server.py -q
```

## 7. 专题修复

先 dry-run：

```powershell
cd "<repos>\hermes-custom"
.venv\Scripts\python.exe scripts\repair_topics.py --config config.yaml
```

确认输出后再应用：

```powershell
.venv\Scripts\python.exe scripts\repair_topics.py --config config.yaml --apply
```

如果卡片的 `source_theme` 是非 Inbox 的旧值，并且你确认要以来源资料为准：

```powershell
.venv\Scripts\python.exe scripts\repair_topics.py --config config.yaml --apply --fix-mismatched
```

脚本只改：

- `capture_metadata.folder_path`
- `garden_cards.source_theme`

不会改 Capture Markdown 正文。

## 8. 本地数据重置

先读 `docs/local-data-reset-runbook.md`。原则：

- 保留 `config.yaml`、API key、模型配置；
- 保留独立 Life 数据库（如果存在；Life 是可选项目）；
- 删除 Knowledge 内容前先 dry-run；
- 先停 watcher / cron，再 apply。

`scripts/reset_hermes_local_data.py` 的 `--new-storage-root` 默认从
`config.yaml` 的 `vault.path` 读取；换过 vault 路径的机器不需要再手传参数。
dry-run 输出的 `new_storage_root` 必须和 `vault.path` 一致再 apply。

## 8.1 批量文件夹重命名（opt-in）

`PATCH /api/library/folders/{folder_path}` 默认同步改名。设置 `worker.enabled: true` 后，同一个端点会提交 `rename_folder` 作业并等待 worker。先按 [Folder write jobs](docs/folder-write-jobs.md) 启动 worker，避免对仍有待处理作业的同一文件夹再次发起改名。

## 9. 常见问题

### 9.1 Dashboard 知识页 503

检查：

1. `HERMES_KNOWLEDGE_URL` 是否设置；
2. Runtime 的 `HERMES_KNOWLEDGE_TOKEN` 是否等于服务的 `HERMES_API_TOKEN`；
3. 服务是否监听在 `127.0.0.1:8000`；
4. 按第 2 节直接请求 Service 的 `GET /api/health`，确认响应中有 `status: ok`。`/api/knowledge/runtime-check` 在 HTTP 模式下只报告 Runtime 选择的传输方式。

### 9.2 工具返回 “Hermes Knowledge is not configured”

- HTTP 模式：确认工具走的是 `call_custom_bridge`，不是直接 import 本地模块；
- 本地模式：确认 `HERMES_KNOWLEDGE_LOCAL_ROOT` 指向存在的 checkout；
- 如果只配了 URL，`synthesize_garden` 应走 HTTP；如果仍报错，升级 Runtime 到包含本次修复的版本。

### 9.3 专题筛选/卡片所属专题不对

```powershell
.venv\Scripts\python.exe scripts\repair_topics.py --config config.yaml --apply
```

然后重新打开 Library / Cards Board。

### 9.4 Git 标签显示“当前目录不是 Git 仓库”

当前会话目录不是 Git 仓库。把会话切到 `hermes-custom` 或 `hermes-agent-main`，或在 Git 标签里选择具体仓库。不要在 `chats/...` 缓存目录里初始化 Git。

### 9.5 RAG 结果使用了旧向量

- 唯一索引是 `capture_embeddings`；
- `captures.embedding` 已在 schema v4 删除；
- 重建索引：

```powershell
.venv\Scripts\python.exe scripts\backfill.py --config config.yaml
# 只查看：.venv\Scripts\python.exe scripts\backfill.py --config config.yaml --dry-run
```

### 9.6 资料伴读生成 AI 建议返回 502 `timed out`

`POST /api/tools/capture_bridge/create_editable_capture_draft`（资料伴读 → 生成 AI 建议，`organize=true`）
会同步调用 LLM；约 7k 字的长文实测 30–60 秒，超过 Runtime 默认的 30 秒客户端超时，界面显示
`502 ... failed: timed out`。服务端通常仍会完成并留下草稿，刷新即可看到。

Runtime 侧调大超时后重启 Dashboard：

```text
HERMES_KNOWLEDGE_TIMEOUT=180
```

（与 `HERMES_KNOWLEDGE_URL` 放在同一处，例如 `~/.hermes/.env`。）

## 10. 备份

至少备份：

- `data/captures.db`（结构化卡片、主题、复习、运行记录）；
- `config.yaml` 里 `vault.path` 指向的目录（Capture Note / Raw Evidence；默认
  `data/vault/`，换过路径的机器以配置为准）；
- `config.yaml`（本地敏感配置，单独保管）。

Markdown 能重建部分索引，但不能重建卡片和运行记录；数据库必须单独备份。

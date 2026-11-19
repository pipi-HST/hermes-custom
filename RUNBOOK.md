---
status: current
owner: knowledge-service
last_verified: 2026-11-18
verified_against: "3e6b974 (hermes-custom) + 2026-11-18 并行修复分支"
---

# Hermes Knowledge Runbook

> 状态：current  
> 适用：命令示例以 Windows PowerShell 为准；macOS/Linux 把 `.venv\Scripts\python.exe` 换成 `.venv/bin/python`。
> 路径约定：`<repos>` 是同时包含 `hermes-custom` 和 `hermes-agent-main` 的目录。

## 1. 端口与进程

| 服务 | 默认地址 | 启动方式 |
|---|---|---|
| Knowledge API | `http://127.0.0.1:8000` | `uvicorn api.main:create_app --factory` |
| Hermes Dashboard | `http://127.0.0.1:9119` | `.venv\Scripts\python.exe -m hermes_cli.main dashboard` |
| Hermes Gateway | 由平台适配器决定 | `.venv\Scripts\python.exe -m hermes_cli.main gateway run` |

本仓库旧的 `dashboard/`（Next.js，端口 3000）已删除，不再提供 `npm run dev`。用户侧 Dashboard 只用 Hermes Runtime 原生 Dashboard（9119）。

## 2. 启动 Knowledge API

```bash
cd <repos>/hermes-custom
.venv\Scripts\python.exe -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
```

Windows PowerShell：

```powershell
cd <repos>\hermes-custom
.venv\Scripts\python.exe -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
```

探活：

```bash
curl http://127.0.0.1:8000/api/health
```

默认未配置 `HERMES_API_TOKEN` 时只接受回环请求。需要远程访问时，必须设置高强度随机 token：

```text
HERMES_API_TOKEN=<random>
```

监控探活可以设置 `HERMES_HEALTH_PUBLIC=1`，只放行 `GET/HEAD /api/health`。

## 3. 启动 Runtime

```bash
cd <repos>/hermes-agent-main
uv run hermes gateway run
# 或
.venv\Scripts\python.exe -m hermes_cli.main gateway run --replace
```

Dashboard：

```bash
.venv\Scripts\python.exe -m hermes_cli.main dashboard --no-open
# http://127.0.0.1:9119
```

## 4. HTTP 模式（生产推荐）

Runtime 侧：

```text
HERMES_KNOWLEDGE_URL=http://127.0.0.1:8000
HERMES_KNOWLEDGE_TOKEN=<same random value as HERMES_API_TOKEN>
HERMES_KNOWLEDGE_DB=<repos>/hermes-custom/data/captures.db
HERMES_KNOWLEDGE_VAULT=<repos>/hermes-custom/data/vault
```

HTTP 模式跑工具不需要本地 Knowledge checkout；但 Dashboard 的少数读路由
（草稿列表、卡片详情、时间线）仍会直读 `HERMES_KNOWLEDGE_DB` /
`HERMES_KNOWLEDGE_VAULT`。这是已知例外，不是推荐生产形态；纯远程部署前必须让
这两个路径与服务端一致，否则这些页面会 404。

Knowledge 侧：

```text
HERMES_API_TOKEN=<same random value>
```

**两边 token 必须相同。** 一个常见的 503 原因是只设置了 `HERMES_KNOWLEDGE_URL`，没有设置 `HERMES_KNOWLEDGE_TOKEN`，或者服务端没有设置 `HERMES_API_TOKEN`。

如果预览知识卡片草稿正常、保存提示契约不兼容，检查 Service `/api/health` 的 `contract_version`。Dashboard 保存要求 v2 或以上；未升级的旧服务仍可用于预览。

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

```bash
cd <repos>/hermes-custom
.venv\Scripts\python.exe -m pytest tests -q
```

Windows：

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

HTTP-only smoke（真实 API + Runtime 工具，不需要本地 Knowledge checkout；需要同级 `hermes-agent-main`）：

```bash
cd <repos>/hermes-custom
.venv\Scripts\python.exe scripts\http_only_smoke.py --work-dir .tmp\hermes-http-only
.venv\Scripts\python.exe scripts\check_docs.py
.venv\Scripts\python.exe scripts\update_current_state.py --check
.venv\Scripts\python.exe scripts\architecture_inventory.py --check --strict
```

`--strict` 需要同级 `hermes-agent-main`（或 `HERMES_RUNTIME_ROOT`）；缺失时直接失败，不跳过。

跨进程 pipeline（需要同级 `hermes-agent-main`；否则设置 `HERMES_RUNTIME_ROOT`）：

```bash
.venv\Scripts\python.exe scripts\knowledge_pipeline_e2e.py --work-dir .tmp\hermes-pipeline-e2e
```

Windows 路径示例：

```powershell
.venv\Scripts\python.exe scripts\knowledge_pipeline_e2e.py --work-dir .tmp\hermes-pipeline-e2e
```

真实浏览器验收：默认使用本地假模型、临时 Vault/SQLite，Runtime 只通过本次启动的 HTTP Service 访问资料库。Windows 默认使用已安装的 Chrome；Linux CI 使用 Playwright 安装的 Chromium。

```powershell
.venv\Scripts\python.exe scripts\knowledge_product_e2e.py --work-dir .tmp\hermes-product-e2e
```

Runtime 侧 Knowledge 测试：

```bash
cd <repos>/hermes-agent-main
.venv\Scripts\python.exe -m pytest tests/tools tests/hermes_cli/test_web_server.py -q
```

## 7. 专题修复

先 dry-run：

```bash
cd <repos>/hermes-custom
.venv\Scripts\python.exe scripts\repair_topics.py --config config.yaml
```

确认输出后再应用：

```bash
.venv\Scripts\python.exe scripts\repair_topics.py --config config.yaml --apply
```

如果卡片的 `source_theme` 是非 Inbox 的旧值，并且你确认要以来源资料为准：

```bash
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

`docs/folder-write-jobs.md` 的 worker 只处理 `rename_folder`；HTTP
`PATCH /api/library/folders` 仍走同步路径。两者不要同时对同一个文件夹操作。

## 9. 常见问题

### 9.1 Dashboard 知识页 503

检查：

1. `HERMES_KNOWLEDGE_URL` 是否设置；
2. Runtime 的 `HERMES_KNOWLEDGE_TOKEN` 是否等于服务的 `HERMES_API_TOKEN`；
3. 服务是否监听在 `127.0.0.1:8000`；
4. 服务 `/api/health` 是否返回 `ok`。

### 9.2 工具返回 “Hermes Knowledge is not configured”

- HTTP 模式：确认工具走的是 `call_custom_bridge`，不是直接 import 本地模块；
- 本地模式：确认 `HERMES_KNOWLEDGE_LOCAL_ROOT` 指向存在的 checkout；
- 如果只配了 URL，`synthesize_garden` 应走 HTTP；如果仍报错，升级 Runtime 到包含本次修复的版本。

### 9.3 专题筛选/卡片所属专题不对

```bash
.venv\Scripts\python.exe scripts\repair_topics.py --config config.yaml --apply
```

然后重新打开 Library / Cards Board。

### 9.4 Git 标签显示“当前目录不是 Git 仓库”

当前会话目录不是 Git 仓库。把会话切到 `hermes-custom` 或 `hermes-agent-main`，或在 Git 标签里选择具体仓库。不要在 `chats/...` 缓存目录里初始化 Git。

### 9.5 RAG 结果使用了旧向量

- 唯一索引是 `capture_embeddings`；
- `captures.embedding` 已在 schema v4 删除；
- 重建索引：

```bash
.venv\Scripts\python.exe scripts\backfill.py --config config.yaml
# 只查看：.venv\Scripts\python.exe scripts\backfill.py --config config.yaml --dry-run
```

## 10. 备份

至少备份：

- `data/captures.db`（结构化卡片、主题、复习、运行记录）；
- `config.yaml` 里 `vault.path` 指向的目录（Capture Note / Raw Evidence；默认
  `data/vault/`，换过路径的机器以配置为准）；
- `config.yaml`（本地敏感配置，单独保管）。

Markdown 能重建部分索引，但不能重建卡片和运行记录；数据库必须单独备份。

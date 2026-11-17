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

旧的 `hermes-custom/dashboard`（Next.js，端口 3000）已废弃；`npm run dev` 会直接退出。

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
HERMES_KNOWLEDGE_DB=<repos>/hermes-custom/data/captures.db   # 仅 Dashboard 直读兜底需要
HERMES_KNOWLEDGE_VAULT=<vault path>                          # 可选；否则读本地 config.yaml
```

Knowledge 侧：

```text
HERMES_API_TOKEN=<same random value>
```

**两边 token 必须相同。** 一个常见的 503 原因是只设置了 `HERMES_KNOWLEDGE_URL`，没有设置 `HERMES_KNOWLEDGE_TOKEN`，或者服务端没有设置 `HERMES_API_TOKEN`。

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
```

跨进程 pipeline（需要同级 `hermes-agent-main`；否则设置 `HERMES_RUNTIME_ROOT`）：

```bash
.venv\Scripts\python.exe scripts\knowledge_pipeline_e2e.py --work-dir .tmp\hermes-pipeline-e2e
```

Windows 路径示例：

```powershell
.venv\Scripts\python.exe scripts\knowledge_pipeline_e2e.py --work-dir .tmp\hermes-pipeline-e2e
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
- 保留独立 Life 数据库；
- 删除 Knowledge 内容前先 dry-run；
- 先停 watcher / cron，再 apply。

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

- 主索引是 `capture_embeddings`；
- `captures.embedding` 是旧兼容列；
- 重建索引：

```bash
.venv\Scripts\python.exe scripts\backfill.py --config config.yaml
# 只查看：.venv\Scripts\python.exe scripts\backfill.py --config config.yaml --dry-run
```

## 10. 备份

至少备份：

- `data/captures.db`（结构化卡片、主题、复习、运行记录）；
- `data/vault/`（Capture Note / Raw Evidence）；
- `config.yaml`（本地敏感配置，单独保管）。

Markdown 能重建部分索引，但不能重建卡片和运行记录；数据库必须单独备份。

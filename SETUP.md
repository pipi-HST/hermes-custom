---
status: current
owner: knowledge-service
last_verified: 2026-11-23
verified_against: "9e5c752 (hermes-custom) / 115bd27 (hermes-agent-main)"
---

# Hermes Knowledge 启动说明

> **当前入口：** [README.md](README.md) 和 [RUNBOOK.md](RUNBOOK.md)。
> 本文保留作补充说明；示例里的旧机器绝对路径已替换为 `<repos>`，实际路径以当前 checkout 为准。
> 命令示例沿用 macOS/Linux 写法；Windows 上把 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。

这个仓库现在只管 Hermes 的知识库能力层。

它负责 Capture、Library / Topic、Reading、RAG、Theme、Knowledge Card、Review、Canvas、Research、Librarian、Answer、Knowledge Graph、Folder write jobs、Run ledger、Knowledge File 同步和 Knowledge API。Life Assistant 已经拆到独立仓库，不再放在这里。

## 三个仓库分别管什么

| 项目 | 本地路径 | GitHub | 职责 |
|------|----------|--------|------|
| Hermes Knowledge | `<repos>/hermes-custom` | `pipi-HST/hermes-custom` | 知识库 Agent、Knowledge File、Knowledge API |
| Hermes Life | `<repos>/hermes-life` | `pipi-HST/hermes-life` | 生活记录、提醒、Life memory、Life Dashboard 源码 |
| Hermes Runtime | `<repos>/hermes-agent-main` | `pipi-HST/hermes-agent-main` | 微信/飞书网关、工具注册、消息分发、用户侧 Dashboard |

人话版：只有一个 Hermes 主入口。知识库和生活助手是两个独立能力项目，都接到同一个 Hermes runtime 下面。

## 配置 Knowledge

```bash
cd "<repos>/hermes-custom"
test -f config.yaml || cp config.example.yaml config.yaml
```

`config.yaml` 不进 Git。这里放 Knowledge 项目的 LLM key、Qwen / DashScope key、数据库路径、Knowledge File storage 路径。这个文件是本地敏感配置，不要复制到文档、日志、Issue 或提交信息里。

## 启动 Knowledge API

```bash
cd "<repos>/hermes-custom"
.venv/bin/python -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
```

默认未配置 `HERMES_API_TOKEN` 时只接受回环地址请求，也不要把该无 token 服务接到对外反向代理后。若必须监听 `0.0.0.0` 或通过代理提供远程访问，请先设置高强度随机 `HERMES_API_TOKEN`，并让客户端通过 `Authorization: Bearer <token>` 发送。

通过自定义域名访问时，设置 `HERMES_API_ALLOWED_HOSTS`，用逗号分隔允许的主机名，省略协议和端口。服务会在校验令牌前校验请求的 Host。

探活使用 `GET /api/health`。配置服务令牌时，探针默认也要带 Bearer 令牌。若需要无凭证的 GET 探针，可设置 `HERMES_HEALTH_PUBLIC=1`，其余 API 继续校验令牌。未配置服务令牌时，回环地址可直接探活。`HERMES_KNOWLEDGE_VERSION` 可覆盖响应里的 `version`，默认取 Git 短 SHA。

现在这个 API 只挂知识库接口：

- 有 `/api/captures`
- 没有 `/api/life/*`

快速检查：

```bash
cd "<repos>/hermes-custom"
.venv/bin/python - <<'PY'
from api.main import create_app

paths = {route.path for route in create_app().routes}
print("/api/captures" in paths)
print(any(path.startswith("/api/life") for path in paths))
PY
```

预期输出：

```text
True
False
```

## 用户侧 Dashboard

本仓库不再包含自定义 Dashboard：旧的 Next.js 项目（`dashboard/`，端口 3000）已于 2026-06-16 废弃、2026-11-18 删除。不要再按 `npm run dev` 启动它。

当前用户侧只有一个 Dashboard，由 Hermes Runtime 提供，默认地址是：

```text
http://127.0.0.1:9119/
```

启动方式：

```bash
cd "<repos>/hermes-agent-main"
.venv/bin/python -m hermes_cli.main dashboard
```

服务端的 `api/routes/dashboard_read.py` 为这个原生 Dashboard 提供草稿、卡片详情、依赖和时间线读形状；`api/routes/captures.py` 的 `GET /api/captures` 也是 Runtime client 使用的服务 API。两者都保留，不属于已删除的 Next.js 项目。

## 启动 Hermes Runtime

```bash
cd "<repos>/hermes-agent-main"
uv run hermes gateway run
```

runtime 连接两个独立能力项目：

- Knowledge（生产推荐 HTTP 模式）：配置 `HERMES_KNOWLEDGE_URL=http://127.0.0.1:8000` 与 `HERMES_KNOWLEDGE_TOKEN=<与服务 HERMES_API_TOKEN 相同>`；仅本地离线调试时才使用 `HERMES_KNOWLEDGE_LOCAL_ROOT=<repos>/hermes-custom`（不要与 URL 同时设置）
- Life（可选独立仓库）：`<repos>/hermes-life`

## 测试

Knowledge 全量测试：

```bash
cd "<repos>/hermes-custom"
.venv/bin/python -m pytest tests/ -q
```

跨进程 Pipeline E2E（真实启动 Knowledge API 和 Hermes Runtime，共用隔离的临时数据库与 Knowledge vault；只用本地 HTTP 服务替代外部 LLM/Embedding 供应商）：

```bash
cd "<repos>/hermes-custom"
.venv/bin/python scripts/knowledge_pipeline_e2e.py --work-dir .tmp/hermes-pipeline-e2e
```

该报告逐项验证 Capture、Runtime 已保存可见性、Reading、Knowledge Card、RAG、Theme、Review、Canvas 和 Knowledge Graph。返回 `ok: true` 只表示所有阶段合同、数据库状态、Markdown 产物及下游对象均通过，不以单个函数返回 `success` 作为通过标准。

Life 测试在独立仓库：

```bash
cd "<repos>/hermes-life"
"<repos>/hermes-custom/.venv/bin/python" -m pytest tests/ -q
```

runtime 的 Life 工具导入测试：

```bash
cd "<repos>/hermes-agent-main"
.venv/bin/python -m pytest tests/tools/test_life_tools_imports.py tests/agent/test_hermes_custom_direct_reply.py -q
```

## 真机测试前的会话清理

每次改完 Hermes 代码后，如果要用微信/飞书做 smoke test，先清掉旧会话，避免上下文污染：

```bash
mkdir -p ~/.hermes/session_backups
if [ -d ~/.hermes/sessions ]; then
  mv ~/.hermes/sessions ~/.hermes/session_backups/sessions-$(date +%Y%m%dT%H%M%S)
fi
mkdir -p ~/.hermes/sessions
```

然后重启 Hermes runtime，再看 `~/.hermes/logs/`。

# Hermes Knowledge 启动说明

> **当前入口：** [README.md](README.md) 和 [RUNBOOK.md](RUNBOOK.md)。
> 本文保留作补充说明；示例里的 `/Users/pipi/...` 是旧机器路径，实际路径以当前 checkout 为准。

这个仓库现在只管 Hermes 的知识库能力层。

它负责 Capture、Library、RAG、Theme View、Garden / Knowledge Cards、Review、Canvas bridge、Knowledge File 同步和 Knowledge API。Life Assistant 已经拆到独立仓库，不再放在这里。

## 三个仓库分别管什么

| 项目 | 本地路径 | GitHub | 职责 |
|------|----------|--------|------|
| Hermes Knowledge | `/Users/pipi/hermes-custom` | `pipi-HST/hermes-custom` | 知识库 Agent、Knowledge File、Knowledge API |
| Hermes Life | `/Users/pipi/hermes-life` | `pipi-HST/hermes-life` | 生活记录、提醒、Life memory、Life Dashboard 源码 |
| Hermes Runtime | `/Users/pipi/Downloads/hermes-agent-main` | `pipi-HST/hermes-agent-main` | 微信/飞书网关、工具注册、消息分发、用户侧 Dashboard |

人话版：只有一个 Hermes 主入口。知识库和生活助手是两个独立能力项目，都接到同一个 Hermes runtime 下面。

## 配置 Knowledge

```bash
cd /Users/pipi/hermes-custom
cp config.example.yaml config.yaml
```

`config.yaml` 不进 Git。这里放 Knowledge 项目的 LLM key、Qwen / DashScope key、数据库路径、Knowledge File storage 路径。这个文件是本地敏感配置，不要复制到文档、日志、Issue 或提交信息里。

## 启动 Knowledge API

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
```

默认未配置 `HERMES_API_TOKEN` 时只接受回环地址请求，也不要把该无 token 服务接到对外反向代理后。若必须监听 `0.0.0.0` 或通过代理提供远程访问，请先设置高强度随机 `HERMES_API_TOKEN`，并让客户端通过 `Authorization: Bearer <token>` 发送。

监控探活：`GET /api/health` 默认同样需要 token。要让 k8s/uptime 之类无凭证探针访问，设置 `HERMES_HEALTH_PUBLIC=1`（只放行 GET/HEAD `/api/health`，其余路由仍需 token）；`HERMES_KNOWLEDGE_VERSION` 可覆盖 health 返回的 `version`（默认取 git 短 sha）。

现在这个 API 只挂知识库接口：

- 有 `/api/captures`
- 没有 `/api/life/*`

快速检查：

```bash
cd /Users/pipi/hermes-custom
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

## Knowledge Dashboard

`/Users/pipi/hermes-custom/dashboard` 是旧的自定义 Next.js Dashboard，已经废弃。`dashboard/package.json` 中的 `dev`、`build`、`start` 会主动退出并提示使用 Hermes runtime dashboard。

当前用户侧 Dashboard 由 Hermes Runtime 提供，默认地址是：

```text
http://127.0.0.1:9119/
```

启动方式：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python hermes dashboard
```

如果需要查看旧 Dashboard 的历史说明，读 `dashboard/DEPRECATED.md`，不要再按旧 `npm run dev` 路径启动。

## 启动 Hermes Runtime

```bash
cd /Users/pipi/Downloads/hermes-agent-main
uv run hermes gateway run
```

runtime 会从两个地方加载能力：

- Knowledge 工具：`/Users/pipi/hermes-custom`
- Life 工具：`/Users/pipi/hermes-life`

## 测试

Knowledge 全量测试：

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m pytest tests/ -q
```

跨进程 Pipeline E2E（真实启动 Knowledge API 和 Hermes Runtime，共用隔离的临时数据库与 Knowledge vault；只用本地 HTTP 服务替代外部 LLM/Embedding 供应商）：

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python scripts/knowledge_pipeline_e2e.py --work-dir /tmp/hermes-pipeline-e2e
```

该报告逐项验证 Capture、Runtime 已入库可见性、Reading、Knowledge Card、RAG、Theme、Review、Canvas 和 Knowledge Graph。返回 `ok: true` 只表示所有阶段合同、数据库状态、Markdown 产物及下游对象均通过，不以单个函数返回 `success` 作为通过标准。

Life 测试在独立仓库：

```bash
cd /Users/pipi/hermes-life
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/ -q
```

runtime 的 Life 工具导入测试：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
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

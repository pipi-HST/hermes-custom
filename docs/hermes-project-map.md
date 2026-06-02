# Hermes 本地项目地图

Hermes 现在拆成了一个 runtime 项目和两个能力项目。

## 职责边界

| 能力 | 负责仓库 | 说明 |
|------|----------|------|
| 微信/飞书网关 | `/Users/pipi/Downloads/hermes-agent-main` | 负责消息入口、工具注册、消息投递 |
| Capture | `/Users/pipi/hermes-custom` | 抓取和整理知识材料 |
| Library | `/Users/pipi/hermes-custom` | 修复 metadata、目录、embedding、Obsidian 一致性 |
| RAG | `/Users/pipi/hermes-custom` | 基于 captures、themes、Garden cards 和来源回答问题 |
| Theme / Garden | `/Users/pipi/hermes-custom` | 生成 MOC、主题页、Garden card、Canvas |
| Review | `/Users/pipi/hermes-custom` | 每日知识点回顾和追问回答 |
| Life Assistant | `/Users/pipi/hermes-life` | 管 `life_*` 表、生活记录、提醒、Life memory |

## 边界规则

Knowledge 代码不能读写 Life 的生活记录、提醒日志、Life memory。

Life 代码不能读写 Obsidian captures、主题页、Garden cards、Review items、Capture drafts。

Hermes runtime 可以把用户消息路由给任一能力项目，但不拥有业务逻辑。

## 当前 runtime 加载路径

Knowledge 工具从这里加载：

```text
/Users/pipi/hermes-custom
```

Life 工具从这里加载：

```text
/Users/pipi/hermes-life
```

## 常用验证命令

Knowledge：

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m pytest tests/ -q
```

Life：

```bash
cd /Users/pipi/hermes-life
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/ -q
```

Runtime Life 集成：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/tools/test_life_tools_imports.py tests/agent/test_hermes_custom_direct_reply.py -q
```


# Hermes + Capture Agent Setup

## 前置条件

- Python 3.11+ ✓ (已安装 `/opt/homebrew/bin/python3.11`)
- Node.js 24+ ✓ (已安装)
- Hermes Agent ✓ (已安装，通过 `uv run hermes` 运行)

## 1. 配置 API Keys

编辑 `~/.hermes/.env`:
```bash
OPENAI_API_KEY=sk-xxx
```

编辑 `~/.hermes/config.yaml`:
```yaml
model:
  provider: openai
  name: gpt-4o
```

## 2. 配置飞书 Gateway

需要在 [飞书开放平台](https://open.feishu.cn) 创建企业自建应用：
- 开通权限：`im:message`、`docx:document`、`calendar:calendar:read`
- 获取 `app_id`、`app_secret`、`verification_token`

编辑 `~/.hermes/config.yaml`:
```yaml
platforms:
  feishu:
    enabled: true
    app_id: "cli_xxx"
    app_secret: "xxx"
    verification_token: "xxx"
```

## 3. 配置微信 Gateway

需要在微信 iLink Bot 平台创建应用，获取凭据后填入：
```yaml
platforms:
  weixin:
    enabled: true
    bot_app_id: "xxx"
    bot_app_secret: "xxx"
```

## 4. 配置 Capture Agent

复制并编辑 `hermes-custom/config.yaml`:
```bash
cd hermes-custom
cp config.example.yaml config.yaml
# 编辑 config.yaml 填入 LLM API key 和飞书凭据
```

## 5. 启动

```bash
# 终端1: 启动 FastAPI
cd hermes-custom && python3.11 api/main.py

# 终端2: 启动仪表盘
cd hermes-custom/dashboard && npm run dev

# 终端3: 启动 Hermes Gateway
cd Downloads/hermes-agent-main && uv run hermes gateway run
```

## 6. 验证

1. 在微信/飞书上给 bot 发消息 → 确认收到回复
2. 发一条链接/文本 → Capture Agent 处理 → 仪表盘可见
3. 发"帮我压缩一下" → 多轮加工生效

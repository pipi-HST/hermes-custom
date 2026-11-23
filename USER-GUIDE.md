---
status: current
owner: knowledge-service
last_verified: 2026-11-23
verified_against: "9e5c752 (hermes-custom) / 115bd27 (hermes-agent-main)"
---

# Hermes Knowledge 开源使用指引（User Guide）

欢迎使用 **Hermes Knowledge**！这是一套面向高频学习者、研究者与知识工作者的本地优先个人知识库系统，围绕 **「资料伴读 → 知识卡片 → 专题输出」** 的认知内化闭环设计，帮你把碎片化输入真正沉淀为可回看、可组合、可溯源的个人观点。

> **安全提示**：本文档中所有 API Key、Token 与本地路径均为占位示例。请始终在本地私有的 `config.yaml` 与 `.env` 中填写真实密钥，**切勿将密钥提交到 Git 仓库、公开 Issue 或截图日志中**。

---

## 一、整体架构与仓库分工

Hermes 采用「运行时 + 知识服务」双仓库解耦架构，数据完全保存在你的本地磁盘（SQLite + Markdown）：

| 仓库 | 默认端口 | 职责 |
|---|---|---|
| **`hermes-custom`**（本仓库） | `127.0.0.1:8000` | 知识库核心业务与存储层：资料采集、原文保真、目录管理、伴读、RAG 混合检索、知识卡片提炼与编辑、专题联网研究、知识图谱 |
| **`hermes-agent-main`** | `127.0.0.1:9119` | 主运行时与用户界面：原生网页工作台（资料伴读 / 知识卡片 / 专题输出）、主 Agent 调度、工具注册、可选微信/飞书消息网关 |

```text
微信 / 飞书 / 网页工作台 (127.0.0.1:9119)
            │
            ▼  HTTP + Bearer Token
Hermes Knowledge Service (127.0.0.1:8000)
            │
            ├── SQLite (data/captures.db)：卡片、索引、向量、图谱、运行台账
            └── Markdown Vault (captures/*.md, _archive/raw/*.md)：保真原文与整理稿
```

---

## 二、环境与密钥准备

### 1. 基础环境

- **Python**：3.12 或以上
- **Git**：用于拉取代码
- **Node.js**：20+（仅在你想二次开发修改网页前端源码时需要；日常直接运行无需手动编译前端）
- **操作系统**：支持 Windows、macOS、Linux

### 2. 需要准备的 API 密钥

| 配置项 | 必填/可选 | 用途 | 填写位置 |
|---|---|---|---|
| **LLM API Key**（推荐 DeepSeek） | 必填 | 伴读问答、卡片提炼、专题输出与联网研究 | `hermes-custom/config.yaml` 及 `~/.hermes/.env` |
| **DashScope / 通义千问 Key** | 推荐 | 语义向量化（`text-embedding-v4`）、图片 OCR、语音转写 | `hermes-custom/config.yaml` |
| **`HERMES_API_TOKEN`** | 推荐 | 知识服务与网页端之间的本地鉴权随机口令（自己随机生成一串即可） | 环境变量 / `.env`（两端保持一致） |
| **微信 / 飞书凭据** | 可选 | 仅当需要通过手机端聊天随时发链接、发笔记时配置 | `hermes-agent-main` 网关配置 |

> **如何生成安全的本地随机 Token**：
> ```bash
> python -c "import secrets; print(secrets.token_urlsafe(32))"
> ```

---

## 三、10 分钟快速部署

假设你准备把两个仓库放在同一个父目录 `<workspace>` 下。

### Step 1：克隆两个仓库

```bash
mkdir -p <workspace>
cd <workspace>

git clone https://github.com/pipi-HST/hermes-custom.git
git clone https://github.com/pipi-HST/hermes-agent-main.git
```

### Step 2：配置并启动知识服务（`hermes-custom`，端口 `8000`）

1. **安装 Python 依赖**

   Windows（PowerShell）：
   ```powershell
   cd <workspace>\hermes-custom
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

   macOS / Linux：
   ```bash
   cd <workspace>/hermes-custom
   python3.12 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

2. **创建私有配置文件 `config.yaml`**（该文件已在 `.gitignore` 中，不会被提交）

   ```bash
   cp config.example.yaml config.yaml
   ```

   打开 `config.yaml`，将占位符替换为你自己的密钥与本地路径：
   ```yaml
   llm:
     provider: openai
     model: deepseek-v4-pro              # 或 deepseek-chat / deepseek-reasoner 等兼容模型
     base_url: "https://api.deepseek.com"
     api_key: "YOUR_LLM_API_KEY"         # 替换为你的真实 Key

   qwen:
     api_key: "YOUR_DASHSCOPE_API_KEY"   # 用于向量检索与 OCR/ASR

   database:
     path: "./data/captures.db"

   vault:
     path: "./data/vault"                # 也可指向你现有的 Obsidian 目录
   ```

3. **启动 Knowledge Service**

   Windows（PowerShell）：
   ```powershell
   $env:HERMES_API_TOKEN = "YOUR_RANDOM_LOCAL_TOKEN"
   .\.venv\Scripts\python.exe -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
   ```

   macOS / Linux：
   ```bash
   export HERMES_API_TOKEN="YOUR_RANDOM_LOCAL_TOKEN"
   .venv/bin/python -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
   ```

4. **验证服务启动成功**：
   ```bash
   curl -H "Authorization: Bearer YOUR_RANDOM_LOCAL_TOKEN" http://127.0.0.1:8000/api/health
   ```
   返回 `{"status":"ok", ...}` 即说明知识服务运行正常。

### Step 3：配置并启动网页工作台（`hermes-agent-main`，端口 `9119`）

新开一个终端窗口：

1. **安装 Runtime 依赖**

   Windows（PowerShell）：
   ```powershell
   cd <workspace>\hermes-agent-main
   uv sync --extra web
   # 若未安装 uv，可用：
   # py -3.12 -m venv .venv
   # .\.venv\Scripts\python.exe -m pip install -e ".[web]"
   ```

   macOS / Linux：
   ```bash
   cd <workspace>/hermes-agent-main
   uv sync --extra web
   ```

2. **配置连接知识服务的环境变量**

   在 `~/.hermes/.env`（或当前终端环境变量）中添加以下配置（**确保 `HERMES_KNOWLEDGE_TOKEN` 与 Step 2 的 `HERMES_API_TOKEN` 完全一致**）：
   ```env
   HERMES_KNOWLEDGE_URL=http://127.0.0.1:8000
   HERMES_KNOWLEDGE_TOKEN=YOUR_RANDOM_LOCAL_TOKEN

   # Runtime 主 Agent 使用的模型密钥（示例为 DeepSeek / OpenAI 兼容接口）
   OPENAI_API_KEY=YOUR_LLM_API_KEY
   OPENAI_BASE_URL=https://api.deepseek.com/v1
   ```

   > 若希望在网页端使用「用本地 App 打开（`open-local`）」按钮直接唤起本机编辑器，可额外补充：
   > ```env
   > HERMES_KNOWLEDGE_VAULT=<workspace>/hermes-custom/data/vault
   > HERMES_KNOWLEDGE_DB=<workspace>/hermes-custom/data/captures.db
   > ```

3. **启动原生网页工作台**

   Windows（PowerShell）：
   ```powershell
   .\.venv\Scripts\python.exe -m hermes_cli.main dashboard
   ```

   macOS / Linux：
   ```bash
   .venv/bin/python -m hermes_cli.main dashboard
   ```

   浏览器打开 **`http://127.0.0.1:9119`**，即可进入 Hermes 纸白工作台！

### Step 4（可选）：一键体验示例数据或接入聊天网关

- **导入 Golden Demo 体验数据**（包含 6 篇示例资料、9 张示例卡片与 3 个专题，无 API Key 也能体验检索）：
  ```bash
  cd <workspace>/hermes-custom
  .venv/bin/python scripts/seed_interview_demo.py
  ```
- **启动微信 / 飞书消息网关**（用于手机端随时发链接、发随手记）：
  ```bash
  cd <workspace>/hermes-agent-main
  uv run hermes gateway run
  ```

---

## 四、核心功能使用指南

Hermes 网页端采用清晰的三步认知内化流：**资料伴读 → 知识卡片 → 专题输出**。

### 1. 资料伴读（输入、精读与解惑）

- **导入与新建资料**：点击右上角 **「新建」**，可以直接新建空白笔记、粘贴文章正文，或导入本地 `.md` / `.txt` 文件；通过微信/飞书发来的链接也会先进入「待整理」草稿箱。
- **原文保真铁律**：所有保存动作都会把原始材料归档到 `_archive/raw/`，默认保存不会让 AI 擅自压缩或改写你的原文。
- **纸面精读与划词伴读**：
  - 点击左侧专题目录打开资料，中间为无框 `740px` 舒适阅读区，支持在右上角「视图」中一键切入 **沉浸阅读**。
  - 在正文中**划选任意一段话**，或在右侧 **「伴读助手」** 直接提问，AI 会结合当前全文与你的历史资料库进行流式解答；需要外部事实时，一键点亮 **「联网搜索」** 即可联合外部网页查证。
- **一键提炼卡片**：读到有启发的好文，直接点击阅读区顶部的 **「提炼卡片」**，即可生成卡片草稿，并点击提示条里的 **「立即去审阅编辑 →」** 跳转审阅。

### 2. 知识卡片（低认知负担沉淀与挑选）

Hermes 拒绝冗长的六段式 AI 八股模板，每张卡片只保留三个最适合人眼扫读的要素：
1. **短标题**：一眼识别记忆点；
2. **一句话结论**：30–60 字直击核心判断；
3. **易扫读正文**：3–5 条加粗关键词要点 + 必要时的一句轻提醒。

- **宽幅纸白画廊**：按专题、卡片类型（`洞察 / 金句`、`方法 / 框架`、`新知 / 概念`、`避坑 / 边界`）、`按最新 / 按常用` 排序与关键词搜索快速浏览。每张卡片会轻柔预览正文要点关键词，不用逐个点开就能快速辨认。
- **滑出阅读与编辑抽屉**：点击任意待确认草稿或已存卡片，右侧滑出精读抽屉：
  - 可阅读完整 Markdown 正文、点击底部来源原文跳回「资料伴读」；
  - 点击 **「编辑」** 直接修改标题、类型、一句话结论与 Markdown 正文；
  - 待确认草稿还支持在抽屉底部用一句话让 AI 润色（如“再精简一点”、“补一个例子”），满意后点 **「确认存入卡片库」**。
- **多卡选题篮**：鼠标悬停在画廊卡片右上角勾选多张卡片，底部自动浮现 **选题篮**，点击 **「带着去专题输出 →」** 即可一键把选中的卡片送入下一环节。

### 3. 专题输出（跨篇综合与研究成稿）

- **带着卡片开写**：从卡片库送来的卡片会自动挂载在底部一体化输入舱上方，你可以直接让 Hermes 基于这几张卡片梳理专题框架、准备面试回答或对比不同观点。
- **可控检索模式**：
  - **仅个人资料库**：严格围绕你的本地资料与卡片推理，适合复盘与整合个人观点；
  - **联网 + 资料库**：走瘦身研究管线，自动并行检索本地资料与外部网页，在正文中标注 `[K]`（个人知识库）与 `[W]`（联网来源），并在右侧 **「参考依据」** 面板完整列出可点击溯源。
  - **扫描跨篇关联**：自动发现你最近保存内容中的潜在跨篇选题。
- **成果回流沉淀**：对某轮高质量输出满意时，可直接点击下方 **「存为笔记」** 或 **「做成卡片」**，让输出重新沉淀为长期资产。

---

## 五、数据管理、备份与安全规范

### 1. 你的数据存在哪里？

默认都在 `<workspace>/hermes-custom/data/`（或你在 `config.yaml` 中指定的 `vault.path`）：
- `captures/*.md`：你的资料笔记（含 YAML frontmatter，可直接用 Obsidian、Typora 或任意 Markdown 编辑器打开）
- `_archive/raw/*.md`：原始证据存档
- `captures.db`：SQLite 数据库（存储知识卡片、向量索引、图谱关系、草稿与运行记录）

> **备份建议**：因为知识卡片和图谱的唯一真相源在 `captures.db`，资料正文在 `captures/*.md`，定期备份整个 `data/`（含 `vault/` 与 `captures.db`）即可完整迁移你的个人知识库。

### 2. 开源与自部署安全须知

- **密钥隔离**：`config.yaml`、`.env`、`.env.*`、`data/*.db`、`data/vault/` 均已加入 `.gitignore`。在任何二次开发提交前，请用 `git status` 确认不包含本地配置与个人数据文件。
- **公网暴露防护**：`127.0.0.1:8000` 与 `127.0.0.1:9119` 默认仅监听本机回环地址。如果你要把服务部署到云服务器并通过域名/反向代理访问：
  1. **必须**设置高强度随机 `HERMES_API_TOKEN`；
  2. 通过 `HERMES_API_ALLOWED_HOSTS` 限制允许的 Host 域名；
  3. 务必在反向代理层启用 HTTPS。
- **本地数据重置**：如需清空测试数据重新开始，请参考 [docs/local-data-reset-runbook.md](docs/local-data-reset-runbook.md)。

---

## 六、常见问题排查（FAQ）

| 现象 | 排查与解决方法 |
|---|---|
| 网页工作台提示 `503` 或无法连接知识库 | 1. 确认端口 `8000` 的 `uvicorn` 服务已启动；<br>2. 检查两端 `HERMES_API_TOKEN` 与 `HERMES_KNOWLEDGE_TOKEN` 是否完全一致；<br>3. 检查 `HERMES_KNOWLEDGE_URL` 是否设为 `http://127.0.0.1:8000`。 |
| 刚存的文章在语义检索里搜不到 | 检查 `config.yaml` 中的 `qwen.api_key`（DashScope Key）是否已填写；若之前离线写入过，可运行 `scripts/backfill.py` 补建向量索引。 |
| 专题目录和卡片所属专题显示不一致 | 运行 `python scripts/repair_topics.py --apply --fix-mismatched` 即可按唯一真相源修复。 |
| 修改了 `hermes-agent-main/web` 前端源码，刷新页面没生效 | 在 `hermes-agent-main/web` 下执行 `npm install && npm run build` 重新生成 `hermes_cli/web_dist`，然后重启 `dashboard` 并在浏览器按 `Ctrl + Shift + R` 强制刷新。 |
| 如何运行自动化测试验证环境？ | `hermes-custom` 下运行 `pytest tests -q`；更多运维命令见 [RUNBOOK.md](RUNBOOK.md) 与 [ARCHITECTURE.md](ARCHITECTURE.md)。 |

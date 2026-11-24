---
status: current
owner: knowledge-service
last_verified: 2026-11-24
verified_against: "aa94b60 (hermes-custom) / fcad2ff (hermes-agent-main)"
---

# Hermes Knowledge

**拒绝「收藏 = 学会」。把每一次碎片阅读，真正沉淀成可回看、可组合、可溯源的个人判断。**

Hermes Knowledge 是一款**本地优先（Local-First）的个人 AI 认知内化工作台**。它不追求炫技式的复杂 AI 面板，而是用安静克制的「编辑部纸白」阅读体验，带你走完从输入到输出的完整闭环：**资料伴读 → 知识卡片 → 专题输出**。

[核心特色](#核心特色) · [为什么不一样](#为什么选择-hermes) · [3 步下载安装](#3-步下载与一键启动) · [日常怎么用](#3-分钟上手体验) · [详细使用手册](USER-GUIDE.md) · [架构与开发文档](#开发者与架构专区)

---

## 为什么选择 Hermes？

大多数人在用笔记软件和 AI 工具时，都会卡在同一个死循环里：**收藏夹塞满了几百篇好文，AI 总结看了一遍就忘，真到写方案、做分享、准备面试时，脑子里依然一片空白。**

Hermes 从底层设计上反其道而行：

| 常见痛点 | 传统收藏夹 / 普通 AI 笔记 | Hermes Knowledge 的做法 |
|---|---|---|
| **一保存就被乱删改** | AI 自动把万字长文压成几百字干瘪摘要，关键案例和数据全丢了 | **原文 100% 保真铁律**：原始文章独立归档，绝不擅自删改你的一字一句 |
| **读不懂懒得往下看** | 遇到陌生概念要切去搜索，打断阅读心流 | **划词流式伴读**：鼠标划选任意段落即刻发问，自动串联你的历史笔记与联网查证 |
| **卡片写成八股没人看** | 强行套「边界 / 反例 / 行动启发」六段式 AI 模板，越存越累 | **3 秒扫读人话卡片**：只留「短标题 + 一句话结论 + 3–5 条加粗要点」，支持一句话让 AI 润色或随手改 |
| **写东西时找不到素材** | 对着空白聊天框瞎聊，分不清哪句是自己存的、哪句是模型编的 | **画廊挑卡 → 选题篮成稿**：像逛画廊一样挑几张卡片送去写作，每句结论标清 `[K]` 本地依据与 `[W]` 网页出处 |
| **界面繁琐、数据锁云端** | 满屏紫渐变图标与复杂配置，笔记被锁在第三方平台 | **编辑部纸白美学 + 纯本地存储**：所有文章都是标准 `.md` 文件，随时可用 Obsidian 直接打开 |

---

## 核心特色

```text
  手机微信 / 网页导入             读透一篇                    沉淀一张                     串成一篇
 ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
 │   原始资料保真    │ ───▶ │     资料伴读      │ ───▶ │     知识卡片      │ ───▶ │     专题输出      │
 │  绝不偷偷压缩原文  │      │ 纸面精读 + 划词问 │      │ 宽幅画廊 + 选题篮 │      │ 带着卡片写研究稿 │
 └──────────────────┘      └──────────────────┘      └──────────────────┘      └──────────────────┘
```

- **去 AI 味的「编辑部纸白」工作台**
  全站统一温润纸白画布与炭黑墨水排版，没有花哨彩条与冗余工程参数。`740px` 黄金中文阅读行宽 + 一键全屏沉浸阅读，让你重新爱上读长文。
- **真正降低认知负担的知识卡片库**
  卡片沉淀不是为了“再建一个收藏夹”，而是为了将来用的时候**挑得出来**。宽幅多列画廊一眼扫过核心结论与加粗关键词；点开右侧滑出精读抽屉，草稿与已存卡片都能看全文、随时改、一键跳回原文章。
- **微信轻输入 + 网页重整理（双端协同）**
  通勤路上刷到公众号好文或突发灵感，随手发给微信/飞书机器人生成待审草稿（日常快答、资料检索、DeepSeek 极速联网三档自动分流）；回到电脑前打开网页端从容精读与内化。
- **有据可查的跨篇专题研究**
  在卡片画廊勾选几张不同文章里提炼出的卡片，底部自动浮现**选题篮**，一键带入「专题输出」。支持「仅个人资料库」与「联网 + 资料库」一键切换，好回答还能反手存回新笔记或新卡片，越用越厚实。

---

## 3 步下载与一键启动

无需繁琐配置——只需下载 **这一个仓库**、填 **一份 `.env` 密钥**、运行 **一条启动命令**。首次运行时脚本会自动拉取配套网页端、配好 Python 环境并打开浏览器。

### 准备工作

1. 电脑已安装 **[Python 3.12+](https://www.python.org/downloads/)**（Windows 安装时请勾选 `Add Python to PATH`）和 **[Git](https://git-scm.com/downloads)**。
2. 准备一个 **[DeepSeek API Key](https://platform.deepseek.com/)**（必填，用于伴读问答、提炼卡片与联网研究）；推荐同时准备一个 **[阿里云百炼 DashScope Key](https://bailian.console.aliyun.com/)**（用于语义向量检索与图片/语音识别，暂不填也能跑）。

### 第 1 步：下载项目

打开终端（Windows 打开 PowerShell，macOS 打开「终端」）：

```bash
git clone https://github.com/pipi-HST/hermes-custom.git
cd hermes-custom
```

### 第 2 步：填入你的 API Key

复制配置模板为 `.env`（该文件已被 `.gitignore` 严格排除，不会上传泄露）：

```powershell
# Windows (PowerShell)
copy .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

用记事本或任意编辑器打开 `.env`，填入你的密钥并保存：

```env
DEEPSEEK_API_KEY=把这里换成你的DeepSeek密钥
DASHSCOPE_API_KEY=把这里换成你的阿里云百炼密钥（没有可先留空）
```

### 第 3 步：一键启动

```powershell
# Windows (PowerShell)
.\start.ps1
```

```bash
# macOS / Linux
chmod +x start.sh && ./start.sh
```

首次运行会自动下载配套网页运行时并安装依赖（约 1–3 分钟，之后每次启动只需几秒）。就绪后浏览器会自动打开 **`http://127.0.0.1:9119`**，按一次 `Ctrl + C` 即可同时关闭所有后台服务。

> **想零成本先看完整效果？**
> 首次启动完成后，运行下面一行命令即可导入内置体验包（含 **6 篇深度文章 + 9 张精选知识卡片 + 3 个专题**），打开网页就能直接体验划词伴读、画廊挑卡与专题输出：
> - Windows：`.\.runtime\hermes-agent-main\.venv\Scripts\python.exe scripts\seed_interview_demo.py`
> - macOS / Linux：`./.runtime/hermes-agent-main/.venv/bin/python scripts/seed_interview_demo.py`

---

## 3 分钟上手体验

打开 `http://127.0.0.1:9119` 后，顺着左侧仅有的三个页面往下走：

1. **去「资料伴读」读一篇好文**：点右上角「新建」粘贴一篇文章或导入 `.md` 文件。鼠标划选文中任意一段话问右侧伴读助手；读完点顶部的黑色 **「提炼卡片」** 按钮。
2. **去「知识卡片」收下你的洞察**：点开刚生成的琥珀点草稿卡，滑出抽屉看全文——可以自己在底部输入一句“再精简一点”让 AI 润色，也可以点「编辑」手改，满意后存入卡片库。
3. **去「专题输出」串珠成链**：在画廊右上角勾几张卡片放进底部悬浮**选题篮**，点 **「带着去专题输出 →」**，让它结合你的卡片库与联网搜索写出一篇结构化专题稿。

想要查看更详尽的功能技巧、Obsidian 共用目录配置、微信/飞书机器人接入与常见问题解答，请阅读：**[Hermes Knowledge 完整用户指南 (USER-GUIDE.md)](USER-GUIDE.md)**。

---

## 数据归属与隐私安全

- **100% 本地可控**：你的所有文章都保存在本地 `data/vault/captures/*.md`，卡片与关联保存在本地 `data/captures.db`，备份时只需复制整个 `data/` 文件夹。
- **密钥零外泄**：`.env` 与 `config.yaml` 仅保存在你的本机，启动脚本会自动生成高强度本地随机口令保护本地端口，且绝不在日志中打印密钥。

---

## 开发者与架构专区

<details>
<summary><strong>展开查看：双仓系统架构、手动分步调试、API 契约与自动化测试命令</strong></summary>

### 1. 系统职责边界

Hermes 采用「运行时 + 知识服务」解耦设计（一键脚本 `start.ps1` / `start.sh` 会在底层自动协调两者）：

- **`hermes-custom`（本仓库，默认 `127.0.0.1:8000`）**：负责 Capture、Library / Topic、Reading、RAG、Theme、Knowledge Card、Review、Canvas、Research（`agents/research/` 独立子系统）、Librarian、Answer、Knowledge Graph、Run ledger 与 SQLite / Markdown 双存储一致性。
- **`hermes-agent-main`（配套运行时，默认 `127.0.0.1:9119`）**：负责原生 Web Dashboard、主 Agent 调度、工具注册及微信/飞书网关。

### 2. 开发者手动分步启动（调试用）

```powershell
# 终端 1：启动 Knowledge Service (8000)
cd "<repos>\hermes-custom"
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path config.yaml)) { Copy-Item config.example.yaml config.yaml }
$env:HERMES_API_TOKEN = "<随机口令>"
.venv\Scripts\python.exe -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000

# 终端 2：启动 Hermes Runtime Dashboard (9119)
cd "<repos>\hermes-agent-main"
uv sync --extra web
$env:HERMES_KNOWLEDGE_URL = "http://127.0.0.1:8000"
$env:HERMES_KNOWLEDGE_TOKEN = "<与上面一致的随机口令>"
.venv\Scripts\python.exe -m hermes_cli.main dashboard --no-open
```

### 3. 运行测试与架构校验

```powershell
cd "<repos>\hermes-custom"
# 全量单元测试与集成测试（1400+ 用例）
.venv\Scripts\python.exe -m pytest tests -q

# 跨进程端到端流水线验收
$env:HERMES_RUNTIME_ROOT = "<repos>\hermes-agent-main"
.venv\Scripts\python.exe scripts\knowledge_pipeline_e2e.py --work-dir .tmp\hermes-pipeline-e2e

# 架构路由清单与文档一致性检查
.venv\Scripts\python.exe scripts\architecture_inventory.py --check --strict
.venv\Scripts\python.exe scripts\check_docs.py
```

### 4. 工程文档导航

| 文档 | 内容说明 |
|---|---|
| [USER-GUIDE.md](USER-GUIDE.md) | 面向用户的完整安装、特色场景与日常使用手册 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 子系统边界、数据唯一真相源、单写入口规则 |
| [RUNBOOK.md](RUNBOOK.md) | 服务启停、排障、数据重置与索引修复脚本 |
| [SETUP.md](SETUP.md) | 多仓库开发配置与网关会话清理说明 |
| [GLOSSARY.md](GLOSSARY.md) | 产品术语、代码字段与历史命名对照表 |
| [CONTEXT.md](CONTEXT.md) | 领域模型与工作流定义 |
| [AGENTS.md](AGENTS.md) | 面向协作开发与 AI Agent 的工程红线 |
| [docs/PRODUCT-REFACTOR-STORY.md](docs/PRODUCT-REFACTOR-STORY.md) | 产品设计演进故事与核心取舍复盘 |
| [docs/CURRENT-STATE.md](docs/CURRENT-STATE.md) | 自动生成的测试数、路由数与数据表快照 |
| [docs/README.md](docs/README.md) | 全部当前文档与历史归档索引（`HANDOFF.md` 见 `docs/history/HANDOFF-2026-05.md`） |

</details>

---

## License

本项目采用 [MIT License](LICENSE) 开源（Copyright (c) 2026 pipi-HST）。
配套运行时仓库 `hermes-agent-main` 基于 [Nous Research / Hermes Agent](https://github.com/NousResearch/hermes-agent)（MIT License）二次定制，已完整保留原协议与版权声明。

---
status: current
owner: knowledge-service
last_verified: 2026-11-23
verified_against: "9e5c752 (hermes-custom) / 115bd27 (hermes-agent-main)"
---

# Hermes Knowledge 使用指南

欢迎使用 **Hermes Knowledge**！这是一套装在你自己电脑里的个人知识助手，帮你把平时随手收藏的好文章、零散灵感，真正变成**看得懂、找得到、用得上**的个人知识资产。

---

## 它能帮你做什么？

很多人都有同一个困扰：收藏夹里存了几百篇文章，真要写总结、准备面试或做分享时，脑子一片空白。

Hermes 把学习拆成最顺手的三步：

1. **资料伴读（先读懂）**：把文章贴进来或导入本地笔记，**原文 100% 完整保留，AI 不会偷偷帮你删改**。读到卡壳的地方，直接划选一段话问右侧伴读助手，它会结合这篇文章和你以前存过的笔记一起给你讲透。
2. **知识卡片（再记牢）**：读完顺手点一下「提炼卡片」，自动生成一张 3 秒能扫完重点的小卡片（短标题 + 一句话结论 + 几条加粗要点），不写臭长八股，你随时可以自己改，或者一句话让 AI 帮你润色。
3. **专题输出（最后用出来）**：卡片攒多了之后，在画廊里勾几张相关的卡片加入底部「选题篮」，一键送进「专题输出」，结合你的历史资料和联网搜索，帮你梳理框架、碰撞观点、直接成稿——而且每条结论都能点回出处。

> 所有文章与笔记都存在你自己的电脑里（纯本地 Markdown 文件 + 本地数据库），不上传到任何第三方知识库平台。

---

## 一、安装前需要准备什么？

只需要准备两样东西：**电脑基础环境** 和 **模型 API Key**。

### 1. 电脑环境

- **Python 3.12 或以上**：前往 [python.org](https://www.python.org/downloads/) 下载安装（Windows 安装时请勾选 **Add Python to PATH**）。
- **Git**：前往 [git-scm.com](https://git-scm.com/downloads) 下载安装。
- 打开终端（Windows 用 PowerShell，macOS 用「终端」），输入下面两行都能显示版本号就说明准备好了：
  ```bash
  python --version
  git --version
  ```

### 2. 模型 API Key（按需准备，全在本地填写）

| 名称 | 是否必填 | 干什么用 | 在哪获取 |
|---|---|---|---|
| **DeepSeek API Key** | **必填** | 负责伴读问答、提炼卡片、专题写作与联网搜索 | [DeepSeek 开放平台](https://platform.deepseek.com/) 注册后创建 |
| **阿里云百炼（DashScope）Key** | 推荐 | 负责“意思相近也能搜出来”的语义检索，以及图片识字、语音转文字 | [阿里云百炼控制台](https://bailian.console.aliyun.com/) 创建 |
| **本地访问口令** | 推荐 | 你自己随便编一串长一点的英文+数字（如 `my-hermes-secret-2026`），用来保护本机服务不被别的程序乱连 | 自己编即可，不花钱 |

> **安全提醒**：你填写的所有 Key 都只保存在自己电脑的私有配置文件里（已被 `.gitignore` 自动屏蔽），**请勿把真实 Key 贴到截图、公开问答或代码提交里**。

---

## 二、4 步安装并启动（约 10 分钟）

Hermes 由两个仓库配合工作，把它们放到同一个文件夹里即可：
- **`hermes-custom`**：负责存资料、提炼卡片和搜索的「知识大脑」；
- **`hermes-agent-main`**：提供打开浏览器就能用的「网页工作台」（以及可选的微信/飞书消息接入）。

### 第 1 步：下载代码

在终端里选一个你常用的目录（下面用 `hermes-app` 举例），执行：

```bash
mkdir hermes-app
cd hermes-app

git clone https://github.com/pipi-HST/hermes-custom.git
git clone https://github.com/pipi-HST/hermes-agent-main.git
```

### 第 2 步：填好你的密钥配置

进入 `hermes-custom`，复制一份配置模板为 `config.yaml`：

- **Windows（PowerShell）**：
  ```powershell
  cd hermes-custom
  copy config.example.yaml config.yaml
  ```
- **macOS / Linux**：
  ```bash
  cd hermes-custom
  cp config.example.yaml config.yaml
  ```

用记事本、VS Code 或任意编辑器打开刚生成的 `config.yaml`，只需改下面几行：

```yaml
llm:
  provider: openai
  model: deepseek-v4-pro              # 也可以用 deepseek-chat
  base_url: "https://api.deepseek.com"
  api_key: "把这里换成你的DeepSeek密钥"

qwen:
  api_key: "把这里换成你的阿里云百炼密钥（暂时没有可先留空）"

database:
  path: "./data/captures.db"          # 默认即可，不需要改

vault:
  path: "./data/vault"                # 你的笔记存放位置，也可以改成你的 Obsidian 目录
```

保存并关闭文件。

### 第 3 步：启动「知识大脑」（端口 8000）

在当前 `hermes-custom` 终端窗口里，依次执行安装依赖和启动命令：

- **Windows（PowerShell）**：
  ```powershell
  py -3.12 -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt

  # 设置本地口令（和第 4 步保持一致即可）
  $env:HERMES_API_TOKEN = "换成你自己编的本地口令"
  .\.venv\Scripts\python.exe -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
  ```
- **macOS / Linux**：
  ```bash
  python3.12 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt

  export HERMES_API_TOKEN="换成你自己编的本地口令"
  .venv/bin/python -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000
  ```

看到屏幕打印出 **`Uvicorn running on http://127.0.0.1:8000`** 就启动成功了！**保持这个窗口开着，不要关闭。**

### 第 4 步：启动「网页工作台」（端口 9119）

**新开一个终端窗口**，进入 `hermes-agent-main` 安装并启动界面：

- **Windows（PowerShell）**：
  ```powershell
  cd hermes-app\hermes-agent-main
  py -3.12 -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -e ".[web]"
  ```
- **macOS / Linux**：
  ```bash
  cd hermes-app/hermes-agent-main
  python3.12 -m venv .venv
  .venv/bin/python -m pip install -e ".[web]"
  ```

接着在你的用户主目录下创建或编辑 `~/.hermes/.env` 文件（Windows 路径通常是 `C:\Users\<你的用户名>\.hermes\.env`），写入以下 4 行并保存：

```env
HERMES_KNOWLEDGE_URL=http://127.0.0.1:8000
HERMES_KNOWLEDGE_TOKEN=换成与第3步完全一致的本地口令
OPENAI_API_KEY=把这里换成你的DeepSeek密钥
OPENAI_BASE_URL=https://api.deepseek.com/v1
```

最后启动网页工作台：

- **Windows（PowerShell）**：
  ```powershell
  .\.venv\Scripts\python.exe -m hermes_cli.main dashboard
  ```
- **macOS / Linux**：
  ```bash
  .venv/bin/python -m hermes_cli.main dashboard
  ```

打开浏览器访问 **`http://127.0.0.1:9119`**，就能看到干净清爽的 Hermes 工作台了！

> **想先体验一下效果？**
> 在 `hermes-custom` 目录运行一次下面这行命令，会自动导入 6 篇示例文章和 9 张示例卡片，立刻就能点着体验全流程：
> ```bash
> .venv/bin/python scripts/seed_interview_demo.py
> ```
> （Windows 请把 `.venv/bin/python` 换成 `.\.venv\Scripts\python.exe`）

---

## 三、日常怎么用？（三步轻松上手）

打开 `http://127.0.0.1:9119`，左侧导航只有 3 个核心页面，按从上到下的顺序用就对了。

### 第一步：在「资料伴读」存文章、读文章

1. **放入资料**：点击右上角 **「新建」**，可以选择「新建知识文件」（直接写或粘贴公众号/网页长文）或「导入 Text / Markdown 文件」。保存后会按专题归入左侧目录。
2. **舒适精读**：点开任意文章即可在中间纯白纸面阅读；想更专注时，点右上角「视图 → 进入沉浸阅读」全屏阅读。
3. **划词提问**：读到看不懂的概念或段落，用鼠标**直接划选那段文字**，在右侧「伴读助手」里问它（例如：“用大白话举个例子解释这段”）。
   - 默认会结合**当前文章 + 你的历史资料库**回答；
   - 如果需要查最新外部信息，点一下伴读顶部的 **「联网搜索」** 即可同时联网查证。
4. **提炼卡片**：读完觉得有收获，点击文章顶部的黑色 **「提炼卡片」** 按钮，等待几秒后点击提示条里的 **「立即去审阅编辑 →」**。

### 第二步：在「知识卡片」审阅、编辑和挑选

1. **审阅新卡片**：刚提炼出的卡片会带一个小琥珀圆点排在最前面。点开后右侧会滑出阅读抽屉，你可以看到完整内容：
   - 觉得措辞不够顺？在抽屉底部直接输入一句指令（比如“再精简一点”、“口语化一点”）点 **「AI 润色」**；
   - 想自己微调？点右上角 **「编辑」** 直接改标题、一句话结论或正文；
   - 满意后点 **「确认存入卡片库」**，它就正式成为你的长期知识卡片。
2. **随时回看与编辑**：已存入的卡片整齐摊开在纸白画廊里，每张卡片都会展示核心结论和 2–3 个正文要点关键词。按顶部的**专题 / 类型 / 排序 / 搜索框**可以快速找卡；点开任何卡片都能看全文、继续编辑，或点底部「来源原文」一键跳回原文章。
3. **挑卡片进「选题篮」**：鼠标移到画廊卡片右上角，点小圆圈勾选几张你想组合使用的卡片，页面底部会浮现黑色 **选题篮**，点击 **「带着去专题输出 →」**。

### 第三步：在「专题输出」组合观点、写研究文稿

1. **带着卡片直接开聊**：刚刚挑好的卡片已经自动挂在输入框上方。你可以直接输入想法，比如：
   - “结合这几张卡片，帮我列一份 5 分钟面试回答框架”
   - “把这几篇里的不同观点对比一下，指出各自的适用场景”
2. **按需切换模式**：
   - **仅个人资料库**：只基于你的个人资料和卡片来梳理，不掺杂外部杂音；
   - **联网 + 资料库**：自动同时查你的资料库和外部网页，回答里会用 `[K]`（你的资料）和 `[W]`（网页来源）标清出处，点右上角 **「参考依据」** 可以随时核对每条来源；
   - **扫描跨篇关联**：不知道写什么专题时，点一下它会帮你翻出最近保存内容之间的隐藏联系。
3. **把好回答存下来**：如果某一轮输出写得特别好，点回答下方的 **「存为笔记」** 或 **「做成卡片」**，就能重新沉淀回资料库或卡片库，形成正向循环。

---

## 四、进阶玩法（按需开启）

- **在微信 / 飞书里随时发文章**：如果你希望通勤时在手机上随手发链接或语音给 Hermes，可以在 `hermes-agent-main` 里按微信/飞书配置启动消息网关（`uv run hermes gateway run`）。手机端发的链接会先整理成草稿，回到电脑打开网页端慢慢精读即可。
- **配合 Obsidian 一起用**：在 `hermes-custom/config.yaml` 中把 `vault.path` 指向你的 Obsidian 仓库目录，Hermes 保存的文章本身就是标准 Markdown 文件，随时可以在 Obsidian 里打开浏览。
- **备份你的全部知识**：定期把 `hermes-custom/data/` 整个文件夹（里面包含你的文章文件和 `captures.db` 数据库）复制一份备份即可完整保留所有资料、卡片和关联。

---

## 五、常见问题速查

- **打开网页提示连不上知识库 / 报 503**
  - 先确认第 3 步的 `8000` 端口终端窗口还在运行；
  - 再核对 `HERMES_API_TOKEN`（第 3 步）和 `~/.hermes/.env` 里的 `HERMES_KNOWLEDGE_TOKEN`（第 4 步）是不是完全一致（注意不要多空格）。
- **搜不到刚刚存进去的文章**
  - 检查 `config.yaml` 里的 `qwen.api_key`（阿里云百炼密钥）是否填写正确；如果是后来才补填的密钥，在 `hermes-custom` 里跑一次 `.venv/bin/python scripts/backfill.py` 即可补齐语义索引。
- **每次关机重启后怎么再打开？**
  - 不用重新安装依赖！只需要开两个终端，分别运行第 3 步的 `uvicorn` 启动命令和第 4 步的 `dashboard` 启动命令，然后打开 `http://127.0.0.1:9119` 即可。
- **希望把服务部署到云服务器远程访问？**
  - 务必设置高强度的随机 `HERMES_API_TOKEN`，并通过反向代理启用 HTTPS，同时配置 `HERMES_API_ALLOWED_HOSTS` 限制允许访问的域名，切勿把无密码保护的服务直接暴露到公网。

---

> 如果你是开发者，想深入了解系统架构、全部 HTTP 接口清单、测试方法或运维排障脚本，请继续查看 [ARCHITECTURE.md](ARCHITECTURE.md)、[RUNBOOK.md](RUNBOOK.md) 与 [SETUP.md](SETUP.md)。

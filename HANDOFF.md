# Hermes Custom — Personal AI Assistant 交接文档

> 最后更新：2026-05-27

> **当前状态提醒（2026-06-19）：** 这是一份历史交接文档，不再是唯一运行手册。里面关于“5 个 Agent”“5 张表”“112 个测试”和 `dashboard/` 端口 3000 的描述已经过时。继续开发前，优先以 `SETUP.md`、`CONTEXT.md`、`db/schema.py`、`tests/`、`dashboard/DEPRECATED.md` 和 `docs/superpowers/plans/2026-06-19-hermes-knowledge-stabilization-roadmap.md` 为当前依据。本文件保留历史上下文，不应被当作实时架构清单。

## 项目概述

基于 Hermes Agent 框架构建的个人 AI 知识管理助手。用户通过微信/飞书发送链接或文本，Agent 提取、清洗、生成草稿供确认，用户满意后存入本地 Obsidian vault。

## 架构

```
微信/飞书 → Hermes Gateway (/Users/pipi/Downloads/hermes-agent-main)
              ↓
         AI Agent (deepseek-v4-pro) — Supervisor 路由
              ↓
         5 个 Agent（见"知识库 Agent 体系"一节）
              ↓
         bridge/ — 薄桥接层（含 hydration）
              ↓
         agents/ — 核心逻辑
              ↓
         ~/Obsidian/hermes-vault/ — 本地 Markdown 文件
              ↓
         SQLite (captures.db) — 草稿 + 向量 + 复习调度
```

## 目录结构

```
/Users/pipi/hermes-custom/           # 本项目
├── config.yaml                      # LLM密钥、vault路径、数据库路径
├── agents/
│   ├── capture_agent.py             # 草稿工作流：create_draft→revise_draft→commit_draft
│   ├── markdown_writer.py           # YAML frontmatter + Markdown 读写（支持 stage=raw/captures）
│   ├── cleaner.py                   # 广告过滤、HTML清洗、格式整理
│   ├── summarizer.py                # LLM 摘要 + 标签生成（从 captures/ 目录扫描已有标签）
│   ├── extractors/                  # 内容提取器（网页、文本、语音、图片）
│   ├── embedder.py                  # Qwen 向量嵌入
│   ├── rag_agent.py                 # RAG 问答
│   ├── review_agent.py              # 艾宾浩斯复习调度
│   ├── garden_agent.py              # 知识花园卡片
│   ├── theme_agent.py               # 主题聚类
│   └── outliner.py                  # 长文大纲
├── bridge/
│   ├── capture_bridge.py            # 4个桥接函数（见"桥接函数"一节）
│   ├── rag_bridge.py                # RAG 搜索（含 hydration）
│   ├── review_bridge.py             # 每日回顾 cron 入口（含 hydration）
│   ├── theme_bridge.py              # 主题聚类 cron 入口（含 hydration）
│   └── garden_bridge.py             # 知识卡片合成入口（含 hydration）
├── db/
│   ├── schema.py                    # SQLite 表定义（5张表）
│   └── repo.py                      # 数据访问层（CaptureRepo + CaptureDraftRepo）
├── api/
│   └── routes/captures.py           # FastAPI 查询接口
├── dashboard/                       # Next.js 仪表盘（port 3000）
├── feishu_tools/                    # 飞书 API 工具（已废弃，保留备用）
├── tests/                           # 测试（pytest，112 个，全部通过）
├── docs/superpowers/
│   ├── specs/                       # 设计文档
│   └── plans/                       # 实现计划
├── data/
│   ├── captures.db                  # SQLite 数据库
│   └── rag.log                      # RAG 调用日志（query/命中数/来源）
└── HANDOFF.md                       # 本文件

/Users/pipi/Downloads/hermes-agent-main/  # Hermes 框架
├── tools/capture_tool.py                 # capture_content 工具注册
├── tools/refine_capture_tool.py          # refine_capture 工具注册（别名→revise）
├── tools/revise_capture_draft_tool.py    # revise_capture_draft 工具注册
├── tools/commit_capture_draft_tool.py    # commit_capture_draft 工具注册
├── tools/search_notes_tool.py            # search_my_notes 工具注册（RAG）
├── tools/theme_scan_tool.py              # theme_scan 工具注册
├── tools/synthesize_garden_tool.py       # synthesize_garden 工具注册
├── tools/daily_review_tool.py            # daily_review 工具注册
├── toolsets.py                           # personal-assistant 工具集
└── .venv/                                # Hermes 虚拟环境

~/Obsidian/hermes-vault/             # Obsidian vault（知识库内容）
├── raw/                             # 已确认入库的原始提取（commit_draft 写入）
├── captures/                        # 加工好的笔记（YAML frontmatter）
├── themes/                          # 主题聚类
├── garden/                          # 知识花园卡片
├── _templates/                      # Obsidian 模板
└── attachments/                     # 附件
```

## 三阶段草稿工作流（当前设计）

旧文档描述的"两步"已演进为"草稿确认"模式：所有内容先进草稿区，用户满意后再入正式库。

```
第1阶段：用户发链接/文本（可附带指令，也可不带）
  → capture_content(content, instruction?)
  → agent.create_draft()
  → 无指令：草稿 status=raw_extracted，不写 Obsidian，询问"想怎么整理？"
  → 有指令：LLM 按指令生成草稿，status=draft_ready，展示草稿预览

第2阶段：用户要求修改
  → revise_capture_draft(draft_id?, instruction)
  → agent.revise_draft()
  → LLM 重新处理 → 更新草稿 → 再次展示预览
  → 可多轮修改，草稿只在 capture_drafts 表中，不碰 Obsidian

第3阶段：用户说"存库"/"可以"/"就这样"
  → commit_capture_draft(draft_id?)
  → agent.commit_draft()
  → 写 raw/{date}-{slug}.md（最简 frontmatter）
  → 写 captures/{date}-{slug}.md（完整 frontmatter）
  → DB insert 到 captures 表
  → async embed
```

### 桥接函数（bridge/capture_bridge.py）

| 函数 | 对应 Agent 方法 | 说明 |
|------|----------------|------|
| `capture_content(content, instruction?)` | `agent.create_draft()` | 提取+可选生成草稿 |
| `revise_capture_draft(draft_id?, instruction)` | `agent.revise_draft()` | 修改草稿 |
| `commit_capture_draft(draft_id?)` | `agent.commit_draft()` | 确认入库 |
| `refine_capture(draft_id?, instruction)` | → `revise_capture_draft()` | 旧工具名别名 |

草稿预览有 1800 字符上限（微信限制），但完整草稿保存在 DB；commit 时写入完整版。

### Obsidian 笔记格式

**raw/（最简）**
```yaml
---
id: "abc123"
title: "原文标题"
date: 2026-05-26
source: "https://mp.weixin.qq.com/s/xxx"
source_type: "wechat"
status: raw
---
正文内容...
```

**captures/（完整）**
```yaml
---
id: "abc123"
title: "原文标题"
date: 2026-05-26
created: 2026-05-26T14:00:00
source: "https://mp.weixin.qq.com/s/xxx"
source_type: "wechat"
tags:
  - ai/llm
  - china-tech
story: "一篇关于...的深度分析"
word_count: 3200
---
加工后的正文...
```

## 知识库 Agent 体系

5 个 Agent 各司其职，bridge 层统一负责从 .md 文件 hydrate 数据（DB 只存路由字段）：

| Agent | 功能 | Hermes 工具 | 触发方式 | 状态 |
|-------|------|------------|---------|------|
| `capture_agent` | 草稿捕获工作流 | `capture_content` / `revise_capture_draft` / `commit_capture_draft` | 用户发消息 | ✅ |
| `rag_agent` | 知识库语义搜索 | `search_my_notes` | 用户提问 | ✅ |
| `review_agent` | 艾宾浩斯复习调度 | `daily_review` | cron 或手动触发 | ✅ |
| `theme_agent` | 主题聚类 + 提问生成 + wiki-link 注入 + 图谱洞察 | `theme_scan` / `graph_report` | cron 或手动触发 | ✅ 全部 4 层完成 |
| `garden_agent` | 知识卡片合成 | `synthesize_garden` | 用户指令或响应主题提问 | ✅ |

### Agent 间协作流程

```
capture_content → 存草稿 → revise → commit → 入库
                                              ↓
              theme_scan（cron）→ 发现聚类 → 生成 pending_question
                                              ↓
              用户回复"好的" → synthesize_garden(question_id) → 生成 Garden Card
                                              ↓
              review_agent（cron）→ 推送 garden_review / capture_review
```

### hydration 约定

所有 bridge 在把 DB row 传给 agent 前，都调用 `_hydrate_capture(vault_path, row)` 从 .md 文件补充：
- `title` ← frontmatter.title
- `summary` / `story` ← frontmatter.story
- `tags` ← frontmatter.tags
- `cached_text` / `body` ← markdown body

## SQLite 表结构

5 张表，**所有文本元数据以 .md frontmatter 为唯一真相源**，DB 不存标题/摘要/标签。

| 表 | 用途 | 关键列 |
|----|------|--------|
| `captures` | 已入库笔记的路由+复习 | id, file_path, raw_path, embedding, review_state, review_interval, review_count, last_reviewed, next_review, theme_scanned_at, created_at, updated_at（12 列） |
| `capture_drafts` | 待确认草稿 | id, raw_text, source, source_type, title, word_count, draft_text, instruction, summary, tags, status, committed_capture_id, created_at, updated_at（14 列） |
| `garden_cards` | 知识花园卡片 | id, title, one_liner, core_arguments, knowledge_links, key_materials, my_view, status, capture_ids, review 字段 |
| `pending_questions` | Agent 待跟进问题 | id, agent_type, question_text, context_ref, status, related_capture_ids, matched_response, expires_at |
| `change_log` | 字段级变更日志 | entity_type, entity_id, field, old_value, new_value, source |

`capture_drafts.status` 生命周期：`raw_extracted` → `draft_ready` → `committed`（或 `abandoned`）

## 配置

```yaml
# /Users/pipi/hermes-custom/config.yaml
llm:
  provider: deepseek
  model: deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key: "sk-..."

vault:
  path: "~/Obsidian/hermes-vault"

qwen:
  api_key: "sk-..."  # 用于向量嵌入

database:
  path: "/Users/pipi/hermes-custom/data/captures.db"
```

## 运行命令

```bash
# 启动 Hermes 网关（用户说的"前后端"就是这个）
cd <hermes-agent-main>
.venv/bin/python -m hermes_cli.main gateway run --replace   # Windows: .venv\Scripts\python.exe

# 跑测试（当前全量 1290+ 用例，以实际收集数为准）
cd <hermes-custom>
.venv/bin/python -m pytest tests/ -q                         # Windows: .venv\Scripts\python.exe -m pytest tests -q

# 启动 FastAPI 后端（Dashboard API，默认仅本机，port 8000）
cd <hermes-custom>
.venv/bin/python -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8000

# 如确需监听局域网，必须先设置高强度随机 token；客户端使用 Bearer token
HERMES_API_TOKEN='<replace-with-random-token>' \
  .venv/bin/python -m uvicorn api.main:create_app --factory --host 0.0.0.0 --port 8000

# 启动 Next.js 仪表盘（port 3000）
cd /Users/pipi/hermes-custom/dashboard
npm run dev

# 查看 RAG 调用日志
tail -f data/rag.log
```

## 已知问题

1. **Hermes 网关需重启才能加载新代码**：`gateway run --replace` 模式下 kill 进程会自动重启。如未使用 `--replace`，需手动重启。

2. **DB schema 迁移**：`schema.py` 内有自动迁移逻辑（`_migrate_captures_table`），一般无需手动删库。若出现列不存在的错误，先删旧库：
   ```bash
   rm -f /Users/pipi/hermes-custom/data/captures.db*
   ```

3. **标题为"未命名"**：纯文本输入无标题时，slug 生成 `untitled`。标题提取逻辑在 `_derive_title()`（capture_agent.py 底部）。

4. **E2E 测试需要 Hermes venv**：`tests/test_e2e_hermes_tools.py` 的 Layer 1 测试需要 Hermes venv（Python 3.11）才能运行，默认 pytest 会跳过。Layer 2（bridge 层）测试在 .venv 下正常通过。

## 计划完成度

### 实现计划（草稿工作流 + DB 瘦身）

`docs/superpowers/plans/2026-05-26-two-step-capture-and-slim-db.md`（7 个 Task，全部完成）

| Task | 状态 |
|------|------|
| 1. markdown_writer stage 参数 | ✅ |
| 2. DB schema 瘦身 | ✅ |
| 3. capture_agent 草稿工作流 | ✅ |
| 4. bridge 更新 | ✅ |
| 5a. summarizer 标签来源 | ✅ |
| 5b. RAG 读 .md | ✅ |
| 5c. review_agent 标题 | ✅ |
| 6. 目录 + 测试 | ✅ |
| 7. 全量测试（112 passed） | ✅ |

### 多 Agent 体系补全

| 改动 | 文件 | 说明 |
|------|------|------|
| theme_bridge hydration | `bridge/theme_bridge.py` | 加 `_hydrate_capture()`，从 .md 补充 title/summary/tags/cached_text |
| garden_bridge 新建 | `bridge/garden_bridge.py` | 知识卡片合成入口，支持 capture_ids 或 question_id 触发 |
| synthesize_garden 工具 | `hermes-agent-main/tools/synthesize_garden_tool.py` | 注册到 personal-assistant 工具集 |
| garden_bridge 测试 | `tests/test_garden_bridge.py` | 3 个测试（hydration + 错误处理） |

## 技术决策记录

| 决策 | 原因 |
|------|------|
| 飞书→本地 Markdown | 飞书 API 不可靠（块级编辑失败、delete 用错端点、URL 漂移） |
| SQLite 瘦身到 12 列（captures 表） | .md frontmatter 是更好的元数据存储，Dataview 能查，避免重复 |
| 草稿确认工作流（create→revise→commit） | 用户需要在入库前审阅和修改内容，不是全自动 |
| capture_drafts 独立表 | 草稿生命周期与正式 captures 不同，独立管理更清晰 |
| Supervisor + Agent 模式 | 所有业务逻辑在 Python function agent，不依赖 Hermes delegate_task |
| 1800 字符微信预览截断 | 微信客户端显示有字数限制；完整草稿保留在 DB，commit 时写完整版 |
| RAG 输出微信适配 | 回答≤600字 + 来源精简 + 总长≤900字；handler 层加指令保护引用格式不被 LLM 改写 |
| 草稿状态机强制校验 | revise/commit 检查 committed/abandoned 状态，防止误操作已入库草稿 |

## Theme Agent 增强进度（2026-05-27）

设计文档：`docs/superpowers/plans/2026-05-27-theme-agent-enhancement.md`

| Layer | 功能 | 状态 |
|-------|------|------|
| L1 (P0) | Wiki-Link 注入 — `append_links()` + `_inject_wiki_links()` | ✅ 完成 |
| L2 (P1) | 主题 MOC 页 — `themes/` 自动创建 | ✅ 完成 |
| L3 (P2) | 主题演化追踪 — MOC 页持续更新 | ✅ 完成 |
| L4 (P3) | 图谱洞察 — cron 定期报告 | ✅ 完成 |

**L1 改动：**
- `agents/markdown_writer.py` 新增 `append_links(vault_path, file_path, links)` — 读 .md，找或创建 `## 相关笔记` 段落，追加 `[[标题]] — 理由` 格式链接，自动去重
- `bridge/theme_bridge.py` 新增 `_inject_wiki_links(vault_path, cluster)` — scan 后自动双向注入链接

**L2 改动：**
- `agents/markdown_writer.py` 新增 `write_moc(vault_path, theme_name, body, tags, capture_count)` — 写入 `themes/{slug}.md`，带 type: theme frontmatter
- `agents/theme_agent.py` 新增 `generate_theme_name(captures)` + `build_moc(theme_name, captures)` — LLM 生成主题名、描述、开放问题
- `bridge/theme_bridge.py` 新增 `_create_moc(vault_path, agent, cluster)` — 聚类 ≥3 篇时自动创建 MOC 页

**L3 改动：**
- `agents/markdown_writer.py` 新增 `read_moc()` + `update_moc()` — 读取 MOC，追加新笔记到相关笔记 + 更新日志，递增 captures 计数，自动去重
- `agents/theme_agent.py` 新增 `match_to_themes(vault_path, new_cap)` — 通过 tag 重叠匹配到已有 MOC（≥2 个共同 tag 视为匹配）
- `bridge/theme_bridge.py` 新增 `_evolve_themes(vault_path, agent, unclustered_caps)` — 未聚类的新 capture 匹配已有 MOC 后调用 update_moc

**L4 改动：**
- `agents/theme_agent.py` 新增 `graph_report(vault_path)` — 扫描全部 captures 和 themes，统计总笔记、主题数、已连接百分比、孤岛笔记（无 [[wiki-link]]）、桥接笔记（被 ≥2 个 MOC 引用）
- `bridge/theme_bridge.py` 新增 `run_graph_report()` — cron 入口，加载 config 后调用 graph_report
- `hermes-agent-main/tools/graph_report_tool.py` 新增 `graph_report` 工具，注册到 `personal-assistant` 工具集
- `hermes-agent-main/toolsets.py` 在 `personal-assistant` 工具集中添加 `graph_report`

**测试**：150 passed, 2 skipped（新增 38 个：L1 10 + L2 10 + L3 12 + L4 6；e2e 工具集测试也更新以包含 graph_report）

## 多媒体 Capture 支持（2026-05-28）

| 模态 | 支持方式 | 状态 |
|------|---------|------|
| 链接（微信/小红书/普通网页） | LinkExtractor 自动抓取 | ✅ 原有 |
| 图片（小红书截图等） | ImageExtractor → 千问 VL OCR → raw_text → 草稿流程 | ✅ 新增 |
| 语音（微信语音消息） | iLink 已做 ASR → 转写文本作为普通消息 → TextExtractor → 草稿流程 | ✅ 新增路由 |
| 音频文件（.mp3/.silk 等） | VoiceExtractor → 千问 Paraformer ASR → raw_text → 草稿流程 | ✅ 新增 .silk 支持 |
| 纯文本 | TextExtractor 兜底 | ✅ 原有 |

**改动：**
- `hermes-agent-main/tools/capture_tool.py` — 更新 tool description，告诉 supervisor 处理图片路径和语音转写文本
- `agents/extractors/voice_extractor.py` — can_handle() 新增 `.silk`（微信语音格式）
- `tests/test_media_capture.py` — 新增 12 个测试覆盖图片 OCR、语音转写、提取器路由

**测试**：162 passed, 2 skipped（新增 12 个）

**Hermes 网关 STT 配置**（2026-05-28）：
- `hermes-agent-main/tools/transcription_tools.py` 新增 `paraformer` provider — 用 DashScope 异步 API（支持最长 4 小时音频）
- 使用 `qwen3-asr-flash-2026-02-10` 模型
- 自动检测 .silk/.amr/.opus 格式并用 ffmpeg 转 .wav（微信语音是 .silk）
- >9MB 文件自动分片（10 分钟/段），逐段异步提交 + 轮询结果 + 拼接
- `~/.hermes/config.yaml` 配置 `stt.provider: paraformer` + DashScope API key + 新模型
- 更新 supervisor system_prompt，加入语音/图片/音频路由规则

## 本轮改动（2026-05-27）

| 改动 | 说明 |
|------|------|
| Python 升级到 3.11 | `.venv` 虚拟环境（uv 管理），解决 hermes-agent-main 的 `Callable \| None` 语法不兼容 |
| DB 脏数据清理 | 删除 16 条飞书遗留记录 + 16 条重复 file_path，剩余 13 条干净数据 |
| 草稿状态机修复 | `revise_draft` / `commit_draft` 检查 committed/abandoned 状态（+3 测试） |
| RAG 引用格式保护 | LLM prompt 要求 [编号] 引用 + handler 层强指令 + 微信友好长度控制 |
| RAG 调用日志 | `data/rag.log` 记录每次查询、命中数、来源 |
| 向量搜索去重 | `db/vector_search.py` 按 file_path 去重，避免同一文章多次出现 |
| daily_review 工具 | review_agent 可通过 Hermes 手动触发每日复习 |
| theme_agent 单元测试 | 新增 10 个测试覆盖三级过滤、提问生成、pending_questions |

## 外部依赖

| 服务 | 用途 |
|------|------|
| DeepSeek API (api.deepseek.com) | LLM 摘要、标签、加工（model: deepseek-v4-pro） |
| Qwen API (dashscope.aliyuncs.com) | 文本向量嵌入 |
| Hermes Gateway | 微信/飞书消息接入 |
| lark-cli | 飞书 CLI 工具（已安装，用于文档操作） |

## 参考文档

- 实现计划（已更新，含完成状态）：`docs/superpowers/plans/2026-05-26-two-step-capture-and-slim-db.md`
- 设计规格（草稿工作流设计背景）：`docs/superpowers/specs/2026-05-26-two-step-capture-and-slim-db.md`
- 飞书迁移决策（历史参考）：`docs/superpowers/specs/2026-05-26-feishu-to-obsidian.md`

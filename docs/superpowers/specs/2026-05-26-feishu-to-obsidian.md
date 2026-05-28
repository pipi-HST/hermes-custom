# 飞书知识库存储迁移至本地 Markdown

> **Goal:** 移除飞书文档作为内容存储，切换为本地 Markdown + Obsidian vault，Agent 与文件系统直接交互。

**Architecture:** Agent 写本地 `.md` 文件（带 YAML frontmatter）→ Obsidian 打开同一 vault → 元数据保留在 SQLite。云部署时 web 渲染层独立于 Agent 代码。

**Tech Stack:** Python pathlib, PyYAML, Obsidian vault

---

## 一、目录结构

```
~/Obsidian/hermes-vault/
├── captures/              # 捕获笔记 — Agent 主要写入目标
├── themes/                # 主题聚类
├── garden/                # 花园卡片（深度合成）
├── _templates/            # Obsidian 模板
└── attachments/           # 图片、附件
```

用阶段（captures/themes/garden）做文件夹——代表笔记生命周期，不轻易变。标签做分类，多对多灵活组合。

## 二、单篇笔记格式

```markdown
---
id: "b74d8071576c"
title: "刚刚，国产AI自己造了AI，全球首例！"
date: 2026-05-26
created: 2026-05-26T13:40:44
source: "https://mp.weixin.qq.com/s/Esk0AFhP-apyWY8eWruv-Q"
source_type: "wechat"
tags:
  - ai/llm
  - ai/agent
  - china-tech
story: "面壁智能发布AI自主编写框架ForgeTrain，提出Forge Engineering范式"
word_count: 3973
---

面壁智能发布全球首个完全由AI编写的大模型预训练框架...

## 核心事件
...
```

### Frontmatter 字段

| 字段 | 说明 | 来源 |
|------|------|------|
| `id` | SQLite capture_id | Agent 生成 |
| `title` | 标题 | extracted / LLM |
| `date` | Obsidian 日记字段 | 采集日期 |
| `created` | ISO 时间戳 | 采集时间 |
| `source` | 原文 URL | extractor |
| `source_type` | 来源类型 | extractor |
| `tags` | 嵌套标签列表 | summarizer |
| `story` | 核心摘要 | summarizer |
| `word_count` | 正文字数 | cleaner |

### 标签格式

使用嵌套标签 `ai/llm` 格式——Obsidian 原生支持，右侧面板自动分组：

```yaml
tags:
  - ai/llm
  - ai/agent
  - china-tech
  - case-study
```

### 文件命名

```
captures/{YYYY-MM-DD}-{slug}.md
```

`slug` 由标题提取，Agent 生成英文简写（空格转连字符，去标点，保留 ASCII）。

## 三、SQLite 元数据层

不动现有 schema，新增 `file_path` 字段：

| 字段 | 旧值（飞书） | 新值（Markdown） |
|------|------------|-----------------|
| `feishu_doc_id` | doc token | **移除** |
| `feishu_doc_url` | 飞书链接 | **移除** |
| `raw_doc_url` | 飞书原始版链接 | **移除** |
| `feishu_folder` | 飞书文件夹名 | tag 映射的目标文件夹名 |
| `file_path` | 不存在 | `captures/2026-05-26-forge-train.md` |

RAG 搜索时从 `summary` 字段检索，人类阅读时打开 `file_path` 对应的 `.md` 文件。

## 四、Agent 写入流程

```
process(raw_input)
  → extract → clean → check → summarize → tag
  → 生成 slug + frontmatter
  → Path(file_path).write_text(frontmatter + body)
  → repo.insert(file_path=file_path, summary=..., tags=..., ...)
  → async enrich (embedding, outline)
```

一行 `write_text()` 替代了原来的：
```
create_doc → write_doc_content → delete_doc → create_doc(rebuild) → blocks API → 更新 doc_id → 更新 doc_url
```

7 步变 1 步，没有网络调用，没有 ID 漂移，没有中间状态。

### 重新加工流程

```
reprocess(capture_id, instruction)
  → 读现在 .md 文件
  → LLM 按 instruction 处理
  → 覆盖写同一个 .md 文件
  → 更新 SQLite summary/tags
```

飞书 rebuild 导致 doc_id 变化 → URL 失效。本地文件路径不变，`file_path` 永远稳定。

## 五、要删除的代码

### 飞书 API 调用（`capture_agent.py`）

```python
# 删除
doc = create_doc(...)
new_id = write_doc_content(doc["doc_id"], display_text, ...)
raw_doc = create_doc(...)
new_raw_id = write_doc_content(raw_doc["doc_id"], raw_text, ...)

# 替换为
file_path = f"captures/{date_str}-{slug}.md"
vault_dir.mkdir(parents=True, exist_ok=True)
(Path(config.vault_path) / file_path).write_text(...)
```

### 配置（`config.yaml`）

```yaml
# 删除
feishu:
  app_id: "..."
  app_secret: "..."
  folder_token: "..."
  raw_folder_token: "..."

# 替换为
vault:
  path: "~/Obsidian/hermes-vault"
```

### 全文件可以不删的

`feishu_tools/feishu_doc.py` 保留但不再在流程中调用。未来若恢复"飞书分享"功能，作为可选桥接。

## 六、改动范围

| 文件 | 操作 | 说明 |
|------|------|------|
| `config.yaml` | 修改 | feishu → vault 配置 |
| `agents/capture_agent.py` | 修改 | 过程/再加 process 加文件写入逻辑 |
| `db/schema.py` | 修改 | 新增 file_path 字段，删除 feishu 字段 |
| `db/repo.py` | 修改 | insert/update 适配新字段 |
| `bridge/capture_bridge.py` | 修改 | 返回值移除 feishu_doc_url |
| `api/routes/captures.py` | 修改 | 移除 feishu 字段 |
| `agents/extractors/*` | 不改 | 提取逻辑与存储无关 |
| `agents/cleaner.py` | 不改 | 清洗逻辑与存储无关 |
| `agents/summarizer.py` | 不改 | 摘要标签与存储无关 |
| `agents/rag_agent.py` | 少改 | file_path 替代 feishu_doc_url |
| `feishu_tools/feishu_doc.py` | 不动 | 保留，未来可选 |

---

## 七、自检

- [x] 无占位符
- [x] 字段命名一致（file_path 贯穿所有文件）
- [x] 改动范围清晰，核心 agent 逻辑不动
- [x] 文件命名规则完整

# 两步捕获流程 + SQLite 瘦身

> **Goal:** 捕获改为两步（raw → 按指令加工 → captures），SQLite 只存向量和复习数据，.md frontmatter 为元数据唯一真相源。

**Architecture:** 提取后先存 raw/ 待加工，用户给出指令后 Agent 处理并自动打标签摘要，存入 captures/。SQLite 从 20 列瘦至 10 列。

---

## 一、两步捕获流程

```
微信/飞书发链接
  ↓
Agent 提取正文
  ↓
存入 raw/{date}-{slug}.md（只有标题 + 正文，最简 frontmatter）
  ↓
Agent 回复："已提取《xxx》，共 N 字。想怎么整理？"
  ↓
用户说"提炼重点" / "压缩到 500 字" / "翻译成英文" 等
  ↓
Agent 按指令处理 → 自动打标签 + 写摘要 → 存 captures/{date}-{slug}.md（完整 frontmatter）
  ↓
Agent 回复："已整理。摘要：xxx，标签：xxx"
```

### raw/ 笔记格式（最简）

```markdown
---
id: ""
title: "原文标题"
source: "https://mp.weixin.qq.com/s/xxx"
source_type: "wechat"
date: 2026-05-26
status: raw
---

提取的正文内容...
```

无标签、无摘要、无 story 字段。`status: raw` 标记未加工。

### captures/ 笔记格式（完整）

```markdown
---
id: "b74d8071576c"
title: "原文标题"
date: 2026-05-26
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

## 二、SQLite 瘦身

### 保留字段（10 列）

| 字段 | 原因 |
|------|------|
| `id` | 主键 |
| `file_path` | 指向 .md 文件 |
| `raw_path` | 指向 raw/ 原文件 |
| `embedding` | 向量搜索，文件系统不可替代 |
| `review_state` | 复习调度需要索引排序 |
| `review_interval` | 复习调度 |
| `last_reviewed` | 复习调度 |
| `next_review` | 复习调度，需要索引 |
| `created_at` | 时间戳 |
| `updated_at` | 时间戳 |

### 删除字段（从 frontmatter 读取）

`title`, `summary`, `tags`, `source_url`, `source_type`, `cached_text`, `raw_text`, `word_count`, `outline`

## 三、各组件改动

| 组件 | 改动 |
|------|------|
| **capture_agent.py** | process() 第一步只提取 → 写 raw/；新增 finalize() 第二步按指令加工 → 写 captures/ |
| **markdown_writer.py** | write_note 支持 stage=raw 写最简 frontmatter |
| **summarizer.py** | existing_tags 从扫描 captures/ 所有 .md frontmatter 获取 |
| **rag_agent.py** | 读 .md 文件 body 做全文源 |
| **repo.py** | insert 只写保留字段；list_all 扫描文件系统过滤 |
| **review_agent.py** | DB 查到期笔记，标题摘要从 .md 读 |
| **bridge/capture_bridge.py** | capture_content 返回"已提取，怎么整理"；新增 finalize_capture 对应第二步 |

## 四、Hermes 工具变化

Supervisor tool 路由：

```
用户发链接 → capture_content → Agent 提取 → 存 raw/ → 回问
用户说"提炼/压缩/翻译" → refine_capture → Agent 加工 → 打标签摘要 → 存 captures/
```

`refine_capture` 保持不变（它已经在做"按指令加工"这件事），但加工完成后自动打标签和摘要。

## 五、迁移

1. 删旧 captures.db
2. 改 schema、repo、capture_agent、bridge
3. 改 summarizer 的 tag 收集方式
4. 改 rag 和 review 的数据读取
5. 更新测试
6. 全量验证

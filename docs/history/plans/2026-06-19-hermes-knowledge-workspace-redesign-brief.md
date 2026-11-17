---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes Knowledge Workspace 重设计需求文档

日期：2026-06-19

本文档整理此前围绕 Hermes 个人知识库、飞书式文件夹体验、Obsidian 联动、旧 Hermes UI 迁移和 AI-native 知识工作台定位的讨论。目标是给新的设计/实现 AI 一个完整、清晰、可执行的 brief。

## 1. 产品定位

Hermes Knowledge 的目标不是再做一个聊天页，也不是单纯做 RAG 数据库管理界面。

核心定位：

> AI-native 的个人知识工作台：人负责捕捉和判断，AI 负责整理、关联、复用。

这个定位里，人是主导者，AI 是整理层和复用层。用户应该先能自由记录、自由修改、自由归类，然后 AI 再补标题、摘要、标签、关联、RAG 索引、Garden Card 和复用建议。

设计上必须避免让用户感觉自己在“操作一条复杂 pipeline”。用户心智应该更像：

- 我打开一个知识工作台。
- 我进入某个一级文件夹。
- 我看到里面有哪些文件。
- 我可以新建、粘贴、导入、修改、移动。
- AI 在旁边帮我整理，而不是挡在保存前面。

## 2. 当前核心问题

此前用户更愿意把内容放到飞书，而不是放进 Hermes 数据库，原因不是简单的“用户习惯”或“信任不足”，而是 Hermes 缺少几个飞书式关键 affordance：

1. 缺少可见的文件夹结构。
2. 缺少随手新建和随手修改的自由度。
3. 缺少“我把这条内容放在哪里”的位置感。
4. 标题、分类、入库、检索等流程显得太重。
5. 用户看不清哪些是自己的判断，哪些是 AI 整理结果。
6. 旧 Hermes 工作台里状态、按钮、审阅、对话、工具调用混在一起，界面显得拥挤。
7. 原有 Garden/Theme/Capture 等概念对用户来说偏系统视角，不够贴近日常知识管理。

所以新 UI 的重点不是“再加几个按钮”，而是重新组织信息架构。

## 3. 已确认的设计原则

### 3.1 文件夹是人的结构

文件夹代表用户主动选择的位置。它解决的是“我把这条内容放在哪里”的心智问题。

文件夹不是 tag，也不是 AI 自动分类结果。

### 3.2 标签是 AI 辅助检索属性

标签可以由 AI 自动生成，用于搜索、过滤、RAG 辅助召回。

标签不能替代文件夹，因为标签是横向属性，文件夹是用户心智里的位置。

### 3.3 Garden/Theme 是 AI 发现的关联网络

Garden Card、Theme、关联图谱适合作为智能视图或右侧建议，不应该和用户文件夹混成一个概念。

### 3.4 AI 建议不阻塞保存

AI 可以建议文件夹、标签、关联、卡片，但不应该挡在用户保存之前。

默认流程应该是：

1. 用户先保存到 Inbox 或某个一级文件夹。
2. AI 生成标题、摘要、标签、关联建议。
3. 用户选择是否采纳建议。

### 3.5 不新增独立知识库

新 UI 应继续复用已有 pipeline：

Capture -> LibraryAgent -> RAG

短文本/长文本的 RAG 索引规则继续复用旧规则：

- 短文本按整体检索。
- 长文本按已有章节/结构规则检索。

文件夹只影响组织和过滤，不应该破坏 RAG indexing。

## 4. 文件夹模型

### 4.1 只做一级文件夹

经过讨论后，当前阶段不应该鼓励多层文件夹。

推荐模型：

```text
Inbox
AI 产品
数据 Agent
待读
临时
```

文件夹下面直接放文件，不做二级文件夹。

原因：

- 个人知识库早期不需要复杂目录树。
- 多级文件夹会增加维护成本。
- 用户现在更需要的是快速放置和快速找回。
- AI 可以帮忙建议一级分类，但不应该创建一堆深层目录。

### 4.2 Inbox 是默认入口

用户没有选择文件夹时，默认进 Inbox。

Inbox 的作用是临时收纳，不是正式分类。

### 4.3 智能视图不是文件夹

以下内容应该作为智能视图，而不是文件夹：

- 最近入库
- 待审阅
- 有 AI 建议
- Garden Cards
- 相关知识
- 未索引

智能视图可以跨文件夹聚合内容。

### 4.4 点击一级文件夹后的行为

这是新 UI 必须满足的核心交互：

用户点击一级文件夹后，中间主区域应该展示这个文件夹下面的所有文件内容。

不是只显示一个空的分类状态，也不是只切换标题。

推荐展示方式：

- 文件标题
- 摘要或正文预览
- 来源类型
- 状态
- 更新时间
- 标签

用户点击某个文件后，再进入详情阅读/编辑状态。

## 5. Obsidian 联动方案

当前 Hermes 的 Obsidian 物理存储有系统目录语义，例如：

```text
captures/
_archive/raw/
Garden Card/
```

而新 Hermes 前台文件夹是用户工作台语义，例如：

```text
Inbox
AI 产品
数据 Agent
待读
临时
```

这两套语义不要强行一开始合并。

### 5.1 推荐短期方案：逻辑文件夹优先

MVP 阶段建议：

- Obsidian 物理文件仍保留在现有系统目录中。
- 前台文件夹写入 Markdown frontmatter。
- SQLite/metadata 同步读取 `folder_path`。

示例：

```yaml
title: AI 产品的笔记入口应该让人先控制
entry_mode: editable_capture
folder_path: AI 产品
tags:
  - ai-product
  - capture-flow
```

好处：

- 不破坏现有 Capture/Library/RAG pipeline。
- 不破坏 repair、watcher、file_path、raw_path 等已有机制。
- Hermes 前台可以立即显示一级文件夹。
- Obsidian 里也能通过 frontmatter 搜索或 Dataview 使用分类。

### 5.2 长期方案：可选物理目录镜像

后续可以考虑支持：

```text
captures/AI 产品/xxx.md
captures/数据 Agent/xxx.md
```

但这不是第一阶段。

要做物理目录镜像前，必须先确认：

- watcher 能识别用户在 Obsidian 里移动文件。
- repair 能同步新的 file_path。
- RAG 索引不因文件移动失效。
- raw 原文归档仍能和 capture note 关联。
- 重复检测不会因为路径改变出错。

### 5.3 Obsidian 移动文件时的处理

如果用户在 Obsidian 中手动移动文件，Hermes 应尽量同步：

- 如果能识别新路径和 frontmatter，更新 `file_path` 和 `folder_path`。
- 如果无法判断，放入 Inbox 或标记为“位置待确认”。
- 不要静默丢失。

## 6. 标题和自由编辑流程

用户希望粘贴整理页面一开始就支持输入标题。

规则：

- 用户可以在创建草稿前输入标题。
- 如果用户不输入标题，AI 自动生成。
- 如果用户输入标题，AI 不覆盖。
- `title_source=user|ai` 要保留。
- summary/tags 仍由 AI 生成。
- 入库仍走原有 Capture -> LibraryAgent -> RAG pipeline。

这部分已被实现过 P0：

- 前端 create 请求带 `title`。
- 后端 create editable draft 接收 `title`。
- Agent create editable draft 根据用户标题设置 `title_source=user`。

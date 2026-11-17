---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes Web 满血对话入口设计

日期：2026-06-14

## 背景

Hermes Knowledge 当前主要通过微信使用。微信适合随手发送链接、文件、图片和短指令，也适合做“生成草稿 / 展开草稿 / 存库”这类轻量确认。但微信不适合承载长答案、完整引用报告、长草稿审阅和工具调用过程展示。

这个设计的目标不是替代微信，也不是重做 Obsidian 或知识库后台，而是补一个电脑端入口：让用户在需要深度阅读、长答案和可视化证据时，可以在 Web 上和同一个 Hermes Knowledge 对话。

## 产品定位

Web GUI 是 Hermes Knowledge 的“满血对话入口”：

- 微信负责低摩擦输入、短反馈和确认动作。
- Web 负责长答案、引用展开、草稿审阅和工具调用过程可见。
- Obsidian 仍然是长期知识容器。
- Hermes 主 Agent 仍然是统一入口，不新增第二套业务 Agent。

## v0.1 范围

第一版只做“Web 满血对话 + 来源/工具侧栏”。

包含：

1. 对话区
   - 支持 Markdown 长回答。
   - 不按微信短回复预算压缩。
   - 支持继续追问、展开、存库、修改草稿等自然语言指令。

2. 来源证据栏
   - 展示 RAG 实际使用的材料。
   - 展示标题、章节、来源类型、Obsidian 路径或本地文件引用。
   - 支持展开命中片段。

3. 工具调用栏
   - 展示最近一轮调用了哪些工具。
   - 展示成功、失败、耗时、生成的 draft/capture/garden/file 等 artifact。
   - 用于调试，也用于面试演示“Agent 不是黑箱”。

4. 长输出处理
   - RAG 长答案和完整引用报告可以直接在 Web 中展示。
   - Capture 长草稿可以在 Web 中完整审阅。
   - 文件输出仍保留，但不作为 Web 的默认阅读方式。

不包含：

- 登录、多用户、权限系统。
- 完整知识库后台。
- Obsidian 文件浏览器替代品。
- 独立于 Hermes 的新 Agent runtime。
- 重新实现 Capture/RAG/Garden/Reading 业务逻辑。

## 架构选择

采用方案 A：复用 Hermes Web Chat，并增强知识库侧栏。

目标架构：

```text
Hermes Web Chat
  -> Hermes 主 Agent
  -> personal-assistant toolset
  -> hermes-custom bridge
  -> Capture / RAG / Reading / Garden / Library
  -> SQLite + Obsidian
```

关键原则：

- Web 和微信走同一套主 Agent、toolset、bridge 和业务模块。
- Web 不直接绕过 Hermes 去调用业务 API。
- Web 不复制一套“保存 / 检索 / 入库”的业务判断。
- Web 只是更适合长内容和结构化结果展示的交互表面。

## 数据与结果协议

当前 bridge 已经使用 envelope 风格返回工具结果。Web 需要消费比微信更多的结构化字段。

建议把工具结果按以下语义消费：

```text
chat_preview
  微信短回复或 Web 对话区的摘要回复。

full_content / durable_artifacts
  Web 可展开的完整回答、完整草稿、完整报告或文件引用。

sources
  右侧来源证据栏的数据来源。

meta
  draft_id、capture_id、session_id、状态、分页信息等机器可读信息。

next_actions
  可渲染成快捷按钮或操作提示。

agent_runs
  工具调用过程、耗时、状态和错误摘要。
```

如果现有 envelope 不足以支撑 Web 展示，应优先补 bridge 的结构化输出，而不是让前端解析自然语言。

## 页面结构

### 1. 对话区

用途：承载完整 AI 回答。

行为：

- 用户输入自然语言问题。
- 前端发给 Hermes Web Chat runtime。
- 主 Agent 按 `personal-assistant` 工具集路由。
- 回答支持 Markdown、代码块、标题、列表和引用编号。

### 2. 来源证据栏

用途：让用户知道答案依据是什么。

展示：

- 来源标题。
- 来源类型：capture / section / garden / theme / web / original。
- 命中章节。
- Obsidian 文件路径或链接。
- 命中片段。

规则：

- 只展示实际被答案使用的来源。
- 不展示裸 id 作为主信息。
- 裸 id 只能作为调试 meta。

### 3. 工具调用栏

用途：让 Agent 运行过程可见。

展示：

- 工具名。
- 状态：success / warning / error。
- 耗时。
- 输入摘要。
- 输出摘要。
- artifact。
- 错误原因和可重试动作。

数据来源优先使用 `agent_run_status` 和 `agent_runs.db`。

### 4. 草稿审阅模式（v0.2）

第一版不强做，但设计上保留入口。

目标：

- 左侧显示原文/来源片段。
- 右侧显示整理稿。
- 底部输入修改意见。
- 支持确认入库。

## 错误处理

Web 端不应该只显示“工具失败”。

失败时至少展示：

- 哪个工具失败。
- 失败发生在哪一步。
- 用户可以怎么继续。
- 是否已有可恢复 artifact，例如 draft_id、session_id、report_path。

例如：

```text
RAG 回答生成失败，但已经找到 4 条相关材料。
你可以重试，或先查看已命中的来源。
```

## 验收标准

v0.1 完成后，应能验证：

1. Web 入口可以使用 `personal-assistant` 工具集发起知识库对话。
2. RAG 长答案可以完整显示，不受微信短回复限制。
3. 来源栏展示实际引用材料，且材料名可读。
4. 工具调用栏能看到最近一轮调用状态。
5. Capture 草稿可以在 Web 中完整查看。
6. 微信和 Web 使用同一套后端业务逻辑，不出现两套入库/检索路径。

## 面试表达

可以这样解释：

> 微信入口解决低摩擦捕获，Web 入口解决深度阅读和长答案展示。两者不是两套产品，而是同一套 Hermes Knowledge 能力在不同场景下的两种交互表面。Web 复用 Hermes 主 Agent、personal-assistant 工具集和 bridge 层，因此不会绕开原有的草稿确认、来源追踪和运行观测机制。

## 开放问题

1. Hermes 原生 Web Chat 当前是否已经能稳定选择 `personal-assistant` toolset。
2. 当前 Web 前端能否直接读取 tool result 的结构化 envelope。
3. `agent_run_status` 是否足以支撑工具调用栏，还是需要新增按 session/run 查询的 API。
4. Web 对话是否需要独立 session，还是和微信共享同一个用户 session。


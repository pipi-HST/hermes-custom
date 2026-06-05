# Reading Session 阅读讨论会话设计

更新日期：2026-06-05

## 背景

现在 Hermes Knowledge 已经能完成“材料进入知识库”的主链路：用户发链接、文件、文本或图文材料，Capture 生成可审阅草稿，用户确认后入库，Library 补目录、章节、properties 和 embedding，后续 RAG、Theme、Garden、Review 再复用这些材料。

但还有一个真实阅读场景没有被很好覆盖：

> 用户正在读一篇公众号或长文章，读到某个概念、段落或论证不理解，想和 AI 多轮讨论。讨论几轮后，用户希望把原文相关内容、自己的问题、AI 的解释，以及最后形成的理解一起整理进知识库。

这个场景既不是普通 Capture，也不是普通 RAG：

- Capture 关注“把材料整理成可入库笔记”。
- RAG 关注“基于已有知识库回答问题”。
- Review 关注“把已有知识重新讲给用户听”。
- Garden 关注“把跨材料价值沉淀成可复用知识卡片”。

阅读讨论需要一个新的中间对象：它围绕一篇具体材料，保存多轮理解过程，并最终生成可入库的“阅读讨论笔记”。

## Capability

新增 **Reading Session / 阅读讨论会话** 能力。

用户可以围绕一篇文章、一个 capture、一个链接、一个截图或一段摘录发起阅读讨论。Hermes 在讨论过程中记录原文定位、用户问题、AI 解释、引用片段和阶段性理解。用户说“把刚才讨论整理进笔记”时，系统生成一篇可审阅的阅读讨论笔记草稿；用户确认后写入 Obsidian，并双链到原 capture。

目标不是保存聊天记录，而是保存“我读懂这篇材料的过程”。

## 非目标

第一版不做这些事：

- 不做专门阅读器 UI。
- 不自动把每次 RAG 追问都保存成阅读笔记。
- 不把讨论内容直接追加进原 capture 正文。
- 不默认生成 Garden card。
- 不替代 Capture 的入库流程。
- 不把微信完整聊天记录原样塞进 Obsidian。
- 不支持多人协同批注。

## 推荐方案

采用 **Reading Session 作为一等对象**。

有三种可能方案：

### 方案 A：只依赖当前聊天上下文

用户讨论几轮后说“整理刚才讨论”，Hermes 从当前会话历史里抽取内容生成笔记。

优点：实现最快。

缺点：依赖 Hermes 会话上下文，容易被旧消息污染；重启或上下文压缩后不稳定；无法可靠知道讨论对应哪篇文章、哪段原文。

### 方案 B：新增 Reading Session 状态

系统显式创建 reading session，把来源材料、问题、回答、引用片段和状态保存到 SQLite。最后基于 session 生成阅读讨论笔记草稿。

优点：稳定、可追溯、可测试；能和 Capture/RAG/Library/Garden 清晰分工；重启后仍能继续。

缺点：需要新增 DB 表、bridge 工具和 runtime 工具描述。

### 方案 C：做完整阅读器界面

左侧原文，右侧 AI 对话，支持选中文本提问和一键生成笔记。

优点：长期体验最好。

缺点：开发成本高，会把当前重点从知识库 pipeline 转到前端产品化，不适合作为第一版。

**推荐：方案 B。** 它比方案 A 稳，比方案 C 轻，最符合当前 Hermes Knowledge 的工具型架构。

## 用户故事

### 1. 从文章开始讨论

用户发：

> 这篇文章我看到 Terminal-Bench 那段没太懂，帮我解释一下：https://mp.weixin.qq.com/...

预期：

- 如果文章还没入库，系统先提取原文或创建 raw/capture draft。
- 系统创建 reading session。
- 系统定位用户提到的段落或概念。
- 回答解释，不直接入库。
- 回复里提示：可以继续追问，或说“整理这次阅读讨论”生成笔记。

### 2. 从已入库 capture 开始讨论

用户发：

> 上次那篇 Opus 4.8 的文章，41 天发版那里我还是没懂。

预期：

- 系统通过 RAG 找到相关 capture 和 section。
- 创建或恢复该 capture 的 active reading session。
- 围绕命中的 section 解释。

### 3. 从截图或摘录开始讨论

用户发一张截图，或粘贴一段原文：

> 这段话什么意思？

预期：

- 如果能识别对应 capture，则挂到该 capture。
- 如果无法识别来源，则创建 source_type=`excerpt` 的临时 reading session。
- 后续用户补链接或说“就是刚才那篇文章”时，可以合并来源。

### 4. 多轮讨论后生成笔记

用户发：

> 把刚才讨论整理进笔记。

预期：

- 系统生成阅读讨论笔记草稿。
- 微信只返回短预览。
- 完整草稿保存在 DB 或文件输出中。
- 用户确认后写入 Obsidian。

### 5. 从讨论中提炼 Garden card

用户发：

> 这个理解挺重要，把它做成知识卡片。

预期：

- Reading Session 不直接写 Garden。
- 主 Agent 可把当前 session 摘要、来源 capture、关键问答交给 Garden。
- Garden 仍然走草稿与重复检查流程。

## 核心对象

### Reading Session

一次围绕某个材料的阅读讨论。

建议字段：

- `id`
- `source_type`: `capture | draft | raw | url | excerpt | image`
- `source_id`
- `source_url`
- `source_title`
- `status`: `active | summarized | committed | archived`
- `focus`: 用户当前讨论焦点，例如“Terminal-Bench 段落”“国家能力概念”
- `created_at`
- `updated_at`
- `last_turn_at`
- `committed_note_id`
- `meta_json`

### Reading Turn

一次用户问题和 AI 回答。

建议字段：

- `id`
- `session_id`
- `turn_index`
- `user_message`
- `assistant_answer`
- `focus`
- `cited_source_json`: 引用的 capture section、raw excerpt 或 URL 片段
- `confidence`: `high | medium | low`
- `created_at`
- `meta_json`

### Reading Note Draft

把一个 session 整理成可入库笔记前的草稿。

建议字段：

- `id`
- `session_id`
- `title`
- `summary`
- `draft_text`
- `status`: `open | committed | discarded`
- `created_at`
- `updated_at`

第一版可以复用现有 draft pattern，但不要混进 `capture_drafts`，避免 Capture 草稿和讨论笔记草稿语义混乱。

## Obsidian 笔记形态

阅读讨论笔记建议放在：

```text
reading/
```

文件名格式：

```text
阅读讨论-原文标题-YYYY-MM-DD-短id.md
```

笔记结构：

```markdown
---
type: reading_note
source_capture_id:
source_title:
source_url:
reading_session_id:
created:
status: active
---

# 阅读讨论：原文标题

来源：[[原文 Capture]]

## 我卡住的地方

> 原文摘录或段落摘要

## 讨论记录

### 问题 1：用户原问题

Hermes 解释：

...

依据：
- [[原文 Capture#章节]]

### 问题 2：用户原问题

Hermes 解释：

...

## 最终理解

这次讨论后，可以把这部分理解为：

...

## 后续可复用点

- 可能沉淀为 Garden card 的观点、方法、事实或风险
```

原则：

- 不覆盖原 capture。
- 不把全文聊天记录原样入库。
- 保留关键问题、解释和最终理解。
- 原文依据必须可追溯。
- 如果来源是已入库 capture，必须双链到原文。

## 工具设计

第一版建议新增 5 个工具。

### `start_reading_session`

用途：围绕某篇材料开始阅读讨论。

输入：

- `message`
- `source_url`
- `capture_id`
- `draft_id`
- `excerpt`

输出：

- `reading_session_id`
- `chat_preview`
- `source_map`
- `next_actions`

行为：

- 如果有 URL 且未入库，可以调用现有提取能力获取原文，但不必立刻生成完整 Capture。
- 如果能匹配已有 capture，优先挂到 capture。
- 如果只有摘录，创建 excerpt session。

### `discuss_reading`

用途：围绕当前或指定 reading session 继续解释。

输入：

- `session_id`
- `message`
- `focus`

输出：

- AI 解释
- 引用片段
- 当前 session 状态

行为：

- 优先使用 session source。
- 若问题需要背景，可调用 RAG 查相关材料，但回答必须区分“原文依据”和“补充背景”。

### `show_reading_session`

用途：查看当前阅读讨论状态。

输出：

- 来源标题
- 当前焦点
- 最近几个问题
- 已引用片段
- 是否已有阅读笔记草稿

### `summarize_reading_session`

用途：生成阅读讨论笔记草稿。

输入：

- `session_id`
- `instruction`

输出：

- `reading_note_draft_id`
- 微信短预览
- 完整草稿可展开或文件化

行为：

- 默认生成“讨论笔记”，不是重新总结整篇文章。
- 必须保留用户问过的问题和最终理解。
- 必须标明引用来源。

### `commit_reading_note`

用途：确认阅读讨论笔记入库。

输入：

- `draft_id`

输出：

- Obsidian 文件路径
- source capture 链接
- Library 后处理状态

行为：

- 写入 `reading/`。
- 如果 source capture 存在，建立双向关系字段或 wiki 链接。
- 可触发 Library 对新 reading note 补 metadata。

## 与现有模块关系

### Capture

Capture 仍然负责材料提取和整理稿生成。Reading Session 不替代 Capture。

如果用户围绕一篇未入库文章讨论，第一版可先创建临时 source；当用户说“把原文也存库”时，再走 Capture 草稿确认流程。

### RAG

RAG 给 Reading Session 提供检索能力。区别是：

- 普通 RAG 回答用户问题。
- Reading Session 回答并记录“这次围绕某篇材料的理解过程”。

Reading Session 的回答可以调用 RAG，但必须把结果写回 session turns。

### Library

Library 负责 reading note 入库后的结构维护：

- properties 补齐。
- source capture 链接校验。
- 长阅读笔记可补目录。
- embedding 更新。

### Garden

Reading Session 不直接创建 Garden card。

如果讨论中出现可复用观点、方法、事实、风险或开放问题，主 Agent 可建议：

> 这次讨论里有一个可复用知识点，要不要做成 Garden card？

确认后交给 Garden 的现有草稿流程。

### Review

Reading note 可以成为 Review 候选，尤其适合“我曾经卡住、后来理解了”的概念。

Review 讲解时应能引用 reading note 和原 capture。

## 主 Agent 路由原则

应该进入 Reading Session 的用户表达：

- “这段什么意思？”
- “这个概念我没懂。”
- “这里作者想表达什么？”
- “这篇文章里 XX 那段解释一下。”
- “结合原文帮我理解一下。”
- “刚才讨论的整理成笔记。”

不应该进入 Reading Session 的表达：

- “总结这篇文章” → Capture。
- “基于我的知识库回答 XX” → RAG。
- “扫描最近内容整理成卡片” → Theme/Garden。
- “复习一个知识点” → Review。

模糊情况：

- 如果用户同时发文章链接和“这段没懂”，优先 Reading Session。
- 如果用户只说“总结”，优先 Capture。
- 如果用户对已入库材料追问“展开一点”，且最近没有 active reading session，优先 RAG continue。

## 状态流转

```text
active
  -> summarized
  -> committed

active
  -> archived

summarized
  -> active      # 用户继续讨论，草稿需要重新生成

summarized
  -> discarded
```

规则：

- 一个用户可以有多个 session，但微信上下文默认只激活最近一个。
- 如果新问题明显指向另一篇文章，应创建新 session。
- 如果 active session 超过一定时间未更新，仍保留，但主 Agent 不应盲目沿用；需要在回复里确认来源。
- 已 committed 的 session 可以继续追问，但新内容应开启新 draft revision。

## 微信输出策略

微信短回复只做：

- 当前解释。
- 引用来源简述。
- 下一步提示。

不要默认把完整阅读笔记塞进微信。

生成阅读讨论笔记时，默认回复：

```text
已生成阅读讨论笔记草稿：《...》

这次主要整理了：
1. ...
2. ...
3. ...

回复“展开阅读笔记”查看全文；满意回复“存入阅读笔记”。
```

长内容按需文件化，复用已有长输出策略。

## 数据与隐私边界

- Reading Session 属于 Knowledge 项目，不属于 Life 项目。
- 不读写 `life_*` 表。
- 不写入 Obsidian 原 capture 正文，除非用户明确要求追加链接。
- 不自动保存所有聊天，只保存进入 Reading Session 的 turns。
- 用户可以丢弃 session 或 draft。

## 失败处理

### 找不到来源文章

如果用户问“这段什么意思”但没有上下文：

```text
我还不知道你指的是哪篇或哪一段。你可以发原文片段、截图，或者告诉我是哪篇文章。
```

### 无法定位段落

如果能找到文章但定位不到具体段落：

```text
我找到了这篇文章，但没定位到你说的那一段。你可以贴一下原文句子，我再围绕那段解释。
```

### 生成笔记时 session 太短

如果只有一问一答且没有明确可沉淀内容：

```text
这次讨论内容还比较少。我可以先生成一条简短阅读札记，或者你继续追问几轮后再整理。
```

### 讨论内容和原文脱节

如果用户一路发散，最终整理时要分区：

- 原文依据
- 讨论延展
- 我的理解

不能把所有延展都写成原文观点。

## 测试计划

### 单元测试

- 创建 reading session：URL、capture_id、excerpt 三种入口。
- discuss reading：记录 user message、assistant answer、source map。
- summarize reading：生成阅读笔记草稿，包含原文依据、问题、解释、最终理解。
- commit reading note：写入 Obsidian `reading/`，链接 source capture。
- 模糊路由：总结文章走 Capture；这段没懂走 Reading；知识库问题走 RAG。
- session 状态：active、summarized、committed、archived。

### 集成测试

- 微信发公众号链接 + “这段没懂”。
- 多轮追问后生成阅读笔记草稿。
- 展开草稿，确认入库。
- Obsidian 中 reading note 链接原 capture。
- RAG 后续能引用 reading note 和原 capture。

### 回归测试

- 现有 Capture 草稿流程不受影响。
- 现有 RAG continue 不误用 reading session。
- Garden 草稿仍需确认和重复检查。
- Life repo 不受影响。

## 验收标准

第一版完成后应满足：

- 用户能围绕一篇文章多轮提问，不丢失讨论对象。
- 系统能明确区分原文依据、AI 解释和讨论延展。
- 用户能把讨论整理成独立 Obsidian reading note。
- reading note 能链接回原 capture。
- 未确认前不写入正式 Obsidian 笔记。
- 微信回复不超长，完整内容可按需展开或文件化。
- 不破坏现有 Capture、RAG、Garden、Review 流程。

## 建议实施顺序

### Phase 1：Reading Session 数据模型和基础工具

- 新增 DB 表。
- 新增 repo。
- 新增 bridge。
- 新增 runtime 工具描述。
- 支持 start/discuss/show。

### Phase 2：阅读讨论笔记草稿

- 新增 summarize reading session。
- 新增 show reading note draft。
- 新增 commit reading note。
- 写入 Obsidian `reading/`。

### Phase 3：和 RAG / Capture / Library 打通

- 已入库材料优先用 capture section。
- 未入库材料可临时 source 或引导 Capture。
- commit 后 Library 补 metadata 和 embedding。

### Phase 4：Garden / Review 后续联动

- 从高价值 reading note 建议 Garden card。
- Review 可以选择 reading note 作为讲解对象。

## 开放问题

当前建议先按以下默认值实现：

- 阅读讨论笔记作为独立 Obsidian note，而不是追加到原 capture。
- 目录为 `reading/`。
- 最近 active session 作为微信默认上下文。
- 用户确认后才入库。
- Garden card 不自动生成。

后续可以再讨论：

- reading note 是否需要在原 capture 中自动追加反向链接。
- active session 多久后需要显式确认。
- excerpt-only session 是否允许直接入库。
- 阅读讨论是否需要专门的 Dashboard 页面。

## Self-Review

- Placeholder scan: 没有保留占位符。
- Internal consistency: Reading Session 是 Knowledge 项目对象，不读写 Life；它不替代 Capture/RAG/Garden/Review。
- Scope check: 第一版聚焦微信多轮阅读讨论和 Obsidian reading note，不做阅读器 UI。
- Ambiguity check: 明确采用独立 reading note，确认后入库，Garden 不自动生成。

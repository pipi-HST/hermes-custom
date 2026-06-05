# Reading Session 阅读讨论会话设计

更新日期：2026-06-05

## 背景

Hermes Knowledge 已经跑通“材料进入知识库”的主链路：用户发链接、文件、文本或图文材料，Capture 生成可审阅草稿，用户确认后入库，Library 补 properties、目录纲要、章节切分和 embedding，后续 RAG、Theme、Garden、Review 复用这些材料。

但还有一个真实阅读场景没有被完整覆盖：

> 用户正在读一篇公众号、长文章、PDF、截图或已入库 Capture，某个地方没看懂，想和 AI 多轮讨论。讨论后，用户可能只是理解一下，也可能想把原文和讨论成果整理成笔记。

这个场景既不是普通 Capture，也不是普通 RAG：

- Capture 关注“把材料整理成可入库笔记”。
- RAG 关注“基于已有知识库回答问题”。
- Review 关注“把已有知识重新讲给用户听”。
- Garden 关注“把跨材料价值沉淀成可复用知识卡片”。

Reading Session 的定位是：围绕一份具体材料保存临时阅读上下文，让 Hermes 能持续回答“这段什么意思”“这里为什么重要”“和我之前看到的内容有什么关系”，但不自动入库。

## 核心原则

1. **讨论不是入库。** 用户只是在问问题时，只建立临时 Reading Session，不自动生成 Capture 草稿、不自动写 Obsidian。
2. **输出形态由用户意图决定。** 最终可以是原文增强版主 Capture、局部学习笔记、讨论札记；系统不能替用户固定成某一种。
3. **增强版直接成为主 Capture。** 如果用户选择“原文增强版笔记”，它应生成增强 Capture 草稿，确认后合并写回主 Capture，而不是新建一篇重复主笔记。
4. **先草稿，后确认。** 所有写入 Obsidian 或覆盖主 Capture 的动作，都必须先生成可审阅草稿。
5. **Raw 永不改。** Raw 是原始归档材料，增强、改写、合并只影响 Capture 或新笔记。
6. **来源边界要清楚，但不要模板化。** 回答和笔记应按理解逻辑组织，只在必要处用简洁来源标记说明依据来自原文、知识库或网页。
7. **意图识别要智能，不做关键词硬编码。** 路由输出结构化意图，代码只校验结构、置信度和安全边界。

## 推荐方案

采用 **Reading Session 作为一等对象**。

Reading Session 保存当前材料、讨论 turns、轻量证据、用户当前焦点和后续草稿关系。它本身是临时状态，不等于正式知识库笔记。

方案取舍：

- 只依赖当前聊天上下文：实现快，但重启、上下文压缩、跨材料追问时不稳定。
- Reading Session 状态：稳定、可追溯、可测试，和 Capture/RAG/Library/Garden 边界清楚。
- 完整阅读器 UI：长期体验好，但第一版成本过高。

**第一版采用 Reading Session 状态，不做专门阅读器 UI。**

## 用户故事

### 1. 从文章开始讨论

用户发：

> 这篇文章我看到 Terminal-Bench 那段没太懂，帮我解释一下：https://mp.weixin.qq.com/...

预期：

- Hermes 创建 Reading Session。
- 如果文章已入库，优先基于 Capture 和 section 讨论。
- 如果文章未入库，提取当前讨论所需原文或创建临时 source，不自动生成 Capture 草稿。
- 回答用户问题，可按需并行搜索本地知识库和网络。
- 不写 Obsidian，不提示强制入库。

### 2. 从已入库 Capture 开始讨论

用户发：

> 上次那篇 Opus 4.8 的文章，41 天发版那里我还是没懂。

预期：

- 通过标题、链接、section、embedding 或最近 session 匹配原 Capture。
- 创建或恢复该 Capture 的 Reading Session。
- 优先基于 Capture 解释；需要核对作者原意或细节时再回 raw/原网页。

### 3. 从截图、摘录或文件开始讨论

用户发截图、粘贴段落或上传文件：

> 这段话什么意思？

预期：

- 能匹配已有 Capture 时挂到该 Capture。
- 无法匹配时创建 `excerpt`、`image` 或 `file` 类型临时 session。
- 后续用户补链接或标题时，可以把 session source 更新到更准确的材料。

### 4. 多轮讨论后整理

用户发：

> 把刚才讨论整理进笔记。

预期：

- 如果输出形态不明确，Hermes 先短问：

```text
你想整理成哪种？
1. 原文增强版笔记：保留文章主线，并把刚才讨论补进去
2. 局部学习笔记：只整理这次卡住的概念/段落
3. 讨论札记：保留问题、解释和最终理解
回复 1/2/3 即可。
```

- 用户回复 1/2/3 后，只对本次整理生效，不写成长期默认。
- 生成可审阅草稿；用户确认后才写入。

### 5. 从讨论中提炼 Garden card

用户发：

> 这个理解挺重要，把它做成知识卡片。

预期：

- Reading Session 不直接写 Garden。
- 主 Agent 把 session 摘要、来源 Capture、关键解释和轻量证据交给 Garden。
- Garden 仍然走草稿、重复检查和确认流程。

## 输出形态

### 原文增强版笔记

适用意图：

- “把刚才讨论补进原文”
- “合并到主笔记”
- “整理成原文增强版”
- 用户在模糊选择中回复 `1`

行为：

- 生成 enhanced Capture draft。
- 尽量保留原 Capture 主结构。
- 把讨论中有价值的解释自然合并到相关正文段落。
- 最后可保留 `## 阅读讨论补充`，记录关键卡点、澄清和必要来源。
- 用户确认后写回主 Capture。
- 写回前必须备份旧 Capture 文件，并在 DB 记录版本关系。
- Raw 文件不改。

### 局部学习笔记

适用意图：

- “只把这个概念整理成笔记”
- “这段单独记一下”
- 用户在模糊选择中回复 `2`

行为：

- 生成独立 learning note draft。
- 聚焦一个概念、段落、论证或方法。
- 默认不合并进主 Capture。
- 只有用户明确说“补进原文/合并到主笔记”时，才进入 enhanced Capture 流程。

### 讨论札记

适用意图：

- “保留这次问答过程”
- “整理成讨论札记”
- 用户在模糊选择中回复 `3`

行为：

- 生成独立 discussion note draft。
- 默认不保存完整聊天流水。
- 保留关键问题、解释、最终理解和必要来源。
- 用户明确要求“保留完整问答”时，才把更完整的 Q&A 放入笔记。

## 核心对象

### Reading Session

一次围绕某份材料的阅读讨论。

建议字段：

- `id`
- `source_type`: `capture | draft | raw | url | excerpt | image | file`
- `source_id`
- `source_url`
- `source_title`
- `status`: `active | summarized | committed | archived`
- `focus`
- `created_at`
- `updated_at`
- `last_turn_at`
- `expires_at`
- `committed_artifact_id`
- `meta_json`

Retention：

- 默认临时保留 7 天。
- 到期后可以清理 session、turns 和轻量证据。
- 只有用户明确整理、保存、入库后，才生成长期 Obsidian artifact。

### Reading Turn

一次用户问题和 AI 回答。

建议字段：

- `id`
- `session_id`
- `turn_index`
- `user_message`
- `assistant_answer`
- `focus`
- `confidence`: `high | medium | low`
- `created_at`
- `meta_json`

### Reading Evidence

讨论时用到的轻量证据。

建议字段：

- `id`
- `session_id`
- `turn_id`
- `source_kind`: `original | knowledge_base | web`
- `source_id`
- `title`
- `url`
- `excerpt`
- `used_for`
- `created_at`
- `meta_json`

规则：

- 保存来源类型、标题/URL/笔记 id、命中的短摘录、用于回答的要点。
- 不保存整篇网页或全文搜索结果。
- 最终回答和笔记只列实际使用过的来源。

### Reading Draft

把 session 整理成可写入 artifact 前的草稿。

建议字段：

- `id`
- `session_id`
- `output_type`: `enhance_capture | local_learning_note | discussion_note`
- `target_capture_id`
- `title`
- `summary`
- `draft_text`
- `status`: `open | committed | discarded`
- `created_at`
- `updated_at`
- `meta_json`

第一版可以复用现有 draft 存储模式，但语义上不要混进普通 `capture_drafts`，避免 Capture 草稿和 Reading 草稿混淆。

### Capture Version Backup

增强版写回主 Capture 前的版本记录。

建议字段：

- `id`
- `capture_id`
- `reading_draft_id`
- `backup_file_path`
- `previous_file_hash`
- `new_file_hash`
- `created_at`
- `meta_json`

规则：

- 文件备份放本地备份目录，不提交 Git。
- DB 记录版本关系，方便回滚和排查。

## 工具设计

第一版建议新增或调整 6 类工具能力。

### `start_reading_session`

用途：围绕某篇材料开始阅读讨论。

输入：

- `message`
- `source_url`
- `capture_id`
- `draft_id`
- `excerpt`
- `file_ref`
- `image_ref`

输出：

- `reading_session_id`
- `chat_preview`
- `source_map`
- `next_actions`

行为：

- 如果能匹配已有 Capture，优先挂到 Capture。
- 如果有 URL 且未入库，可以提取讨论所需文本，但不默认生成完整 Capture 草稿。
- 如果只有摘录、截图或文件，创建临时 source session。

### `discuss_reading`

用途：围绕当前或指定 Reading Session 继续解释。

输入：

- `session_id`
- `message`
- `focus`

输出：

- `answer`
- `evidence`
- `session_state`

行为：

- 优先使用 session source。
- 如果问题需要背景，AI 可自行判断是否并行搜索本地知识库和网络。
- 回答目标是解释清楚，不按来源类型机械分区。
- 对事实、数据、政策、benchmark、模型能力、论文结论等高风险内容，要保守、标来源、标不确定性。
- 保存本轮实际使用过的轻量证据。

### `show_reading_session`

用途：查看当前阅读讨论状态。

输出：

- 来源标题
- 当前焦点
- 最近关键问题
- 已引用证据摘要
- 是否已有草稿

### `summarize_reading_session`

用途：生成 Reading Session 草稿。

输入：

- `session_id`
- `output_type`: `enhance_capture | local_learning_note | discussion_note`
- `instruction`

输出：

- `reading_draft_id`
- `chat_preview`
- `artifact_kind`
- `needs_confirmation`

行为：

- 如果 `output_type` 不明确，主 Agent 先问 1/2/3，不直接调用。
- 生成草稿，不直接写 Obsidian。
- 笔记按理解逻辑组织，不固定分成“原文依据/知识库补充/外部资料补充/我的理解”四段。
- 必要处用简洁来源标记，例如 `[原文]`、`[知识库]`、`[网页]`。
- 没有标来源的解释可以理解为 AI 的推理或表达，不需要额外写“AI 自己说的”。

### `commit_reading_artifact`

用途：确认 Reading Draft 写入正式 artifact。

输入：

- `draft_id`

输出：

- `artifact_type`
- `file_path`
- `backup_file_path`
- `library_status`

行为：

- `enhance_capture`：备份旧 Capture，合并写回主 Capture，记录版本关系，触发 Library 后处理。
- `local_learning_note`：写入独立 note，可链接回 source Capture。
- `discussion_note`：写入独立 note，可链接回 source Capture。
- Raw 永不改。

### `discard_reading_session`

用途：丢弃临时 session 或草稿。

行为：

- 将 session 或 draft 标记为 archived/discarded。
- 不删除已提交 artifact。

## 主 Agent 路由原则

入口由用户意图决定：

- “总结/提取重点/保存/入库/全文保存”倾向 Capture。
- “这段什么意思/我没看懂/解释一下/怎么看/和我讨论一下”倾向 Reading Session。
- “基于我的知识库回答/查一下我之前有没有相关材料”倾向 RAG。
- “扫描最近内容整理成主题页/知识卡片”倾向 Theme/Garden。
- “复习/讲一个点”倾向 Review。

这些不是关键词硬编码规则，而是路由语义的例子。推荐让主 Agent 或 routing tool 输出结构化意图：

```json
{
  "primary_intent": "discuss_current_material",
  "secondary_intents": ["maybe_save_later"],
  "target_material": "current_link_or_recent_session",
  "needs_clarification": false,
  "confidence": 0.82
}
```

代码职责：

- 校验结构化意图字段。
- 对低置信度或高风险写入动作进行确认。
- 禁止未确认写入 Obsidian 或覆盖 Capture。
- 记录工具调用状态和 evidence。

低置信度时，短问确认：

```text
你是想先讨论这篇文章，还是直接整理成笔记？
```

多材料指代规则：

- 默认用链接、摘录、标题关键词在最近 Reading Sessions 中匹配。
- 能确定就直接接上。
- 如果时间间隔较久、多个材料都相似、或置信度低，再问确认。

## 与现有模块关系

### Capture

Capture 仍负责材料提取和正式整理稿生成。Reading Session 不替代 Capture。

如果用户只是围绕未入库文章讨论，不生成 Capture 草稿；如果用户明确说“总结/存库/整理成主笔记”，才走 Capture 或 enhanced Capture draft。

### RAG

RAG 是 Reading Session 的背景检索能力之一。Reading Session 可以调用 RAG 找相关知识库材料，但回答应围绕当前阅读问题，不变成全库问答。

### Library

Library 负责 Reading artifact 入库后的维护：

- properties 补齐。
- source Capture 链接校验。
- 长笔记目录纲要。
- section/identity embedding 更新。
- enhanced Capture 写回后的索引刷新。

### Garden

Reading Session 不自动创建 Garden card。

只有用户明确要求“做成知识卡片/存入花园”时，主 Agent 才把 session 摘要、来源 Capture、关键解释和证据交给 Garden。Garden 继续执行草稿、重复检查和确认流程。

### Review

局部学习笔记或讨论札记可以成为 Review 候选，尤其适合“我曾经卡住、后来理解了”的概念。

Review 讲解时可以引用 reading note 和原 Capture。

### Memory

Reading Session 可以帮助 Hermes 识别用户的稳定偏好，但不能把具体文章观点或单次讨论结论写入长期记忆。

允许沉淀的例子：

- 用户喜欢先用人话解释，再给结构化要点。
- 用户不喜欢过度模板化整理稿。
- 用户希望不确定时短问确认。

不允许自动沉淀的例子：

- 某篇文章的具体观点。
- 用户某次临时判断。
- 未经用户确认的知识结论。

## Obsidian 笔记形态

### Enhanced Capture

写回主 Capture，文件路径沿用原 Capture。

建议正文策略：

- 保留原 Capture 主线。
- 在相关章节自然补入讨论中形成的解释。
- 最后可加：

```markdown
## 阅读讨论补充

- 关键卡点：
- 澄清后的理解：
- 相关来源：
```

该 section 不是强制模板，允许按文章内容灵活调整。

### Local Learning Note

建议放在：

```text
learning/
```

结构可以轻量：

```markdown
---
type: learning_note
source_capture_id:
reading_session_id:
created:
---

# 标题

## 我卡住的点

## 解释

## 这次形成的理解

## 来源
```

### Discussion Note

建议放在：

```text
reading/
```

结构可以轻量：

```markdown
---
type: discussion_note
source_capture_id:
reading_session_id:
created:
---

# 标题

## 讨论背景

## 关键问题与解释

## 最终理解

## 来源
```

原则：

- 默认不保存完整聊天记录。
- 只保留整理后的理解、关键问题和必要来源。
- 用户明确要求时才保留更完整的 Q&A。

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

- 一个用户可以有多个 session。
- 微信上下文默认匹配最近且最相关的 session，不盲目只用最新一条。
- 已 committed 的 session 可以继续追问，但新整理应生成新 draft revision。
- 临时 session 默认 7 天过期。

## 微信输出策略

讨论阶段：

- 直接回答用户问题。
- 必要时给简洁来源。
- 不默认提示入库，不默认生成草稿。

整理阶段：

```text
已生成阅读整理草稿：《...》

这次主要整理了：
1. ...
2. ...
3. ...

回复“展开”查看全文；满意回复“确认”。
```

长内容按需文件化，复用长输出协议。只有用户要求输出文件，才生成文件；普通微信回复保持短而可读。

## 数据与隐私边界

- Reading Session 属于 Knowledge 项目，不属于 Life 项目。
- 不读写 `life_*` 表。
- 不自动保存所有聊天，只保存进入 Reading Session 的 turns。
- 不自动生成 Obsidian artifact。
- 不自动写 Garden。
- 不自动写长期记忆中的具体知识内容。
- Raw 文件永不修改。

## 失败处理

### 找不到来源文章

```text
我还不知道你指的是哪篇或哪一段。你可以发原文片段、截图，或者告诉我是哪篇文章。
```

### 无法定位段落

```text
我找到了这篇文章，但没定位到你说的那一段。你可以贴一下原文句子，我再围绕那段解释。
```

### 意图不确定

```text
你是想先讨论这篇文章，还是直接整理成笔记？
```

### 整理类型不确定

```text
你想整理成哪种？
1. 原文增强版笔记：保留文章主线，并把刚才讨论补进去
2. 局部学习笔记：只整理这次卡住的概念/段落
3. 讨论札记：保留问题、解释和最终理解
回复 1/2/3 即可。
```

### 讨论内容和原文边界可能混淆

整理时不机械分区，但必须在容易误解的位置标明：

- 作者原文真正说了什么。
- 哪些是知识库或网页补充。
- 哪些是讨论后形成的理解或推论。

## 测试计划

### 单元测试

- 创建 Reading Session：URL、capture_id、excerpt、file、image 五种入口。
- 已入库材料优先使用 Capture，必要时回 raw。
- `discuss_reading` 记录 turn 和实际使用过的 evidence。
- 低置信度意图返回 clarification，不误调用 Capture/RAG。
- 模糊“整理进笔记”返回 1/2/3 选择。
- `summarize_reading_session` 支持三种 output type。
- Enhanced Capture commit 前创建文件备份和 DB 版本记录。
- Local learning note / discussion note 默认不改主 Capture。
- Session 默认 7 天过期。

### 集成测试

- 微信发公众号链接 + “这段没懂”，只讨论不入库。
- 多轮追问后选择 `1`，生成 enhanced Capture draft，确认后写回主 Capture。
- 多轮追问后选择 `2`，生成独立局部学习笔记。
- 多轮追问后选择 `3`，生成讨论札记。
- 已入库 Capture 追问优先命中 Capture section。
- 需要外部事实时可并行用知识库和联网搜索，最终只展示实际使用来源。

### 回归测试

- 现有 Capture 草稿流程不受影响。
- 现有 RAG continue 不误用 Reading Session。
- Garden 草稿仍需确认和重复检查。
- Library 的 properties、outline、embedding 流程不受影响。
- Life repo 不受影响。

## 验收标准

第一版完成后应满足：

- 用户能围绕一篇材料多轮提问，不丢失讨论对象。
- 普通讨论不会自动生成 Capture 草稿或 Obsidian 笔记。
- 用户明确整理时，系统能按意图生成三类草稿。
- 原文增强版可确认后写回主 Capture，并保留旧版备份。
- 局部学习笔记和讨论札记可以独立入库，并链接来源。
- 回答能为了“解释清楚”灵活使用原文、知识库、网页和模型推理。
- 来源标注简洁、准确，只对应实际使用材料。
- 微信回复不超长，长内容按需展开或文件化。

## 建议实施顺序

### Phase 1：Reading Session 数据模型和基础工具

- 新增 DB 表或 repository。
- 新增 bridge 工具。
- 新增 runtime 工具描述。
- 支持 start/discuss/show。
- 实现 7 天临时 retention。

### Phase 2：智能路由和证据记录

- 设计结构化意图输出合同。
- 低置信度时短问确认。
- 支持本地知识库和联网搜索的轻量 evidence 记录。
- 保证实际来源和尾注一致。

### Phase 3：三类草稿

- `enhance_capture`
- `local_learning_note`
- `discussion_note`
- 模糊整理时先问 1/2/3。

### Phase 4：确认写入和 Library 联动

- Enhanced Capture 写回前备份旧文件。
- DB 记录版本关系。
- 独立 note 写入 `learning/` 或 `reading/`。
- 触发 Library 后处理。

### Phase 5：Garden / Review 后续联动

- 用户明确要求时，Reading Session 可交给 Garden。
- Review 可以把 learning note / discussion note 作为讲解对象。

## 非目标

第一版不做：

- 专门阅读器 UI。
- 自动把每次 RAG 追问保存成阅读笔记。
- 未确认就覆盖 Capture。
- 默认生成 Garden card。
- 把微信完整聊天记录原样塞进 Obsidian。
- 多人协同批注。
- 对每次搜索都向用户要权限。
- 把来源类型强行变成固定笔记章节。

## Self-Review

- Placeholder scan: 没有保留 TBD/TODO 或“以后补”占位。
- Internal consistency: Reading Session 是临时讨论对象；只有用户明确整理和确认后才形成长期 artifact。
- Scope check: 第一版聚焦微信多轮阅读讨论、三类草稿、增强 Capture 写回和 evidence 记录，不做阅读器 UI。
- Ambiguity check: 旧版“固定独立 reading note、不覆盖 capture”的假设已删除，改为按用户意图选择输出形态。

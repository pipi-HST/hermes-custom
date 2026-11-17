---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# 知识库 AI Markdown 与学习笔记找回审计

日期：2026-06-20

## 问题

1. 知识库右侧 AI 对话面板里，AI 回答中的 Markdown 被当成普通文本显示，例如 `**加粗**` 没有渲染。
2. 在工作台 / Hermes 对话中，把一次基于知识库检索的回答保存成学习笔记后，再要求 Hermes 找回这条笔记，返回内容不够准，且引用混入了无关材料。

## 导出的关键记录

### 保存为学习笔记

- `agent_runs.id`: `da2f8bc3f921440b9c68ea99c77743e9`
- `agent`: `reading`
- `intent`: `save_answer_to_knowledge`
- `trigger`: `knowledge_workbench`
- `created_at`: `2026-06-20T06:51:13.130020+00:00`
- `input_json`: `{"answer_source":"latest_rag_full_answer","assistant_message_id":"assistant-1781938241136-3","hermes_session_id":"ff4cd274","output_type":"local_learning_note","source_count":1}`
- `output_json`: `{"draft_id":"a3b27790641f"}`

结论：保存动作成功创建了 `reading_draft`，并且使用的是 `latest_rag_full_answer`，不是被前端截断的预览文本。

### 入库为 learning note

- `agent_runs.id`: `08dfd943d9b24037a035e749c50451b7`
- `agent`: `reading`
- `intent`: `commit_reading_artifact`
- `created_at`: `2026-06-20T06:51:26.247096+00:00`
- `artifact_id`: `41cc4cc1ec5f`
- `draft_id`: `a3b27790641f`
- `path`: `learning/基于知识库回答这个问题：最近市场会涨还是会跌-41cc4cc1.md`

对应文件：

- `/Users/pipi/Obsidian/hermes-vault/learning/基于知识库回答这个问题：最近市场会涨还是会跌-41cc4cc1.md`

结论：学习笔记已成功写入 Obsidian vault，并被登记为 `captures.id = 41cc4cc1ec5f`。

### 入库后的索引

`capture_embeddings` 中已有 3 条记录：

- `identity`: 标题、摘要、正文线索
- `section-1`: `问题：基于知识库回答这个问题：最近市场会涨还是会跌`
- `section-2`: `解释：基于知识库回答这个问题：最近市场会涨还是会跌`

结论：这条学习笔记已经被检索索引覆盖，不是“没存进去”或“没建索引”。

### 后续找回时的错误检索

- `agent_runs.id`: `9a306311465a406a95a50ef0f3903402`
- `agent`: `rag`
- `intent`: `search_knowledge_base`
- `created_at`: `2026-06-20T06:53:06.959458+00:00`
- `query`: `最近市场会涨还是会跌`
- `source_titles`:
  - `基于知识库回答这个问题：最近市场会涨还是会跌`
  - `基于知识库回答这个问题：最近市场会涨还是会跌`
  - `【行业知识圈儿.宏观】2026年5月15日 财报分析和二季度市场展望`
  - `段永平论茅台护城河`
  - `Subagent 的三岔路`
  - `Subagent 的三岔路`

结论：RAG 确实找到了学习笔记，但它没有停止在“找回这条笔记”这个任务上，而是继续做了图扩展检索，混入其他材料，并让 LLM 重新生成了一次市场判断。

## 根因

`search_my_notes` 只有一种通用 RAG 模式：

1. 对查询做 embedding 检索。
2. 用 graph retrieval 扩展相关 Capture / Garden / Theme。
3. 把多个来源交给 LLM 重新生成答案。
4. 根据生成答案里的 `[1][2]` 拼接引用。

这个流程适合“基于知识库回答问题”，但不适合“找我之前保存的那条学习笔记”。后者需要确定性找回对象，而不是重新做问答。

本次错误不是保存失败，而是检索意图没有分流：

- 保存成功。
- 入库成功。
- 索引成功。
- 失败点在找回阶段：`learning_note` 命中后仍被当成普通 RAG 证据之一。

## 已实施修复

### 1. 知识库 AI 对话 Markdown 渲染

修改文件：

- `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx`

处理：

- 用户消息保持普通文本。
- AI 助手消息改用已有 `Markdown` 组件渲染。
- 给链接和代码块补了基本的宽度保护，避免聊天气泡横向撑破。

验证：

- Playwright 打开 `http://127.0.0.1:9119/knowledge-library`。
- 在右侧 AI 面板发送：`请用 Markdown 回答：用一句加粗结论和两个要点说明这篇文章讲什么。`
- 快照显示 AI 回复已渲染为 `strong` 和 `list/listitem` 节点，不再裸露 `**...**`。

### 2. 学习笔记确定性找回

修改文件：

- `/Users/pipi/hermes-custom/bridge/rag_bridge.py`

处理：

- 在普通 embedding / graph / LLM RAG 之前，先检查 `learning/` 下已入库学习笔记。
- 对中文自然话术做轻量清洗，例如去掉“我的笔记”“关于”“找出来”“学习笔记”等意图词。
- 如果查询词明确命中 learning note 的标题、文件路径、摘要或正文，则直接返回该学习笔记。
- 直接返回时：
  - 不调用 embedding。
  - 不调用 LLM。
  - 不做 graph 扩展。
  - `source_map` 只保留这条学习笔记。
  - `meta.retrieval_mode = exact_learning_note`。

验证：

真实数据库查询：

```text
query = 把我的笔记里关于基于知识库回答这个问题：最近市场会涨还是会跌的相关回答找出来
status = success
retrieval_mode = exact_learning_note
source_map = 41cc4cc1ec5f / learning/基于知识库回答这个问题：最近市场会涨还是会跌-41cc4cc1.md
```

回归测试：

```text
.venv/bin/python -m pytest -q /Users/pipi/hermes-custom/tests/test_rag_bridge.py -k 'exact_learning_note or uses_best_section_embedding'
2 passed, 5 deselected
```

前端构建：

```text
cd /Users/pipi/Downloads/hermes-agent-main/web && npm run build
passed
```

## 后续建议

下一步可以把 `search_my_notes` 在工具描述上进一步拆清楚：

- “回答知识库问题”：走 RAG。
- “找之前保存的笔记 / 学习笔记 / 回答”：优先走 exact learning note lookup。

这能减少主 Agent 把用户原话改写成过短查询词的概率，让工具选择更稳定。

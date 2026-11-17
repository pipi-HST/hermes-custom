---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# 草稿工作流 + DB 瘦身 — Implementation Plan

> 最后更新：2026-05-27
> 状态：全部 7 Tasks 完成 ✅

**Goal:** 实现草稿确认工作流（create_draft → revise_draft → commit_draft），SQLite 瘦身到 12 列

**Architecture:**
- 所有内容先进草稿区（capture_drafts 表），可多轮修改，用户确认后才写 raw/ 和 captures/
- DB 只存路由字段和复习状态，所有文本元数据以 .md frontmatter 为唯一真相源

**Tech Stack:** Python pathlib, PyYAML, httpx

---

### Task 1: 更新 markdown_writer 支持两阶段 ✅ 完成

**Files:**
- Modify: `agents/markdown_writer.py`
- Modify: `tests/test_markdown_writer.py`

核心改动：`write_note` 根据 `stage` 参数决定 frontmatter 内容：
- `stage="raw"` → 最简 frontmatter（id, title, date, source, source_type, status）
- `stage="captures"` → 完整 frontmatter（id, title, date, created, source, source_type, tags, story, word_count）

已实现测试：`test_write_note_stage_raw_minimal_frontmatter`、`test_write_note_stage_captures_full_frontmatter`。

---

### Task 2: 瘦身 DB schema + repo ✅ 完成

**Files:**
- Modify: `db/schema.py`
- Modify: `db/repo.py`
- Modify: `tests/test_schema.py`

实际瘦身到 **12 列**（比原计划多 review_count 和 theme_scanned_at）：

`captures` 表：id, file_path, raw_path, embedding, review_state, review_interval, review_count, last_reviewed, next_review, theme_scanned_at, created_at, updated_at

新增表：`capture_drafts`（草稿区，14 列）、`change_log`、`garden_cards`、`pending_questions`

`schema.py` 内有 `_migrate_captures_table()` 自动迁移逻辑，无需手动删库。

---

### Task 3: 重写 capture_agent — 草稿工作流 ✅ 完成（设计演进）

**Files:**
- Modify: `agents/capture_agent.py`

**实际演进为三阶段草稿工作流**（比原计划的两步更灵活）：

```python
def create_draft(self, raw_input, instruction="", source_url="", source_type=""):
    # 提取 → 清洗 → 存 capture_drafts 表（不写任何 .md）
    # 有 instruction → 直接调 revise_draft
    # 无 instruction → 返回草稿 ID，询问用户"想怎么整理？"

def revise_draft(self, draft_id, instruction):
    # 读草稿 → LLM 按指令处理 → 自动打标签（扫描 captures/ 已有标签）
    # 更新 capture_drafts 表，status=draft_ready
    # 可多轮修改，草稿只在 DB，不碰 Obsidian

def commit_draft(self, draft_id):
    # 写 raw/{date}-{slug}.md（最简 frontmatter）
    # 写 captures/{date}-{slug}.md（完整 frontmatter）
    # DB insert 到 captures 表，async embed
    # 草稿 status 改为 committed
```

**遗留方法**：`process()` 和 `finalize()` 仍存在但作为别名，主流程不再走它们，可在下次清理时删除。

---

### Task 4: 更新 bridge ✅ 完成（演进版本）

**Files:**
- Modify: `bridge/capture_bridge.py`

实际桥接函数（4 个）：

| 函数 | 对应 Agent 方法 | 说明 |
|------|----------------|------|
| `capture_content(content, instruction?)` | `agent.create_draft()` | 提取+可选生成草稿 |
| `revise_capture_draft(draft_id?, instruction)` | `agent.revise_draft()` | 修改草稿 |
| `commit_capture_draft(draft_id?)` | `agent.commit_draft()` | 确认入库 |
| `refine_capture(draft_id?, instruction)` | → `revise_capture_draft()` | 旧工具名别名 |

草稿预览有 1800 字符截断（微信限制）；commit 时写完整草稿。

---

### Task 5: 调整下游组件 ✅ 全部完成

原计划要求在 agent 文件里直接改，实际架构是 **hydration 放在 bridge 层**。

**5a. summarizer 标签来源** ✅ 完成

实现在 `capture_agent.py` 的 `_get_existing_tags(vault_path)`（line 212）：
扫描 `captures/` 目录下所有 .md 文件的 frontmatter，收集已有标签传给 `Summarizer.summarize()`。

**5b. RAG 读 .md 文件** ✅ 完成

实现在 `bridge/rag_bridge.py` 的 `_hydrate_capture()`（line 49）：
DB 返回 slim row → 读 .md 文件 → 注入 title/story/tags/body 后再传给 `RagAgent.query()`。

**5c. review_agent 标题/摘要** ✅ 完成

`bridge/review_bridge.py` 向 ReviewAgent 注入 `vault_path`，`_get_random_captures()` 在采样时对每条记录调用 `_hydrate()` 从 .md frontmatter 补充 title 和 story。`run_daily()` 的消息格式也已同步改用 `story` 字段。

**Files:**
- Modify: `agents/review_agent.py`（加 `vault_path` 参数、`_hydrate()` 方法、`_get_random_captures()` 注入 hydration）
- Modify: `bridge/review_bridge.py`（传 `vault_path=config.vault_path`）
- Add: `tests/test_review_agent.py`（3 个 hydration 测试：正常读 frontmatter、无 vault_path 时原样返回、文件不存在时原样返回）

---

### Task 6: 目录 + 测试 ✅ 完成

**Files:**
- Create: `~/Obsidian/hermes-vault/raw/`（已创建，有实际文件）
- Modify: `tests/test_e2e_full_flow.py`（覆盖草稿工作流：create_draft → revise → commit）
- Add: `tests/test_capture_draft_workflow.py`（草稿流程单元测试）

---

### Task 7: 全量测试 ✅ 完成

Run: `/opt/homebrew/bin/python3.11 -m pytest tests/ -v --ignore=tests/test_e2e_hermes_tools.py`

Result: **73 passed**

---

## 与原计划的差异说明

| 原计划 | 实际实现 | 原因 |
|--------|---------|------|
| 两步流程（process → finalize） | 三阶段草稿（create_draft → revise_draft → commit_draft） | 草稿区允许用户多轮修改，比直接写文件更安全 |
| DB 瘦到 10 列 | 12 列（加 review_count、theme_scanned_at） | 复习调度需要这些字段 |
| 新增 refine_capture() | 改为 revise_capture_draft() + commit_capture_draft() | 更清晰地分离"修改"和"入库"两个动作 |
| hydration 改 agent 文件 | hydration 放 bridge 层 | 架构上 bridge 负责适配，agent 保持纯粹 |

---

## 后续补充：多 Agent 体系 hydration 补全

DB 瘦身后，所有从 DB 读取数据的 bridge 都需要 hydration。以下组件在 Task 5 之后补充修复：

| 组件 | 问题 | 修复 |
|------|------|------|
| `bridge/theme_bridge.py` | `scan()` 传空 title/summary/tags/cached_text 给 ThemeAgent | 加 `_hydrate_capture()`，从 .md 补充字段 |
| `bridge/review_bridge.py` | `_get_random_captures()` 拿不到 title | 传 `vault_path`，agent 内加 `_hydrate()` |
| `bridge/garden_bridge.py` | 不存在，garden_agent 无入口 | 新建 bridge + 注册 `synthesize_garden` Hermes 工具 |

全量测试：**79 passed**（76 原有 + 3 garden_bridge）

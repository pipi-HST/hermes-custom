---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Theme Garden Candidate Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Theme -> Garden flow stable after "no recent content": the user can ask what candidate groups exist, choose a numbered group, and get a Garden draft from structured Theme state instead of ad hoc RAG grouping.

**Architecture:** Keep `theme_scan` as the scanner, and add a small read-only Theme candidate listing path backed by `pending_questions`. Garden synthesis should continue to use `question_id` so source selection is deterministic. Clean Garden Markdown generation so empty placeholder sections are omitted instead of being written into Obsidian.

**Tech Stack:** Python, SQLite, existing Hermes result envelopes, existing Hermes runtime tool registry, pytest.

---

### Task 1: Add Read-Only Theme Candidate Listing

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/theme_bridge.py`
- Test: `/Users/pipi/hermes-custom/tests/test_theme_bridge.py`

- [ ] **Step 1: Write failing test for listing pending Theme candidates**

Add a test that creates two pending Theme questions and asserts the listing returns a numbered preview plus `next_actions` carrying `question_id`.

```python
def test_list_theme_candidates_returns_pending_questions(repo):
    from agents.theme_agent import create_pending_question
    from bridge.theme_bridge import list_theme_candidates

    first = create_pending_question(
        repo,
        "theme",
        "AI Agent 工程化这组材料可以沉淀成什么卡片？",
        context_ref="theme-ai-agent",
        related_capture_ids=["cap-a", "cap-b"],
    )
    second = create_pending_question(
        repo,
        "theme",
        "知识管理这组材料可以沉淀成什么卡片？",
        context_ref="theme-km",
        related_capture_ids=["cap-c", "cap-d"],
    )

    result = list_theme_candidates(limit=10)

    assert result["status"] == "success"
    assert "1. AI Agent 工程化" in result["chat_preview"]
    assert "2. 知识管理" in result["chat_preview"]
    assert result["next_actions"][0]["intent"] == "synthesize_garden"
    assert result["next_actions"][0]["payload"]["question_id"] == first["id"]
    assert result["next_actions"][1]["payload"]["question_id"] == second["id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_bridge.py::test_list_theme_candidates_returns_pending_questions -q
```

Expected: FAIL because `list_theme_candidates` does not exist.

- [ ] **Step 3: Implement minimal listing function**

Add `list_theme_candidates(limit: int = 5)` in `bridge/theme_bridge.py`. It should:

- Read pending rows from `pending_questions`.
- Filter `agent_type='theme'` and `status='pending'`.
- Sort newest first.
- Return `success_envelope`.
- Build `next_actions` with `intent='synthesize_garden'`, payload `question_id` and `capture_ids`.

Implementation shape:

```python
def _short_question_text(text: str, max_len: int = 48) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:max_len]


def list_theme_candidates(*, limit: int = 5) -> dict:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    rows = repo.conn.execute(
        """SELECT * FROM pending_questions
           WHERE agent_type='theme' AND status='pending'
           ORDER BY created_at DESC
           LIMIT ?""",
        (max(1, min(int(limit or 5), 10)),),
    ).fetchall()

    if not rows:
        return success_envelope(
            agent="theme",
            intent="list_theme_candidates",
            chat_preview="现在没有可直接展开的 Theme 候选组。可以先扫描最近内容，或明确要求全库扫描。",
            meta={"candidate_count": 0},
        )

    lines = [f"现在有 {len(rows)} 组可展开成知识卡片的候选：", ""]
    actions = []
    for index, row in enumerate(rows, 1):
        capture_ids = _json_list(row["related_capture_ids"])
        lines.append(f"{index}. {_short_question_text(row['question_text'])}（{len(capture_ids)} 篇材料）")
        actions.append(
            next_action(
                label=f"展开第 {index} 组为知识卡片",
                intent="synthesize_garden",
                payload={"question_id": row["id"], "capture_ids": capture_ids},
            )
        )
    lines.append("")
    lines.append("回复编号即可展开，例如“第一组”。")
    return success_envelope(
        agent="theme",
        intent="list_theme_candidates",
        chat_preview="\n".join(lines),
        next_actions=actions,
        meta={"candidate_count": len(rows)},
    )
```

- [ ] **Step 4: Run focused bridge tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_bridge.py -q
```

Expected: PASS.

---

### Task 2: Expose Candidate Listing As Hermes Tool

**Files:**
- Create: `/Users/pipi/Downloads/hermes-agent-main/tools/theme_candidates_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/__init__.py` only if this repo requires explicit imports; otherwise do not touch it.
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_candidates_tool.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py`

- [ ] **Step 1: Write failing tool registration test**

Create:

```python
def test_theme_candidates_tool_schema_guides_followup_after_empty_recent_scan():
    import tools.theme_candidates_tool as tool

    description = tool.THEME_CANDIDATES_SCHEMA["description"]

    assert "已有候选组" in description
    assert "没有待扫描的新内容" in description
    assert "第一组" in description
    assert "question_id" in description
    assert "不要用 search_my_notes" in description


def test_theme_candidates_tool_registers_in_personal_assistant_toolset():
    import tools.theme_candidates_tool  # noqa: F401
    from tools.registry import registry

    entry = registry.get_entry("theme_candidates")

    assert entry is not None
    assert entry.toolset == "personal-assistant"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_candidates_tool.py -q
```

Expected: FAIL because the tool file does not exist.

- [ ] **Step 3: Add `theme_candidates` tool**

Create `/Users/pipi/Downloads/hermes-agent-main/tools/theme_candidates_tool.py`:

```python
"""Hermes tool: theme_candidates — 展示已有 Theme 候选组."""
from __future__ import annotations

import sys
sys.path.insert(0, "/Users/pipi/hermes-custom")

from bridge.theme_bridge import list_theme_candidates
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

THEME_CANDIDATES_SCHEMA = {
    "name": "theme_candidates",
    "description": (
        "展示已有候选组：当用户在 theme_scan 返回“没有待扫描的新内容”后问"
        "“有哪些内容”“有哪些组”“第一组是哪组”时调用。"
        "本工具只读取已经由 Theme Agent 生成的 pending candidates，不做全库搜索。"
        "如果用户回复“第一组/第二组”等编号，应使用返回 next_actions 里的 question_id "
        "调用 synthesize_garden，不要用 search_my_notes 重新拼材料。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "最多展示多少组，默认 5，最大 10。",
            }
        },
        "required": [],
    },
}


def _handler(args: dict, **kw) -> str:
    try:
        result = list_theme_candidates(limit=int(args.get("limit") or 5))
        return envelope_json(result)
    except Exception as exc:
        return envelope_error_json(agent="theme", intent="list_theme_candidates", exc=exc)


registry.register(
    name="theme_candidates",
    toolset="personal-assistant",
    schema=THEME_CANDIDATES_SCHEMA,
    handler=_handler,
    emoji="🌱",
)
```

- [ ] **Step 4: Run runtime tool tests**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_candidates_tool.py /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py -q
```

Expected: PASS.

---

### Task 3: Tighten `theme_scan` Tool Handoff Wording

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/theme_scan_tool.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_scan_tool.py`

- [ ] **Step 1: Add test for routing "有哪些内容"**

Extend the existing schema test:

```python
def test_theme_scan_schema_routes_existing_candidate_followups_to_theme_candidates():
    import tools.theme_scan_tool as theme_scan_tool

    description = theme_scan_tool.THEME_SCAN_SCHEMA["description"]

    assert "有哪些内容" in description
    assert "theme_candidates" in description
    assert "不要用 search_my_notes" in description
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_scan_tool.py::test_theme_scan_schema_routes_existing_candidate_followups_to_theme_candidates -q
```

Expected: FAIL until the description is updated.

- [ ] **Step 3: Update schema text only**

Add one sentence to `THEME_SCAN_SCHEMA["description"]`:

```python
"如果刚刚返回“没有待扫描的新内容”，而用户追问“有哪些内容/有哪些组/第一组是什么”，"
"应调用 theme_candidates 查看已有候选组，不要用 search_my_notes 临时拼组。"
```

- [ ] **Step 4: Run focused runtime tests**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_scan_tool.py /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_candidates_tool.py -q
```

Expected: PASS.

---

### Task 4: Remove Empty Placeholder Sections From Garden Cards

**Files:**
- Modify: `/Users/pipi/hermes-custom/agents/garden_agent.py`
- Test: `/Users/pipi/hermes-custom/tests/test_garden_agent.py`

- [ ] **Step 1: Write failing test for no placeholders**

Add:

```python
def test_garden_markdown_omits_empty_placeholder_sections():
    from agents.garden_agent import GardenAgent

    agent = GardenAgent(llm_api_key="test")
    markdown = agent._build_synthesis_markdown(
        "AI 工程化",
        {
            "one_liner": "AI 效能来自工程体系。",
            "core_arguments": [
                {"claim": "工具、记忆和检查共同降低错误率。", "source_ids": ["cap1"]}
            ],
            "key_materials": [
                {"type": "case", "content": "自动化钩子能把检查前移。", "capture_id": "cap1"}
            ],
        },
        links=[],
        captures=[
            {"id": "cap1", "title": "AI 工程实践", "file_path": "captures/AI工程实践.md"}
        ],
        existing_cards=[],
        card_kind="viewpoint",
    )

    assert "（待补充）" not in markdown
    assert "暂无关联" not in markdown
    assert "待继续想" not in markdown
    assert "这张卡还需要" not in markdown
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_garden_markdown_omits_empty_placeholder_sections -q
```

Expected: FAIL because placeholders are currently written.

- [ ] **Step 3: Make Markdown builder omit empty sections**

Change `_build_synthesis_markdown` so it:

- Adds argument/material sections only when there is content.
- Adds `## 关联` only when links exist.
- Does not append `## 待继续想`.
- Keeps `## 来源笔记` because it is useful and concrete.

Minimal implementation approach:

```python
if arguments:
    lines.extend(["", f"## {h2}"])
    ...

if materials:
    lines.extend(["", f"## {h3}"])
    ...

if h4 == "来源依据":
    lines.extend(["", f"## {h4}"])
    self._append_source_notes(lines, captures)
else:
    lines.extend(["", "## 来源笔记"])
    self._append_source_notes(lines, captures)

if links:
    lines.extend(["", f"## {h5}"])
    ...
```

- [ ] **Step 4: Run focused Garden tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_garden_agent.py /Users/pipi/hermes-custom/tests/test_garden_bridge.py -q
```

Expected: PASS.

---

### Task 5: Verification, Restart, And Manual Smoke Test

**Files:**
- No code files.

- [ ] **Step 1: Run custom test suite**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/ -q
```

Expected: all tests pass.

- [ ] **Step 2: Run runtime tool tests**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_scan_tool.py /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_candidates_tool.py /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_garden_draft_tools.py /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Restart Hermes with clean session memory**

Run:

```bash
ts=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "/Users/pipi/.hermes/session_backups/codex-$ts"
find /Users/pipi/.hermes/sessions -maxdepth 1 -type f -print -exec mv {} "/Users/pipi/.hermes/session_backups/codex-$ts/" \;
pkill -f "hermes_cli.main gateway run" || true
cd /Users/pipi/Downloads/hermes-agent-main
nohup .venv/bin/python -m hermes_cli.main gateway run --replace > "/Users/pipi/.hermes/logs/gateway-restart-codex-$ts.log" 2>&1 &
```

Expected: one gateway process starts and logs show `Gateway running with 2 platform(s)`.

- [ ] **Step 4: Manual WeChat smoke test**

Ask:

```text
扫描最近保存的内容，看看有没有可以整理成知识卡片的关联
```

If it says no new content, ask:

```text
有哪些内容
```

Expected:

- Main Agent calls `theme_candidates`, not `search_my_notes`.
- It shows numbered candidate groups from pending Theme questions.
- Replying `第一组` calls `synthesize_garden` with `question_id`.
- Garden preview lists readable source titles.
- `存入花园` writes an Obsidian card without empty placeholder sections.

---

## Self-Review

- Spec coverage: Covers the unstable "有哪些内容" follow-up, structured numbered group selection, Garden source readability, and empty placeholder cleanup.
- Placeholder scan: This plan contains no implementation placeholders; all tests and code shapes are explicit.
- Type consistency: Uses existing `success_envelope`, `next_action`, `pending_questions`, `question_id`, and `capture_ids` contracts already present in the codebase.

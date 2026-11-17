---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Save Answer To Knowledge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "save this good AI answer" flow that turns a selected Hermes answer into a reviewable knowledge draft, then writes it to the right Obsidian artifact only after user confirmation.

**Architecture:** Reuse the existing Reading Session storage instead of creating another knowledge subsystem. A new answer-draft bridge creates `reading_sessions`, `reading_turns`, `reading_evidence`, and `reading_drafts` from either the Knowledge Workbench UI or a Hermes tool call. The Workbench reviews and commits these drafts alongside Capture drafts, while strict target rules prevent multi-source answers from being silently merged into the wrong Capture.

**Tech Stack:** Python, SQLite, FastAPI dashboard APIs, Hermes tool registry, React/TypeScript Knowledge Workbench, Obsidian Markdown vault, pytest, Vite build.

---

## 1. Product Contract

### 1.1 User Problem

The user may receive a high-quality Hermes answer during Knowledge Chat and want to preserve the understanding. This is different from saving an external article:

- The source is an AI answer plus the user's question.
- The answer may cite several Captures, Garden Cards, web pages, or raw reasoning.
- It should not automatically pollute `captures/`.
- It should still be reviewable before becoming long-term knowledge.

### 1.2 User-Facing Flow

```text
Good Hermes answer
-> User clicks "存为知识" or says "把刚才这段存起来"
-> System creates a reviewable answer draft
-> User checks it in Knowledge Workbench
-> User edits or confirms
-> System writes to Obsidian + DB + index-maintenance path
```

### 1.3 Storage Decision Rules

The first implementation supports three output shapes:

| Shape | When To Use | Final Location |
| --- | --- | --- |
| `local_learning_note` | Default for a useful explanation, especially when multiple sources are involved | `learning/{title}-{draft_id}.md` |
| `knowledge_card` | When the answer contains reusable viewpoint, method, fact, or risk | `garden/{title}-{garden_card_id}.md` |
| `enhance_capture` | Only when there is one explicit target Capture | Existing `captures/*.md`, with backup |

`enhance_capture` must not be selected automatically when an answer has multiple material sources. If the user asks to "补进主笔记" but the system sees multiple possible Captures, return a clarification response listing candidate titles and ask the user to choose one.

### 1.4 Draft Content Format

Answer drafts use `reading_drafts` and store the generated Markdown in `draft_text`.

For `local_learning_note`, final Markdown body:

```markdown
# {title}

## 我当时的问题

{user_question}

## Hermes 的解释

{assistant_answer_reorganized}

## 可沉淀的理解

{concise_takeaway}

## 参考来源

- 原文/知识库/外部资料/AI 解释：{actual_sources}
```

Frontmatter for the committed file:

```yaml
type: learning_note
source_type: ai_discussion
reading_session_id: "{reading_session_id}"
hermes_session_id: "{hermes_session_id}"
assistant_message_id: "{assistant_message_id}"
created: "{iso_datetime}"
```

For `knowledge_card`, use the existing Garden Card structure and commit path. For `enhance_capture`, use the existing enhanced Capture backup-and-write path.

---

## 2. File Structure

### `/Users/pipi/hermes-custom`

- Create: `bridge/answer_bridge.py`
  - Owns answer-draft creation, output-type normalization, source analysis, target Capture gating, and result envelopes.
- Modify: `bridge/reading_bridge.py`
  - Reuse commit behavior; minimally adjust draft formatting and metadata handling if answer drafts need richer frontmatter.
- Modify: `db/reading_repo.py`
  - Add small query helpers for open reading drafts if needed by dashboard APIs.
- Create: `tests/test_answer_bridge.py`
  - Focused tests for output selection, multi-source target guard, draft creation, and evidence recording.

### `/Users/pipi/Downloads/hermes-agent-main`

- Create: `tools/save_answer_to_knowledge_tool.py`
  - Hermes runtime tool for natural-language "save the previous answer" requests.
- Modify: `toolsets.py`
  - Register `save_answer_to_knowledge` in `personal-assistant`.
- Modify: `skills/personal-assistant/SKILL.md`
  - Tell the main Agent when to call this tool, and when not to call Capture/Garden directly.
- Modify: `hermes_cli/web_server.py`
  - Add Knowledge Workbench APIs for answer-draft creation and reading-draft review/commit.
- Modify: `tests/hermes_cli/test_web_server.py`
  - API tests for answer-draft endpoint and reading-draft detail endpoint.
- Create: `tests/tools/test_save_answer_to_knowledge_tool.py`
  - Tool schema/handler tests.
- Modify: `web/src/lib/api.ts`
  - Add TypeScript types and API client methods for answer drafts and reading drafts.
- Modify: `web/src/lib/knowledgeChatState.ts`
  - Preserve stable message ids and metadata needed by "save this answer".
- Modify: `web/src/pages/KnowledgeChatPage.tsx`
  - Add "存为知识" action on assistant messages, create draft, show review mode, and commit reading drafts.

---

## 3. Implementation Tasks

### Task 1: Add Answer Draft Bridge

**Files:**
- Create: `/Users/pipi/hermes-custom/bridge/answer_bridge.py`
- Test: `/Users/pipi/hermes-custom/tests/test_answer_bridge.py`

- [x] **Step 1: Write failing tests for output routing**

Create `/Users/pipi/hermes-custom/tests/test_answer_bridge.py`:

```python
import sqlite3

from bridge.answer_bridge import (
    choose_answer_output_type,
    source_capture_candidates,
)


def test_defaults_multi_source_answer_to_learning_note():
    sources = [
        {"type": "capture_note", "id": "cap_a", "title": "A"},
        {"type": "capture_note", "id": "cap_b", "title": "B"},
    ]

    decision = choose_answer_output_type(
        instruction="把刚才这段存起来",
        requested_output_type="auto",
        sources=sources,
        target_capture_id="",
    )

    assert decision["output_type"] == "local_learning_note"
    assert decision["target_capture_id"] == ""
    assert decision["needs_target_choice"] is False


def test_requires_explicit_target_for_multi_source_capture_enhancement():
    sources = [
        {"type": "capture_note", "id": "cap_a", "title": "A"},
        {"type": "capture_note", "id": "cap_b", "title": "B"},
    ]

    decision = choose_answer_output_type(
        instruction="补进主笔记",
        requested_output_type="enhance_capture",
        sources=sources,
        target_capture_id="",
    )

    assert decision["output_type"] == ""
    assert decision["needs_target_choice"] is True
    assert decision["candidate_captures"] == [
        {"id": "cap_a", "title": "A"},
        {"id": "cap_b", "title": "B"},
    ]


def test_allows_enhance_capture_with_explicit_target():
    sources = [
        {"type": "capture_note", "id": "cap_a", "title": "A"},
        {"type": "capture_note", "id": "cap_b", "title": "B"},
    ]

    decision = choose_answer_output_type(
        instruction="补进 A 这篇",
        requested_output_type="enhance_capture",
        sources=sources,
        target_capture_id="cap_a",
    )

    assert decision["output_type"] == "enhance_capture"
    assert decision["target_capture_id"] == "cap_a"
    assert decision["needs_target_choice"] is False


def test_source_capture_candidates_dedupes_and_ignores_empty_ids():
    sources = [
        {"type": "capture_note", "id": "cap_a", "title": "A"},
        {"type": "knowledge_base", "source_id": "cap_a", "title": "A again"},
        {"type": "web", "id": "", "title": "External"},
    ]

    assert source_capture_candidates(sources) == [{"id": "cap_a", "title": "A"}]
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_answer_bridge.py -q
```

Expected: FAIL because `bridge.answer_bridge` does not exist.

- [x] **Step 3: Implement routing helpers**

Create `/Users/pipi/hermes-custom/bridge/answer_bridge.py`:

```python
"""Bridge for saving high-quality Hermes answers as reviewable knowledge drafts."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.capture_agent import load_config
from bridge.result_envelope import artifact_ref, error_envelope, next_action, success_envelope
from bridge.run_ledger import run_with_ledger
from db.reading_repo import ReadingRepo
from db.schema import init_db

OUTPUT_TYPES = {"auto", "local_learning_note", "knowledge_card", "enhance_capture"}


def source_capture_candidates(sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    candidates: list[dict[str, str]] = []
    for item in sources or []:
        source_type = str(item.get("type") or item.get("source_kind") or "").strip()
        source_id = str(item.get("id") or item.get("source_id") or "").strip()
        if source_type not in {"capture", "capture_note", "knowledge_base"}:
            continue
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        candidates.append(
            {
                "id": source_id,
                "title": str(item.get("title") or source_id).strip(),
            }
        )
    return candidates


def choose_answer_output_type(
    *,
    instruction: str,
    requested_output_type: str,
    sources: list[dict[str, Any]],
    target_capture_id: str,
) -> dict[str, Any]:
    requested = (requested_output_type or "auto").strip()
    if requested not in OUTPUT_TYPES:
        requested = "auto"

    candidates = source_capture_candidates(sources)
    wants_card = bool(re.search(r"卡片|知识卡|可复用", instruction or ""))
    wants_capture = bool(re.search(r"补进|合并|主笔记|原文增强", instruction or ""))

    if requested == "knowledge_card" or (requested == "auto" and wants_card):
        return {
            "output_type": "knowledge_card",
            "target_capture_id": "",
            "needs_target_choice": False,
            "candidate_captures": candidates,
        }

    if requested == "enhance_capture" or (requested == "auto" and wants_capture):
        if target_capture_id:
            return {
                "output_type": "enhance_capture",
                "target_capture_id": target_capture_id,
                "needs_target_choice": False,
                "candidate_captures": candidates,
            }
        if len(candidates) == 1:
            return {
                "output_type": "enhance_capture",
                "target_capture_id": candidates[0]["id"],
                "needs_target_choice": False,
                "candidate_captures": candidates,
            }
        return {
            "output_type": "",
            "target_capture_id": "",
            "needs_target_choice": True,
            "candidate_captures": candidates,
        }

    return {
        "output_type": "local_learning_note",
        "target_capture_id": "",
        "needs_target_choice": False,
        "candidate_captures": candidates,
    }
```

- [x] **Step 4: Run routing tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_answer_bridge.py -q
```

Expected: PASS.

- [x] **Step 5: Add failing test for draft creation**

Append to `/Users/pipi/hermes-custom/tests/test_answer_bridge.py`:

```python
from bridge import answer_bridge
from db.schema import init_db


def test_save_answer_creates_learning_note_draft(tmp_path, monkeypatch):
    db_path = tmp_path / "captures.db"
    conn = init_db(str(db_path))
    conn.row_factory = sqlite3.Row

    monkeypatch.setattr(
        answer_bridge,
        "load_config",
        lambda _path: type(
            "Cfg",
            (),
            {
                "db_path": str(db_path),
                "vault_path": str(tmp_path / "vault"),
            },
        )(),
    )

    result = answer_bridge.save_answer_to_knowledge(
        hermes_session_id="session-1",
        user_message="这个概念为什么重要？",
        assistant_answer="它重要，因为它把一次性回答变成可复用的判断框架。",
        instruction="把刚才这段存起来",
        output_type="auto",
        assistant_message_id="assistant-1",
        sources=[
            {"type": "capture_note", "id": "cap_a", "title": "材料 A"},
            {"type": "capture_note", "id": "cap_b", "title": "材料 B"},
        ],
    )

    assert result["ok"] is True
    assert result["meta"]["output_type"] == "local_learning_note"
    draft_id = result["meta"]["reading_draft_id"]

    row = conn.execute("SELECT * FROM reading_drafts WHERE id = ?", (draft_id,)).fetchone()
    assert row["output_type"] == "local_learning_note"
    assert "这个概念为什么重要" in row["draft_text"]
    assert "它重要，因为" in row["draft_text"]
```

- [x] **Step 6: Implement `save_answer_to_knowledge`**

Add to `/Users/pipi/hermes-custom/bridge/answer_bridge.py`:

```python
_config = None
_repo: ReadingRepo | None = None


def _init():
    global _config, _repo
    if _config is None:
        _config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
        conn = init_db(_config.db_path)
        _repo = ReadingRepo(conn)
    return _config, _repo


def _run(intent: str, input_ref: dict[str, Any], operation):
    _, repo = _init()
    return run_with_ledger(
        repo.conn,
        agent="reading",
        intent=intent,
        trigger="knowledge_workbench",
        input_ref=input_ref,
        operation=operation,
    )


def save_answer_to_knowledge(
    *,
    hermes_session_id: str = "",
    user_message: str = "",
    assistant_answer: str = "",
    instruction: str = "",
    output_type: str = "auto",
    assistant_message_id: str = "",
    sources: list[dict[str, Any]] | None = None,
    target_capture_id: str = "",
) -> dict:
    sources = sources or []
    return _run(
        "save_answer_to_knowledge",
        {
            "hermes_session_id": hermes_session_id,
            "assistant_message_id": assistant_message_id,
            "output_type": output_type,
            "source_count": len(sources),
        },
        lambda: _save_answer_to_knowledge(
            hermes_session_id=hermes_session_id,
            user_message=user_message,
            assistant_answer=assistant_answer,
            instruction=instruction,
            output_type=output_type,
            assistant_message_id=assistant_message_id,
            sources=sources,
            target_capture_id=target_capture_id,
        ),
    )


def _save_answer_to_knowledge(
    *,
    hermes_session_id: str,
    user_message: str,
    assistant_answer: str,
    instruction: str,
    output_type: str,
    assistant_message_id: str,
    sources: list[dict[str, Any]],
    target_capture_id: str,
) -> dict:
    _, repo = _init()
    decision = choose_answer_output_type(
        instruction=instruction,
        requested_output_type=output_type,
        sources=sources,
        target_capture_id=target_capture_id,
    )
    if decision["needs_target_choice"]:
        choices = "\n".join(
            f"- {item['title']}（{item['id']}）"
            for item in decision["candidate_captures"]
        ) or "- 没有识别到明确 Capture 来源"
        return success_envelope(
            agent="reading",
            intent="save_answer_to_knowledge",
            chat_preview=(
                "这段回答涉及多个材料，我不能自动判断要补进哪一篇主笔记。\n\n"
                f"候选材料：\n{choices}\n\n"
                "你可以改成保存为学习笔记/知识卡片，或明确说要补进哪一篇。"
            ),
            meta={
                "needs_target_choice": True,
                "candidate_captures": decision["candidate_captures"],
            },
        )

    title = _answer_draft_title(user_message=user_message, assistant_answer=assistant_answer)
    session = repo.create_session(
        source_type="conversation",
        source_id=hermes_session_id,
        source_title=title,
        focus=_clip(user_message or instruction, 120),
        meta={
            "origin": "assistant_answer",
            "hermes_session_id": hermes_session_id,
            "assistant_message_id": assistant_message_id,
            "instruction": instruction,
        },
    )
    turn = repo.add_turn(
        session["id"],
        user_message=user_message,
        assistant_answer=assistant_answer,
        focus=_clip(user_message or instruction, 120),
        meta={"assistant_message_id": assistant_message_id},
    )
    for item in sources[:12]:
        repo.add_evidence(
            session_id=session["id"],
            turn_id=turn["id"],
            source_kind=str(item.get("type") or item.get("source_kind") or ""),
            source_id=str(item.get("id") or item.get("source_id") or ""),
            title=str(item.get("title") or item.get("label") or ""),
            url=str(item.get("url") or ""),
            excerpt=str(item.get("excerpt") or item.get("section") or ""),
            used_for="answer_draft_source",
        )

    draft_text = _build_answer_draft_text(
        title=title,
        user_message=user_message,
        assistant_answer=assistant_answer,
        output_type=decision["output_type"],
        sources=sources,
    )
    draft = repo.create_draft(
        session_id=session["id"],
        output_type=decision["output_type"],
        target_capture_id=decision["target_capture_id"],
        title=title,
        summary=_clip(assistant_answer, 180),
        draft_text=draft_text,
        meta={
            "origin": "assistant_answer",
            "hermes_session_id": hermes_session_id,
            "assistant_message_id": assistant_message_id,
            "instruction": instruction,
            "sources": sources,
        },
    )
    label = {
        "local_learning_note": "学习笔记",
        "knowledge_card": "知识卡片",
        "enhance_capture": "主笔记增强",
    }[decision["output_type"]]
    return success_envelope(
        agent="reading",
        intent="save_answer_to_knowledge",
        chat_preview=(
            f"已生成{label}草稿：《{title}》\n\n"
            f"草稿编号：{draft['id']}\n"
            "你可以在右侧/草稿审阅区检查，满意后确认保存。"
        ),
        durable_artifacts=[
            artifact_ref(
                type="reading_draft",
                id=draft["id"],
                title=title,
                status=draft["status"],
            )
        ],
        next_actions=[
            next_action(
                label="确认保存",
                intent="commit_reading_artifact",
                payload={"draft_id": draft["id"]},
                requires_confirmation=True,
            )
        ],
        meta={
            "reading_session_id": session["id"],
            "reading_draft_id": draft["id"],
            "output_type": decision["output_type"],
            "target_capture_id": decision["target_capture_id"],
            "needs_target_choice": False,
        },
    )


def _answer_draft_title(*, user_message: str, assistant_answer: str) -> str:
    base = re.sub(r"\s+", " ", user_message or assistant_answer).strip()
    base = re.sub(r"[#*_`>\\[\\]()]+" , "", base)
    return (base[:36].strip(" ，。；：") or "对话沉淀笔记")


def _build_answer_draft_text(
    *,
    title: str,
    user_message: str,
    assistant_answer: str,
    output_type: str,
    sources: list[dict[str, Any]],
) -> str:
    source_lines = []
    for item in sources[:12]:
        label = str(item.get("title") or item.get("label") or item.get("url") or item.get("id") or "").strip()
        kind = str(item.get("type") or item.get("source_kind") or "AI 解释").strip()
        if label:
            source_lines.append(f"- {kind}：{label}")
    if not source_lines:
        source_lines.append("- AI 解释：来自当前 Hermes 对话")
    if output_type == "knowledge_card":
        return (
            f"# {title}\n\n"
            "## 内容\n\n"
            f"{assistant_answer.strip()}\n\n"
            "## 来源\n\n"
            + "\n".join(source_lines)
            + "\n"
        )
    return (
        f"# {title}\n\n"
        "## 我当时的问题\n\n"
        f"{user_message.strip() or '这轮对话中有一段值得保存的解释。'}\n\n"
        "## Hermes 的解释\n\n"
        f"{assistant_answer.strip()}\n\n"
        "## 可沉淀的理解\n\n"
        f"{_clip(assistant_answer, 240)}\n\n"
        "## 参考来源\n\n"
        + "\n".join(source_lines)
        + "\n"
    )


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"
```

- [x] **Step 7: Run answer bridge tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_answer_bridge.py -q
```

Expected: PASS.

---

### Task 2: Add Hermes Runtime Tool

**Files:**
- Create: `/Users/pipi/Downloads/hermes-agent-main/tools/save_answer_to_knowledge_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/toolsets.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/skills/personal-assistant/SKILL.md`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_save_answer_to_knowledge_tool.py`

- [x] **Step 1: Write failing tool test**

Create `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_save_answer_to_knowledge_tool.py`:

```python
import json


def test_save_answer_tool_registers_in_personal_assistant():
    import tools.save_answer_to_knowledge_tool  # noqa: F401
    from tools.registry import registry

    tools = registry.for_toolset("personal-assistant")
    names = {tool.name for tool in tools}

    assert "save_answer_to_knowledge" in names


def test_save_answer_tool_handler_returns_envelope(monkeypatch):
    import tools.save_answer_to_knowledge_tool as tool

    monkeypatch.setattr(
        tool,
        "do_save",
        lambda **kwargs: {
            "ok": True,
            "USER_VISIBLE_REPLY": "已生成学习笔记草稿",
            "meta": {"reading_draft_id": "draft1"},
        },
    )

    raw = tool._handler(
        {
            "hermes_session_id": "session1",
            "user_message": "为什么重要？",
            "assistant_answer": "因为它可复用。",
            "instruction": "存起来",
            "output_type": "auto",
            "assistant_message_id": "assistant1",
            "sources": [],
            "target_capture_id": "",
        }
    )

    payload = json.loads(raw)
    assert payload["ok"] is True
    assert "已生成学习笔记草稿" in payload["USER_VISIBLE_REPLY"]
```

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_save_answer_to_knowledge_tool.py -q
```

Expected: FAIL because the tool file does not exist.

- [x] **Step 3: Create tool wrapper**

Create `/Users/pipi/Downloads/hermes-agent-main/tools/save_answer_to_knowledge_tool.py`:

```python
"""Hermes tool: save_answer_to_knowledge — 保存优秀回答为可审阅知识草稿."""
from __future__ import annotations

import sys

HERMES_CUSTOM = "/Users/pipi/hermes-custom"
if HERMES_CUSTOM not in sys.path:
    sys.path.insert(0, HERMES_CUSTOM)

from bridge.answer_bridge import save_answer_to_knowledge as do_save
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

SAVE_ANSWER_SCHEMA = {
    "name": "save_answer_to_knowledge",
    "description": (
        "把当前对话中一段高质量 AI 回答保存为可审阅知识草稿。"
        "适合用户说“把刚才这段存起来”“这个解释很好，做成卡片”"
        "“保存这轮讨论”等。不要直接写 Obsidian；工具只生成 reading_draft。"
        "如果用户要求补进主笔记但回答涉及多个 Capture，工具会要求用户选择目标。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hermes_session_id": {"type": "string", "description": "当前 Hermes 会话 id。"},
            "user_message": {"type": "string", "description": "被保存回答对应的用户问题。"},
            "assistant_answer": {"type": "string", "description": "要保存的 AI 回答原文。"},
            "instruction": {"type": "string", "description": "用户关于保存形态的原话。"},
            "output_type": {
                "type": "string",
                "enum": ["auto", "local_learning_note", "knowledge_card", "enhance_capture"],
                "description": "保存形态；不确定传 auto。",
            },
            "assistant_message_id": {"type": "string", "description": "前端消息 id；没有则为空。"},
            "sources": {
                "type": "array",
                "description": "实际引用来源，来自回答引用、工具结果或主 Agent 上下文。",
                "items": {"type": "object"},
            },
            "target_capture_id": {
                "type": "string",
                "description": "明确要补进的 Capture id；不明确时留空。",
            },
        },
        "required": ["assistant_answer"],
    },
}


def _handler(args: dict, **kw) -> str:
    try:
        return envelope_json(
            do_save(
                hermes_session_id=args.get("hermes_session_id", ""),
                user_message=args.get("user_message", ""),
                assistant_answer=args.get("assistant_answer", ""),
                instruction=args.get("instruction", ""),
                output_type=args.get("output_type", "auto"),
                assistant_message_id=args.get("assistant_message_id", ""),
                sources=args.get("sources") or [],
                target_capture_id=args.get("target_capture_id", ""),
            )
        )
    except Exception as exc:
        return envelope_error_json(agent="reading", intent="save_answer_to_knowledge", exc=exc)


registry.register(
    name=SAVE_ANSWER_SCHEMA["name"],
    toolset="personal-assistant",
    schema=SAVE_ANSWER_SCHEMA,
    handler=_handler,
    emoji="💾",
)
```

- [x] **Step 4: Register/import the tool**

Update `/Users/pipi/Downloads/hermes-agent-main/toolsets.py` to include the module wherever `personal-assistant` tools are imported. Add:

```python
"tools.save_answer_to_knowledge_tool",
```

near the existing `tools.reading_session_tool` entry.

- [x] **Step 5: Update skill prompt**

Append to `/Users/pipi/Downloads/hermes-agent-main/skills/personal-assistant/SKILL.md`:

```markdown
### 保存优秀回答为知识

当用户说“把刚才这段存起来”“这个回答很好，做成卡片”“保存这轮讨论”等，优先调用 `save_answer_to_knowledge`。

规则：
- 只保存真正要沉淀的回答，不把整段聊天流水当成笔记。
- 不确定保存形态时传 `output_type="auto"`。
- 用户明确说“做成卡片”时传 `knowledge_card`。
- 用户明确说“补进某篇主笔记”且能确定唯一 Capture 时传 `enhance_capture` 和 `target_capture_id`。
- 如果回答引用多个 Capture，不要自行选择一篇主笔记；交给工具返回候选或默认保存为学习笔记。
- 生成草稿后，把 `USER_VISIBLE_REPLY` 原样告诉用户，等待用户审阅和确认。
```

- [x] **Step 6: Run tool tests**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_save_answer_to_knowledge_tool.py /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py -q
```

Expected: PASS.

---

### Task 3: Add Dashboard APIs For Answer Drafts

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/hermes_cli/test_web_server.py`

- [x] **Step 1: Write failing API tests**

Append to `/Users/pipi/Downloads/hermes-agent-main/tests/hermes_cli/test_web_server.py`:

```python
def test_post_knowledge_answer_draft_calls_bridge(monkeypatch, tmp_path, client):
    import hermes_cli.web_server as web_server

    monkeypatch.setattr(web_server, "_knowledge_custom_root", lambda: tmp_path)
    called = {}

    def fake_save(**kwargs):
        called.update(kwargs)
        return {
            "ok": True,
            "USER_VISIBLE_REPLY": "已生成学习笔记草稿",
            "meta": {"reading_draft_id": "draft1"},
        }

    monkeypatch.setitem(__import__("sys").modules, "bridge.answer_bridge", type("M", (), {
        "save_answer_to_knowledge": staticmethod(fake_save),
    }))

    response = client.post(
        "/api/knowledge/answer-drafts",
        json={
            "hermes_session_id": "session1",
            "user_message": "为什么重要？",
            "assistant_answer": "因为它可复用。",
            "instruction": "存起来",
            "output_type": "auto",
            "assistant_message_id": "assistant1",
            "sources": [{"type": "capture_note", "id": "cap1", "title": "A"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["meta"]["reading_draft_id"] == "draft1"
    assert called["assistant_answer"] == "因为它可复用。"


def test_get_knowledge_reading_draft_returns_full_text(monkeypatch, tmp_path, client):
    import sqlite3
    import hermes_cli.web_server as web_server
    from db.schema import init_db

    db_path = tmp_path / "captures.db"
    conn = init_db(str(db_path))
    conn.execute(
        """INSERT INTO reading_drafts
           (id, session_id, output_type, title, summary, draft_text, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("draft1", "session1", "local_learning_note", "标题", "摘要", "# 标题\n正文", "open"),
    )
    conn.commit()

    monkeypatch.setattr(web_server, "_knowledge_db_path", lambda: db_path)

    response = client.get("/api/knowledge/reading-drafts/draft1")

    assert response.status_code == 200
    assert response.json()["draft"]["draft_text"] == "# 标题\n正文"
```

- [x] **Step 2: Run API tests to verify failure**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest /Users/pipi/Downloads/hermes-agent-main/tests/hermes_cli/test_web_server.py -q
```

Expected: FAIL because routes do not exist or fixtures need local adjustment. If the project uses a different TestClient fixture name, update only the fixture name to match existing tests.

- [x] **Step 3: Add request bodies and row mapper**

In `/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py`, near the Knowledge API models, add:

```python
class _KnowledgeAnswerDraftBody(BaseModel):
    hermes_session_id: str = ""
    user_message: str = ""
    assistant_answer: str
    instruction: str = ""
    output_type: str = "auto"
    assistant_message_id: str = ""
    sources: list[dict] = []
    target_capture_id: str = ""


def _reading_draft_row(row: sqlite3.Row, *, full: bool = False) -> dict:
    data = dict(row)
    result = {
        "id": data.get("id", ""),
        "session_id": data.get("session_id", ""),
        "output_type": data.get("output_type", ""),
        "target_capture_id": data.get("target_capture_id", ""),
        "title": data.get("title", "") or "未命名",
        "summary": data.get("summary", ""),
        "status": data.get("status", ""),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
        "draft_preview": _clip_text(str(data.get("draft_text") or ""), 420),
        "meta": _json_value(str(data.get("meta_json") or "{}"), {}),
    }
    if full:
        result["draft_text"] = str(data.get("draft_text") or "")
    return result
```

- [x] **Step 4: Add API routes**

Add below the capture draft routes in `/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py`:

```python
@app.post("/api/knowledge/answer-drafts")
async def post_knowledge_answer_draft(request: Request, body: _KnowledgeAnswerDraftBody):
    _require_token(request)
    root = _knowledge_custom_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from bridge.answer_bridge import save_answer_to_knowledge
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Answer bridge unavailable: {exc}") from exc
    return save_answer_to_knowledge(
        hermes_session_id=body.hermes_session_id,
        user_message=body.user_message,
        assistant_answer=body.assistant_answer,
        instruction=body.instruction,
        output_type=body.output_type,
        assistant_message_id=body.assistant_message_id,
        sources=body.sources,
        target_capture_id=body.target_capture_id,
    )


@app.get("/api/knowledge/reading-drafts/{draft_id}")
async def get_knowledge_reading_draft(request: Request, draft_id: str):
    _require_token(request)
    with _knowledge_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reading_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Reading draft not found")
    return {"draft": _reading_draft_row(row, full=True)}


@app.post("/api/knowledge/reading-drafts/{draft_id}/commit")
async def post_knowledge_reading_draft_commit(request: Request, draft_id: str):
    _require_token(request)
    root = _knowledge_custom_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from bridge.reading_bridge import commit_reading_artifact
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Reading bridge unavailable: {exc}") from exc
    return commit_reading_artifact(draft_id=draft_id)
```

- [x] **Step 5: Run API tests**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest /Users/pipi/Downloads/hermes-agent-main/tests/hermes_cli/test_web_server.py -q
```

Expected: PASS for the new tests and no regression in existing web server tests.

---

### Task 4: Add Workbench UI Entry And Review Mode

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/web/src/lib/api.ts`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/web/src/lib/knowledgeChatState.ts`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx`

- [x] **Step 1: Extend API client types**

Add to `/Users/pipi/Downloads/hermes-agent-main/web/src/lib/api.ts`:

```ts
export interface KnowledgeAnswerDraftSource {
  type?: string;
  id?: string;
  source_id?: string;
  title?: string;
  label?: string;
  url?: string;
  excerpt?: string;
}

export interface KnowledgeReadingDraft {
  id: string;
  session_id: string;
  output_type: "local_learning_note" | "knowledge_card" | "enhance_capture" | string;
  target_capture_id: string;
  title: string;
  summary: string;
  status: string;
  created_at: string;
  updated_at: string;
  draft_preview: string;
  draft_text?: string;
  meta?: Record<string, unknown>;
}

export interface KnowledgeReadingDraftResponse {
  draft: KnowledgeReadingDraft;
}

export interface KnowledgeAnswerDraftRequest {
  hermes_session_id: string;
  user_message: string;
  assistant_answer: string;
  instruction: string;
  output_type: "auto" | "local_learning_note" | "knowledge_card" | "enhance_capture";
  assistant_message_id: string;
  sources: KnowledgeAnswerDraftSource[];
  target_capture_id?: string;
}
```

Add methods to `api`:

```ts
createKnowledgeAnswerDraft: (body: KnowledgeAnswerDraftRequest) =>
  fetchJSON<{
    ok: boolean;
    USER_VISIBLE_REPLY?: string;
    meta?: Record<string, unknown>;
  }>("/api/knowledge/answer-drafts", {
    method: "POST",
    body: JSON.stringify(body),
  }),
getKnowledgeReadingDraft: (draftId: string) =>
  fetchJSON<KnowledgeReadingDraftResponse>(
    `/api/knowledge/reading-drafts/${encodeURIComponent(draftId)}`,
  ),
commitKnowledgeReadingDraft: (draftId: string) =>
  fetchJSON<Record<string, unknown>>(
    `/api/knowledge/reading-drafts/${encodeURIComponent(draftId)}/commit`,
    { method: "POST" },
  ),
```

- [x] **Step 2: Preserve message ids**

In `/Users/pipi/Downloads/hermes-agent-main/web/src/lib/knowledgeChatState.ts`, keep the current `KnowledgeMessage.id` as the UI message id. Do not regenerate ids inside the "save answer" handler. No schema migration is needed.

- [x] **Step 3: Add local state for answer drafts**

In `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx`, add:

```ts
type WorkbenchMode = "chat" | "draft_review" | "answer_draft_review" | "source" | "result";
type AnswerOutputType = "auto" | "local_learning_note" | "knowledge_card" | "enhance_capture";

const [answerDraftDetail, setAnswerDraftDetail] = useState<KnowledgeReadingDraft | null>(null);
const [answerDraftLoading, setAnswerDraftLoading] = useState(false);
const [answerDraftActionLoading, setAnswerDraftActionLoading] = useState<"create" | "commit" | null>(null);
```

- [x] **Step 4: Add save handler**

Add to `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx`:

```ts
const createAnswerDraft = useCallback(async (
  message: KnowledgeMessage,
  outputType: AnswerOutputType,
) => {
  if (!state.sessionId || message.role !== "assistant") return;
  const messageIndex = state.messages.findIndex((item) => item.id === message.id);
  const previousUser = [...state.messages.slice(0, messageIndex)]
    .reverse()
    .find((item) => item.role === "user");

  setAnswerDraftActionLoading("create");
  setDraftError("");
  setDraftNotice("");
  try {
    const result = await api.createKnowledgeAnswerDraft({
      hermes_session_id: state.sessionId,
      user_message: previousUser?.text ?? "",
      assistant_answer: message.text,
      instruction: outputType === "knowledge_card" ? "做成知识卡片" : "存为知识",
      output_type: outputType,
      assistant_message_id: message.id,
      sources: state.sourceHints.map((source) => ({
        type: source.origin,
        title: source.label,
        url: source.url,
      })),
    });
    const draftId = typeof result.meta?.reading_draft_id === "string"
      ? result.meta.reading_draft_id
      : "";
    setDraftNotice(result.USER_VISIBLE_REPLY || "已生成回答沉淀草稿。");
    if (draftId) {
      const payload = await api.getKnowledgeReadingDraft(draftId);
      setAnswerDraftDetail(payload.draft);
      setSelection({ type: "draft", draftId });
      setMode("answer_draft_review");
    }
  } catch (error) {
    setDraftError(error instanceof Error ? error.message : String(error));
  } finally {
    setAnswerDraftActionLoading(null);
  }
}, [state.sessionId, state.messages, state.sourceHints]);
```

- [x] **Step 5: Add message action UI**

Update `TranscriptMessage` props in `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx`:

```ts
function TranscriptMessage({
  message,
  streaming,
  onSaveAnswer,
}: {
  message: KnowledgeMessage;
  streaming?: boolean;
  onSaveAnswer?: (message: KnowledgeMessage, outputType: AnswerOutputType) => void;
}) {
```

Inside the assistant message header, render:

```tsx
{!streaming && !isUser && onSaveAnswer && (
  <div className="flex items-center gap-1">
    <button
      type="button"
      onClick={() => onSaveAnswer(message, "local_learning_note")}
      className="rounded border border-current/10 px-2 py-1 text-xs text-muted-foreground transition hover:border-primary/30 hover:text-foreground"
    >
      存为笔记
    </button>
    <button
      type="button"
      onClick={() => onSaveAnswer(message, "knowledge_card")}
      className="rounded border border-current/10 px-2 py-1 text-xs text-muted-foreground transition hover:border-primary/30 hover:text-foreground"
    >
      做成卡片
    </button>
  </div>
)}
```

Pass the handler where messages render:

```tsx
{state.messages.map((message) => (
  <TranscriptMessage
    key={message.id}
    message={message}
    onSaveAnswer={createAnswerDraft}
  />
))}
```

- [x] **Step 6: Add answer draft review panel**

Add a simple panel component in `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx`:

```tsx
function AnswerDraftReviewPanel({
  detail,
  loading,
  actionLoading,
  onCommit,
}: {
  detail: KnowledgeReadingDraft | null;
  loading: boolean;
  actionLoading: "create" | "commit" | null;
  onCommit: () => void;
}) {
  if (loading) return <LoadingState text="读取回答沉淀草稿..." />;
  if (!detail) return <EmptyState text="请选择或生成一个回答沉淀草稿。" />;
  return (
    <>
      <div className="shrink-0 border-b border-current/10 p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <span className="inline-flex rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs text-primary">
              {detail.output_type === "knowledge_card" ? "知识卡片草稿" : "学习笔记草稿"}
            </span>
            <h2 className="mt-2 text-lg font-semibold tracking-normal text-foreground">
              {detail.title || "未命名草稿"}
            </h2>
            {detail.summary && (
              <p className="mt-2 text-sm leading-6 text-muted-foreground">{detail.summary}</p>
            )}
          </div>
          <ActionButton
            onClick={onCommit}
            disabled={detail.status !== "open" || !!actionLoading}
            loading={actionLoading === "commit"}
          >
            确认保存
          </ActionButton>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <MarkdownBlock content={detail.draft_text || detail.draft_preview || ""} />
      </div>
    </>
  );
}
```

Render it in the center workspace:

```tsx
{mode === "answer_draft_review" ? (
  <AnswerDraftReviewPanel
    detail={answerDraftDetail}
    loading={answerDraftLoading}
    actionLoading={answerDraftActionLoading}
    onCommit={() => void commitAnswerDraft()}
  />
) : mode === "draft_review" ? (
  <DraftReviewPanel ... />
) : (
  <ChatMode ... />
)}
```

- [x] **Step 7: Add commit handler**

Add to `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx`:

```ts
const commitAnswerDraft = useCallback(async () => {
  if (!answerDraftDetail) return;
  setAnswerDraftActionLoading("commit");
  setDraftError("");
  setDraftNotice("");
  try {
    await api.commitKnowledgeReadingDraft(answerDraftDetail.id);
    setDraftNotice(`已保存：《${answerDraftDetail.title || "回答沉淀草稿"}》。`);
    const payload = await api.getKnowledgeReadingDraft(answerDraftDetail.id);
    setAnswerDraftDetail(payload.draft);
  } catch (error) {
    setDraftError(error instanceof Error ? error.message : String(error));
  } finally {
    setAnswerDraftActionLoading(null);
  }
}, [answerDraftDetail]);
```

- [x] **Step 8: Build frontend**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm run build
```

Expected: TypeScript and Vite build pass.

---

### Task 5: End-To-End Verification

**Files:**
- No code files unless verification exposes failures.

- [x] **Step 1: Run backend tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_answer_bridge.py -q
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest \
  /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_save_answer_to_knowledge_tool.py \
  /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py \
  /Users/pipi/Downloads/hermes-agent-main/tests/hermes_cli/test_web_server.py -q
```

Expected: PASS.

- [x] **Step 2: Run frontend build**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm run build
```

Expected: PASS.

- [ ] **Step 3: Restart Hermes with clean session state**

Run:

```bash
TS=$(date +%Y%m%dT%H%M%S)
BACKUP="/Users/pipi/.hermes/session_backups/sessions-${TS}-answer-draft"
mkdir -p "$BACKUP"
if [ -d /Users/pipi/.hermes/sessions ]; then
  mv /Users/pipi/.hermes/sessions/* "$BACKUP"/ 2>/dev/null || true
fi
mkdir -p /Users/pipi/.hermes/sessions
launchctl kickstart -k "gui/$(id -u)/ai.hermes.gateway"
pkill -f "hermes_cli.main dashboard --host 127.0.0.1 --port 9119" || true
cd /Users/pipi/Downloads/hermes-agent-main
screen -dmS hermes-dashboard /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m hermes_cli.main dashboard --host 127.0.0.1 --port 9119 --no-open --skip-build
sleep 4
curl -sS http://127.0.0.1:9119/api/status
```

Expected: JSON shows `"gateway_running": true`, WeChat connected if configured, and dashboard listening on `127.0.0.1:9119`.

- [ ] **Step 4: Manual Workbench smoke test**

Open:

```text
http://127.0.0.1:9119/knowledge-chat
```

Perform:

1. Ask a knowledge-base question that produces a useful answer.
2. Click `存为笔记` on the assistant answer.
3. Confirm the page switches to answer draft review, not Capture draft review.
4. Confirm the draft shows the original question, Hermes explanation, takeaway, and sources.
5. Click `确认保存`.
6. Confirm a Markdown file appears under `/Users/pipi/Obsidian/hermes-vault/learning/`.

- [ ] **Step 5: Multi-source guard smoke test**

In Knowledge Chat, after an answer cites multiple materials, ask:

```text
把刚才这段补进主笔记
```

Expected:

- Hermes calls `save_answer_to_knowledge`.
- Tool does not choose a Capture silently.
- Response lists candidate materials or suggests saving as learning note/card.

- [ ] **Step 6: Knowledge Card smoke test**

Click `做成卡片` on a useful answer.

Expected:

- Answer draft review shows type `knowledge_card`.
- Confirmation writes a Garden Card under `/Users/pipi/Obsidian/hermes-vault/garden/`.
- The card frontmatter includes `type: garden_card` and `reading_session_id`.

---

## 4. Self-Review

### Spec Coverage

- "好回答也可以入库" is covered by `save_answer_to_knowledge` and Workbench message actions.
- "补进哪个主笔记不现实" is covered by the multi-source target guard.
- "必须先草稿态" is covered by `reading_drafts` and answer draft review mode.
- "页面中 check 后再存" is covered by `answer_draft_review`.
- "存在哪个文件怎么存" is covered by `learning/*.md`, `garden/*.md`, and explicit Capture enhancement rules.

### Placeholder Scan

This plan avoids open placeholder markers and defines concrete files, commands, function names, route paths, and tests.

### Type Consistency

- Frontend `output_type` values match backend `choose_answer_output_type`.
- Dashboard route `/api/knowledge/answer-drafts` returns `meta.reading_draft_id`, which the frontend uses to load `/api/knowledge/reading-drafts/{draft_id}`.
- Commit route uses existing `commit_reading_artifact`, so final writes remain centralized in Reading bridge logic.


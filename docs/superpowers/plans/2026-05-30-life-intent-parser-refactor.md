# Life Intent Parser Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Life Assistant natural-language understanding out of hardcoded execution branches and into a structured intent parser.

**Architecture:** Add a `LifeIntentParser` module that returns a small validated `LifeIntent` object. `LifeAgent.record_life_event()` delegates message understanding to the parser, then executes only structured intents through deterministic code.

**Tech Stack:** Python 3.9, pytest, existing Hermes SQLite repositories, optional DeepSeek/OpenAI-compatible chat completions for runtime fallback.

---

### Task 1: Intent Parser Test Surface

**Files:**
- Create: `tests/test_life_intent_parser.py`
- Modify: `tests/test_life_agent.py`

- [x] **Step 1: Write failing parser tests**

Add tests for supported P1 phrases:

```python
from datetime import datetime

from agents.life_intent_parser import LifeIntentParser


def test_parser_extracts_explicit_wake_time():
    parser = LifeIntentParser()
    intent = parser.parse(
        "中午13点半醒的",
        received_at=datetime.fromisoformat("2026-05-30T13:40:00+08:00"),
    )
    assert intent.intent == "record_event"
    assert intent.event_type == "sleep_end"
    assert intent.occurred_at == "2026-05-30T13:30:00+08:00"


def test_parser_extracts_meal_without_keyword_enum():
    parser = LifeIntentParser()
    intent = parser.parse(
        "午饭吃了腰果饼干",
        received_at=datetime.fromisoformat("2026-05-30T12:20:00+08:00"),
    )
    assert intent.intent == "record_event"
    assert intent.event_type == "meal"
    assert intent.payload["meal_slot"] == "lunch"
    assert intent.payload["content"] == "腰果饼干"


def test_parser_keeps_manual_status_query_out_of_records():
    parser = LifeIntentParser()
    intent = parser.parse(
        "我今天坐了多久？",
        received_at=datetime.fromisoformat("2026-05-30T18:00:00+08:00"),
    )
    assert intent.intent == "manual_status_query"
    assert intent.event_type == ""
```

- [x] **Step 2: Write failing LifeAgent test**

Add a fake parser test to prove the agent executes structured intents:

```python
from agents.life_intent_parser import LifeIntent


class FakeParser:
    def parse(self, message, *, received_at):
        return LifeIntent(
            intent="record_event",
            event_type="sleep_end",
            occurred_at="2026-05-30T13:30:00+08:00",
        )


def test_life_agent_executes_structured_sleep_end_intent(tmp_path):
    conn = init_life_db(str(tmp_path / "life.db"))
    agent = LifeAgent(LifeRepo(conn), parser=FakeParser())
    agent.record_life_event("我睡了", received_at="2026-05-30T02:00:00+08:00")

    result = agent.record_life_event(
        "随便一种醒来表达",
        received_at="2026-05-30T13:40:00+08:00",
    )

    assert result["status"] == "recorded"
    assert result["event"]["event_type"] == "sleep_end"
    assert result["event"]["occurred_at"] == "2026-05-30T13:30:00+08:00"
```

- [x] **Step 3: Run tests and confirm RED**

Run:

```bash
python3 -m pytest tests/test_life_intent_parser.py tests/test_life_agent.py -q
```

Expected: imports fail or assertions fail because `agents.life_intent_parser` and parser injection do not exist yet.

### Task 2: Parser Module

**Files:**
- Create: `agents/life_intent_parser.py`

- [x] **Step 1: Implement minimal parser module**

Create `LifeIntent`, `LifeIntentParser`, and parser helpers. The parser should use exact fast paths for high-confidence commands and local deterministic extraction for the existing supported P1 cases; it should expose an optional LLM adapter seam for later runtime fallback.

- [x] **Step 2: Run parser tests**

Run:

```bash
python3 -m pytest tests/test_life_intent_parser.py -q
```

Expected: all parser tests pass.

### Task 3: LifeAgent Execution Refactor

**Files:**
- Modify: `agents/life_agent.py`
- Modify: `bridge/life_bridge.py`
- Modify: `tests/test_life_agent.py`
- Modify: `tests/test_life_bridge.py`

- [x] **Step 1: Inject parser into LifeAgent**

Change `LifeAgent.__init__` to accept `parser=None` and default to `LifeIntentParser()`.

- [x] **Step 2: Replace message parsing branches with structured execution**

In `record_life_event`, parse once, then dispatch by `intent` and `event_type`. Keep deterministic validators and state updates in `LifeAgent`.

- [x] **Step 3: Keep existing behavior**

Existing tests for sleep, meal, water, exercise, skip, reschedule, undo, and manual status query must keep passing.

- [x] **Step 4: Run Life tests**

Run:

```bash
python3 -m pytest tests/test_life_intent_parser.py tests/test_life_agent.py tests/test_life_bridge.py tests/test_life_repo.py -q
```

Expected: all pass.

### Task 4: Verification, Commit, Runtime

**Files:**
- No extra code files beyond Task 1-3.

- [x] **Step 1: Run Hermes Life tool integration**

Run:

```bash
PYTHONPATH=/Users/pipi/hermes-custom /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_e2e_hermes_tools.py -q -k "life or toolset"
```

Expected: pass.

- [ ] **Step 2: Commit only Life files**

Stage only Life parser, Life agent, Life bridge/tests, and this plan.

- [ ] **Step 3: Restart Hermes gateway**

Use `launchctl kickstart -k "gui/$(id -u)/ai.hermes.gateway"` and verify logs show Weixin connected, Gateway running, and Cron ticker started.

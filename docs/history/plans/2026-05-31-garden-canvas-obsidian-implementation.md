---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Garden Canvas Obsidian Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual Hermes tool that generates readable Obsidian `.canvas` relationship maps for Theme/MOC-centered Garden knowledge networks.

**Architecture:** Keep Canvas generation in `hermes-custom`: `agents/canvas_agent.py` builds deterministic Obsidian Canvas JSON, and `bridge/canvas_bridge.py` resolves themes, captures, Garden cards, writes the Canvas file, and returns a result envelope. `hermes-agent-main` only exposes a thin `generate_canvas` tool and routes user wording such as "关系图/画板/Canvas" to the bridge.

**Tech Stack:** Python 3.11, SQLite, existing `CaptureRepo`/`init_db`, Obsidian Canvas JSON, pytest, Hermes result envelopes, runtime tool registry.

---

## File Structure

- Create `agents/canvas_agent.py`: pure Canvas graph builder and serializer. No DB access and no file writes.
- Create `bridge/canvas_bridge.py`: DB/vault resolver, Canvas file writer, result envelope builder.
- Create `tests/test_canvas_agent.py`: unit tests for Canvas JSON, layout, readable labels, node caps, and missing-file warnings.
- Create `tests/test_canvas_bridge.py`: bridge tests for Theme/MOC generation, pending Theme candidate generation, and safe overwrite behavior.
- Modify `/Users/pipi/Downloads/hermes-agent-main/toolsets.py`: add `generate_canvas` to `personal-assistant`.
- Create `/Users/pipi/Downloads/hermes-agent-main/tools/generate_canvas_tool.py`: thin runtime wrapper around `bridge.canvas_bridge.generate_canvas`.
- Create `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_generate_canvas_tool.py`: schema and registration tests.
- Modify `/Users/pipi/Downloads/hermes-agent-main/agent/hermes_custom_envelope_adapter.py`: allow direct WeChat reply for `generate_canvas`.
- Modify `/Users/pipi/Downloads/hermes-agent-main/tests/agent/test_hermes_custom_direct_reply.py`: direct reply coverage.

Do not modify existing Life Assistant files in this pass.

## Task 1: Pure Canvas Agent

**Files:**
- Create: `agents/canvas_agent.py`
- Create: `tests/test_canvas_agent.py`

- [ ] **Step 1: Write failing Canvas serialization tests**

Create `tests/test_canvas_agent.py` with:

```python
from __future__ import annotations

import json

from agents.canvas_agent import (
    CanvasBuildInput,
    CanvasItem,
    CanvasTheme,
    build_obsidian_canvas,
    safe_canvas_filename,
)


def test_build_obsidian_canvas_uses_file_nodes_and_readable_labels():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[
                CanvasItem(id="cap1", title="Building Effective AI Agents", file_path="captures/effective.md"),
            ],
            garden_cards=[
                CanvasItem(id="card1", title="AI Agent 工程化方法卡", file_path="garden/agent-method.md", kind="method"),
            ],
        )
    )

    payload = json.loads(result.json_text)
    assert payload["nodes"][0]["type"] == "file"
    assert payload["nodes"][0]["file"] == "themes/ai-agent.md"
    assert any(node["file"] == "captures/effective.md" for node in payload["nodes"])
    assert any(node["file"] == "garden/agent-method.md" for node in payload["nodes"])
    assert "cap1" not in result.json_text
    assert "AI Agent 工程化方法卡" in result.json_text


def test_build_obsidian_canvas_positions_captures_left_and_cards_right():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[CanvasItem(id="cap1", title="来源", file_path="captures/source.md")],
            garden_cards=[CanvasItem(id="card1", title="方法卡", file_path="garden/method.md", kind="method")],
        )
    )

    payload = json.loads(result.json_text)
    by_file = {node["file"]: node for node in payload["nodes"] if node["type"] == "file"}
    assert by_file["captures/source.md"]["x"] < by_file["themes/ai-agent.md"]["x"]
    assert by_file["garden/method.md"]["x"] > by_file["themes/ai-agent.md"]["x"]


def test_build_obsidian_canvas_caps_large_graph_and_reports_trimmed_items():
    captures = [
        CanvasItem(id=f"cap{i}", title=f"来源{i}", file_path=f"captures/{i}.md")
        for i in range(12)
    ]
    cards = [
        CanvasItem(id=f"card{i}", title=f"卡片{i}", file_path=f"garden/{i}.md", kind="viewpoint")
        for i in range(10)
    ]

    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="大主题",
            theme=CanvasTheme(title="大主题", file_path="themes/big.md"),
            captures=captures,
            garden_cards=cards,
        )
    )

    payload = json.loads(result.json_text)
    file_nodes = [node for node in payload["nodes"] if node["type"] == "file"]
    assert len([node for node in file_nodes if node["file"].startswith("captures/")]) == 10
    assert len([node for node in file_nodes if node["file"].startswith("garden/")]) == 8
    assert "已省略 2 篇来源笔记" in result.warnings
    assert "已省略 2 张 Garden 卡片" in result.warnings


def test_safe_canvas_filename_removes_invalid_path_characters():
    assert safe_canvas_filename('AI/Agent:关系图?') == "AIAgent关系图-关系图.canvas"
```

- [ ] **Step 2: Run Canvas agent tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_canvas_agent.py -q
```

Expected: collection or import failure because `agents.canvas_agent` does not exist.

- [ ] **Step 3: Implement minimal Canvas agent**

Create `agents/canvas_agent.py` with dataclasses:

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


MAX_CAPTURES = 10
MAX_GARDEN_CARDS = 8


@dataclass(frozen=True)
class CanvasTheme:
    title: str
    file_path: str


@dataclass(frozen=True)
class CanvasItem:
    id: str
    title: str
    file_path: str
    kind: str = ""


@dataclass(frozen=True)
class CanvasBuildInput:
    title: str
    theme: CanvasTheme
    captures: list[CanvasItem] = field(default_factory=list)
    garden_cards: list[CanvasItem] = field(default_factory=list)


@dataclass(frozen=True)
class CanvasBuildResult:
    json_text: str
    warnings: list[str]
    node_count: int
    edge_count: int


def safe_canvas_filename(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "", title or "").strip() or "未命名"
    if not cleaned.endswith("关系图"):
        cleaned = f"{cleaned}-关系图"
    return f"{cleaned}.canvas"


def build_obsidian_canvas(data: CanvasBuildInput) -> CanvasBuildResult:
    captures = data.captures[:MAX_CAPTURES]
    cards = data.garden_cards[:MAX_GARDEN_CARDS]
    warnings: list[str] = []
    if len(data.captures) > len(captures):
        warnings.append(f"已省略 {len(data.captures) - len(captures)} 篇来源笔记")
    if len(data.garden_cards) > len(cards):
        warnings.append(f"已省略 {len(data.garden_cards) - len(cards)} 张 Garden 卡片")

    nodes = [_file_node("theme", data.theme.file_path, 0, 0, "4")]
    edges = []
    for index, capture in enumerate(captures):
        node_id = f"capture-{index + 1}"
        nodes.append(_file_node(node_id, capture.file_path, -520, -260 + index * 120, "1"))
        edges.append(_edge(f"edge-capture-{index + 1}", node_id, "theme", "evidence"))
    for index, card in enumerate(cards):
        node_id = f"garden-{index + 1}"
        nodes.append(_file_node(node_id, card.file_path, 520, -220 + index * 130, _color_for_kind(card.kind)))
        edges.append(_edge(f"edge-garden-{index + 1}", "theme", node_id, _label_for_kind(card.kind)))

    payload = {
        "nodes": nodes,
        "edges": edges,
        "metadata": {"generated_by": "hermes", "title": data.title},
    }
    return CanvasBuildResult(
        json_text=json.dumps(payload, ensure_ascii=False, indent=2),
        warnings=warnings,
        node_count=len(nodes),
        edge_count=len(edges),
    )


def _file_node(node_id: str, file_path: str, x: int, y: int, color: str) -> dict:
    return {
        "id": node_id,
        "type": "file",
        "file": file_path,
        "x": x,
        "y": y,
        "width": 360,
        "height": 220,
        "color": color,
    }


def _edge(edge_id: str, from_node: str, to_node: str, label: str) -> dict:
    return {
        "id": edge_id,
        "fromNode": from_node,
        "fromSide": "right",
        "toNode": to_node,
        "toSide": "left",
        "label": label,
    }


def _label_for_kind(kind: str) -> str:
    return {
        "method": "method",
        "caution": "risk",
        "question": "question",
        "fact": "distills",
        "viewpoint": "distills",
    }.get(kind or "", "related")


def _color_for_kind(kind: str) -> str:
    return {
        "method": "2",
        "caution": "1",
        "question": "6",
        "fact": "3",
        "viewpoint": "5",
    }.get(kind or "", "5")
```

- [ ] **Step 4: Run Canvas agent tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_canvas_agent.py -q
```

Expected: `4 passed`.

## Task 2: Canvas Bridge

**Files:**
- Create: `bridge/canvas_bridge.py`
- Create: `tests/test_canvas_bridge.py`

- [ ] **Step 1: Write failing bridge tests**

Create `tests/test_canvas_bridge.py` with tests that:

- build a temp SQLite DB and temp vault;
- insert captures with `file_path`;
- write a `themes/ai-agent.md` file;
- insert Garden cards linked by `capture_ids`;
- call `generate_canvas(theme="AI Agent", db_path=..., vault_path=...)`;
- assert `canvas/AI Agent-关系图.canvas` exists and contains file nodes.

Also add tests for:

- `question_id` from `pending_questions.related_capture_ids`;
- missing Garden cards still creates Capture + Theme Canvas;
- existing non-Hermes Canvas is not overwritten and creates `-Hermes.canvas`.

- [ ] **Step 2: Run bridge tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_canvas_bridge.py -q
```

Expected: failure because `bridge.canvas_bridge` does not exist.

- [ ] **Step 3: Implement bridge resolver and writer**

Create `bridge/canvas_bridge.py` with:

- `generate_canvas(theme="", question_id="", capture_ids=None, garden_card_ids=None, db_path="", vault_path="") -> dict`
- config fallback via `load_config`;
- `run_with_ledger`;
- resolver that:
  - uses `question_id` first if provided;
  - otherwise finds a Theme/MOC by matching `themes/*.md` stem/title;
  - gathers captures from `pending_questions.related_capture_ids`, explicit `capture_ids`, or Garden cards linked to matching captures;
  - gathers Garden cards whose `capture_ids` overlap selected captures;
- writer that:
  - writes to `<vault>/canvas/<safe filename>`;
  - updates Hermes-managed Canvas in place;
  - writes `-Hermes.canvas` if an existing file is not Hermes-managed;
- success envelope with durable artifact type `obsidian_canvas`.

- [ ] **Step 4: Run bridge tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_canvas_bridge.py -q
```

Expected: bridge tests pass.

## Task 3: Runtime Tool Wiring

**Files:**
- Create: `/Users/pipi/Downloads/hermes-agent-main/tools/generate_canvas_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/toolsets.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/agent/hermes_custom_envelope_adapter.py`
- Create: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_generate_canvas_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tests/agent/test_hermes_custom_direct_reply.py`

- [ ] **Step 1: Write failing runtime tests**

Create `tests/tools/test_generate_canvas_tool.py` in `hermes-agent-main`:

```python
def test_generate_canvas_tool_schema_routes_canvas_requests():
    import tools.generate_canvas_tool as tool

    description = tool.GENERATE_CANVAS_SCHEMA["description"]

    assert "Canvas" in description
    assert "关系图" in description
    assert "画板" in description
    assert "Obsidian" in description


def test_generate_canvas_tool_registers_in_personal_assistant_toolset():
    import tools.generate_canvas_tool  # noqa: F401
    from tools.registry import registry

    entry = registry.get_entry("generate_canvas")

    assert entry is not None
    assert entry.toolset == "personal-assistant"
```

Add a direct reply test in `tests/agent/test_hermes_custom_direct_reply.py` asserting `generate_canvas` returns `chat_preview` directly for WeChat.

- [ ] **Step 2: Run runtime tests and verify RED**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest -o addopts='' tests/tools/test_generate_canvas_tool.py tests/agent/test_hermes_custom_direct_reply.py -q
```

Expected: import or assertion failure because the tool is not registered.

- [ ] **Step 3: Implement runtime wrapper**

Create `tools/generate_canvas_tool.py` in `hermes-agent-main`:

```python
"""Hermes tool: generate_canvas — generate Obsidian Canvas relationship maps."""
from __future__ import annotations

import sys

HERMES_CUSTOM = "/Users/pipi/hermes-custom"
if HERMES_CUSTOM not in sys.path:
    sys.path.insert(0, HERMES_CUSTOM)

from bridge.canvas_bridge import generate_canvas
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

GENERATE_CANVAS_SCHEMA = {
    "name": "generate_canvas",
    "description": (
        "为 Obsidian 知识库生成 Canvas 关系图/画板。"
        "当用户说“生成 Canvas”“做成关系图”“把这个主题做成画板”时调用。"
        "优先围绕 Theme/MOC 展开，左侧放来源 captures，右侧放 Garden cards。"
        "本工具只生成 .canvas 文件，不改写原笔记内容。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "theme": {"type": "string", "description": "主题/MOC 名称或自然语言主题。"},
            "question_id": {"type": "string", "description": "可选，Theme candidate 的 pending question id。"},
            "capture_ids": {"type": "array", "items": {"type": "string"}},
            "garden_card_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [],
    },
}


def _handler(args: dict, **kw) -> str:
    try:
        result = generate_canvas(
            theme=args.get("theme", ""),
            question_id=args.get("question_id", ""),
            capture_ids=args.get("capture_ids"),
            garden_card_ids=args.get("garden_card_ids"),
        )
        return envelope_json(result)
    except Exception as exc:
        return envelope_error_json(agent="canvas", intent="generate_canvas", exc=exc)


registry.register(
    name="generate_canvas",
    toolset="personal-assistant",
    schema=GENERATE_CANVAS_SCHEMA,
    handler=_handler,
    emoji="🗺️",
)
```

Modify `toolsets.py` to include `generate_canvas` in `personal-assistant`.

Modify `hermes_custom_envelope_adapter.py` to include `generate_canvas` in `DIRECT_REPLY_TOOL_NAMES`.

- [ ] **Step 4: Run runtime tests and verify GREEN**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest -o addopts='' tests/tools/test_generate_canvas_tool.py tests/agent/test_hermes_custom_direct_reply.py -q
```

Expected: tests pass.

## Task 4: Integration Verification and Restart

**Files:**
- Modify: `tests/test_e2e_hermes_tools.py` if the expected tool list needs `generate_canvas`.

- [ ] **Step 1: Run focused custom tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_canvas_agent.py tests/test_canvas_bridge.py tests/test_e2e_hermes_tools.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run runtime focused tests**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest -o addopts='' tests/tools/test_generate_canvas_tool.py tests/agent/test_hermes_custom_direct_reply.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Manually generate one Canvas from local code**

Run a small Python snippet from `/Users/pipi/hermes-custom`:

```bash
.venv/bin/python - <<'PY'
from bridge.canvas_bridge import generate_canvas
result = generate_canvas(theme="AI Agent")
print(result["status"])
print(result["chat_preview"])
PY
```

Expected: a success envelope or a clear "no matching Theme/MOC" error. If success, verify a `.canvas` file exists under `/Users/pipi/Obsidian/hermes-vault/canvas/`.

- [ ] **Step 4: Restart Hermes and clear sessions**

Back up and clear `/Users/pipi/.hermes/sessions`, restart the gateway, and verify logs show:

- `✓ weixin connected`
- `✓ feishu connected`
- `Gateway running with 2 platform(s)`

- [ ] **Step 5: Stop for user smoke test**

Ask the user to send:

```text
把 AI Agent 这个主题做成关系图
```

Expected user-visible reply: concise Canvas generation summary, no raw Canvas JSON.

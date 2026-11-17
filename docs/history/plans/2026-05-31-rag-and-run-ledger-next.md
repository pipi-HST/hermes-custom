---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# RAG And Run Ledger Next Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Hermes knowledge workflows observable end-to-end, then upgrade `search_my_notes` into a reliable RAG answerer that uses Garden/Theme/Capture context with traceable evidence.

**Architecture:** First implement the Agent Run Ledger from `docs/superpowers/plans/2026-05-31-hermes-agent-run-ledger.md` so every important Agent run records trigger, input, output, evidence, and status. Then keep a single user-facing RAG tool (`search_my_notes`) but improve its internal retrieval package: capture/section hits remain the detailed evidence, while related Garden cards and Theme pages are added as high-level context when available. Every RAG answer writes a ledger entry so later debugging answers "what did it search, what did it use, and why did it answer that way?" without guessing from chat text.

**Tech Stack:** Python, SQLite, existing Hermes Agent Result Envelope, existing `capture_embeddings`, existing `garden_cards`, Obsidian Markdown files, pytest, Hermes runtime tool registry.

---

## Product Decision

Do not split RAG into multiple user-visible modes. The user should keep asking normal questions. Internally, RAG should:

1. Retrieve broadly enough from existing indexed captures and sections.
2. Enrich evidence with related Garden cards and Theme/MOC pages.
3. Answer with citations that point to actual used sources.
4. Record a run ledger entry with enough evidence to audit tool behavior.

The run ledger is not just debugging infrastructure. It is the way Hermes becomes "process driven": each Agent action has a trace, status, durable artifacts, source counts, and completion evidence.

## File Structure

Follow the existing run ledger plan for the ledger base:

- `docs/superpowers/plans/2026-05-31-hermes-agent-run-ledger.md`

Create or modify for RAG v1:

- Modify: `bridge/rag_bridge.py` — build enriched RAG context and return trace metadata.
- Modify: `agents/rag_agent.py` — prompt RAG to use Garden/Theme as orientation and Capture/section as evidence.
- Create: `agents/rag_context.py` — small focused helper to assemble Garden/Theme/Capture context packs.
- Test: `tests/test_rag_context.py` — context enrichment behavior.
- Test: `tests/test_rag_bridge.py` — bridge returns source maps and trace metadata.
- Test: `tests/test_rag_agent.py` — prompt contract and citation formatting.
- Modify after ledger exists: `bridge/run_ledger.py` or RAG bridge instrumentation from the run ledger plan.
- Modify runtime only if needed: `/Users/pipi/Downloads/hermes-agent-main/tools/search_notes_tool.py` — description update only, no new tool.

## Execution Order

### Task 1: Finish Agent Run Ledger P0

**Files:** Use the exact files and steps in `docs/superpowers/plans/2026-05-31-hermes-agent-run-ledger.md`.

- [ ] **Step 1: Implement ledger schema and repo**

Run the plan's Task 1 exactly:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_agent_run_repo.py -q
```

Expected: `tests/test_agent_run_repo.py` passes after `agent_runs` and `agent_run_events` exist.

- [ ] **Step 2: Implement completion evidence helpers**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_run_evidence.py -q
```

Expected: evidence extraction detects Garden draft/card creation and treats RAG no-result searches as completed, not failed.

- [ ] **Step 3: Instrument bridge entrypoints**

Instrument at least:

- `bridge/rag_bridge.py::search_knowledge_base`
- `bridge/theme_bridge.py::run_theme_scan`
- `bridge/garden_bridge.py::synthesize_garden`
- `bridge/garden_bridge.py::commit_garden_draft`

Required result: each tool call creates one `agent_runs` row, and status becomes `success`, `error`, or `incomplete`.

- [ ] **Step 4: Expose runtime status tool**

Add `/Users/pipi/Downloads/hermes-agent-main/tools/agent_run_status_tool.py` from the existing run ledger plan.

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_agent_run_status_tool.py /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py -q
```

Expected: `agent_run_status` is registered in `personal-assistant`.

### Task 2: Add RAG Context Pack Builder

**Files:**
- Create: `agents/rag_context.py`
- Test: `tests/test_rag_context.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_rag_context.py`:

```python
import json

from agents.rag_context import build_rag_context_pack
from db.schema import init_db


def test_context_pack_adds_garden_cards_related_to_capture_hits(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, ?)",
        ("cap1", "captures/A.md", "2026-05-31T00:00:00+00:00"),
    )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, core_arguments, knowledge_links, key_materials,
            my_view, status, capture_ids, created_at, updated_at, review_interval)
           VALUES (?, ?, ?, ?, ?, ?, '', 'seed', ?, ?, ?, '1d')""",
        (
            "g1",
            "AI 工程化",
            "AI 的价值来自工程流程。",
            json.dumps([{"claim": "规则、工具和检查共同提升可靠性。"}], ensure_ascii=False),
            "[]",
            "[]",
            json.dumps(["cap1"], ensure_ascii=False),
            "2026-05-31T00:00:00+00:00",
            "2026-05-31T00:00:00+00:00",
        ),
    )

    pack = build_rag_context_pack(
        conn,
        capture_hits=[{"id": "cap1", "title": "材料 A", "file_path": "captures/A.md"}],
        vault_path=str(tmp_path),
    )

    assert pack["capture_count"] == 1
    assert pack["garden_count"] == 1
    assert pack["garden_cards"][0]["title"] == "AI 工程化"
    assert "工程流程" in pack["garden_cards"][0]["one_liner"]


def test_context_pack_deduplicates_garden_cards(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    for capture_id in ("cap1", "cap2"):
        conn.execute(
            "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, ?)",
            (capture_id, f"captures/{capture_id}.md", "2026-05-31T00:00:00+00:00"),
        )
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, core_arguments, knowledge_links, key_materials,
            my_view, status, capture_ids, created_at, updated_at, review_interval)
           VALUES (?, ?, ?, ?, ?, ?, '', 'seed', ?, ?, ?, '1d')""",
        (
            "g1",
            "同一张卡",
            "同一张卡只出现一次。",
            "[]",
            "[]",
            "[]",
            json.dumps(["cap1", "cap2"], ensure_ascii=False),
            "2026-05-31T00:00:00+00:00",
            "2026-05-31T00:00:00+00:00",
        ),
    )

    pack = build_rag_context_pack(
        conn,
        capture_hits=[
            {"id": "cap1", "title": "材料 1", "file_path": "captures/cap1.md"},
            {"id": "cap2", "title": "材料 2", "file_path": "captures/cap2.md"},
        ],
        vault_path=str(tmp_path),
    )

    assert pack["garden_count"] == 1
```

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_rag_context.py -q
```

Expected: fail because `agents.rag_context` does not exist.

- [ ] **Step 3: Implement minimal context pack**

Create `agents/rag_context.py`:

```python
"""Build enriched context for RAG without changing the public tool surface."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_rag_context_pack(conn, *, capture_hits: list[dict[str, Any]], vault_path: str) -> dict[str, Any]:
    garden_cards = _garden_cards_for_captures(conn, [str(hit.get("id", "")) for hit in capture_hits])
    return {
        "captures": capture_hits,
        "capture_count": len(capture_hits),
        "garden_cards": garden_cards,
        "garden_count": len(garden_cards),
        "theme_pages": _theme_pages_for_captures(capture_hits, vault_path),
        "theme_count": len(_theme_pages_for_captures(capture_hits, vault_path)),
    }


def _garden_cards_for_captures(conn, capture_ids: list[str]) -> list[dict[str, Any]]:
    wanted = {cid for cid in capture_ids if cid}
    if not wanted:
        return []
    rows = conn.execute(
        "SELECT id, title, one_liner, core_arguments, capture_ids FROM garden_cards ORDER BY updated_at DESC"
    ).fetchall()
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        card_capture_ids = set(_json_list(row["capture_ids"]))
        if not (wanted & card_capture_ids):
            continue
        card_id = str(row["id"])
        if card_id in seen:
            continue
        seen.add(card_id)
        cards.append({
            "id": card_id,
            "title": row["title"],
            "one_liner": row["one_liner"],
            "core_arguments": _json_list(row["core_arguments"]),
            "capture_ids": list(card_capture_ids),
        })
        if len(cards) >= 3:
            break
    return cards


def _theme_pages_for_captures(capture_hits: list[dict[str, Any]], vault_path: str) -> list[dict[str, str]]:
    themes: list[dict[str, str]] = []
    seen: set[str] = set()
    vault = Path(vault_path).expanduser()
    for hit in capture_hits:
        file_path = str(hit.get("file_path", ""))
        if not file_path:
            continue
        note_path = vault / file_path
        if not note_path.exists():
            continue
        text = note_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "themes/" not in line:
                continue
            start = line.find("[[themes/")
            if start < 0:
                continue
            end = line.find("]]", start)
            if end < 0:
                continue
            target = line[start + 2:end]
            if target in seen:
                continue
            seen.add(target)
            themes.append({"path": target, "title": target.split("/")[-1]})
            if len(themes) >= 3:
                return themes
    return themes


def _json_list(value) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []
```

- [ ] **Step 4: Run tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_rag_context.py -q
```

Expected: pass.

### Task 3: Feed Garden/Theme Context Into RAG Prompt

**Files:**
- Modify: `agents/rag_agent.py`
- Test: `tests/test_rag_agent.py`

- [ ] **Step 1: Write failing prompt test**

Add to `tests/test_rag_agent.py`:

```python
from agents.rag_agent import build_rag_prompt


def test_rag_prompt_includes_garden_orientation_before_detailed_sources():
    prompt = build_rag_prompt(
        "AI 工程化怎么理解？",
        captures=[{
            "title": "Building Effective AI Agents",
            "body": "从简单方案开始，必要时增加 agent 复杂度。",
        }],
        context_pack={
            "garden_cards": [{
                "title": "AI 工程化",
                "one_liner": "AI 的价值来自工程流程，而不只是模型能力。",
            }],
            "theme_pages": [{"title": "ai-agent", "path": "themes/ai-agent.md"}],
        },
    )

    assert "高层知识卡片" in prompt
    assert "AI 的价值来自工程流程" in prompt
    assert "主题线索" in prompt
    assert "Building Effective AI Agents" in prompt
```

- [ ] **Step 2: Run test to verify red**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_rag_agent.py::test_rag_prompt_includes_garden_orientation_before_detailed_sources -q
```

Expected: fail because `build_rag_prompt` does not accept `context_pack`.

- [ ] **Step 3: Update prompt builder**

Modify `agents/rag_agent.py`:

```python
def build_rag_prompt(question: str, captures: list[dict], context_pack: dict | None = None) -> str:
    parts = [f"用户问题：{question}\n"]
    context_pack = context_pack or {}
    garden_cards = context_pack.get("garden_cards") or []
    theme_pages = context_pack.get("theme_pages") or []

    if garden_cards:
        parts.append("\n高层知识卡片（用于理解方向，不替代原文证据）：")
        for card in garden_cards[:3]:
            parts.append(f"- 《{card.get('title', '')}》：{card.get('one_liner', '')}")

    if theme_pages:
        parts.append("\n主题线索（用于理解材料之间的关系）：")
        for theme in theme_pages[:3]:
            parts.append(f"- {theme.get('title', '')} ({theme.get('path', '')})")

    parts.append("\n知识库相关内容（用 [编号] 引用）：")
    for i, c in enumerate(captures, 1):
        title = c.get("title") or "无标题"
        section = c.get("section_title") or ""
        text = c.get("body") or c.get("story", "")
        heading = f"[{i}] 《{title}》"
        if section:
            heading += f" / {section}"
        parts.append(f"{heading}\n{text[:8000]}\n")
    parts.append("请用 [1][2] 等编号在回答中标注每条信息的来源。Garden/Theme 只能帮助组织答案，不能作为无来源结论。")
    return "\n".join(parts)
```

Update `RagAgent.query` to accept `context_pack: dict | None = None` and pass it into `build_rag_prompt`.

- [ ] **Step 4: Run RAG agent tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_rag_agent.py -q
```

Expected: pass.

### Task 4: Connect Context Pack And Ledger To `search_knowledge_base`

**Files:**
- Modify: `bridge/rag_bridge.py`
- Test: `tests/test_rag_bridge.py`

- [ ] **Step 1: Write failing bridge test**

Add to `tests/test_rag_bridge.py`:

```python
def test_search_knowledge_base_adds_context_pack_meta(monkeypatch, tmp_path):
    from bridge import rag_bridge

    class FakeConfig:
        db_path = str(tmp_path / "captures.db")
        vault_path = str(tmp_path)
        qwen_api_key = "qwen"
        llm_api_key = "llm"
        llm_model = "deepseek-v4-pro"
        llm_base_url = "http://test"

    class FakeEmbedder:
        def __init__(self, api_key): pass
        def embed(self, text): return b"vector"

    class FakeRagAgent:
        def __init__(self, **kwargs): pass
        def query(self, question, results, context_pack=None):
            assert context_pack is not None
            return {"answer": "AI 工程化需要流程支撑。[1]", "sources": [
                {"title": "材料 A", "summary": "", "file_path": "captures/A.md"}
            ]}

    monkeypatch.setattr(rag_bridge, "load_config", lambda path: FakeConfig())
    monkeypatch.setattr(rag_bridge, "Embedder", FakeEmbedder)
    monkeypatch.setattr(rag_bridge, "_indexed_search", lambda query, repo, embedder, limit=5: [
        {"id": "cap1", "title": "材料 A", "file_path": "captures/A.md", "body": "正文"}
    ])
    monkeypatch.setattr(rag_bridge, "RagAgent", FakeRagAgent)
    rag_bridge._config = None
    rag_bridge._repo = None

    envelope = rag_bridge.search_knowledge_base("AI 工程化是什么？")

    assert envelope["meta"]["context_pack"]["capture_count"] == 1
    assert envelope["meta"]["context_pack"]["garden_count"] == 0
    assert envelope["meta"]["result_count"] == 1
```

- [ ] **Step 2: Run test to verify red**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_rag_bridge.py::test_search_knowledge_base_adds_context_pack_meta -q
```

Expected: fail because context pack is not passed.

- [ ] **Step 3: Modify `bridge/rag_bridge.py`**

Import and use the context pack:

```python
from agents.rag_context import build_rag_context_pack
```

After results are hydrated and before `rag.query`:

```python
context_pack = build_rag_context_pack(
    repo.conn,
    capture_hits=results,
    vault_path=config.vault_path,
)
resp = rag.query(query, results, context_pack=context_pack)
```

Add to envelope meta:

```python
"context_pack": {
    "capture_count": context_pack["capture_count"],
    "garden_count": context_pack["garden_count"],
    "theme_count": context_pack["theme_count"],
},
```

If the run ledger is implemented first, wrap this bridge with ledger instrumentation according to `2026-05-31-hermes-agent-run-ledger.md`, so the ledger stores `query`, `result_count`, `used_index`, `used_fallback`, and `context_pack` counts.

- [ ] **Step 4: Run RAG bridge tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_rag_bridge.py tests/test_rag_agent.py tests/test_rag_context.py -q
```

Expected: pass.

### Task 5: Runtime Tool Contract And Smoke Test

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/search_notes_tool.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_search_notes_tool.py`

- [ ] **Step 1: Add runtime schema test**

Create or extend `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_search_notes_tool.py`:

```python
def test_search_notes_schema_describes_single_rag_tool_with_sources():
    import tools.search_notes_tool as tool

    description = tool.SEARCH_SCHEMA["description"]

    assert "个人知识库" in description
    assert "Garden" in description
    assert "Theme" in description
    assert "引用" in description
    assert "不要编造" in description
```

- [ ] **Step 2: Run test to verify red**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_search_notes_tool.py -q
```

Expected: fail until description is updated.

- [ ] **Step 3: Update description only**

Set the description to:

```python
"搜索个人知识库并回答问题。内部会结合 Garden 知识卡片、Theme 主题线索和 Capture/章节证据；回答必须保留 [编号] 引用，不要编造知识库中没有的信息。"
```

- [ ] **Step 4: Run runtime tests**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_search_notes_tool.py /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py -q
```

Expected: pass.

### Task 6: Manual WeChat Verification

**Files:** no code files.

- [ ] **Step 1: Run local tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_rag_context.py tests/test_rag_agent.py tests/test_rag_bridge.py tests/test_run_evidence.py tests/test_run_ledger.py -q
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_search_notes_tool.py /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_agent_run_status_tool.py /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py -q
```

Expected: all selected tests pass. Full `hermes-custom` tests may still show unrelated Life Assistant long-term-decision failures until that other thread lands.

- [ ] **Step 2: Restart Hermes with clean sessions**

Run:

```bash
ts=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "/Users/pipi/.hermes/session_backups/codex-$ts"
find /Users/pipi/.hermes/sessions -maxdepth 1 -type f -print -exec mv {} "/Users/pipi/.hermes/session_backups/codex-$ts/" \;
pkill -f "hermes_cli.main gateway run" || true
cd /Users/pipi/Downloads/hermes-agent-main
nohup .venv/bin/python -m hermes_cli.main gateway run --replace > "/Users/pipi/.hermes/logs/gateway-restart-codex-$ts.log" 2>&1 &
```

Expected: gateway logs show WeChat and Feishu connected.

- [ ] **Step 3: WeChat smoke test**

Ask:

```text
AI Agent 工程化到底应该怎么理解？结合我的知识库回答
```

Expected:

- Tool call: `search_my_notes`.
- RAG answer uses at least one capture/section source with `[1]` style citation.
- If related Garden card exists, answer organization reflects it, but citations still point to actual source notes.
- `agent_run_status` shows the latest RAG run with `result_count`, `source_count`, `used_index`, and `context_pack` counts.

Ask:

```text
刚刚 RAG 查了哪些材料？
```

Expected:

- Tool call: `agent_run_status`.
- Response lists recent `rag/search_knowledge_base` run and evidence summary.

## Definition Of Done

- Agent Run Ledger exists and records at least RAG, Theme, Garden synthesize, and Garden commit runs.
- `search_my_notes` remains the only user-facing RAG question tool.
- RAG internally enriches detailed Capture/section evidence with Garden/Theme context.
- RAG answer sources are still precise and cite actual evidence.
- WeChat smoke test can verify both answer quality and the ledger trace.

## What Comes After This

After this plan, the next product decision should be Review Agent v2:

- Use the same run ledger to see what Review selected and why.
- Let Review pick one useful point per day from Garden/Capture.
- Use RAG follow-up when the user asks "展开一下/这和我有什么关系".
- Avoid quiz-style review unless explicitly requested.

---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Graph-Aware RAG V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `search_my_notes` from "vector search plus light context" into a graph-aware RAG retriever that starts from the best matching notes and expands one hop through Garden cards, Theme candidates/pages, and related Capture sections before answering.

**Architecture:** Keep one user-facing RAG tool. Internally split retrieval into three deterministic stages: seed retrieval, graph expansion, and final evidence selection. Use existing SQLite tables and Obsidian links instead of adding a graph database. Record the retrieval path in Run Ledger so the user can ask what RAG searched and see seed hits, expanded nodes, and final evidence.

**Tech Stack:** Python, SQLite, existing `capture_embeddings`, existing `garden_cards`, existing `theme_questions`, Obsidian Markdown, pytest, Hermes Agent Result Envelope.

---

## Product Decision

This is RAG, but not just naive vector RAG. V1 should behave like this:

1. First find likely entry points.
2. If a Garden card is relevant, use it as a high-level orientation and expand to its `capture_ids`.
3. If a Theme candidate/page is relevant, expand to its related captures.
4. If a Capture/Section is relevant, expand to Garden cards and Theme groups that mention that capture.
5. Select a small final evidence set from concrete Capture sections.
6. Answer using final evidence, while showing Garden/Theme only as orientation unless their text is directly quoted.

Do not build a heavy graph database yet. The existing knowledge graph is already encoded in:

- `garden_cards.capture_ids`
- `garden_cards.knowledge_links`
- `theme_questions.related_capture_ids`
- capture frontmatter `linked_themes`
- Obsidian links such as `[[themes/...]]`
- `capture_embeddings` identity/section rows

V1 should turn those links into a retrieval plan.

## Non-Goals

- Do not expose multiple RAG modes to the user.
- Do not add a new user-facing tool.
- Do not rewrite Capture, Theme, or Garden agents.
- Do not perform multi-hop deep search by default.
- Do not add a graph database.
- Do not call an LLM during retrieval. LLM is only for final answer generation.

## File Structure

- Create: `agents/rag_graph.py` — deterministic graph expansion and final evidence selection.
- Modify: `agents/rag_context.py` — include graph trace and high-level nodes in context pack.
- Modify: `bridge/rag_bridge.py` — call graph expansion after seed retrieval and before hydration/LLM.
- Modify: `agents/rag_agent.py` — prompt tells the model how to use graph orientation versus numbered evidence.
- Modify: `agents/run_evidence.py` — extract graph trace summary for run ledger.
- Test: `tests/test_rag_graph.py` — graph expansion unit tests.
- Modify: `tests/test_rag_context.py` — context pack includes graph nodes.
- Modify: `tests/test_rag_bridge.py` — bridge records graph trace and final evidence.
- Modify: `tests/test_rag_agent.py` — prompt contract distinguishes orientation from evidence.
- Modify: `tests/test_run_evidence.py` — ledger evidence exposes graph trace counts.

## Data Contract

`agents.rag_graph.build_graph_retrieval()` returns:

```python
{
    "seed_hits": [capture_or_section_hit],
    "expanded_garden_cards": [garden_card_dict],
    "expanded_theme_groups": [theme_group_dict],
    "expanded_capture_ids": ["cap1", "cap2"],
    "final_hits": [capture_or_section_hit],
    "trace": {
        "seed_count": 3,
        "garden_count": 1,
        "theme_count": 1,
        "expanded_capture_count": 4,
        "final_count": 5,
        "stopped_reason": "budget" | "enough_evidence" | "no_expansion",
    },
}
```

`final_hits` are the only numbered answer evidence. Garden/Theme objects are high-level orientation unless future work adds dedicated Garden/Theme evidence citations.

## Retrieval Rules

1. Seed retrieval stays close to the current `_indexed_search()`.
2. Expand one hop only:
   - Capture -> Garden cards containing that capture.
   - Capture -> Theme candidates containing that capture.
   - Garden -> captures listed in `capture_ids`.
   - Garden -> linked Garden cards in `knowledge_links`, then their `capture_ids`.
   - Theme candidate -> captures in `related_capture_ids`.
   - Capture -> Theme pages from `linked_themes` and `[[themes/...]]`.
3. Select final evidence from concrete Capture/Section rows:
   - Keep seed section hits.
   - Add best section/identity rows for expanded captures.
   - Deduplicate by `(capture_id, section_id)`.
   - Prefer content sections over identity when section score is available.
   - Never select managed section titles.
4. Performance controls:
   - At most 5 seed hits.
   - At most 3 Garden cards.
   - At most 3 Theme groups/pages.
   - At most 20 expanded capture candidates.
   - At most 6 final evidence hits.
   - Retrieval must be deterministic and avoid extra LLM calls.
5. Quality controls:
   - If final evidence has fewer than 2 useful hits, keep the best available seed hits.
   - If graph expansion only adds low-signal nodes, mark `stopped_reason="enough_evidence"` and do not add them.
   - Run Ledger must record both seed and final counts so failures are debuggable.

## Task 1: Add Graph Retrieval Unit

**Files:**
- Create: `agents/rag_graph.py`
- Test: `tests/test_rag_graph.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_rag_graph.py`:

```python
from __future__ import annotations

import json
import struct

from agents.rag_graph import build_graph_retrieval
from db.embedding_repo import CaptureEmbeddingRepo
from db.schema import init_db


def _vec(first: float, second: float = 0.0) -> bytes:
    values = [0.0] * 1024
    values[0] = first
    values[1] = second
    return struct.pack("1024f", *values)


def test_graph_retrieval_expands_capture_seed_to_garden_captures(tmp_path):
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
            "AI Agent 工程化",
            "工程化来自工具、流程和护栏。",
            "[]",
            "[]",
            "[]",
            json.dumps(["cap1", "cap2"], ensure_ascii=False),
            "2026-05-31T00:00:00+00:00",
            "2026-05-31T00:00:00+00:00",
        ),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="s1",
        section_title="工程化入口",
        index_text="AI Agent 工程化 工具 护栏",
        embedding=_vec(1.0, 0.0),
        source_hash="cap1-s1",
        file_path="captures/cap1.md",
    )
    repo.upsert(
        capture_id="cap2",
        kind="section",
        section_id="s2",
        section_title="工作流模式",
        index_text="workflow router evaluator optimizer",
        embedding=_vec(0.95, 0.0),
        source_hash="cap2-s2",
        file_path="captures/cap2.md",
    )

    result = build_graph_retrieval(
        conn,
        query_embedding=_vec(1.0, 0.0),
        seed_hits=[
            {
                "id": "cap1",
                "file_path": "captures/cap1.md",
                "matched_kind": "section",
                "section_id": "s1",
                "section_title": "工程化入口",
                "score": 1.0,
            }
        ],
        vault_path=str(tmp_path),
        final_limit=4,
    )

    assert result["expanded_garden_cards"][0]["title"] == "AI Agent 工程化"
    assert "cap2" in result["expanded_capture_ids"]
    assert [hit["id"] for hit in result["final_hits"]] == ["cap1", "cap2"]
    assert result["trace"]["garden_count"] == 1


def test_graph_retrieval_skips_managed_sections_from_expanded_captures(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, ?)",
        ("cap1", "captures/cap1.md", "2026-05-31T00:00:00+00:00"),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="related",
        section_title="相关笔记",
        index_text="AI Agent 工程化",
        embedding=_vec(1.0, 0.0),
        source_hash="related",
        file_path="captures/cap1.md",
    )
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        index_text="AI Agent 工程化 identity",
        embedding=_vec(0.8, 0.0),
        source_hash="identity",
        file_path="captures/cap1.md",
    )

    result = build_graph_retrieval(
        conn,
        query_embedding=_vec(1.0, 0.0),
        seed_hits=[{"id": "cap1", "file_path": "captures/cap1.md", "score": 1.0}],
        vault_path=str(tmp_path),
        final_limit=3,
    )

    assert result["final_hits"][0]["matched_kind"] == "identity"
    assert result["final_hits"][0]["section_title"] == ""
```

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_rag_graph.py -q
```

Expected: fail because `agents.rag_graph` does not exist.

- [ ] **Step 3: Implement graph retrieval**

Create `agents/rag_graph.py`:

```python
"""Deterministic one-hop graph expansion for RAG retrieval."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agents.embedder import cosine_similarity
from agents.managed_sections import is_managed_section_title
from agents.markdown_writer import read_note
from db.embedding_repo import CaptureEmbeddingRepo

_THEME_LINK_RE = re.compile(r"\[\[themes/([^|\]]+)(?:\|([^\]]+))?\]\]")


def build_graph_retrieval(
    conn,
    *,
    query_embedding: bytes,
    seed_hits: list[dict[str, Any]],
    vault_path: str,
    final_limit: int = 6,
    max_garden: int = 3,
    max_theme: int = 3,
    max_expanded_captures: int = 20,
) -> dict[str, Any]:
    seed_capture_ids = [str(hit.get("id") or "") for hit in seed_hits if hit.get("id")]
    garden_cards = _garden_cards_for_captures(conn, seed_capture_ids, limit=max_garden)
    linked_card_ids = _linked_card_ids(garden_cards)
    if linked_card_ids:
        garden_cards.extend(_garden_cards_by_ids(conn, linked_card_ids, limit=max_garden))
        garden_cards = _dedupe_by_id(garden_cards)[:max_garden]

    theme_groups = _theme_groups_for_captures(conn, seed_capture_ids, limit=max_theme)
    theme_pages = _theme_pages_for_captures(seed_hits, vault_path, limit=max_theme)
    expanded_capture_ids = _expanded_capture_ids(
        seed_capture_ids,
        garden_cards,
        theme_groups,
        max_expanded_captures,
    )
    final_hits = _select_final_hits(
        conn,
        query_embedding=query_embedding,
        seed_hits=seed_hits,
        expanded_capture_ids=expanded_capture_ids,
        final_limit=final_limit,
    )
    stopped_reason = "no_expansion"
    if expanded_capture_ids:
        stopped_reason = "enough_evidence"
    if len(expanded_capture_ids) >= max_expanded_captures:
        stopped_reason = "budget"
    return {
        "seed_hits": seed_hits,
        "expanded_garden_cards": garden_cards,
        "expanded_theme_groups": theme_groups,
        "expanded_theme_pages": theme_pages,
        "expanded_capture_ids": expanded_capture_ids,
        "final_hits": final_hits,
        "trace": {
            "seed_count": len(seed_hits),
            "garden_count": len(garden_cards),
            "theme_count": len(theme_groups) + len(theme_pages),
            "expanded_capture_count": len(expanded_capture_ids),
            "final_count": len(final_hits),
            "stopped_reason": stopped_reason,
        },
    }


def _garden_cards_for_captures(conn, capture_ids: list[str], *, limit: int) -> list[dict[str, Any]]:
    wanted = set(capture_ids)
    if not wanted:
        return []
    try:
        rows = conn.execute(
            """SELECT id, title, one_liner, core_arguments, knowledge_links, capture_ids
               FROM garden_cards
               WHERE status != 'archived'
               ORDER BY updated_at DESC"""
        ).fetchall()
    except Exception:
        return []
    cards = []
    for row in rows:
        card_capture_ids = _json_list(row["capture_ids"])
        if wanted & set(card_capture_ids):
            cards.append(_garden_card_dict(row, card_capture_ids))
        if len(cards) >= limit:
            break
    return cards


def _garden_cards_by_ids(conn, card_ids: list[str], *, limit: int) -> list[dict[str, Any]]:
    if not card_ids:
        return []
    placeholders = ",".join("?" for _ in card_ids)
    try:
        rows = conn.execute(
            f"""SELECT id, title, one_liner, core_arguments, knowledge_links, capture_ids
                FROM garden_cards
                WHERE id IN ({placeholders}) AND status != 'archived'
                ORDER BY updated_at DESC""",
            tuple(card_ids),
        ).fetchall()
    except Exception:
        return []
    return [_garden_card_dict(row, _json_list(row["capture_ids"])) for row in rows[:limit]]


def _theme_groups_for_captures(conn, capture_ids: list[str], *, limit: int) -> list[dict[str, Any]]:
    wanted = set(capture_ids)
    if not wanted:
        return []
    try:
        rows = conn.execute(
            """SELECT id, question_text, related_capture_ids, status
               FROM theme_questions
               ORDER BY created_at DESC"""
        ).fetchall()
    except Exception:
        return []
    groups = []
    for row in rows:
        related = _json_list(row["related_capture_ids"])
        if wanted & set(related):
            groups.append(
                {
                    "id": row["id"],
                    "title": row["question_text"],
                    "capture_ids": related,
                    "status": row["status"],
                }
            )
        if len(groups) >= limit:
            break
    return groups


def _theme_pages_for_captures(
    seed_hits: list[dict[str, Any]],
    vault_path: str,
    *,
    limit: int,
) -> list[dict[str, str]]:
    vault = Path(vault_path).expanduser()
    pages = []
    seen = set()
    for hit in seed_hits:
        note = read_note(vault, str(hit.get("file_path") or ""))
        if not note:
            continue
        frontmatter, body = note
        for name in _frontmatter_theme_names(frontmatter):
            _append_theme_page(pages, seen, name, f"themes/{name}.md")
        for match in _THEME_LINK_RE.finditer(body):
            target = match.group(1).strip()
            title = (match.group(2) or target).strip()
            _append_theme_page(pages, seen, title, f"themes/{target}.md")
        if len(pages) >= limit:
            break
    return pages[:limit]


def _select_final_hits(
    conn,
    *,
    query_embedding: bytes,
    seed_hits: list[dict[str, Any]],
    expanded_capture_ids: list[str],
    final_limit: int,
) -> list[dict[str, Any]]:
    by_key = {}
    for hit in seed_hits:
        if not hit.get("matched_kind"):
            continue
        key = (str(hit.get("id") or ""), str(hit.get("section_id") or ""))
        if key[0]:
            by_key[key] = hit
    repo = CaptureEmbeddingRepo(conn)
    for capture_id in expanded_capture_ids:
        for row in repo.list_by_capture(capture_id):
            if row.get("kind") == "section" and is_managed_section_title(row.get("section_title", "")):
                continue
            if row.get("kind") not in ("identity", "section"):
                continue
            item = dict(row)
            item["id"] = item["capture_id"]
            item["matched_kind"] = item["kind"]
            item["score"] = cosine_similarity(query_embedding, item["embedding"])
            key = (item["capture_id"], item.get("section_id") or "")
            existing = by_key.get(key)
            if existing is None or item["score"] > existing.get("score", 0):
                by_key[key] = item
    ranked = sorted(by_key.values(), key=lambda item: item.get("score", 0), reverse=True)
    return ranked[:final_limit]


def _expanded_capture_ids(
    seed_capture_ids: list[str],
    garden_cards: list[dict[str, Any]],
    theme_groups: list[dict[str, Any]],
    limit: int,
) -> list[str]:
    seen = set()
    ordered = []
    for capture_id in seed_capture_ids:
        _append_capture_id(ordered, seen, capture_id, limit)
    for card in garden_cards:
        for capture_id in card.get("capture_ids", []):
            _append_capture_id(ordered, seen, str(capture_id), limit)
    for group in theme_groups:
        for capture_id in group.get("capture_ids", []):
            _append_capture_id(ordered, seen, str(capture_id), limit)
    return ordered


def _garden_card_dict(row, capture_ids: list[str]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "one_liner": row["one_liner"],
        "core_arguments": _json_list(row["core_arguments"]),
        "knowledge_links": _json_list(row["knowledge_links"]),
        "capture_ids": capture_ids,
    }


def _linked_card_ids(cards: list[dict[str, Any]]) -> list[str]:
    linked = []
    for card in cards:
        for card_id in card.get("knowledge_links", []):
            if isinstance(card_id, str) and card_id:
                linked.append(card_id)
    return linked


def _dedupe_by_id(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        item_id = item.get("id")
        if item_id in seen:
            continue
        seen.add(item_id)
        result.append(item)
    return result


def _frontmatter_theme_names(frontmatter: dict[str, Any]) -> list[str]:
    linked = frontmatter.get("linked_themes") or []
    if isinstance(linked, str):
        linked = [linked]
    return [str(item).strip() for item in linked if str(item).strip()]


def _append_theme_page(pages: list[dict[str, str]], seen: set[str], title: str, path: str) -> None:
    if path in seen:
        return
    seen.add(path)
    pages.append({"title": title, "path": path})


def _append_capture_id(ordered: list[str], seen: set[str], capture_id: str, limit: int) -> None:
    if not capture_id or capture_id in seen or len(ordered) >= limit:
        return
    seen.add(capture_id)
    ordered.append(capture_id)


def _json_list(text: Any) -> list[Any]:
    try:
        value = json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []
```

- [ ] **Step 4: Verify tests pass**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_rag_graph.py -q
```

Expected: all tests pass.

## Task 2: Wire Graph Retrieval Into RAG Bridge

**Files:**
- Modify: `bridge/rag_bridge.py`
- Test: `tests/test_rag_bridge.py`

- [ ] **Step 1: Write failing bridge test**

Add this test to `tests/test_rag_bridge.py`:

```python
def test_search_knowledge_base_records_graph_trace(monkeypatch, tmp_path):
    from bridge import rag_bridge

    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    rel_path = _write_capture(vault)
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap1", rel_path),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="second",
        section_title="第二部分",
        index_text="节奏狙击 Terminal-Bench",
        embedding=_vec(1.0, 0.0),
        source_hash="second",
        file_path=rel_path,
    )

    class FakeEmbedder:
        def __init__(self, **_kwargs):
            pass

        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    class FakeRagAgent:
        def __init__(self, **_kwargs):
            pass

        def query(self, _question, captures, *, context_pack=None):
            return {
                "answer": "回答。[1]",
                "sources": [
                    {
                        "title": captures[0]["title"],
                        "file_path": captures[0]["file_path"],
                        "section_title": captures[0]["section_title"],
                    }
                ],
            }

    monkeypatch.setattr(
        rag_bridge,
        "load_config",
        lambda _path: CaptureConfig(
            vault_path=str(vault),
            db_path=str(db_path),
            qwen_api_key="qwen-test",
            llm_api_key="llm-test",
        ),
    )
    monkeypatch.setattr(rag_bridge, "Embedder", FakeEmbedder)
    monkeypatch.setattr(rag_bridge, "RagAgent", FakeRagAgent)
    rag_bridge._config = None
    rag_bridge._repo = None

    envelope = rag_bridge.search_knowledge_base("Terminal-Bench")

    assert envelope["meta"]["graph_trace"]["seed_count"] == 1
    assert envelope["meta"]["graph_trace"]["final_count"] >= 1
```

- [ ] **Step 2: Run test to verify red**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_rag_bridge.py::test_search_knowledge_base_records_graph_trace -q
```

Expected: fail because `graph_trace` is missing.

- [ ] **Step 3: Modify bridge**

In `bridge/rag_bridge.py`, import:

```python
from agents.rag_graph import build_graph_retrieval
```

Inside `_search_knowledge_base()`, keep the current `_indexed_search()` as seed retrieval, then add:

```python
    graph = build_graph_retrieval(
        repo.conn,
        query_embedding=embedder.embed(query),
        seed_hits=results,
        vault_path=config.vault_path,
        final_limit=6,
    )
    results = graph["final_hits"] or results
```

Avoid embedding the query twice by storing `query_embedding = embedder.embed(query)` and passing it both to `_indexed_search_by_embedding()` and graph retrieval. If this refactor is too large for one task, keep the duplicate call in V1 and add a follow-up optimization.

Add graph trace to meta:

```python
            "graph_trace": graph["trace"],
```

- [ ] **Step 4: Verify bridge tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_rag_bridge.py -q
```

Expected: all RAG bridge tests pass.

## Task 3: Enrich Context Pack With Graph Orientation

**Files:**
- Modify: `agents/rag_context.py`
- Modify: `tests/test_rag_context.py`

- [ ] **Step 1: Write failing context test**

Add:

```python
def test_context_pack_accepts_graph_nodes(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    graph = {
        "expanded_garden_cards": [
            {
                "id": "g1",
                "title": "AI Agent 工程化",
                "one_liner": "用工程手段管理 Agent。",
                "core_arguments": [],
                "capture_ids": ["cap1"],
            }
        ],
        "expanded_theme_groups": [
            {
                "id": "t1",
                "title": "Agent 复杂度控制",
                "capture_ids": ["cap1", "cap2"],
                "status": "pending",
            }
        ],
        "expanded_theme_pages": [
            {"title": "Agent 方法论", "path": "themes/Agent 方法论.md"}
        ],
        "trace": {"seed_count": 1, "final_count": 1},
    }

    pack = build_rag_context_pack(
        conn,
        capture_hits=[{"id": "cap1", "title": "材料 A", "file_path": "captures/A.md"}],
        vault_path=str(tmp_path),
        graph=graph,
    )

    assert pack["garden_cards"][0]["title"] == "AI Agent 工程化"
    assert pack["theme_groups"][0]["title"] == "Agent 复杂度控制"
    assert pack["graph_trace"]["seed_count"] == 1
```

- [ ] **Step 2: Run test to verify red**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_rag_context.py::test_context_pack_accepts_graph_nodes -q
```

Expected: fail because `build_rag_context_pack()` has no `graph` argument.

- [ ] **Step 3: Update context pack**

Change signature:

```python
def build_rag_context_pack(
    conn,
    *,
    capture_hits: list[dict[str, Any]],
    vault_path: str,
    graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

Prefer graph-provided orientation when present:

```python
    graph = graph or {}
    theme_pages = graph.get("expanded_theme_pages") or _theme_pages_for_captures(capture_hits, vault_path)
    garden_cards = graph.get("expanded_garden_cards") or _garden_cards_for_captures(
        conn, [str(hit.get("id", "")) for hit in capture_hits]
    )
    theme_groups = graph.get("expanded_theme_groups") or []
```

Return:

```python
        "theme_groups": theme_groups,
        "theme_group_count": len(theme_groups),
        "graph_trace": graph.get("trace") or {},
```

- [ ] **Step 4: Verify context tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_rag_context.py -q
```

Expected: all context tests pass.

## Task 4: Prompt Contract For Graph Context

**Files:**
- Modify: `agents/rag_agent.py`
- Modify: `tests/test_rag_agent.py`

- [ ] **Step 1: Write failing prompt test**

Add:

```python
def test_build_prompt_includes_graph_theme_groups_as_orientation_only():
    captures = [{"title": "材料", "story": "", "body": "正文证据"}]
    context_pack = {
        "garden_cards": [
            {"title": "Agent 工程化", "one_liner": "用流程和护栏管理模型。"}
        ],
        "theme_groups": [
            {"title": "复杂度控制", "capture_ids": ["cap1", "cap2"]}
        ],
        "graph_trace": {"seed_count": 1, "final_count": 1},
    }

    prompt = build_rag_prompt("怎么理解？", captures, context_pack=context_pack)

    assert "检索路径" in prompt
    assert "复杂度控制" in prompt
    assert "Garden/Theme 是理解线索" in prompt
    assert "[1] 《材料》" in prompt
```

- [ ] **Step 2: Run test to verify red**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_rag_agent.py::test_build_prompt_includes_graph_theme_groups_as_orientation_only -q
```

Expected: fail because theme groups and graph trace are not included.

- [ ] **Step 3: Update prompt builder**

In `build_rag_prompt()`, extend the high-level context block:

```python
    theme_groups = context_pack.get("theme_groups") or []
    graph_trace = context_pack.get("graph_trace") or {}
    if theme_groups:
        lines.append("Theme 候选组：")
        for group in theme_groups[:3]:
            lines.append(f"- {group.get('title', '')}")
    if graph_trace:
        lines.append(
            "检索路径："
            f"seed={graph_trace.get('seed_count', 0)}, "
            f"garden={graph_trace.get('garden_count', 0)}, "
            f"theme={graph_trace.get('theme_count', 0)}, "
            f"final={graph_trace.get('final_count', 0)}"
        )
    lines.append("Garden/Theme 是理解线索；编号来源只来自下方 Capture 证据。")
```

- [ ] **Step 4: Verify agent tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_rag_agent.py -q
```

Expected: all RAG agent tests pass.

## Task 5: Ledger Evidence For Graph Retrieval

**Files:**
- Modify: `agents/run_evidence.py`
- Modify: `tests/test_run_evidence.py`

- [ ] **Step 1: Write failing ledger test**

Add:

```python
def test_rag_evidence_extracts_graph_trace():
    evidence = extract_run_evidence(
        {
            "status": "success",
            "agent": "rag",
            "intent": "search_knowledge_base",
            "meta": {
                "result_count": 4,
                "used_index": True,
                "graph_trace": {
                    "seed_count": 3,
                    "garden_count": 1,
                    "theme_count": 1,
                    "final_count": 4,
                    "stopped_reason": "enough_evidence",
                },
            },
        }
    )

    assert evidence["graph_seed_count"] == 3
    assert evidence["graph_final_count"] == 4
    assert evidence["graph_stopped_reason"] == "enough_evidence"
```

- [ ] **Step 2: Run test to verify red**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_run_evidence.py::test_rag_evidence_extracts_graph_trace -q
```

Expected: fail because graph trace is not extracted.

- [ ] **Step 3: Update evidence extraction**

In the RAG branch of `extract_run_evidence()`, add:

```python
        graph_trace = meta.get("graph_trace") or {}
        if graph_trace:
            evidence.update(
                {
                    "graph_seed_count": int(graph_trace.get("seed_count") or 0),
                    "graph_garden_count": int(graph_trace.get("garden_count") or 0),
                    "graph_theme_count": int(graph_trace.get("theme_count") or 0),
                    "graph_final_count": int(graph_trace.get("final_count") or 0),
                    "graph_stopped_reason": str(graph_trace.get("stopped_reason") or ""),
                }
            )
```

- [ ] **Step 4: Verify evidence tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_run_evidence.py -q
```

Expected: all run evidence tests pass.

## Task 6: Focused Verification And Hermes Smoke

**Files:**
- No new source files.

- [ ] **Step 1: Run focused Hermes Custom tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_rag_graph.py \
  /Users/pipi/hermes-custom/tests/test_rag_context.py \
  /Users/pipi/hermes-custom/tests/test_rag_bridge.py \
  /Users/pipi/hermes-custom/tests/test_rag_agent.py \
  /Users/pipi/hermes-custom/tests/test_run_evidence.py \
  /Users/pipi/hermes-custom/tests/test_agent_run_bridge.py \
  -q
```

Expected: all focused tests pass.

- [ ] **Step 2: Restart Hermes with clean session memory**

Run:

```bash
ts=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="/Users/pipi/.hermes/session_backups/codex-$ts"
mkdir -p "$backup_dir"
find /Users/pipi/.hermes/sessions -maxdepth 1 -type f -print -exec mv {} "$backup_dir/" \;
pkill -f "hermes_cli.main gateway run" || true
cd /Users/pipi/Downloads/hermes-agent-main
nohup .venv/bin/python -m hermes_cli.main gateway run --replace > "/Users/pipi/.hermes/logs/gateway-restart-codex-$ts.log" 2>&1 &
```

- [ ] **Step 3: Verify process and logs**

Run:

```bash
pgrep -af "hermes_cli.main gateway run|python -m hermes_cli.main"
tail -100 /Users/pipi/.hermes/logs/gateway.log
```

Expected: one gateway process is running, and logs show Weixin/Feishu connected.

- [ ] **Step 4: Manual WeChat smoke**

Ask:

```text
AI Agent 工程化到底应该怎么理解？结合我的知识库回答
```

Then ask:

```text
刚刚 RAG 查了哪些材料？
```

Expected:

- First answer uses `search_my_notes`.
- Second answer uses `agent_run_status`.
- Run Ledger includes `graph_trace`.
- Sources include article titles and section names.
- Managed sections such as `相关笔记`, `知识关联`, `参考来源` do not appear as final evidence.
- If Garden/Theme are involved, the trace says so without presenting them as fake numbered evidence.

## Rollout Notes

This plan intentionally keeps V1 simple. The important upgrade is not "more search"; it is "search with structure":

- Seed retrieval finds relevant concrete notes.
- Graph expansion finds nearby knowledge objects.
- Final selection returns concrete evidence sections.
- Ledger records why those sections were selected.

After V1 passes smoke tests, the next possible upgrade is Garden/Theme direct embedding. That would let RAG begin from a Garden card or Theme page even when no Capture section is the strongest initial vector hit. Do not implement that until V1 is stable.

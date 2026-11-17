---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# RAG Managed Section Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent RAG from treating Obsidian management sections such as `相关笔记`, `知识关联`, and `参考来源` as primary answer evidence.

**Architecture:** Keep the public `search_my_notes` behavior unchanged. Add one small shared helper that identifies managed section titles, use it during new section index generation, and also guard runtime retrieval so existing stale section embeddings cannot pollute answers before the next full reindex. Preserve identity embeddings as fallback evidence for a capture, because the note itself can still be relevant even when one managed subsection is not.

**Tech Stack:** Python, SQLite, existing `capture_embeddings`, existing RAG bridge, pytest.

---

## Product Decision

The problem is not that RAG found the wrong notes. The problem is that it surfaced the wrong part of otherwise useful notes: management/link sections such as `相关笔记` are useful for Obsidian navigation, but they are bad evidence for answering a user question.

Fix this as retrieval hygiene, not as a new user mode:

1. New indexes should not create section embeddings for managed sections.
2. Old managed-section embeddings already in SQLite should be ignored at search time.
3. If a capture has both a managed-section hit and a content-section hit, choose the content section.
4. If a capture only has an identity hit and managed-section hits, choose the identity hit.
5. Do not delete notes, rewrite Obsidian content, or change WeChat UX in this task.

## File Structure

- Create: `agents/managed_sections.py` — shared managed-section title helper.
- Modify: `agents/capture_indexer.py` — skip managed sections when building new section indexes.
- Modify: `bridge/rag_bridge.py` — ignore managed section rows before grouping/selecting best hits.
- Test: `tests/test_capture_indexer.py` — section indexer skips managed sections and keeps real content sections.
- Test: `tests/test_rag_bridge.py` — RAG selection does not pick stale managed-section embeddings even if they score highest.

## Success Criteria

- `build_section_indexes()` does not emit section items for `相关笔记`, `知识关联`, `参考来源`, or `Hermes 管理`.
- `_indexed_search()` does not return `matched_kind="section"` with `section_title="相关笔记"` even if that embedding has the highest score.
- Existing good behavior remains: when a real content section matches best, RAG still hydrates only that section.
- Focused tests pass:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_capture_indexer.py \
  /Users/pipi/hermes-custom/tests/test_rag_bridge.py \
  -q
```

## Task 1: Add Managed Section Helper

**Files:**
- Create: `agents/managed_sections.py`
- Test indirectly in later tasks.

- [ ] **Step 1: Create the helper**

Create `agents/managed_sections.py`:

```python
"""Helpers for Obsidian sections that are useful for management but poor RAG evidence."""
from __future__ import annotations

import re


_MANAGED_SECTION_TITLES = {
    "目录纲要",
    "相关笔记",
    "相关笔记引用",
    "知识关联",
    "参考来源",
    "系统记录",
    "Hermes 管理",
}


def is_managed_section_title(title: str) -> bool:
    clean = re.sub(r"\s+", " ", str(title or "")).strip()
    return clean in _MANAGED_SECTION_TITLES
```

- [ ] **Step 2: Sanity-check import**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python - <<'PY'
from agents.managed_sections import is_managed_section_title
assert is_managed_section_title("相关笔记")
assert not is_managed_section_title("方法论")
PY
```

Expected: command exits with status `0`.

## Task 2: Stop Indexing Managed Sections

**Files:**
- Modify: `agents/capture_indexer.py`
- Modify: `tests/test_capture_indexer.py`

- [ ] **Step 1: Write the failing test**

Append this test to `tests/test_capture_indexer.py`:

```python
def test_section_indexes_skip_managed_sections():
    frontmatter = {"title": "AI Agent 工程化"}
    body = """# AI Agent 工程化

## 核心方法
这里是可以回答问题的正文内容。

## 相关笔记
- [[Building Effective AI Agents]]

## 知识关联
- 这只是管理区链接。

## 参考来源
[1] 原文

## 后续判断
这里也是可以回答问题的正文内容。
"""

    sections = build_section_indexes("cap1", "captures/a.md", frontmatter, body)

    assert [section.section_title for section in sections] == ["核心方法", "后续判断"]
    assert all("相关笔记" not in section.index_text for section in sections)
    assert all("知识关联" not in section.index_text for section in sections)
    assert all("参考来源" not in section.index_text for section in sections)
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_capture_indexer.py::test_section_indexes_skip_managed_sections \
  -q
```

Expected before implementation: fail because managed sections are still indexed.

- [ ] **Step 3: Implement the minimal indexer change**

Modify `agents/capture_indexer.py`:

```python
from agents.managed_sections import is_managed_section_title
```

Then replace the special-case skip in `_markdown_sections()`:

```python
        if is_managed_section_title(title):
            continue
```

This replaces the current `if title == "目录纲要": continue` branch.

- [ ] **Step 4: Verify indexer tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_capture_indexer.py \
  -q
```

Expected: all capture indexer tests pass.

## Task 3: Guard Runtime RAG Against Old Managed Embeddings

**Files:**
- Modify: `bridge/rag_bridge.py`
- Modify: `tests/test_rag_bridge.py`

- [ ] **Step 1: Write the failing test**

Append this test to `tests/test_rag_bridge.py`:

```python
def test_indexed_search_ignores_managed_section_embeddings(tmp_path):
    from bridge import rag_bridge
    from db.repo import CaptureRepo

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
        kind="identity",
        index_text="AI Agent 工程化整体身份",
        embedding=_vec(0.7, 0.0),
        source_hash="identity",
        file_path=rel_path,
    )
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="related",
        section_title="相关笔记",
        index_text="Building Effective AI Agents Opus 4.8 Claude Code",
        embedding=_vec(1.0, 0.0),
        source_hash="related",
        file_path=rel_path,
    )
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="second",
        section_title="第二部分",
        index_text="节奏狙击 Terminal-Bench 故意留坑",
        embedding=_vec(0.9, 0.0),
        source_hash="second",
        file_path=rel_path,
    )

    class FakeEmbedder:
        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    results = rag_bridge._indexed_search(
        "AI Agent 工程化",
        CaptureRepo(conn),
        FakeEmbedder(),
        limit=1,
    )

    assert len(results) == 1
    assert results[0]["section_title"] == "第二部分"
    assert results[0]["matched_kind"] == "section"
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_rag_bridge.py::test_indexed_search_ignores_managed_section_embeddings \
  -q
```

Expected before implementation: fail because `相关笔记` wins by score.

- [ ] **Step 3: Implement runtime filtering**

Modify `bridge/rag_bridge.py` imports:

```python
from agents.managed_sections import is_managed_section_title
```

Then inside `_indexed_search()`, replace the row grouping loop:

```python
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("kind") == "section" and is_managed_section_title(
            str(row.get("section_title") or "")
        ):
            continue
        grouped.setdefault(row["capture_id"], []).append(row)
```

Keep the rest of `_indexed_search()` unchanged. This keeps the change narrow and makes old managed-section embeddings harmless.

- [ ] **Step 4: Verify RAG bridge tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_rag_bridge.py \
  -q
```

Expected: all RAG bridge tests pass.

## Task 4: Rebuild Or Refresh Indexes

**Files:**
- No code file changes required unless an existing reindex command already exists and needs a tiny option.

- [ ] **Step 1: Find the existing reindex command**

Run:

```bash
rg "build_section_indexes|capture_embeddings|reindex|index" \
  /Users/pipi/hermes-custom/scripts \
  /Users/pipi/hermes-custom/bridge \
  /Users/pipi/hermes-custom/agents \
  -g '*.py'
```

Expected: identify the current command/path used by Library Agent or maintenance scripts to rebuild `capture_embeddings`.

- [ ] **Step 2: Run the existing safe reindex path**

Use the existing command discovered in Step 1. If the only available path is the Library Agent maintenance tool, trigger that path rather than inventing a new script.

Expected after refresh: no new `capture_embeddings` rows should be created for managed sections.

- [ ] **Step 3: Spot-check the database**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python - <<'PY'
from agents.managed_sections import is_managed_section_title
from agents.capture_agent import load_config
from db.schema import init_db

cfg = load_config('/Users/pipi/hermes-custom/config.yaml')
conn = init_db(cfg.db_path)
rows = conn.execute(
    "SELECT capture_id, section_title FROM capture_embeddings WHERE kind = 'section'"
).fetchall()
managed = [dict(row) for row in rows if is_managed_section_title(row['section_title'])]
print(f"managed_section_embeddings={len(managed)}")
for row in managed[:10]:
    print(row)
PY
```

Expected: ideally `managed_section_embeddings=0`. If old rows remain, runtime filtering still protects RAG, but schedule a later cleanup/reindex task.

## Task 5: Focused Verification And Hermes Restart

**Files:**
- No source changes.

- [ ] **Step 1: Run focused tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_capture_indexer.py \
  /Users/pipi/hermes-custom/tests/test_rag_bridge.py \
  /Users/pipi/hermes-custom/tests/test_run_evidence.py \
  /Users/pipi/hermes-custom/tests/test_agent_run_bridge.py \
  -q
```

Expected: all pass. If unrelated dirty files make one of the run ledger tests fail, report the exact failure and keep the managed-section tests as the acceptance gate for this task.

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

- [ ] **Step 3: Verify Hermes process**

Run:

```bash
pgrep -af "hermes_cli.main gateway run|python -m hermes_cli.main"
tail -80 /Users/pipi/.hermes/logs/gateway-restart-codex-$ts.log
```

Expected: one gateway process is running, and the log shows the gateway started without immediate crash.

- [ ] **Step 4: Manual WeChat smoke test**

Ask in WeChat:

```text
AI Agent 工程化到底应该怎么理解？结合我的知识库回答
```

Then ask:

```text
刚刚 RAG 查了哪些材料？
```

Expected:

- `search_my_notes` is called for the first message.
- `agent_run_status` is called for the second message.
- The returned source list includes note titles and section names.
- Section names should be real content sections, not `相关笔记`, `知识关联`, or `参考来源`.

## Is The Project Almost Done After This?

After this task, the core Hermes knowledge pipeline is MVP-complete:

- Capture can ingest, draft, revise, and commit notes.
- Library can repair/sync the vault, normalize metadata, and build indexes.
- Theme can scan notes and generate readable MOC pages.
- Garden can find relationships and create confirm-before-commit knowledge cards.
- Review can explain one remembered point and track review interactions.
- RAG can answer against the knowledge base with visible sources.
- Run Ledger can explain what each Agent did.

But the project should not be considered "finished forever." The remaining work is hardening, not inventing the core shape:

1. Build a small regression/smoke suite that runs the whole Capture → Library → Theme/Garden → RAG path on fixture notes.
2. Improve long-document RAG so it can retrieve multiple relevant sections from the same long capture when needed.
3. Add better operational controls: reindex status, last scan time, and clearer failure messages.
4. Polish dashboard/Obsidian views after the backend behavior is stable.

Practical milestone: once managed-section filtering passes and the WeChat smoke test shows clean source sections, this phase can be called "knowledge pipeline v1 complete." The next phase should be reliability and evaluation, not more Agent redesign.

---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Capture Metadata Storage Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Agent-only capture metadata such as `outline` out of Obsidian frontmatter into SQLite, while keeping human-facing Obsidian notes clean and adding a short body directory only for long notes.

**Architecture:** Keep Markdown files as the human-readable knowledge surface, with only minimal Obsidian Properties in frontmatter. Add a `capture_metadata` SQLite table for Agent-readable structured data, including outline JSON and future metadata such as review hints. Library Agent becomes the owner of metadata migration and directory rendering: it writes DB metadata for Agents, and only writes `## 目录纲要` into long Markdown bodies when useful for human reading.

**Tech Stack:** Python, SQLite, YAML frontmatter, existing `LibraryAgent`, existing `Outliner`, existing `markdown_writer`, pytest.

---

## Storage Rules

Human-facing frontmatter stays small:

```yaml
---
id: c4bffa9d9f93
title: Building Effective AI Agents
date: 2026-05-30
source: https://www.anthropic.com/engineering/building-effective-agents
source_type: link
tags:
  - ai-agent
type: capture
status: processed
---
```

Agent-only metadata moves to SQLite:

```text
capture_metadata
- capture_id
- outline_json
- outline_source_hash
- directory_rendered_at
- metadata_json
- created_at
- updated_at
```

Body directory remains human-facing and optional:

```markdown
## 目录纲要

- 两类架构：工作流与代理
- 简单优先，仅在必要时增加复杂度
- 常用工作流模式
```

Rules:
- Do not store `outline` in Markdown frontmatter for new or migrated notes.
- Keep minimal Obsidian Properties in frontmatter: `id/title/date/source/source_type/tags/type/status`.
- Store Agent outline in SQLite `capture_metadata.outline_json`.
- Insert `## 目录纲要` only when body length is above the Library threshold.
- Render body directory as short navigation, not full machine metadata.
- Do not delete capture/raw Markdown files in this change.

## File Structure

- Modify `db/schema.py`: add `capture_metadata` table creation and test.
- Create `db/metadata_repo.py`: CRUD helper for per-capture metadata.
- Modify `agents/library_agent.py`: read/write outline metadata through DB; remove `outline` from frontmatter during apply; render body directory for long notes only.
- Modify `agents/capture_indexer.py`: read outline from metadata when available, fallback to legacy frontmatter during migration.
- Modify `bridge/theme_bridge.py`: if it needs outline context later, use metadata helper or keep current behavior if not needed.
- Modify `bridge/rag_bridge.py`: no required behavior change unless RAG starts reading outline directly; section search already uses `capture_embeddings`.
- Modify `tests/test_schema.py`: add `capture_metadata` schema test.
- Create `tests/test_metadata_repo.py`: repository behavior tests.
- Modify `tests/test_library_agent.py`: update outline tests to assert DB metadata and cleaned frontmatter.
- Modify `tests/test_capture_indexer.py`: assert metadata outline can feed identity index.
- Modify real data through `scripts/library_maintenance.py --apply` after tests pass.

## Task 1: Add Capture Metadata Schema

**Files:**
- Modify: `db/schema.py`
- Test: `tests/test_schema.py`

- [ ] **Step 1: Write failing schema test**

Add this test to `tests/test_schema.py`:

```python
def test_capture_metadata_table_exists(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(capture_metadata)")]

    assert cols == [
        "capture_id",
        "outline_json",
        "outline_source_hash",
        "directory_rendered_at",
        "metadata_json",
        "created_at",
        "updated_at",
    ]
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_schema.py::test_capture_metadata_table_exists -q
```

Expected: FAIL because `capture_metadata` does not exist.

- [ ] **Step 3: Implement schema**

Add this SQL constant to `db/schema.py`:

```python
CREATE_CAPTURE_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS capture_metadata (
    capture_id TEXT PRIMARY KEY,
    outline_json TEXT NOT NULL DEFAULT '[]',
    outline_source_hash TEXT NOT NULL DEFAULT '',
    directory_rendered_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""
```

Execute it in `init_db()` after `CREATE_CAPTURE_EMBEDDINGS_TABLE`:

```python
conn.execute(CREATE_CAPTURE_EMBEDDINGS_TABLE)
conn.execute(CREATE_CAPTURE_METADATA_TABLE)
conn.commit()
```

- [ ] **Step 4: Run schema test**

Run:

```bash
.venv/bin/python -m pytest tests/test_schema.py::test_capture_metadata_table_exists -q
```

Expected: PASS.

## Task 2: Add Metadata Repository

**Files:**
- Create: `db/metadata_repo.py`
- Test: `tests/test_metadata_repo.py`

- [ ] **Step 1: Write failing repository tests**

Create `tests/test_metadata_repo.py`:

```python
from db.metadata_repo import CaptureMetadataRepo
from db.schema import init_db


def test_upsert_outline_stores_json_and_hash(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = CaptureMetadataRepo(conn)
    outline = [{"level": 1, "title": "主线", "summary": "核心内容"}]

    repo.upsert_outline("cap1", outline, source_hash="hash1")

    row = repo.get("cap1")
    assert row is not None
    assert row["outline"] == outline
    assert row["outline_source_hash"] == "hash1"


def test_upsert_outline_replaces_existing_outline(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = CaptureMetadataRepo(conn)

    repo.upsert_outline("cap1", [{"level": 1, "title": "旧"}], source_hash="old")
    repo.upsert_outline("cap1", [{"level": 1, "title": "新"}], source_hash="new")

    row = repo.get("cap1")
    assert row is not None
    assert row["outline"] == [{"level": 1, "title": "新"}]
    assert row["outline_source_hash"] == "new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_metadata_repo.py -q
```

Expected: FAIL because `db.metadata_repo` does not exist.

- [ ] **Step 3: Implement repository**

Create `db/metadata_repo.py`:

```python
"""Repository helpers for Agent-only capture metadata."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


class CaptureMetadataRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, capture_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM capture_metadata WHERE capture_id = ?",
            (capture_id,),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            data["outline"] = json.loads(data.get("outline_json") or "[]")
        except json.JSONDecodeError:
            data["outline"] = []
        try:
            data["metadata"] = json.loads(data.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            data["metadata"] = {}
        return data

    def upsert_outline(
        self,
        capture_id: str,
        outline: list[dict],
        *,
        source_hash: str,
        directory_rendered_at: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO capture_metadata
               (capture_id, outline_json, outline_source_hash,
                directory_rendered_at, metadata_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, '{}', ?, ?)
               ON CONFLICT(capture_id) DO UPDATE SET
                 outline_json=excluded.outline_json,
                 outline_source_hash=excluded.outline_source_hash,
                 directory_rendered_at=COALESCE(
                    excluded.directory_rendered_at,
                    capture_metadata.directory_rendered_at
                 ),
                 updated_at=excluded.updated_at""",
            (
                capture_id,
                json.dumps(outline, ensure_ascii=False),
                source_hash,
                directory_rendered_at,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get(capture_id) or {}
```

- [ ] **Step 4: Run repository tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_metadata_repo.py -q
```

Expected: PASS.

## Task 3: Library Agent Stores Outline in DB and Cleans Frontmatter

**Files:**
- Modify: `agents/library_agent.py`
- Test: `tests/test_library_agent.py`

- [ ] **Step 1: Write failing Library Agent test**

Replace the current outline apply expectation with a DB-backed one in `tests/test_library_agent.py`:

```python
def test_apply_stores_outline_in_db_and_removes_frontmatter_outline(tmp_path: Path) -> None:
    body = "# 测试长文\n\n第一段。\n\n第二段。\n\n" + "补充内容。\n\n" * 40
    rel_path = _write_capture(tmp_path, body=body)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("long", rel_path))
    conn.commit()
    outliner = FakeOutliner()

    report = LibraryAgent(
        tmp_path,
        outliner=outliner,
        min_outline_chars=100,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    assert report["dry_run"] is False
    assert report["updated"] == 1
    note = read_note(tmp_path, rel_path)
    assert note is not None
    frontmatter, updated_body = note
    assert "outline" not in frontmatter
    assert "## 目录纲要" in updated_body
    assert updated_body.count("## 目录纲要") == 1
    row = conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        ("long",),
    ).fetchone()
    assert row is not None
    assert "战略转向" in row["outline_json"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_library_agent.py::test_apply_stores_outline_in_db_and_removes_frontmatter_outline -q
```

Expected: FAIL because current Library Agent writes `outline` into frontmatter and does not write `capture_metadata`.

- [ ] **Step 3: Implement DB-backed outline maintenance**

Modify `agents/library_agent.py`:

```python
import hashlib
from datetime import datetime, timezone
from db.metadata_repo import CaptureMetadataRepo
```

Add helper methods inside `LibraryAgent`:

```python
def _capture_id_for_file(self, rel_path: str, frontmatter: dict[str, Any]) -> str:
    frontmatter_id = str(frontmatter.get("id") or "")
    if frontmatter_id:
        return frontmatter_id
    conn = self._index_db_conn()
    if conn is None:
        return ""
    row = conn.execute(
        "SELECT id FROM captures WHERE file_path = ?",
        (rel_path,),
    ).fetchone()
    return str(row["id"]) if row else ""

def _metadata_repo(self) -> CaptureMetadataRepo | None:
    conn = self._index_db_conn()
    return CaptureMetadataRepo(conn) if conn is not None else None
```

In `_maintain_one`, replace frontmatter outline as canonical source with DB metadata:

```python
metadata_repo = self._metadata_repo()
capture_id = self._capture_id_for_file(rel_path, frontmatter)
metadata = metadata_repo.get(capture_id) if metadata_repo and capture_id else None
metadata_outline = metadata.get("outline") if metadata else []
legacy_outline = frontmatter.get("outline") or []
outline = metadata_outline or legacy_outline
has_outline = bool(outline)
has_directory = _has_directory(body)
```

When apply succeeds:

```python
if not outline:
    outline = self.outliner.generate(body, len(body))

source_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
if metadata_repo and capture_id:
    metadata_repo.upsert_outline(
        capture_id,
        outline,
        source_hash=source_hash,
        directory_rendered_at=(
            datetime.now(timezone.utc).isoformat()
            if has_directory or len(body) >= self.min_outline_chars
            else None
        ),
    )

frontmatter.pop("outline", None)
updated_body = body if has_directory else _insert_directory(body, outline)
_write_markdown_parts(note_path, frontmatter, updated_body)
```

Keep the report fields `missing_frontmatter_outline` for compatibility, but treat it as `missing_metadata_outline` in the item:

```python
"missing_metadata_outline": not has_outline,
"missing_directory": not has_directory,
```

- [ ] **Step 4: Run Library Agent outline tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_library_agent.py -k outline -q
```

Expected: PASS.

## Task 4: Remove Legacy Frontmatter Outline During Maintenance

**Files:**
- Modify: `agents/library_agent.py`
- Test: `tests/test_library_agent.py`

- [ ] **Step 1: Write failing legacy migration test**

Add to `tests/test_library_agent.py`:

```python
def test_existing_frontmatter_outline_is_migrated_to_db_and_removed(tmp_path: Path) -> None:
    outline = [{"level": 1, "title": "已有目录", "summary": "已经整理过。"}]
    body = "# 测试长文\n\n## 目录纲要\n\n- 已有目录\n\n正文。\n\n" + "更多。\n\n" * 40
    rel_path = _write_capture(tmp_path, body=body, outline=outline)
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("legacy", rel_path))
    conn.commit()

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(),
        min_outline_chars=100,
        db_conn=conn,
    ).maintain_outlines(apply=True)

    frontmatter, updated_body = read_note(tmp_path, rel_path)
    assert "outline" not in frontmatter
    assert updated_body.count("## 目录纲要") == 1
    row = conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        ("legacy",),
    ).fetchone()
    assert row is not None
    assert "已有目录" in row["outline_json"]
    assert report["updated"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_library_agent.py::test_existing_frontmatter_outline_is_migrated_to_db_and_removed -q
```

Expected: FAIL because existing logic skips files that already have both frontmatter outline and body directory.

- [ ] **Step 3: Implement legacy migration path**

In `_maintain_one`, do not early-return just because legacy frontmatter outline and directory both exist. Instead skip only when metadata exists, frontmatter has no `outline`, and directory exists:

```python
if metadata_outline and not frontmatter.get("outline") and has_directory:
    report["skipped_existing"] += 1
    return
```

If `frontmatter.get("outline")` exists, apply mode should migrate it to DB and remove it even when the body directory already exists.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_library_agent.py -k "outline or frontmatter" -q
```

Expected: PASS.

## Task 5: Capture Indexer Reads Outline From DB Metadata When Available

**Files:**
- Modify: `agents/capture_indexer.py`
- Modify: `agents/library_agent.py`
- Test: `tests/test_capture_indexer.py`
- Test: `tests/test_library_agent.py`

- [ ] **Step 1: Write failing indexer test**

Add to `tests/test_capture_indexer.py`:

```python
def test_identity_index_uses_metadata_outline_when_frontmatter_has_no_outline():
    from agents.capture_indexer import build_identity_index

    metadata_outline = [
        {"title": "数据库里的目录", "summary": "Agent 专用结构。"},
    ]
    item = build_identity_index(
        "cap1",
        "captures/a.md",
        {"title": "标题", "story": "摘要", "tags": ["ai"]},
        "正文第一段。",
        outline=metadata_outline,
    )

    assert "数据库里的目录" in item.index_text
    assert "Agent 专用结构" in item.index_text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_indexer.py::test_identity_index_uses_metadata_outline_when_frontmatter_has_no_outline -q
```

Expected: FAIL because `build_identity_index()` does not accept `outline=`.

- [ ] **Step 3: Update indexer signature**

Modify `agents/capture_indexer.py`:

```python
def build_identity_index(
    capture_id: str,
    file_path: str,
    frontmatter: dict,
    body: str,
    *,
    outline: list[dict] | None = None,
) -> IndexItem:
    outline_text = _outline_text(outline if outline is not None else frontmatter.get("outline") or [])
```

- [ ] **Step 4: Pass metadata outline from Library Agent embedding maintenance**

In `LibraryAgent.maintain_embedding_index`, after reading frontmatter/body:

```python
metadata_repo = CaptureMetadataRepo(conn)
metadata = metadata_repo.get(capture_id)
metadata_outline = metadata.get("outline") if metadata else None
index_items = [
    build_identity_index(
        capture_id,
        file_path,
        frontmatter,
        body,
        outline=metadata_outline,
    ),
    *build_section_indexes(capture_id, file_path, frontmatter, body),
]
```

- [ ] **Step 5: Run indexer and library tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_indexer.py tests/test_library_agent.py -q
```

Expected: PASS.

## Task 6: Keep Markdown Writer From Writing Agent Outline to Frontmatter

**Files:**
- Modify: `agents/markdown_writer.py`
- Test: `tests/test_markdown_writer.py`
- Test: `tests/test_capture_draft_workflow.py`

- [ ] **Step 1: Write failing markdown writer test**

Update or add this test in `tests/test_markdown_writer.py`:

```python
def test_write_note_stage_captures_omits_agent_outline_from_frontmatter(tmp_path):
    from agents.markdown_writer import NoteMeta, read_note, write_note

    rel = write_note(
        tmp_path,
        NoteMeta(
            id="cap1",
            title="长文",
            date="2026-05-30",
            created="2026-05-30T00:00:00+00:00",
            source="https://example.com",
            source_type="link",
            tags=["ai"],
            story="摘要",
            word_count=1000,
            outline=[{"level": 1, "title": "不应进入 frontmatter"}],
        ),
        "正文。",
        stage="captures",
    )

    frontmatter, body = read_note(tmp_path, rel)
    assert "outline" not in frontmatter
    assert body == "正文。"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_markdown_writer.py::test_write_note_stage_captures_omits_agent_outline_from_frontmatter -q
```

Expected: FAIL if current writer still writes `outline`.

- [ ] **Step 3: Remove outline writing from capture frontmatter**

In `agents/markdown_writer.py`, remove this behavior:

```python
if meta.outline:
    frontmatter["outline"] = meta.outline
```

Keep `NoteMeta.outline` temporarily for compatibility with callers, but treat it as non-Obsidian metadata.

- [ ] **Step 4: Run writer and capture workflow tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_markdown_writer.py tests/test_capture_draft_workflow.py -q
```

Expected: PASS.

## Task 7: Real Data Migration

**Files:**
- No code files expected.
- Real data affected:
  - `/Users/pipi/hermes-custom/data/captures.db`
  - `/Users/pipi/Obsidian/hermes-vault/captures/*.md`

- [ ] **Step 1: Run targeted tests before touching real data**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_schema.py \
  tests/test_metadata_repo.py \
  tests/test_library_agent.py \
  tests/test_capture_indexer.py \
  tests/test_markdown_writer.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run dry-run maintenance**

Run:

```bash
.venv/bin/python scripts/library_maintenance.py
```

Expected:
- Reports notes needing metadata migration.
- Does not rewrite Markdown.
- Does not modify SQLite.

- [ ] **Step 3: Run apply maintenance**

Run:

```bash
.venv/bin/python scripts/library_maintenance.py --apply
```

Expected:
- Existing frontmatter `outline` values are moved into `capture_metadata.outline_json`.
- Frontmatter no longer contains `outline`.
- Long notes retain or receive `## 目录纲要`.
- Short notes do not receive `## 目录纲要`.
- Existing embedding index can still be maintained.

- [ ] **Step 4: Verify real files and DB**

Run:

```bash
sqlite3 data/captures.db "SELECT COUNT(*) FROM capture_metadata WHERE outline_json <> '[]';"
rg -n "^outline:" /Users/pipi/Obsidian/hermes-vault/captures
rg -n "^## 目录纲要$" /Users/pipi/Obsidian/hermes-vault/captures
```

Expected:
- SQLite count is greater than zero.
- `rg "^outline:"` returns no capture files, or only files intentionally excluded from migration.
- `rg "^## 目录纲要$"` returns only long notes that need human navigation.

## Task 8: Verification and Hermes Restart

**Files:**
- No code files expected.

- [ ] **Step 1: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: PASS.

- [ ] **Step 2: Restart Hermes with session reset**

Run:

```bash
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p /Users/pipi/.hermes/session_backups/$timestamp
cp -a /Users/pipi/.hermes/sessions/. /Users/pipi/.hermes/session_backups/$timestamp/ 2>/dev/null || true
find /Users/pipi/.hermes/sessions -mindepth 1 -maxdepth 1 -delete
pkill -f "hermes_cli.main gateway run" || true
nohup /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m hermes_cli.main gateway run --replace \
  > /Users/pipi/.hermes/logs/gateway-restart-$timestamp.log 2>&1 &
```

- [ ] **Step 3: Verify gateway status**

Run:

```bash
cat /Users/pipi/.hermes/gateway_state.json
tail -n 80 /Users/pipi/.hermes/logs/gateway.log
```

Expected:
- Gateway state is `running`.
- WeChat is connected.
- Feishu is connected or reconnecting without blocking WeChat.

## Success Criteria

- Agent-only `outline` no longer lives in Obsidian frontmatter.
- New DB table `capture_metadata` stores outline JSON per capture.
- Library Agent migrates legacy frontmatter outlines to DB safely.
- Long notes keep a human-readable `## 目录纲要`; short notes do not get one.
- Capture indexing can use metadata outline without depending on file Properties.
- Full test suite passes.
- Hermes is restarted with session memory cleared before manual WeChat testing.

## Rollback

- DB backup from `scripts/library_maintenance.py --apply` can restore `data/captures.db`.
- Markdown changes are limited to removing frontmatter `outline` and optionally adding body directory.
- If migration needs rollback, reinsert `outline` into frontmatter from `capture_metadata.outline_json` with a small one-off script before deleting metadata rows.
- Do not drop old compatibility fallbacks until real data has been verified.

## Self-Review

- Spec coverage: The plan separates human-facing Obsidian Properties from Agent-only metadata, moves outline to DB, keeps body directory for long notes, and preserves clean Markdown.
- Placeholder scan: No `TBD`, `TODO`, or undefined later work remains.
- Type consistency: `capture_metadata`, `outline_json`, `outline_source_hash`, `CaptureMetadataRepo`, and `build_identity_index(..., outline=...)` are used consistently.

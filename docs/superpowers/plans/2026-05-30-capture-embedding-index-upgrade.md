# Capture Embedding Index Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the weak summary-only capture embedding with a reusable identity + section embedding index so Theme Agent can find meaningful note clusters and RAG can retrieve specific evidence from long notes.

**Architecture:** Keep Markdown/Obsidian as the human-readable source of truth. Add a SQLite `capture_embeddings` index table maintained by Library Agent: `identity` rows represent whole-note semantic identity, and `section` rows represent evidence-bearing note sections. Theme Agent consumes only identity embeddings; RAG consumes identity + section embeddings and expands matching capture sections from Markdown.

**Tech Stack:** Python, SQLite, YAML frontmatter, existing `Embedder` (`text-embedding-v4`), existing `LibraryAgent`, existing `ThemeAgent`, existing `RAG` bridge, pytest.

---

## File Structure

- Modify `db/schema.py`: create and migrate `capture_embeddings`.
- Create `db/embedding_repo.py`: CRUD and search helpers for index rows.
- Create `agents/capture_indexer.py`: build identity/section index text from Obsidian capture notes.
- Modify `agents/library_agent.py`: add dry-run/apply indexing maintenance.
- Modify `bridge/librarian_bridge.py`: include index coverage in audit/repair output.
- Modify `bridge/theme_bridge.py`: load identity embeddings for Theme scan.
- Modify `agents/theme_agent.py`: keep scan logic mostly unchanged, but consume richer `summary/tags/cached_text` hydrated from index.
- Modify `db/vector_search.py` or create `db/index_search.py`: search `capture_embeddings` for RAG.
- Modify `bridge/rag_bridge.py`: prefer new index search and cite capture section sources.
- Add tests:
  - `tests/test_embedding_repo.py`
  - `tests/test_capture_indexer.py`
  - extend `tests/test_library_agent.py`
  - extend `tests/test_librarian_bridge.py`
  - extend `tests/test_theme_bridge.py`
  - extend `tests/test_rag_bridge.py` or `tests/test_e2e_hermes_tools.py`

## Data Model

`capture_embeddings` rows:

```sql
CREATE TABLE IF NOT EXISTS capture_embeddings (
    id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('identity', 'section')),
    section_id TEXT NOT NULL DEFAULT '',
    section_title TEXT NOT NULL DEFAULT '',
    index_text TEXT NOT NULL DEFAULT '',
    embedding BLOB,
    source_hash TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(capture_id, kind, section_id)
)
```

Rules:
- `identity`: one row per capture.
- `section`: zero or more rows per capture, only when the note is long or has meaningful headings/outline.
- `source_hash`: hash of the text used to build that index row. If unchanged, do not re-embed.
- `captures.embedding` remains for compatibility during migration, but new code should prefer `capture_embeddings`.

## Task 1: Add Embedding Index Schema

**Files:**
- Modify: `db/schema.py`
- Test: `tests/test_schema.py`
- Create: `tests/test_embedding_repo.py`

- [ ] **Step 1: Write failing schema test**

Add to `tests/test_schema.py`:

```python
def test_capture_embeddings_table_exists(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(capture_embeddings)")]

    assert cols == [
        "id",
        "capture_id",
        "kind",
        "section_id",
        "section_title",
        "index_text",
        "embedding",
        "source_hash",
        "file_path",
        "created_at",
        "updated_at",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_schema.py::test_capture_embeddings_table_exists -q
```

Expected: FAIL because `capture_embeddings` does not exist.

- [ ] **Step 3: Implement schema**

Add `CREATE_CAPTURE_EMBEDDINGS_TABLE` to `db/schema.py`, execute it inside `init_db()`, and add a small migration helper that creates the table without touching existing `captures`.

- [ ] **Step 4: Run schema test**

Run:

```bash
.venv/bin/python -m pytest tests/test_schema.py::test_capture_embeddings_table_exists -q
```

Expected: PASS.

## Task 2: Add Embedding Repository

**Files:**
- Create: `db/embedding_repo.py`
- Test: `tests/test_embedding_repo.py`

- [ ] **Step 1: Write failing repository tests**

Create `tests/test_embedding_repo.py`:

```python
from db.schema import init_db
from db.embedding_repo import CaptureEmbeddingRepo


def test_upsert_identity_embedding_replaces_existing_row(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = CaptureEmbeddingRepo(conn)

    repo.upsert(
        capture_id="cap1",
        kind="identity",
        section_id="",
        section_title="",
        index_text="标题：A\n摘要：旧",
        embedding=b"old",
        source_hash="h1",
        file_path="captures/a.md",
    )
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        section_id="",
        section_title="",
        index_text="标题：A\n摘要：新",
        embedding=b"new",
        source_hash="h2",
        file_path="captures/a.md",
    )

    rows = repo.list_by_capture("cap1")
    assert len(rows) == 1
    assert rows[0]["index_text"].endswith("摘要：新")
    assert rows[0]["embedding"] == b"new"


def test_list_stale_rows_detects_hash_changes(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        section_id="",
        section_title="",
        index_text="old",
        embedding=b"old",
        source_hash="old-hash",
        file_path="captures/a.md",
    )

    stale = repo.needs_reindex("cap1", "identity", "", "new-hash")
    fresh = repo.needs_reindex("cap1", "identity", "", "old-hash")

    assert stale is True
    assert fresh is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_embedding_repo.py -q
```

Expected: FAIL because `db.embedding_repo` does not exist.

- [ ] **Step 3: Implement repository**

Implement:

```python
class CaptureEmbeddingRepo:
    def __init__(self, conn): ...
    def upsert(...): ...
    def list_by_capture(self, capture_id: str) -> list[dict]: ...
    def needs_reindex(self, capture_id: str, kind: str, section_id: str, source_hash: str) -> bool: ...
    def search(self, query_embedding: bytes, *, kinds: tuple[str, ...], limit: int) -> list[dict]: ...
```

Use existing `agents.embedder.cosine_similarity` inside `search()`. Keep search simple and in-memory for now because the current dataset is small.

- [ ] **Step 4: Run repository tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_embedding_repo.py -q
```

Expected: PASS.

## Task 3: Build Identity and Section Index Text

**Files:**
- Create: `agents/capture_indexer.py`
- Test: `tests/test_capture_indexer.py`

- [ ] **Step 1: Write failing indexer tests**

Create `tests/test_capture_indexer.py`:

```python
from agents.capture_indexer import build_identity_index, build_section_indexes


def test_identity_index_uses_title_story_tags_outline_and_body_hint():
    frontmatter = {
        "title": "Opus 4.8 发布",
        "story": "Anthropic 通过 41 天补丁包调整开发者策略。",
        "tags": ["ai-agent", "anthropic"],
        "outline": [{"title": "Terminal-Bench", "summary": "未补的坑很关键。"}],
    }
    body = "正文第一段讲 41 天节奏狙击。\n\n第二段讲 Terminal-Bench。"

    item = build_identity_index("cap1", "captures/a.md", frontmatter, body)

    assert item.kind == "identity"
    assert "Opus 4.8 发布" in item.index_text
    assert "ai-agent" in item.index_text
    assert "Terminal-Bench" in item.index_text
    assert "41 天节奏狙击" in item.index_text


def test_section_indexes_split_markdown_headings():
    frontmatter = {"title": "长文"}
    body = "# 长文\n\n## 第一部分\n内容 A。\n\n## 第二部分\n内容 B。"

    sections = build_section_indexes("cap1", "captures/a.md", frontmatter, body)

    assert [s.section_title for s in sections] == ["第一部分", "第二部分"]
    assert "内容 A" in sections[0].index_text
    assert "内容 B" in sections[1].index_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_indexer.py -q
```

Expected: FAIL because `agents.capture_indexer` does not exist.

- [ ] **Step 3: Implement index builder**

Implement a small dataclass:

```python
@dataclass(frozen=True)
class IndexItem:
    capture_id: str
    kind: str
    section_id: str
    section_title: str
    index_text: str
    source_hash: str
    file_path: str
```

Rules:
- Identity index text is title + story/summary + tags + outline + first useful body paragraph.
- Section index text is capture title + section title + section body clipped to a safe length.
- Use deterministic `source_hash = sha256(index_text.encode("utf-8")).hexdigest()`.
- Do not call LLM here.
- Do not mutate Markdown here.

- [ ] **Step 4: Run indexer tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_indexer.py -q
```

Expected: PASS.

## Task 4: Library Agent Maintains Embedding Index

**Files:**
- Modify: `agents/library_agent.py`
- Modify: `scripts/library_maintenance.py`
- Test: `tests/test_library_agent.py`

- [ ] **Step 1: Write failing Library Agent tests**

Add tests:

```python
def test_library_agent_dry_run_reports_missing_embedding_index(tmp_path):
    rel_path = _write_capture(
        tmp_path,
        body="## 第一部分\n这是一篇关于 AI Agent 的长文。" * 20,
        title="AI Agent 长文",
    )
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("cap1", rel_path))
    conn.commit()

    agent = LibraryAgent(tmp_path, outliner=FakeOutliner(), db_conn=conn, embedder=None)
    report = agent.maintain_embedding_index(apply=False)

    assert report["missing_or_stale"] == 1
    assert report["updated"] == 0


def test_library_agent_apply_builds_identity_and_section_embeddings(tmp_path):
    rel_path = _write_capture(
        tmp_path,
        body="## 第一部分\n内容 A。\n\n## 第二部分\n内容 B。",
        title="AI Agent 长文",
    )
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("cap1", rel_path))
    conn.commit()

    class FakeEmbedder:
        def embed(self, text: str) -> bytes:
            return text[:8].encode("utf-8")

    agent = LibraryAgent(tmp_path, outliner=FakeOutliner(), db_conn=conn, embedder=FakeEmbedder())
    report = agent.maintain_embedding_index(apply=True)

    rows = conn.execute("SELECT kind, section_title FROM capture_embeddings").fetchall()
    assert report["updated"] == 3
    assert [row["kind"] for row in rows].count("identity") == 1
    assert [row["kind"] for row in rows].count("section") == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_library_agent.py -k embedding_index -q
```

Expected: FAIL because `LibraryAgent` has no embedding index maintenance.

- [ ] **Step 3: Implement Library Agent indexing**

Add optional constructor dependencies to `LibraryAgent`:

```python
def __init__(self, vault_path, *, outliner, min_outline_chars=..., db_conn=None, embedder=None):
```

Add:

```python
def maintain_embedding_index(self, *, apply: bool = False) -> dict[str, Any]:
```

Behavior:
- Iterate DB captures, hydrate Markdown note.
- Build identity item and section items.
- Dry-run reports missing/stale rows.
- Apply embeds only missing/stale rows.
- If `embedder` is missing during apply, report an error item instead of crashing.
- Do not rewrite Markdown.

- [ ] **Step 4: Run Library Agent tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_library_agent.py -q
```

Expected: PASS.

## Task 5: Expose Index Maintenance in Librarian Bridge

**Files:**
- Modify: `bridge/librarian_bridge.py`
- Modify: `scripts/library_maintenance.py`
- Test: `tests/test_librarian_bridge.py`

- [ ] **Step 1: Write failing bridge test**

Add to `tests/test_librarian_bridge.py`:

```python
def test_librarian_bridge_reports_embedding_index_status(monkeypatch):
    class IndexLibraryAgent(FakeLibraryAgent):
        def maintain_embedding_index(self, *, apply: bool = False) -> dict:
            return {"scanned": 3, "missing_or_stale": 2, "updated": 0, "errors": 0, "items": []}

    monkeypatch.setattr(librarian_bridge, "LibraryAgent", IndexLibraryAgent)
    monkeypatch.setattr(librarian_bridge, "Outliner", lambda **_kwargs: object())

    envelope = librarian_bridge.audit_library(apply=False)

    assert envelope["meta"]["embedding_index"]["missing_or_stale"] == 2
    assert "索引" in envelope["chat_preview"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_librarian_bridge.py::test_librarian_bridge_reports_embedding_index_status -q
```

Expected: FAIL because bridge does not include embedding index.

- [ ] **Step 3: Implement bridge summary**

Call `maintain_embedding_index(apply=apply)` after outline maintenance. Include counts in `chat_preview` and `meta`.

- [ ] **Step 4: Run bridge tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_librarian_bridge.py -q
```

Expected: PASS.

## Task 6: Theme Agent Uses Identity Embeddings

**Files:**
- Modify: `bridge/theme_bridge.py`
- Modify: `agents/theme_agent.py` only if needed
- Test: `tests/test_theme_bridge.py`

- [ ] **Step 1: Write failing Theme bridge test**

Add a test that inserts two captures, two `capture_embeddings(kind='identity')` rows, and verifies `_hydrate_capture` or a new helper loads the identity embedding and index text even when legacy `captures.embedding` is empty.

Expected assertions:

```python
assert hydrated["embedding"] == identity_embedding
assert "index_text" in hydrated
assert hydrated["summary"]  # derived from frontmatter/story or index text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_theme_bridge.py -k identity_embedding -q
```

Expected: FAIL because Theme bridge only reads `captures.embedding`.

- [ ] **Step 3: Implement identity loading**

Change `run_theme_scan()` query from:

```sql
SELECT * FROM captures WHERE embedding IS NOT NULL AND theme_scanned_at IS NULL
```

to a query that joins or checks `capture_embeddings` identity rows:

```sql
SELECT c.*
FROM captures c
JOIN capture_embeddings e
  ON e.capture_id = c.id AND e.kind = 'identity' AND e.embedding IS NOT NULL
WHERE c.theme_scanned_at IS NULL
ORDER BY c.created_at ASC
LIMIT 50
```

When hydrating each capture, prefer identity `embedding` over legacy `captures.embedding`.

- [ ] **Step 4: Run Theme tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_theme_agent.py tests/test_theme_bridge.py -q
```

Expected: PASS.

## Task 7: RAG Searches Identity + Section Embeddings

**Files:**
- Create or modify: `db/index_search.py`
- Modify: `bridge/rag_bridge.py`
- Test: add/extend `tests/test_rag_bridge.py`

- [ ] **Step 1: Write failing RAG search test**

Test setup:
- One capture note with two sections.
- `capture_embeddings` contains one identity row and two section rows.
- Fake query embedding ranks section two highest.

Expected:
- RAG returns the capture with `section_title` for section two.
- `source_map` has `type="capture_section"` and `section="第二部分"`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_rag_bridge.py -q
```

Expected: FAIL because RAG uses legacy `db.vector_search`.

- [ ] **Step 3: Implement indexed RAG search**

Implement new search path:
- embed query once.
- search `capture_embeddings` for `kind in ('identity', 'section')`.
- group by capture_id.
- prefer exact section hits for long notes.
- hydrate Markdown note.
- if section hit exists, extract only that section body for LLM context.
- fall back to legacy vector/text search if no index rows exist.

- [ ] **Step 4: Run RAG tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_rag_agent.py tests/test_rag_bridge.py -q
```

Expected: PASS.

## Task 8: One-Time Migration for Existing Captures

**Files:**
- Modify: `scripts/library_maintenance.py`
- Maybe create: `scripts/rebuild_embedding_index.py` only if CLI complexity grows
- Test: `tests/test_library_agent.py`

- [ ] **Step 1: Add dry-run command expectation**

Run:

```bash
.venv/bin/python scripts/library_maintenance.py
```

Expected:
- No DB writes.
- Report includes embedding index scanned/missing counts.

- [ ] **Step 2: Add apply command expectation**

Run:

```bash
.venv/bin/python scripts/library_maintenance.py --apply
```

Expected:
- Builds missing/stale identity and section embeddings.
- Does not modify Markdown files except existing outline behavior when already approved by `--apply`.

- [ ] **Step 3: Add report counts**

The report should include:

```text
embedding_index:
  scanned
  missing_or_stale
  updated
  errors
```

## Task 9: Verification and Hermes Restart

**Files:**
- No code files expected.

- [ ] **Step 1: Run targeted tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_schema.py \
  tests/test_embedding_repo.py \
  tests/test_capture_indexer.py \
  tests/test_library_agent.py \
  tests/test_librarian_bridge.py \
  tests/test_theme_agent.py \
  tests/test_theme_bridge.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: PASS.

- [ ] **Step 3: Dry-run real library audit**

Run:

```bash
.venv/bin/python scripts/library_maintenance.py
```

Expected:
- Shows how many captures need identity/section index.
- No writes.

- [ ] **Step 4: Apply real migration**

Run:

```bash
.venv/bin/python scripts/library_maintenance.py --apply
```

Expected:
- Existing captures get identity rows.
- Long/sectioned captures get section rows.
- No Markdown deletion.

- [ ] **Step 5: Restart Hermes with session reset**

Follow current Hermes hygiene:

```bash
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p /Users/pipi/.hermes/session_backups/$timestamp
cp -a /Users/pipi/.hermes/sessions/. /Users/pipi/.hermes/session_backups/$timestamp/
find /Users/pipi/.hermes/sessions -mindepth 1 -maxdepth 1 -delete
pkill -f "hermes_cli.main gateway run" || true
nohup /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m hermes_cli.main gateway run --replace \
  > /Users/pipi/.hermes/logs/gateway-restart-$timestamp.log 2>&1 &
```

Verify:

```bash
cat /Users/pipi/.hermes/gateway_state.json
tail -n 80 /Users/pipi/.hermes/logs/gateway.log
```

Expected:
- Gateway running.
- Weixin connected.
- Feishu connected or reconnecting without blocking Weixin.

## Success Criteria

- Existing 35 captures receive `identity` index rows.
- Long or headed captures receive useful `section` index rows.
- Theme scan no longer depends on summary-only `captures.embedding`.
- RAG can cite `capture_section` when a section row is the best match.
- Library audit tells the user whether index coverage is missing/stale.
- No raw/capture Markdown files are deleted or rewritten by embedding index maintenance.

## Rollback

- Do not drop `captures.embedding`.
- If new search has issues, RAG falls back to old `vector_search` and text search.
- If Theme identity indexing has issues, Theme scan can temporarily fall back to legacy `captures.embedding`.
- `capture_embeddings` can be rebuilt from Markdown and DB routes, so it is cache-like and safe to regenerate.

## Self-Review

- Spec coverage: Covers identity embeddings for Theme, section embeddings for RAG, Library ownership, migration, and verification.
- Placeholder scan: No TBD/TODO placeholders remain.
- Scope check: This is one focused indexing upgrade. It does not redesign Garden or Review behavior yet.
- Type consistency: `kind`, `section_id`, `section_title`, `index_text`, `source_hash`, and `file_path` are used consistently across schema, repo, indexer, Theme, and RAG tasks.

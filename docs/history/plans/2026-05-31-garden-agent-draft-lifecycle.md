---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Garden Agent Draft Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Garden Agent create reviewable Garden Drafts first, then write final Obsidian Garden Cards only after explicit user confirmation.

**Architecture:** Keep `themes/` MOC pages as topic maps and `garden/` cards as user-reviewed opinion artifacts. `synthesize_garden` becomes a draft-producing bridge call backed by a new `garden_drafts` SQLite table; `commit_garden_draft` is the only path that writes `garden_cards` rows and `garden/*.md` files. Hermes main gets explicit tools for synthesize, revise, and commit so the gateway can route multi-turn WeChat review without treating Garden output as already accepted.

**Tech Stack:** Python 3.11, SQLite, existing Hermes Agent Result Envelope, Obsidian Markdown, pytest, `/Users/pipi/hermes-custom`, `/Users/pipi/Downloads/hermes-agent-main`.

---

## Scope

This P0 is intentionally narrow.

In scope:
- Add a `garden_drafts` table.
- Change `synthesize_garden` so it creates a draft, not a final card.
- Add `revise_garden_draft`.
- Add `commit_garden_draft`.
- Add Hermes runtime tools for revise and commit.
- Keep WeChat replies short enough to review: title, one-liner, 2-4 key claims, and clear next steps.

Out of scope:
- Automatic Garden idea mining from the whole vault.
- A Garden frontend page.
- Bidirectional wiki links from every Capture into every Garden Card.
- Review Agent scheduling changes.
- Theme/MOC layout changes.
- Life Assistant files.

## Working Assumptions

- Garden Cards are opinion artifacts, not material summaries.
- A Garden Card can be based on one capture, multiple captures, or a `pending_questions` cluster, but it should still be user-reviewed before landing in `garden/`.
- `garden_cards` remains the committed table consumed by Review/RAG.
- `garden_drafts` is a transient review table similar in spirit to `capture_drafts`.
- User-facing Chinese text is acceptable in bridge previews and tool descriptions.
- Commit should use existing `garden_cards` schema to avoid broad migrations in Review Agent.

## File Map

Modify `/Users/pipi/hermes-custom`:

- `db/schema.py`
  - Add `CREATE_GARDEN_DRAFTS_TABLE`.
  - Run it in `init_db`.

- `agents/garden_agent.py`
  - Keep `GardenAgent.synthesize()` focused on LLM synthesis.
  - Add DB helpers:
    - `create_garden_draft(repo, garden_data) -> str`
    - `get_garden_draft(repo, draft_id) -> dict | None`
    - `get_latest_open_garden_draft(repo) -> dict | None`
    - `update_garden_draft(repo, draft_id, garden_data, status="draft_ready") -> dict | None`
    - `commit_garden_draft_to_card(repo, draft_id) -> str | None`
  - Keep existing `create_garden_card()` and `update_garden_card()` for committed cards.

- `bridge/garden_bridge.py`
  - Change `synthesize_garden()` to create `garden_drafts`.
  - Add `revise_garden_draft(draft_id="", instruction="")`.
  - Add `commit_garden_draft(draft_id="")`.
  - Add private preview/path helpers inside this file, not a broad new module.

- `bridge/result_envelope.py`
  - Add `commit_garden_draft` to `STATE_CHANGING_INTENTS`.
  - Keep `accept_garden_card` for backward compatibility unless unused tests prove safe removal.

- `tests/test_schema.py`
  - Add a schema test for `garden_drafts`.

- `tests/test_garden_agent.py`
  - Add tests for draft DB helpers and commit helper.

- `tests/test_garden_bridge.py`
  - Update synthesize behavior test.
  - Add revise and commit bridge tests.

- `tests/test_result_envelope.py`
  - Update state-changing intent coverage.

- `tests/test_e2e_hermes_tools.py`
  - Update expected personal-assistant toolset.
  - Add or adjust Garden tool envelope tests.

Modify `/Users/pipi/Downloads/hermes-agent-main`:

- `tools/synthesize_garden_tool.py`
  - Update description: creates reviewable draft, not final card.

- `tools/revise_garden_draft_tool.py`
  - New tool wrapper.

- `tools/commit_garden_draft_tool.py`
  - New tool wrapper.

- `toolsets.py`
  - Add both new tools to `personal-assistant`.

- `tests/tools/test_garden_draft_tools.py`
  - New focused tests for runtime wrappers.

- `tests/test_toolsets.py` or an existing toolset coverage file if present
  - Assert the new tools are available.

Do not modify:
- Life Assistant files already dirty in the worktree.
- Theme Agent behavior.
- Review Agent scoring or selection.

---

## Task 1: Add Garden Draft Schema

**Files:**
- Modify: `/Users/pipi/hermes-custom/db/schema.py`
- Test: `/Users/pipi/hermes-custom/tests/test_schema.py`

- [ ] **Step 1: Write the failing schema test**

Add this test to `tests/test_schema.py`:

```python
def test_garden_drafts_table_exists(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(garden_drafts)")]

    assert cols == [
        "id",
        "title",
        "one_liner",
        "core_arguments",
        "knowledge_links",
        "key_materials",
        "markdown",
        "capture_ids",
        "question_id",
        "instruction",
        "status",
        "committed_card_id",
        "created_at",
        "updated_at",
    ]
    conn.close()
```

- [ ] **Step 2: Run the schema test and verify red**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_schema.py::test_garden_drafts_table_exists -q
```

Expected: fail because `garden_drafts` does not exist.

- [ ] **Step 3: Add the schema**

In `db/schema.py`, add this constant after `CREATE_GARDEN_CARDS_TABLE`:

```python
CREATE_GARDEN_DRAFTS_TABLE = """
CREATE TABLE IF NOT EXISTS garden_drafts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    one_liner TEXT NOT NULL DEFAULT '',
    core_arguments TEXT NOT NULL DEFAULT '[]',
    knowledge_links TEXT NOT NULL DEFAULT '[]',
    key_materials TEXT NOT NULL DEFAULT '[]',
    markdown TEXT NOT NULL DEFAULT '',
    capture_ids TEXT NOT NULL DEFAULT '[]',
    question_id TEXT NOT NULL DEFAULT '',
    instruction TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft_ready',
    committed_card_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""
```

In `init_db()`, execute it immediately after `CREATE_GARDEN_CARDS_TABLE`:

```python
conn.execute(CREATE_GARDEN_CARDS_TABLE)
conn.execute(CREATE_GARDEN_DRAFTS_TABLE)
conn.execute(CREATE_PENDING_QUESTIONS_TABLE)
```

- [ ] **Step 4: Run the schema test and verify green**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_schema.py::test_garden_drafts_table_exists -q
```

Expected: pass.

- [ ] **Step 5: Commit the schema task**

Only if this task is implemented in its own commit:

```bash
cd /Users/pipi/hermes-custom
git add db/schema.py tests/test_schema.py
git commit -m "feat: add garden drafts schema"
```

---

## Task 2: Add Garden Draft DB Helpers

**Files:**
- Modify: `/Users/pipi/hermes-custom/agents/garden_agent.py`
- Test: `/Users/pipi/hermes-custom/tests/test_garden_agent.py`

- [ ] **Step 1: Update imports in the test**

In `tests/test_garden_agent.py`, change the garden imports to include the draft helpers:

```python
from agents.garden_agent import (
    GardenAgent,
    commit_garden_draft_to_card,
    create_garden_card,
    create_garden_draft,
    get_garden_draft,
    get_latest_open_garden_draft,
    update_garden_card,
    update_garden_draft,
)
```

- [ ] **Step 2: Write failing helper tests**

Add these tests to `tests/test_garden_agent.py`:

```python
def test_create_and_get_garden_draft(repo):
    draft_id = create_garden_draft(repo, {
        "title": "测试观点草稿",
        "one_liner": "一个还没有确认的观点。",
        "core_arguments": [{"claim": "观点一", "source_ids": ["cap1"]}],
        "knowledge_links": ["gc1"],
        "key_materials": [{"type": "quote", "content": "素材", "capture_id": "cap1"}],
        "markdown": "# 测试观点草稿\n\n正文",
        "capture_ids": ["cap1"],
        "question_id": "q1",
        "instruction": "从这批材料提炼观点",
    })

    row = get_garden_draft(repo, draft_id)

    assert row is not None
    assert row["title"] == "测试观点草稿"
    assert row["status"] == "draft_ready"
    assert json.loads(row["capture_ids"]) == ["cap1"]
    assert json.loads(row["core_arguments"])[0]["claim"] == "观点一"
```

```python
def test_get_latest_open_garden_draft_skips_committed(repo):
    old_id = create_garden_draft(repo, {
        "title": "旧草稿",
        "one_liner": "旧",
        "core_arguments": [],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 旧草稿\n",
        "capture_ids": [],
    })
    new_id = create_garden_draft(repo, {
        "title": "新草稿",
        "one_liner": "新",
        "core_arguments": [],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 新草稿\n",
        "capture_ids": [],
    })
    repo.conn.execute(
        "UPDATE garden_drafts SET status='committed' WHERE id=?",
        (new_id,),
    )
    repo.conn.commit()

    latest = get_latest_open_garden_draft(repo)

    assert latest["id"] == old_id
```

```python
def test_update_garden_draft_changes_content_and_instruction(repo):
    draft_id = create_garden_draft(repo, {
        "title": "原始草稿",
        "one_liner": "原始",
        "core_arguments": [],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 原始草稿\n",
        "capture_ids": ["cap1"],
    })

    updated = update_garden_draft(repo, draft_id, {
        "title": "修改后草稿",
        "one_liner": "修改后",
        "core_arguments": [{"claim": "新观点"}],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 修改后草稿\n",
        "instruction": "更强调边界",
    })

    assert updated is not None
    row = get_garden_draft(repo, draft_id)
    assert row["title"] == "修改后草稿"
    assert row["instruction"] == "更强调边界"
    assert json.loads(row["core_arguments"])[0]["claim"] == "新观点"
```

```python
def test_commit_garden_draft_to_card_creates_card_and_marks_draft(repo):
    draft_id = create_garden_draft(repo, {
        "title": "可入库观点",
        "one_liner": "确认后才能进入 garden。",
        "core_arguments": [{"claim": "草稿先审阅"}],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 可入库观点\n\n确认后内容。",
        "capture_ids": ["cap1"],
    })

    card_id = commit_garden_draft_to_card(repo, draft_id)

    assert card_id
    card = repo.conn.execute(
        "SELECT * FROM garden_cards WHERE id=?",
        (card_id,),
    ).fetchone()
    draft = get_garden_draft(repo, draft_id)
    assert card["title"] == "可入库观点"
    assert card["status"] == "seed"
    assert draft["status"] == "committed"
    assert draft["committed_card_id"] == card_id
```

- [ ] **Step 3: Run helper tests and verify red**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_create_and_get_garden_draft /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_get_latest_open_garden_draft_skips_committed /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_update_garden_draft_changes_content_and_instruction /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_commit_garden_draft_to_card_creates_card_and_marks_draft -q
```

Expected: fail because the helper functions are not implemented.

- [ ] **Step 4: Add minimal helper implementations**

In `agents/garden_agent.py`, add these functions near `create_garden_card()`:

```python
def _json_text(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)
```

```python
def create_garden_draft(repo, garden_data: dict) -> str:
    """写入 garden_drafts 表，返回 draft ID。"""
    draft_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    repo.conn.execute(
        """INSERT INTO garden_drafts (id, title, one_liner, core_arguments,
           knowledge_links, key_materials, markdown, capture_ids, question_id,
           instruction, status, committed_card_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_ready', '', ?, ?)""",
        (
            draft_id,
            garden_data.get("title", ""),
            garden_data.get("one_liner", ""),
            _json_text(garden_data.get("core_arguments", [])),
            _json_text(garden_data.get("knowledge_links", [])),
            _json_text(garden_data.get("key_materials", [])),
            garden_data.get("markdown", ""),
            _json_text(garden_data.get("capture_ids", [])),
            garden_data.get("question_id", ""),
            garden_data.get("instruction", ""),
            now,
            now,
        ),
    )
    repo.conn.commit()
    return draft_id
```

```python
def get_garden_draft(repo, draft_id: str) -> Optional[dict]:
    row = repo.conn.execute(
        "SELECT * FROM garden_drafts WHERE id = ?",
        (draft_id,),
    ).fetchone()
    return dict(row) if row else None
```

```python
def get_latest_open_garden_draft(repo) -> Optional[dict]:
    row = repo.conn.execute(
        """SELECT * FROM garden_drafts
           WHERE status IN ('draft_ready', 'revision_requested')
           ORDER BY updated_at DESC, created_at DESC
           LIMIT 1"""
    ).fetchone()
    return dict(row) if row else None
```

```python
def update_garden_draft(
    repo,
    draft_id: str,
    garden_data: dict,
    status: str = "draft_ready",
) -> Optional[dict]:
    existing = get_garden_draft(repo, draft_id)
    if not existing:
        return None

    now = datetime.now(timezone.utc).isoformat()
    repo.conn.execute(
        """UPDATE garden_drafts
           SET title=?, one_liner=?, core_arguments=?, knowledge_links=?,
               key_materials=?, markdown=?, instruction=?, status=?, updated_at=?
           WHERE id=?""",
        (
            garden_data.get("title", existing["title"]),
            garden_data.get("one_liner", existing["one_liner"]),
            _json_text(garden_data.get("core_arguments", json.loads(existing["core_arguments"]))),
            _json_text(garden_data.get("knowledge_links", json.loads(existing["knowledge_links"]))),
            _json_text(garden_data.get("key_materials", json.loads(existing["key_materials"]))),
            garden_data.get("markdown", existing["markdown"]),
            garden_data.get("instruction", existing["instruction"]),
            status,
            now,
            draft_id,
        ),
    )
    repo.conn.commit()
    return get_garden_draft(repo, draft_id)
```

```python
def commit_garden_draft_to_card(repo, draft_id: str) -> Optional[str]:
    draft = get_garden_draft(repo, draft_id)
    if not draft or draft["status"] == "committed":
        return None

    card_id = create_garden_card(repo, {
        "title": draft["title"],
        "one_liner": draft["one_liner"],
        "core_arguments": json.loads(draft["core_arguments"]),
        "knowledge_links": json.loads(draft["knowledge_links"]),
        "key_materials": json.loads(draft["key_materials"]),
        "capture_ids": json.loads(draft["capture_ids"]),
    })
    now = datetime.now(timezone.utc).isoformat()
    repo.conn.execute(
        """UPDATE garden_drafts
           SET status='committed', committed_card_id=?, updated_at=?
           WHERE id=?""",
        (card_id, now, draft_id),
    )
    repo.conn.commit()
    return card_id
```

- [ ] **Step 5: Run helper tests and verify green**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_create_and_get_garden_draft /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_get_latest_open_garden_draft_skips_committed /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_update_garden_draft_changes_content_and_instruction /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_commit_garden_draft_to_card_creates_card_and_marks_draft -q
```

Expected: pass.

- [ ] **Step 6: Commit the DB helper task**

```bash
cd /Users/pipi/hermes-custom
git add agents/garden_agent.py tests/test_garden_agent.py
git commit -m "feat: add garden draft repository helpers"
```

---

## Task 3: Change Garden Bridge to Draft-First

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/garden_bridge.py`
- Modify: `/Users/pipi/hermes-custom/bridge/result_envelope.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_garden_bridge.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_result_envelope.py`

- [ ] **Step 1: Add `commit_garden_draft` as a confirmation-required intent**

In `tests/test_result_envelope.py`, update the existing state-changing intent coverage to include:

```python
"commit_garden_draft",
```

Then run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_result_envelope.py -q
```

Expected: fail if the test enumerates all state-changing intents.

- [ ] **Step 2: Update result envelope intents**

In `bridge/result_envelope.py`, add:

```python
"commit_garden_draft",
```

to `STATE_CHANGING_INTENTS`.

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_result_envelope.py -q
```

Expected: pass.

- [ ] **Step 3: Replace the old synthesize bridge test**

In `tests/test_garden_bridge.py`, replace `test_synthesize_garden_returns_seed_card_with_accept_action` with:

```python
def test_synthesize_garden_creates_reviewable_draft_not_card(repo, tmp_path):
    """Garden 合成应先生成草稿，不直接写入 garden_cards 或 garden/*.md。"""
    from bridge.garden_bridge import synthesize_garden

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    (captures_dir / "cap.md").write_text(
        "---\ntitle: 测试素材\nstory: 摘要\n---\n\n正文内容\n",
        encoding="utf-8",
    )
    repo.conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("cap1", "captures/cap.md"),
    )
    repo.conn.commit()

    agent_result = {
        "title": "测试观点",
        "one_liner": "这是一个可继续演化的观点。",
        "core_arguments": [{"claim": "观点要先审阅", "source_ids": ["cap1"]}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 测试观点\n\n正文",
    }
    db_path = str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]))
    with patch("bridge.garden_bridge.load_config") as mock_cfg, \
            patch.object(GardenAgent, "synthesize", return_value=agent_result):
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = synthesize_garden(capture_ids=["cap1"])

    assert result["status"] == "success"
    artifact = result["durable_artifacts"][0]
    assert artifact["type"] == "garden_draft"
    assert artifact["status"] == "draft_ready"
    assert result["next_actions"][0]["intent"] == "commit_garden_draft"
    assert result["next_actions"][0]["requires_confirmation"] is True
    assert "确认" in result["chat_preview"]
    assert "观点要先审阅" in result["chat_preview"]

    draft = repo.conn.execute(
        "SELECT * FROM garden_drafts WHERE id = ?",
        (artifact["id"],),
    ).fetchone()
    card_count = repo.conn.execute("SELECT count(*) FROM garden_cards").fetchone()[0]
    assert draft["title"] == "测试观点"
    assert card_count == 0
    assert not (tmp_path / "garden").exists()
```

- [ ] **Step 4: Add commit bridge test**

Add this test to `tests/test_garden_bridge.py`:

```python
def test_commit_garden_draft_writes_card_file_and_marks_draft(repo, tmp_path):
    """确认 Garden Draft 后才写 garden_cards 和 Obsidian 文件。"""
    from agents.garden_agent import create_garden_draft
    from bridge.garden_bridge import commit_garden_draft

    draft_id = create_garden_draft(repo, {
        "title": "确认后的观点",
        "one_liner": "确认才进入 garden。",
        "core_arguments": [{"claim": "先草稿后入库"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 确认后的观点\n\n正文",
        "capture_ids": ["cap1"],
    })
    db_path = str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]))

    with patch("bridge.garden_bridge.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = commit_garden_draft(draft_id)

    assert result["status"] == "success"
    artifact = result["durable_artifacts"][0]
    assert artifact["type"] == "garden_card"
    assert artifact["status"] == "seed"
    assert artifact["path"].startswith("garden/")
    assert (tmp_path / artifact["path"]).exists()
    assert "# 确认后的观点" in (tmp_path / artifact["path"]).read_text(encoding="utf-8")

    draft = repo.conn.execute(
        "SELECT * FROM garden_drafts WHERE id=?",
        (draft_id,),
    ).fetchone()
    assert draft["status"] == "committed"
    assert draft["committed_card_id"] == artifact["id"]
```

- [ ] **Step 5: Add revise bridge test**

Add this test to `tests/test_garden_bridge.py`:

```python
def test_revise_garden_draft_updates_latest_open_draft(repo, tmp_path):
    """用户提出修改意见时，默认修改最近一个开放 Garden Draft。"""
    from agents.garden_agent import create_garden_draft
    from bridge.garden_bridge import revise_garden_draft

    create_garden_draft(repo, {
        "title": "旧观点",
        "one_liner": "旧观点",
        "core_arguments": [],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 旧观点\n",
        "capture_ids": ["cap1"],
    })
    agent_result = {
        "title": "修改后的观点",
        "one_liner": "修改后更贴近用户意见。",
        "core_arguments": [{"claim": "保留原观点但强调边界"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 修改后的观点\n\n新正文",
    }
    db_path = str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]))

    with patch("bridge.garden_bridge.load_config") as mock_cfg, \
            patch.object(GardenAgent, "synthesize", return_value=agent_result):
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = revise_garden_draft(instruction="强调边界")

    assert result["status"] == "success"
    assert result["durable_artifacts"][0]["type"] == "garden_draft"
    assert "保留原观点但强调边界" in result["chat_preview"]
    row = repo.conn.execute("SELECT * FROM garden_drafts").fetchone()
    assert row["title"] == "修改后的观点"
    assert row["instruction"] == "强调边界"
```

- [ ] **Step 6: Run bridge tests and verify red**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_garden_bridge.py::test_synthesize_garden_creates_reviewable_draft_not_card /Users/pipi/hermes-custom/tests/test_garden_bridge.py::test_commit_garden_draft_writes_card_file_and_marks_draft /Users/pipi/hermes-custom/tests/test_garden_bridge.py::test_revise_garden_draft_updates_latest_open_draft -q
```

Expected: fail until bridge behavior is implemented.

- [ ] **Step 7: Update bridge imports**

In `bridge/garden_bridge.py`, replace the garden import with:

```python
from agents.garden_agent import (
    GardenAgent,
    commit_garden_draft_to_card,
    create_garden_draft,
    get_garden_draft,
    get_latest_open_garden_draft,
    update_garden_draft,
)
```

- [ ] **Step 8: Add private helpers to the bridge**

Add these helpers below `_hydrate_capture()`:

```python
def _json_list(text: str) -> list:
    try:
        value = json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []
```

```python
def _draft_preview(title: str, one_liner: str, arguments: list[dict], draft_id: str) -> str:
    claims = []
    for item in arguments[:4]:
        claim = item.get("claim", "").strip()
        if claim:
            claims.append(f"- {claim}")
    claims_text = "\n".join(claims) if claims else "- 暂无明确观点，请补充修改意见"
    return (
        f"已生成 Garden 观点草稿：《{title}》\n\n"
        f"一句话：{one_liner}\n\n"
        f"观点预览：\n{claims_text}\n\n"
        f"草稿编号：{draft_id}\n"
        "满意回复“确认观点”或“存入花园”；不满意直接说怎么改。"
    )
```

```python
def _safe_garden_filename(title: str, card_id: str) -> str:
    cleaned = "".join(ch for ch in title if ch not in '/:*?\"<>|').strip()
    cleaned = cleaned[:48] or "未命名观点"
    return f"{cleaned}-{card_id}.md"
```

- [ ] **Step 9: Change `synthesize_garden()` to create a draft**

Replace the block that currently calls `create_garden_card()`, updates pending question, writes `garden/*.md`, and returns `garden_card` with:

```python
    garden_data = {
        "title": result.get("title", ""),
        "one_liner": result.get("one_liner", ""),
        "core_arguments": result.get("core_arguments", []),
        "key_materials": result.get("key_materials", []),
        "knowledge_links": result.get("knowledge_links", []),
        "markdown": result.get("markdown", ""),
        "capture_ids": capture_ids,
        "question_id": question_id,
        "instruction": title,
    }
    draft_id = create_garden_draft(repo, garden_data)

    title_text = result.get("title", "")
    one_liner = result.get("one_liner", "")
    return success_envelope(
        agent="garden",
        intent="synthesize_garden",
        chat_preview=_draft_preview(
            title_text,
            one_liner,
            result.get("core_arguments", []),
            draft_id,
        ),
        durable_artifacts=[
            artifact_ref(
                type="garden_draft",
                id=draft_id,
                title=title_text,
                status="draft_ready",
            )
        ],
        next_actions=[
            next_action(
                label="确认观点入库",
                intent="commit_garden_draft",
                payload={"draft_id": draft_id},
                requires_confirmation=True,
            )
        ],
        meta={
            "title": title_text,
            "one_liner": one_liner,
            "capture_ids": capture_ids,
            "draft_id": draft_id,
        },
    )
```

- [ ] **Step 10: Add `revise_garden_draft()`**

Add this function to `bridge/garden_bridge.py`:

```python
def revise_garden_draft(draft_id: str = "", instruction: str = "") -> dict:
    """修改最近或指定 Garden Draft。"""
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    conn = repo.conn
    vault_path = Path(config.vault_path).expanduser()

    if not draft_id:
        latest = get_latest_open_garden_draft(repo)
        draft_id = latest["id"] if latest else ""
    draft = get_garden_draft(repo, draft_id) if draft_id else None
    if not draft:
        return error_envelope(
            agent="garden",
            intent="revise_garden_draft",
            error="没有找到可修改的 Garden 草稿。",
        )
    if not instruction.strip():
        return error_envelope(
            agent="garden",
            intent="revise_garden_draft",
            error="请说明你想怎么修改这个 Garden 草稿。",
        )

    capture_ids = _json_list(draft["capture_ids"])
    raw_rows = []
    for cid in capture_ids:
        row = conn.execute("SELECT * FROM captures WHERE id = ?", (cid,)).fetchone()
        if row:
            raw_rows.append(dict(row))
    captures = [_hydrate_capture(vault_path, r) for r in raw_rows]
    captures.append({
        "id": f"garden-draft-{draft_id}",
        "title": draft["title"],
        "cached_text": draft["markdown"],
    })

    existing_cards = [
        dict(r) for r in conn.execute("SELECT * FROM garden_cards").fetchall()
    ]
    agent = GardenAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    result = agent.synthesize(captures, existing_cards, title=draft["title"])
    if "error" in result:
        return error_envelope(
            agent="garden",
            intent="revise_garden_draft",
            error=result["error"],
        )

    updated = update_garden_draft(repo, draft_id, {
        "title": result.get("title", draft["title"]),
        "one_liner": result.get("one_liner", draft["one_liner"]),
        "core_arguments": result.get("core_arguments", []),
        "key_materials": result.get("key_materials", []),
        "knowledge_links": result.get("knowledge_links", []),
        "markdown": result.get("markdown", draft["markdown"]),
        "instruction": instruction,
    })

    return success_envelope(
        agent="garden",
        intent="revise_garden_draft",
        chat_preview=_draft_preview(
            updated["title"],
            updated["one_liner"],
            _json_list(updated["core_arguments"]),
            draft_id,
        ),
        durable_artifacts=[
            artifact_ref(
                type="garden_draft",
                id=draft_id,
                title=updated["title"],
                status=updated["status"],
            )
        ],
        next_actions=[
            next_action(
                label="确认观点入库",
                intent="commit_garden_draft",
                payload={"draft_id": draft_id},
                requires_confirmation=True,
            )
        ],
        meta={"draft_id": draft_id, "instruction": instruction},
    )
```

This revision path is deliberately simple: it reuses the original captures plus the previous draft as context. Do not build a separate Garden editing LLM prompt in P0 unless tests show this is insufficient.

- [ ] **Step 11: Add `commit_garden_draft()`**

Add this function to `bridge/garden_bridge.py`:

```python
def commit_garden_draft(draft_id: str = "") -> dict:
    """确认 Garden Draft，写入 garden_cards 和 Obsidian garden 文件。"""
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    vault_path = Path(config.vault_path).expanduser()

    if not draft_id:
        latest = get_latest_open_garden_draft(repo)
        draft_id = latest["id"] if latest else ""
    draft = get_garden_draft(repo, draft_id) if draft_id else None
    if not draft:
        return error_envelope(
            agent="garden",
            intent="commit_garden_draft",
            error="没有找到可确认入库的 Garden 草稿。",
        )

    card_id = commit_garden_draft_to_card(repo, draft_id)
    if not card_id:
        return error_envelope(
            agent="garden",
            intent="commit_garden_draft",
            error="这个 Garden 草稿已经入库或无法入库。",
        )

    garden_dir = vault_path / "garden"
    garden_dir.mkdir(exist_ok=True)
    filename = _safe_garden_filename(draft["title"], card_id)
    md_path = garden_dir / filename
    md_path.write_text(draft["markdown"], encoding="utf-8")
    rel_path = f"garden/{filename}"

    if draft["question_id"]:
        repo.conn.execute(
            "UPDATE pending_questions SET status='answered', matched_response='synthesized' WHERE id=?",
            (draft["question_id"],),
        )
        repo.conn.commit()

    return success_envelope(
        agent="garden",
        intent="commit_garden_draft",
        chat_preview=f"已存入知识花园：《{draft['title']}》",
        durable_artifacts=[
            artifact_ref(
                type="garden_card",
                id=card_id,
                title=draft["title"],
                path=rel_path,
                status="seed",
            )
        ],
        meta={
            "draft_id": draft_id,
            "card_id": card_id,
            "path": rel_path,
        },
    )
```

- [ ] **Step 12: Run bridge tests and verify green**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_garden_bridge.py /Users/pipi/hermes-custom/tests/test_result_envelope.py -q
```

Expected: pass.

- [ ] **Step 13: Commit the bridge task**

```bash
cd /Users/pipi/hermes-custom
git add bridge/garden_bridge.py bridge/result_envelope.py tests/test_garden_bridge.py tests/test_result_envelope.py
git commit -m "feat: make garden synthesis draft-first"
```

---

## Task 4: Add Hermes Runtime Garden Draft Tools

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/synthesize_garden_tool.py`
- Create: `/Users/pipi/Downloads/hermes-agent-main/tools/revise_garden_draft_tool.py`
- Create: `/Users/pipi/Downloads/hermes-agent-main/tools/commit_garden_draft_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/toolsets.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_garden_draft_tools.py`

- [ ] **Step 1: Write runtime tool tests**

Create `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_garden_draft_tools.py`:

```python
import json


def test_revise_garden_draft_tool_preserves_envelope(monkeypatch):
    from bridge.result_envelope import artifact_ref, next_action, success_envelope
    import tools.revise_garden_draft_tool as tool

    expected = success_envelope(
        agent="garden",
        intent="revise_garden_draft",
        chat_preview="已修改 Garden 草稿。",
        durable_artifacts=[
            artifact_ref(
                type="garden_draft",
                id="draft1",
                title="草稿",
                status="draft_ready",
            )
        ],
        next_actions=[
            next_action(
                label="确认观点入库",
                intent="commit_garden_draft",
                payload={"draft_id": "draft1"},
                requires_confirmation=True,
            )
        ],
    )
    monkeypatch.setattr(tool, "do_revise", lambda **kwargs: expected)

    result = json.loads(tool._handler({
        "draft_id": "draft1",
        "instruction": "更强调反例",
    }))

    assert result["agent"] == "garden"
    assert result["intent"] == "revise_garden_draft"
    assert result["next_actions"][0]["requires_confirmation"] is True
```

```python
def test_commit_garden_draft_tool_preserves_envelope(monkeypatch):
    from bridge.result_envelope import artifact_ref, success_envelope
    import tools.commit_garden_draft_tool as tool

    expected = success_envelope(
        agent="garden",
        intent="commit_garden_draft",
        chat_preview="已存入知识花园。",
        durable_artifacts=[
            artifact_ref(
                type="garden_card",
                id="card1",
                title="观点",
                path="garden/观点-card1.md",
                status="seed",
            )
        ],
    )
    monkeypatch.setattr(tool, "do_commit", lambda **kwargs: expected)

    result = json.loads(tool._handler({"draft_id": "draft1"}))

    assert result["agent"] == "garden"
    assert result["intent"] == "commit_garden_draft"
    assert result["durable_artifacts"][0]["type"] == "garden_card"
```

- [ ] **Step 2: Run runtime tool tests and verify red**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' tests/tools/test_garden_draft_tools.py -q
```

Expected: fail because new tool files do not exist.

- [ ] **Step 3: Update synthesize tool description**

In `/Users/pipi/Downloads/hermes-agent-main/tools/synthesize_garden_tool.py`, replace the schema description with:

```python
"description": (
    "从知识库材料中提炼一张可审阅的 Garden 观点草稿。"
    "它不会直接写入最终 garden 卡片；用户确认“确认观点/存入花园”后，"
    "再调用 commit_garden_draft。用户提出修改意见时调用 revise_garden_draft。"
    "可指定 capture_ids 直接合成，或传 question_id 从主题聚类问题触发。"
),
```

- [ ] **Step 4: Create revise tool**

Create `/Users/pipi/Downloads/hermes-agent-main/tools/revise_garden_draft_tool.py`:

```python
"""Hermes tool: revise_garden_draft — 修改 Garden 观点草稿."""
from __future__ import annotations

import sys

HERMES_CUSTOM = "/Users/pipi/hermes-custom"
if HERMES_CUSTOM not in sys.path:
    sys.path.insert(0, HERMES_CUSTOM)

from bridge.garden_bridge import revise_garden_draft as do_revise
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

REVISE_GARDEN_DRAFT_SCHEMA = {
    "name": "revise_garden_draft",
    "description": (
        "根据用户修改意见更新最近或指定的 Garden 观点草稿。"
        "适用于用户对刚生成的 Garden 草稿说“不满意/改一下/补充/强调某点”。"
        "不要用它修改 Capture 草稿。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "draft_id": {
                "type": "string",
                "description": "Garden 草稿 ID；为空时默认最近一个未入库 Garden 草稿",
            },
            "instruction": {
                "type": "string",
                "description": "用户的具体修改意见",
            },
        },
        "required": ["instruction"],
    },
}


def _handler(params: dict, **kw) -> str:
    try:
        result = do_revise(
            draft_id=params.get("draft_id", ""),
            instruction=params.get("instruction", ""),
        )
        return envelope_json(result)
    except Exception as exc:
        return envelope_error_json(agent="garden", intent="revise_garden_draft", exc=exc)


registry.register(
    name=REVISE_GARDEN_DRAFT_SCHEMA["name"],
    schema=REVISE_GARDEN_DRAFT_SCHEMA,
    handler=_handler,
    toolset="personal-assistant",
)
```

- [ ] **Step 5: Create commit tool**

Create `/Users/pipi/Downloads/hermes-agent-main/tools/commit_garden_draft_tool.py`:

```python
"""Hermes tool: commit_garden_draft — 确认 Garden 草稿入库."""
from __future__ import annotations

import sys

HERMES_CUSTOM = "/Users/pipi/hermes-custom"
if HERMES_CUSTOM not in sys.path:
    sys.path.insert(0, HERMES_CUSTOM)

from bridge.garden_bridge import commit_garden_draft as do_commit
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

COMMIT_GARDEN_DRAFT_SCHEMA = {
    "name": "commit_garden_draft",
    "description": (
        "在用户明确确认后，把 Garden 观点草稿写入知识花园。"
        "会创建 garden_cards 记录并写入 Obsidian garden/*.md。"
        "只有用户说“确认观点/存入花园/可以入库”这类确认语义时才调用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "draft_id": {
                "type": "string",
                "description": "Garden 草稿 ID；为空时默认最近一个未入库 Garden 草稿",
            },
        },
        "required": [],
    },
}


def _handler(params: dict, **kw) -> str:
    try:
        result = do_commit(draft_id=params.get("draft_id", ""))
        return envelope_json(result)
    except Exception as exc:
        return envelope_error_json(agent="garden", intent="commit_garden_draft", exc=exc)


registry.register(
    name=COMMIT_GARDEN_DRAFT_SCHEMA["name"],
    schema=COMMIT_GARDEN_DRAFT_SCHEMA,
    handler=_handler,
    toolset="personal-assistant",
)
```

- [ ] **Step 6: Add tools to `personal-assistant`**

In `/Users/pipi/Downloads/hermes-agent-main/toolsets.py`, update the `personal-assistant` list:

```python
"theme_scan", "graph_report", "synthesize_garden",
"revise_garden_draft", "commit_garden_draft",
"daily_review",
```

- [ ] **Step 7: Run runtime tool tests and verify green**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' tests/tools/test_garden_draft_tools.py -q
```

Expected: pass.

- [ ] **Step 8: Commit runtime tool task**

```bash
cd /Users/pipi/Downloads/hermes-agent-main
git add tools/synthesize_garden_tool.py tools/revise_garden_draft_tool.py tools/commit_garden_draft_tool.py toolsets.py tests/tools/test_garden_draft_tools.py
git commit -m "feat: add garden draft lifecycle tools"
```

---

## Task 5: Update Hermes Custom E2E Tool Coverage

**Files:**
- Modify: `/Users/pipi/hermes-custom/tests/test_e2e_hermes_tools.py`

- [ ] **Step 1: Update Garden envelope expectation**

In `test_hermes_synthesize_garden_handler_preserves_confirmation`, change the expected action from:

```python
intent="accept_garden_card",
payload={"card_id": "g1"},
```

to:

```python
intent="commit_garden_draft",
payload={"draft_id": "d1"},
```

and make the artifact, if present in this test, use:

```python
type="garden_draft"
```

- [ ] **Step 2: Update expected personal-assistant toolset**

In `test_hermes_toolset_has_6_tools`, update the expected set to include:

```python
"revise_garden_draft",
"commit_garden_draft",
```

The final expected set should contain the existing tools plus the two new Garden tools. Do not remove Life Assistant tools if they are already present in this test.

- [ ] **Step 3: Add focused handler test for commit Garden draft**

Add this test near the existing Garden handler test:

```python
def test_hermes_commit_garden_draft_handler_preserves_artifact(monkeypatch):
    _skip_if_no_hermes()
    sys.path.insert(0, HERMES_DIR)
    from bridge.result_envelope import artifact_ref, success_envelope
    import tools.commit_garden_draft_tool as commit_tool

    expected = success_envelope(
        agent="garden",
        intent="commit_garden_draft",
        chat_preview="已存入知识花园。",
        durable_artifacts=[
            artifact_ref(
                type="garden_card",
                id="g1",
                title="观点",
                path="garden/观点-g1.md",
                status="seed",
            )
        ],
    )
    monkeypatch.setattr(commit_tool, "do_commit", lambda **kwargs: expected)

    envelope = _tool_envelope(
        commit_tool._handler({"draft_id": "d1"}, task_id="t1", user_task="u1")
    )

    assert envelope["agent"] == "garden"
    assert envelope["intent"] == "commit_garden_draft"
    assert envelope["durable_artifacts"][0]["type"] == "garden_card"
```

- [ ] **Step 4: Run Hermes Custom E2E tool tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_e2e_hermes_tools.py::test_hermes_synthesize_garden_handler_preserves_confirmation /Users/pipi/hermes-custom/tests/test_e2e_hermes_tools.py::test_hermes_commit_garden_draft_handler_preserves_artifact /Users/pipi/hermes-custom/tests/test_e2e_hermes_tools.py::test_hermes_toolset_has_6_tools -q
```

Expected: pass after Task 4 and this task are implemented.

- [ ] **Step 5: Commit E2E coverage**

```bash
cd /Users/pipi/hermes-custom
git add tests/test_e2e_hermes_tools.py
git commit -m "test: cover garden draft tools in hermes e2e"
```

---

## Task 6: Integration Verification

**Files:**
- No source edits expected.

- [ ] **Step 1: Run focused Hermes Custom tests**

Run:

```bash
cd /Users/pipi/hermes-custom
/Users/pipi/hermes-custom/.venv/bin/python -m pytest tests/test_schema.py tests/test_garden_agent.py tests/test_garden_bridge.py tests/test_result_envelope.py tests/test_e2e_hermes_tools.py -q
```

Expected: pass.

- [ ] **Step 2: Run focused Hermes Main tests**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' tests/tools/test_garden_draft_tools.py tests/test_toolsets.py -q
```

If `tests/test_toolsets.py` does not exist in this checkout, run the closest existing toolset coverage:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' tests/hermes_cli/test_tools_config.py::test_all_toolsets_reference_defined_tools -q
```

Expected: pass. If the fallback test name differs, use `rg -n "reference.*defined|defined.*tools|TOOLSETS" tests/hermes_cli tests/tools -g '*.py'` to find the current equivalent.

- [ ] **Step 3: Check git diffs for scope**

Run:

```bash
cd /Users/pipi/hermes-custom
git status --short
git diff -- db/schema.py agents/garden_agent.py bridge/garden_bridge.py bridge/result_envelope.py tests/test_schema.py tests/test_garden_agent.py tests/test_garden_bridge.py tests/test_result_envelope.py tests/test_e2e_hermes_tools.py
```

Expected:
- Only Garden/schema/envelope/test files are changed by this work.
- Existing unrelated Life Assistant dirty files are not staged.

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
git status --short
git diff -- tools/synthesize_garden_tool.py tools/revise_garden_draft_tool.py tools/commit_garden_draft_tool.py toolsets.py tests/tools/test_garden_draft_tools.py
```

Expected:
- Only Garden runtime tool files are changed.

- [ ] **Step 4: Restart Hermes with session reset**

Run:

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="/Users/pipi/.hermes/session_backups/$stamp"
mkdir -p "$backup_dir" /Users/pipi/.hermes/logs /Users/pipi/.hermes/sessions
cp -a /Users/pipi/.hermes/sessions/. "$backup_dir/" 2>/dev/null || true
find /Users/pipi/.hermes/sessions -mindepth 1 -maxdepth 1 -delete 2>/dev/null || true
pkill -f "hermes_cli.main gateway run" 2>/dev/null || true
nohup /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m hermes_cli.main gateway run --replace > "/Users/pipi/.hermes/logs/gateway-restart-$stamp.log" 2>&1 &
sleep 5
pgrep -fl "hermes_cli.main gateway run"
cat /Users/pipi/.hermes/gateway_state.json
tail -80 "/Users/pipi/.hermes/logs/gateway-restart-$stamp.log"
```

Expected:
- A gateway process is running.
- `gateway_state.json` shows the WeChat/iLink platform connected if it was connected before restart.
- No import error for `revise_garden_draft_tool` or `commit_garden_draft_tool`.

- [ ] **Step 5: Manual WeChat smoke test**

Ask the user to send:

```text
从最近几篇 AI Agent 相关笔记里提炼一个可以放进 Garden 的观点
```

Expected first response:
- It calls `synthesize_garden`.
- It returns a Garden draft preview, not a committed card.
- It includes a draft id.
- It says the user can confirm or ask for changes.
- No `garden/*.md` file is written yet.

Then ask the user to reply:

```text
这个观点太泛了，强调它对工程组织的影响
```

Expected second response:
- It calls `revise_garden_draft`.
- It updates the same draft.
- It returns another short draft preview.

Then ask the user to reply:

```text
确认观点，存入花园
```

Expected third response:
- It calls `commit_garden_draft`.
- It writes one `garden/*.md` file.
- It creates one `garden_cards` row.
- It marks the draft `committed`.

- [ ] **Step 6: Verify DB and vault after smoke test**

Run:

```bash
cd /Users/pipi/hermes-custom
/Users/pipi/hermes-custom/.venv/bin/python - <<'PY'
from agents.capture_agent import load_config
from db.schema import init_db
from pathlib import Path

cfg = load_config("config.yaml")
conn = init_db(cfg.db_path)
drafts = conn.execute(
    "SELECT id, title, status, committed_card_id FROM garden_drafts ORDER BY updated_at DESC LIMIT 5"
).fetchall()
cards = conn.execute(
    "SELECT id, title, status FROM garden_cards ORDER BY updated_at DESC LIMIT 5"
).fetchall()
print("drafts")
for row in drafts:
    print(dict(row))
print("cards")
for row in cards:
    print(dict(row))
print("garden files", len(list((Path(cfg.vault_path).expanduser() / "garden").glob("*.md"))))
PY
```

Expected:
- The latest draft is `committed`.
- `committed_card_id` points to the latest Garden card.
- A corresponding `garden/*.md` file exists.

---

## Rollback Plan

If Garden draft lifecycle breaks runtime behavior:

1. Stop using the new tools in Hermes main by removing `revise_garden_draft` and `commit_garden_draft` from `personal-assistant`.
2. Revert `synthesize_garden_tool.py` description if the gateway routing becomes confused.
3. In `/Users/pipi/hermes-custom`, do not delete `garden_drafts`; leaving an unused table is safe.
4. Restore previous `bridge/garden_bridge.py` behavior only if the user urgently needs direct Garden Card creation.

Rollback commands should be file-scoped. Do not run `git reset --hard`.

## Final Commit and Push

After all tests and the Hermes restart pass, create scoped commits if not already committed task-by-task:

```bash
cd /Users/pipi/hermes-custom
git add db/schema.py agents/garden_agent.py bridge/garden_bridge.py bridge/result_envelope.py tests/test_schema.py tests/test_garden_agent.py tests/test_garden_bridge.py tests/test_result_envelope.py tests/test_e2e_hermes_tools.py
git commit -m "feat: add garden draft lifecycle"
git push
```

```bash
cd /Users/pipi/Downloads/hermes-agent-main
git add tools/synthesize_garden_tool.py tools/revise_garden_draft_tool.py tools/commit_garden_draft_tool.py toolsets.py tests/tools/test_garden_draft_tools.py
git commit -m "feat: expose garden draft tools"
git push
```

Before staging in `/Users/pipi/hermes-custom`, run `git status --short` and leave unrelated Life Assistant files unstaged.

## Self-Review

Spec coverage:
- Draft-first Garden synthesis is covered by Tasks 1-3.
- Multi-turn revision is covered by Tasks 2-4.
- Explicit confirmation before writing `garden/` is covered by Task 3.
- Hermes tool routing is covered by Tasks 4-5.
- Tests and restart discipline are covered by Task 6.

Placeholder scan:
- No placeholder markers or unnamed future work remain in implementation steps.
- The only conditional command is the toolset coverage fallback, and it includes the exact `rg` command to find the current equivalent if the expected test file is absent.

Type consistency:
- The draft artifact type is consistently `garden_draft`.
- The commit intent is consistently `commit_garden_draft`.
- The final artifact type is consistently `garden_card`.
- The table name is consistently `garden_drafts`.

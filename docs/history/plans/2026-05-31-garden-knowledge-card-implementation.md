---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Garden Knowledge Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Broaden Garden from opinion-only cards into typed Knowledge Cards: `viewpoint`, `fact`, `method`, `caution`, and `question`.

**Architecture:** Keep the current draft-first Garden lifecycle. Add a `card_kind` field to `garden_drafts` and `garden_cards`, make GardenAgent produce type-aware markdown, and expose optional `card_kind` routing through the bridge and Hermes runtime tool. Existing cards default to `viewpoint`; `card_kind` is a weak system label, not a classification chore for the user.

**Tech Stack:** Python 3.11, SQLite migrations, Obsidian Markdown/YAML frontmatter, pytest, Hermes runtime tool schemas.

---

## Scope

In scope:
- Add and migrate `card_kind` on Garden drafts/cards.
- Preserve existing Garden cards as `viewpoint`.
- Let GardenAgent infer or honor a requested card kind.
- Make Theme suggestions offer "可沉淀方向" instead of asking the user to classify content.
- Render type-aware Markdown bodies.
- Store Garden card frontmatter when committing to Obsidian.
- Update Hermes runtime tool descriptions and parameters.

Out of scope:
- Theme Agent automatic card opportunity suggestions.
- RAG answer strategy changes.
- Review Agent card scheduling.
- Obsidian Canvas generation.
- Life Assistant files.

## File Map

Modify `/Users/pipi/hermes-custom`:

- `db/schema.py`: add `card_kind` columns and migrations.
- `agents/garden_agent.py`: normalize card kinds, prompt for card kind, render type-aware markdown, persist card kind.
- `bridge/garden_bridge.py`: accept/pass `card_kind`, preview typed cards, write Obsidian frontmatter.
- `tests/test_schema.py`: schema and migration coverage.
- `tests/test_garden_agent.py`: card kind persistence and markdown coverage.
- `tests/test_garden_bridge.py`: bridge card kind and frontmatter coverage.

Modify `/Users/pipi/Downloads/hermes-agent-main`:

- `tools/synthesize_garden_tool.py`: optional `card_kind` parameter and broader description.
- `tools/revise_garden_draft_tool.py`: wording from viewpoint draft to knowledge card draft.
- `tools/commit_garden_draft_tool.py`: wording from viewpoint draft to knowledge card draft.

Do not modify dirty Life Assistant files.

## Task 1: Schema and Persistence

**Files:**
- Modify: `/Users/pipi/hermes-custom/db/schema.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_schema.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_garden_agent.py`

- [ ] **Step 1: Add failing schema tests**

Add tests asserting:

```python
def test_garden_cards_table_has_card_kind(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(garden_cards)")]

    assert "card_kind" in cols
    row = conn.execute(
        "SELECT dflt_value FROM pragma_table_info('garden_cards') WHERE name='card_kind'"
    ).fetchone()
    assert row[0] == "'viewpoint'"
    conn.close()


def test_garden_drafts_table_has_card_kind(tmp_path):
    from db.schema import init_db

    conn = init_db(str(tmp_path / "test.db"))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(garden_drafts)")]

    assert "card_kind" in cols
    row = conn.execute(
        "SELECT dflt_value FROM pragma_table_info('garden_drafts') WHERE name='card_kind'"
    ).fetchone()
    assert row[0] == "'viewpoint'"
    conn.close()
```

- [ ] **Step 2: Verify schema tests fail**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_schema.py::test_garden_cards_table_has_card_kind \
  /Users/pipi/hermes-custom/tests/test_schema.py::test_garden_drafts_table_has_card_kind \
  -q
```

Expected: fail because `card_kind` does not exist.

- [ ] **Step 3: Add failing persistence tests**

Add tests asserting:

```python
def test_create_garden_draft_persists_card_kind(repo):
    draft_id = create_garden_draft(repo, {
        "title": "方法草稿",
        "one_liner": "一张方法卡。",
        "card_kind": "method",
        "core_arguments": [],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 方法草稿\n",
        "capture_ids": ["cap1"],
    })

    row = get_garden_draft(repo, draft_id)

    assert row["card_kind"] == "method"


def test_commit_garden_draft_to_card_preserves_card_kind(repo):
    draft_id = create_garden_draft(repo, {
        "title": "注意卡",
        "one_liner": "一个风险提醒。",
        "card_kind": "caution",
        "core_arguments": [],
        "knowledge_links": [],
        "key_materials": [],
        "markdown": "# 注意卡\n",
        "capture_ids": ["cap1"],
    })

    card_id = commit_garden_draft_to_card(repo, draft_id)
    card = repo.conn.execute(
        "SELECT card_kind FROM garden_cards WHERE id=?",
        (card_id,),
    ).fetchone()

    assert card["card_kind"] == "caution"
```

- [ ] **Step 4: Verify persistence tests fail**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_create_garden_draft_persists_card_kind \
  /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_commit_garden_draft_to_card_preserves_card_kind \
  -q
```

Expected: fail because helpers do not persist `card_kind`.

- [ ] **Step 5: Implement schema and helper persistence**

Add `card_kind TEXT NOT NULL DEFAULT 'viewpoint'` to both Garden table definitions.

Add `_migrate_garden_tables(conn)` and call it after creating Garden tables:

```python
def _migrate_garden_tables(conn: sqlite3.Connection) -> None:
    for table in ("garden_cards", "garden_drafts"):
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if "card_kind" not in existing:
            conn.execute(
                f"ALTER TABLE {table} "
                "ADD COLUMN card_kind TEXT NOT NULL DEFAULT 'viewpoint'"
            )
```

Update `create_garden_card`, `create_garden_draft`, `update_garden_draft`, and `commit_garden_draft_to_card` to write and copy `card_kind`.

- [ ] **Step 6: Verify Task 1 tests pass**

Run the four focused tests from Steps 2 and 4. Expected: pass.

## Task 2: Type-Aware GardenAgent

**Files:**
- Modify: `/Users/pipi/hermes-custom/agents/garden_agent.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_garden_agent.py`

- [ ] **Step 1: Add failing GardenAgent tests**

Add tests asserting:

```python
@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_honors_requested_method_card_kind(mock_call):
    mock_call.return_value = json.dumps({
        "card_kind": "method",
        "title": "长文整理先规划",
        "one_liner": "长文整理应先判断结构，再决定压缩粒度。",
        "core_arguments": [{"claim": "先规划再生成。", "source_ids": ["cap1"]}],
        "key_materials": [],
    })
    agent = GardenAgent(llm_api_key="test")

    result = agent.synthesize(
        [{"id": "cap1", "title": "材料", "cached_text": "内容"}],
        [],
        card_kind="method",
    )

    assert result["card_kind"] == "method"
    assert "## 适用场景" in result["markdown"]
    assert "## 做法" in result["markdown"]
    assert "## 我的判断" not in result["markdown"]
    assert "如果用户明确指定" in mock_call.call_args.args[0]


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_renders_caution_card_template(mock_call):
    mock_call.return_value = json.dumps({
        "card_kind": "caution",
        "title": "不要把 Garden 当摘要库",
        "one_liner": "Garden 存可复用知识对象，不存文章摘要。",
        "core_arguments": [{"claim": "摘要应留在 Capture。", "source_ids": ["cap1"]}],
        "key_materials": [],
    })
    agent = GardenAgent(llm_api_key="test")

    result = agent.synthesize(
        [{"id": "cap1", "title": "材料", "cached_text": "内容"}],
        [],
    )

    assert result["card_kind"] == "caution"
    assert "## 风险" in result["markdown"]
    assert "## 怎么避免" in result["markdown"]


@patch("agents.garden_agent.GardenAgent._call_llm")
def test_synthesize_defaults_unknown_card_kind_to_viewpoint(mock_call):
    mock_call.return_value = json.dumps({
        "card_kind": "misc",
        "title": "默认观点卡",
        "one_liner": "未知类型回退为观点卡。",
        "core_arguments": [],
        "key_materials": [],
    })
    agent = GardenAgent(llm_api_key="test")

    result = agent.synthesize(
        [{"id": "cap1", "title": "材料", "cached_text": "内容"}],
        [],
    )

    assert result["card_kind"] == "viewpoint"
    assert "## 核心观点" in result["markdown"]
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_synthesize_honors_requested_method_card_kind \
  /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_synthesize_renders_caution_card_template \
  /Users/pipi/hermes-custom/tests/test_garden_agent.py::test_synthesize_defaults_unknown_card_kind_to_viewpoint \
  -q
```

Expected: fail because `synthesize` does not accept or render `card_kind`.

- [ ] **Step 3: Implement card kind normalization and templates**

Add allowed kinds:

```python
CARD_KINDS = {"viewpoint", "fact", "method", "caution", "question"}
CARD_KIND_LABELS = {
    "viewpoint": "观点卡",
    "fact": "事实卡",
    "method": "方法卡",
    "caution": "注意卡",
    "question": "问题卡",
}
```

Change `GardenAgent.synthesize(..., card_kind: str = "")`, `_step1_extract(content_text, card_kind="")`, and `_build_synthesis_markdown(..., card_kind)`.

Render:

- `viewpoint`: `核心观点`, `为什么这样看`, `依据`, `反例或边界`, `关联`
- `fact`: `事实`, `为什么重要`, `来源依据`, `可用场景`, `关联`
- `method`: `适用场景`, `做法`, `注意点`, `来源依据`, `关联`
- `caution`: `风险`, `典型表现`, `怎么避免`, `来源依据`, `关联`
- `question`: `问题`, `为什么值得追踪`, `已有线索`, `下一步观察`, `关联`

Keep existing content limits: up to 4 arguments, up to 4 materials, up to 8 related cards.

- [ ] **Step 4: Verify Task 2 tests pass**

Run the three tests from Step 2. Expected: pass.

## Task 3: Bridge and Obsidian Output

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/garden_bridge.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_garden_bridge.py`

- [ ] **Step 1: Add failing bridge tests**

Add tests asserting:

```python
def test_synthesize_garden_passes_card_kind_to_agent(repo, tmp_path):
    from bridge.garden_bridge import synthesize_garden

    captures_dir = tmp_path / "captures"
    captures_dir.mkdir()
    (captures_dir / "cap.md").write_text(
        "---\ntitle: 方法素材\nstory: 摘要\n---\n\n正文内容\n",
        encoding="utf-8",
    )
    repo.conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("cap1", "captures/cap.md"),
    )
    repo.conn.commit()
    agent_result = {
        "title": "方法卡",
        "one_liner": "这是方法卡。",
        "card_kind": "method",
        "core_arguments": [{"claim": "先规划再执行"}],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 方法卡\n",
    }
    db_path = str(Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]))

    with patch("bridge.garden_bridge.load_config") as mock_cfg, \
            patch.object(GardenAgent, "synthesize", return_value=agent_result) as mock_synthesize:
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        result = synthesize_garden(capture_ids=["cap1"], card_kind="method")

    assert result["status"] == "success"
    assert result["meta"]["card_kind"] == "method"
    assert "方法卡" in result["chat_preview"]
    assert mock_synthesize.call_args.kwargs["card_kind"] == "method"


def test_commit_garden_draft_writes_card_frontmatter(repo, tmp_path):
    from agents.garden_agent import create_garden_draft
    from bridge.garden_bridge import commit_garden_draft
    import yaml

    draft_id = create_garden_draft(repo, {
        "title": "方法卡",
        "one_liner": "确认才进入 garden。",
        "card_kind": "method",
        "core_arguments": [],
        "key_materials": [],
        "knowledge_links": [],
        "markdown": "# 方法卡\n\n正文",
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

    text = (tmp_path / result["durable_artifacts"][0]["path"]).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["type"] == "garden_card"
    assert frontmatter["card_kind"] == "method"
    assert frontmatter["source_captures"] == ["cap1"]
```

- [ ] **Step 2: Verify bridge tests fail**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_garden_bridge.py::test_synthesize_garden_passes_card_kind_to_agent \
  /Users/pipi/hermes-custom/tests/test_garden_bridge.py::test_commit_garden_draft_writes_card_frontmatter \
  -q
```

Expected: fail because bridge signatures and frontmatter output do not exist.

- [ ] **Step 3: Implement bridge support**

Change `synthesize_garden(capture_ids=None, question_id="", title="", card_kind="")`.

Pass `card_kind` into `GardenAgent.synthesize`.

Persist `card_kind` in `garden_data`.

Show typed preview:

```text
已生成 Garden 方法卡草稿：《标题》
```

When committing, write YAML frontmatter plus markdown body:

```yaml
id: <card_id>
type: garden_card
card_kind: <kind>
status: seed
title: <title>
source_captures: [...]
created: <draft created_at>
updated: <now>
```

- [ ] **Step 4: Verify Task 3 tests pass**

Run the two bridge tests. Expected: pass.

## Task 4: Hermes Runtime Tool Descriptions

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/synthesize_garden_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/revise_garden_draft_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/commit_garden_draft_tool.py`

- [ ] **Step 1: Update synthesize runtime schema**

Add `card_kind` to the runtime tool schema enum:

```python
            "card_kind": {
                "type": "string",
                "enum": ["viewpoint", "fact", "method", "caution", "question"],
                "description": "可选系统提示，指定知识卡片类型；不填则由 Garden Agent 判断，不要要求用户先选择类型。",
            }
```

Pass it to `do_synthesize`.

- [ ] **Step 2: Update wording**

Replace "观点草稿" wording with "知识卡片草稿". Keep confirmation strict, but accept "确认卡片" as the preferred wording and keep "确认观点" only as backward-compatible user phrasing.

- [ ] **Step 3: Run runtime focused tests**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' \
  /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py \
  /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_garden_draft_tools.py \
  -q
```

Expected: pass.

## Task 5: Verification and Restart

**Files:**
- No new source files unless tests expose a small missing helper.

- [ ] **Step 1: Run Hermes custom focused tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_schema.py \
  /Users/pipi/hermes-custom/tests/test_garden_agent.py \
  /Users/pipi/hermes-custom/tests/test_garden_bridge.py \
  -q
```

Expected: pass.

- [ ] **Step 2: Run broader safe test subset**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_e2e_hermes_tools.py \
  /Users/pipi/hermes-custom/tests/test_result_envelope.py \
  -q
```

Expected: pass.

- [ ] **Step 3: Restart Hermes and clear session memory**

Run:

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="/Users/pipi/.hermes/session_backups/$stamp"
mkdir -p "$backup_dir" /Users/pipi/.hermes/logs /Users/pipi/.hermes/sessions
cp -a /Users/pipi/.hermes/sessions/. "$backup_dir/" 2>/dev/null || true
find /Users/pipi/.hermes/sessions -mindepth 1 -maxdepth 1 -delete 2>/dev/null || true
pkill -f "[h]ermes_cli.main gateway run" 2>/dev/null || true
nohup /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m hermes_cli.main gateway run --replace > "/Users/pipi/.hermes/logs/gateway-restart-$stamp.log" 2>&1 &
sleep 6
pgrep -fl "hermes_cli.main gateway run"
cat /Users/pipi/.hermes/gateway_state.json
```

Expected: gateway process exists and state file points to a live gateway.

## Acceptance Criteria

- Existing Garden cards remain valid and default to `viewpoint`.
- New Garden drafts/cards can store `card_kind`.
- Theme suggestions explain possible card directions without asking the user to classify the material first.
- `method` and `caution` cards render with non-viewpoint templates.
- Committed Garden Markdown has frontmatter with `type: garden_card` and `card_kind`.
- Hermes runtime can request `card_kind`, but can also omit it and let Garden infer.
- No Life Assistant files are changed by this implementation.

## Self-Review

- Spec coverage: P0 covers data model, type-aware cards, draft-first flow, Obsidian frontmatter, runtime routing, and Theme suggestion wording. RAG/Review/Canvas are intentionally later phases.
- Placeholder scan: no unfinished placeholder markers remain.
- Type consistency: `card_kind` is used consistently in schema, helpers, bridge, markdown, and runtime schema.

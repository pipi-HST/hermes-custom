# Life Decision Trace And Flexible Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Life memories visibly participate in tick decisions, and make Life record extraction less brittle by using LLM structured extraction for ambiguous messages while keeping deterministic safety checks.

**Architecture:** Implement two isolated Life-only increments. First, add `life_decision_traces` so every `life_tick` can explain candidates, memory influences, selected reminder, and suppression reasons without changing reminder behavior. Second, adjust `LifeIntentParser` so deterministic rules handle high-confidence cases and LLM fallback can handle ambiguous life records before rejection; `LifeAgent` remains the persistence validator.

**Tech Stack:** Python 3.11, SQLite, FastAPI, React/TypeScript dashboard, pytest, Vite/tsc.

---

## File Structure

- Modify `db/life_schema.py`: create `life_decision_traces`.
- Modify `db/life_repo.py`: add create/list helpers for decision traces.
- Modify `agents/life_agent.py`: build trace payloads inside `life_tick`, record traces, and attach memory influence details.
- Modify `api/routes/life.py`: add `/api/life/decision-traces`.
- Modify `dashboard/src/lib/lifeApi.ts`: add trace types and fetcher.
- Modify `dashboard/src/pages/life.tsx`: add a daily-only “记忆影响” panel near runtime status.
- Modify `agents/life_intent_parser.py`: add a configurable `prefer_llm_for_life_records` path and use LLM fallback for ambiguous record-like messages before final `unknown`.
- Modify `bridge/life_bridge.py`: enable the flexible recording parser from config.
- Modify `tests/test_life_repo.py`, `tests/test_life_agent.py`, `tests/test_life_api.py`, `tests/test_life_bridge.py`, `tests/test_life_intent_parser.py`.

## Task 1: Decision Trace Schema And Repo

**Files:**
- Modify: `db/life_schema.py`
- Modify: `db/life_repo.py`
- Test: `tests/test_life_repo.py`

- [ ] **Step 1: Write failing schema/repo tests**

Add to `tests/test_life_repo.py`:

```python
def test_life_schema_creates_decision_trace_table(tmp_path) -> None:
    conn = init_life_db(str(tmp_path / "life.db"))

    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='life_decision_traces'"
    ).fetchone()


def test_life_repo_records_and_lists_decision_traces(life_repo: LifeRepo) -> None:
    trace = life_repo.create_decision_trace(
        tick_at="2026-06-01T10:00:00+08:00",
        status="selected",
        selected_reminder_type="water",
        selected_reminder_id="reminder-1",
        context={"date": "2026-06-01", "water_count": 0},
        candidates=[{"reminder_type": "water", "reason": "due_time"}],
        memory_influences=[{"memory_id": "m1", "effect": "context_only"}],
        decision_reason="喝水提醒到点，且最近1小时没有喝水记录。",
        suppression_reason="",
        warnings=["mac_activity_unavailable"],
    )

    traces = life_repo.list_decision_traces(limit=5)

    assert trace["status"] == "selected"
    assert trace["context_json"] == {"date": "2026-06-01", "water_count": 0}
    assert trace["candidates_json"][0]["reminder_type"] == "water"
    assert traces[0]["id"] == trace["id"]
    assert traces[0]["warnings_json"] == ["mac_activity_unavailable"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_life_repo.py::test_life_schema_creates_decision_trace_table tests/test_life_repo.py::test_life_repo_records_and_lists_decision_traces -q
```

Expected: fails because table and repo methods do not exist.

- [ ] **Step 3: Implement schema**

Add to `db/life_schema.py`:

```python
CREATE_LIFE_DECISION_TRACES_TABLE = """
CREATE TABLE IF NOT EXISTS life_decision_traces (
    id TEXT PRIMARY KEY,
    tick_at TEXT NOT NULL,
    status TEXT NOT NULL,
    selected_reminder_type TEXT NOT NULL DEFAULT '',
    selected_reminder_id TEXT NOT NULL DEFAULT '',
    context_json TEXT NOT NULL DEFAULT '{}',
    candidates_json TEXT NOT NULL DEFAULT '[]',
    memory_influences_json TEXT NOT NULL DEFAULT '[]',
    decision_reason TEXT NOT NULL DEFAULT '',
    suppression_reason TEXT NOT NULL DEFAULT '',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""
```

Call `conn.execute(CREATE_LIFE_DECISION_TRACES_TABLE)` in `migrate_life_schema`.

- [ ] **Step 4: Implement repo methods**

Add to `LifeRepo`:

```python
def create_decision_trace(
    self,
    *,
    tick_at: str,
    status: str,
    selected_reminder_type: str = "",
    selected_reminder_id: str = "",
    context: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    memory_influences: list[dict[str, Any]] | None = None,
    decision_reason: str = "",
    suppression_reason: str = "",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    trace_id = uuid.uuid4().hex[:12]
    self.conn.execute(
        """INSERT INTO life_decision_traces
           (id, tick_at, status, selected_reminder_type, selected_reminder_id,
            context_json, candidates_json, memory_influences_json,
            decision_reason, suppression_reason, warnings_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trace_id,
            tick_at,
            status,
            selected_reminder_type,
            selected_reminder_id,
            json.dumps(context or {}, ensure_ascii=False),
            json.dumps(candidates or [], ensure_ascii=False),
            json.dumps(memory_influences or [], ensure_ascii=False),
            decision_reason,
            suppression_reason,
            json.dumps(warnings or [], ensure_ascii=False),
        ),
    )
    self.conn.commit()
    row = self.conn.execute("SELECT * FROM life_decision_traces WHERE id = ?", (trace_id,)).fetchone()
    return self._decision_trace_row_to_dict(row)

def list_decision_traces(self, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = self.conn.execute(
        """SELECT * FROM life_decision_traces
           ORDER BY tick_at DESC, rowid DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [self._decision_trace_row_to_dict(row) for row in rows]

def _decision_trace_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any]:
    result = dict(row or {})
    result["context_json"] = _load_json(result.get("context_json"), {})
    result["candidates_json"] = _load_json(result.get("candidates_json"), [])
    result["memory_influences_json"] = _load_json(result.get("memory_influences_json"), [])
    result["warnings_json"] = _load_json(result.get("warnings_json"), [])
    return result
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_life_repo.py -q
git add db/life_schema.py db/life_repo.py tests/test_life_repo.py
git commit -m "Add Life decision trace storage"
```

Expected: `tests/test_life_repo.py` passes.

## Task 2: Record Decision Traces From life_tick

**Files:**
- Modify: `agents/life_agent.py`
- Test: `tests/test_life_agent.py`

- [ ] **Step 1: Write failing tick trace tests**

Add to `tests/test_life_agent.py`:

```python
def test_life_tick_records_decision_trace_without_changing_selection(agent: LifeAgent) -> None:
    result = agent.life_tick(
        now="2026-05-30T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    trace = agent.repo.list_decision_traces(limit=1)[0]
    assert result["selected"]["reminder_type"] == "water"
    assert trace["status"] == "selected"
    assert trace["selected_reminder_type"] == "water"
    assert trace["decision_reason"]
    assert any(item["reminder_type"] == "water" for item in trace["candidates_json"])
    assert trace["warnings_json"] == ["mac_activity_unavailable"]


def test_life_tick_records_memory_influence_in_decision_trace(agent: LifeAgent) -> None:
    agent.repo.create_event(
        event_type="exercise",
        occurred_at="2026-06-01T08:00:00+08:00",
        source="wechat",
        status="completed_unknown",
    )
    memory = agent.repo.upsert_memory_item(
        domain="life.water",
        memory_type="pattern",
        status="weak",
        content="最近14天有3天记录喝水，平均每天约1次。",
        confidence="medium",
        source="event_stats",
        effective_from="2026-06-01T09:00:00+08:00",
        evidence={"water_days": 3},
    )

    agent.life_tick(
        now="2026-06-01T10:00:00+08:00",
        activity=ActivitySnapshot(idle_seconds=None, available=False),
        dry_run=True,
    )

    trace = agent.repo.list_decision_traces(limit=1)[0]
    assert trace["selected_reminder_type"] == "water"
    assert trace["memory_influences_json"][0]["memory_id"] == memory["id"]
    assert trace["memory_influences_json"][0]["effect"] == "context_only"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_life_agent.py::test_life_tick_records_decision_trace_without_changing_selection tests/test_life_agent.py::test_life_tick_records_memory_influence_in_decision_trace -q
```

Expected: fails because traces are not written.

- [ ] **Step 3: Add trace helpers in `LifeAgent`**

Add methods:

```python
def _trace_context(self, current: datetime, activity: ActivitySnapshot) -> dict[str, Any]:
    return {
        "date": current.date().isoformat(),
        "idle_seconds": activity.idle_seconds,
        "activity_available": activity.available,
        "sedentary_state": "active" if self._active_sitting_window() else "inactive",
        "notifications_enabled": self.config.notifications_enabled,
    }

def _trace_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "reminder_type": str(item.get("reminder_type") or ""),
            "priority": int(item.get("priority") or 0),
            "reason": str(item.get("reason") or _default_candidate_reason(str(item.get("reminder_type") or ""))),
            "dedupe_key": str(item.get("dedupe_key") or ""),
        }
        for item in candidates
    ]

def _memory_influences_from_pack(self, memory_pack: list[dict[str, Any]], reminder_type: str) -> list[dict[str, Any]]:
    return [
        {
            "memory_id": str(memory["id"]),
            "domain": str(memory["domain"]),
            "memory_type": str(memory["memory_type"]),
            "status": str(memory["status"]),
            "content": str(memory["content"]),
            "effect": "context_only",
            "reminder_type": reminder_type,
        }
        for memory in memory_pack
    ]
```

Add module-level helper:

```python
def _default_candidate_reason(reminder_type: str) -> str:
    return {
        "sedentary": "久坐达到提醒条件。",
        "exercise": "今天还没有完整运动记录，运动提醒到达检查窗口。",
        "sleep": "早睡提醒到达检查窗口。",
        "water": "喝水提醒到达检查窗口，且最近没有喝水记录。",
        "sleep_record": "今天缺少完整睡眠时长记录。",
        "meal": "今天缺少饮食记录。",
        "daily_review": "日复盘到达生成窗口。",
        "weekly_review": "周复盘到达生成窗口。",
        "long_term_decision_review": "长期决定到达复盘时间。",
    }.get(reminder_type, "候选提醒满足规则条件。")
```

- [ ] **Step 4: Write trace in `life_tick`**

After selected reminder log creation, keep `selected_reminder_id = log["id"]`. Build `memory_influences` from `memory_pack`. Before return, call:

```python
self.repo.create_decision_trace(
    tick_at=current.isoformat(),
    status=result["status"],
    selected_reminder_type=str((selected or {}).get("reminder_type") or ""),
    selected_reminder_id=selected_reminder_id,
    context=self._trace_context(current, activity),
    candidates=self._trace_candidates(candidates),
    memory_influences=memory_influences,
    decision_reason=_decision_reason(selected, warnings),
    suppression_reason="notification_rate_limited" if "notification_rate_limited" in warnings else "",
    warnings=warnings,
)
```

Wrap only the trace write in `try/except Exception` and append `life_decision_trace_failed` to warnings if it fails.

Add helper:

```python
def _decision_reason(selected: dict[str, Any] | None, warnings: list[str]) -> str:
    if "notification_rate_limited" in warnings:
        return "本次有候选提醒，但通知通道处于限流冷却，未发送。"
    if selected:
        return _default_candidate_reason(str(selected.get("reminder_type") or ""))
    return "本次没有候选提醒满足触发条件。"
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_life_agent.py tests/test_life_memory.py -q
git add agents/life_agent.py tests/test_life_agent.py
git commit -m "Record Life tick decision traces"
```

Expected: selected reminders remain unchanged and trace tests pass.

## Task 3: Decision Trace API And Dashboard Panel

**Files:**
- Modify: `api/routes/life.py`
- Modify: `dashboard/src/lib/lifeApi.ts`
- Modify: `dashboard/src/pages/life.tsx`
- Test: `tests/test_life_api.py`

- [ ] **Step 1: Write failing API test**

Add to `tests/test_life_api.py`:

```python
def test_life_decision_traces_returns_recent_items(tmp_path) -> None:
    client, repo = _client(tmp_path)
    repo.create_decision_trace(
        tick_at="2026-06-01T10:00:00+08:00",
        status="selected",
        selected_reminder_type="water",
        selected_reminder_id="reminder-1",
        context={"idle_seconds": 12},
        candidates=[{"reminder_type": "water", "reason": "due_time"}],
        memory_influences=[{"memory_id": "m1", "effect": "context_only"}],
        decision_reason="喝水提醒到点。",
        warnings=[],
    )

    response = client.get("/api/life/decision-traces")

    assert response.status_code == 200
    data = response.json()
    assert data["items"][0]["selected_reminder_type"] == "water"
    assert data["items"][0]["context_json"] == {"idle_seconds": 12}
    assert data["items"][0]["memory_influences_json"][0]["effect"] == "context_only"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_life_api.py::test_life_decision_traces_returns_recent_items -q
```

Expected: 404 or missing repo method.

- [ ] **Step 3: Add API endpoint**

In `api/routes/life.py`, add:

```python
@router.get("/life/decision-traces")
def get_life_decision_traces(limit: int = Query(default=10, le=50)):
    with _life_connection(conn, db_path) as db:
        rows = db.execute(
            """SELECT * FROM life_decision_traces
               ORDER BY tick_at DESC, rowid DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return {"items": [_decision_trace_row_to_dict(row) for row in rows]}
```

Add parser:

```python
def _decision_trace_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["context_json"] = _load_json(item.get("context_json"), {})
    item["candidates_json"] = _load_json(item.get("candidates_json"), [])
    item["memory_influences_json"] = _load_json(item.get("memory_influences_json"), [])
    item["warnings_json"] = _load_json(item.get("warnings_json"), [])
    return item
```

- [ ] **Step 4: Add frontend API types**

In `dashboard/src/lib/lifeApi.ts`, add:

```ts
export interface LifeDecisionTrace {
  id: string
  tick_at: string
  status: string
  selected_reminder_type: string
  selected_reminder_id: string
  context_json: Record<string, unknown>
  candidates_json: Array<Record<string, unknown>>
  memory_influences_json: Array<Record<string, unknown>>
  decision_reason: string
  suppression_reason: string
  warnings_json: string[]
  created_at: string
}

export interface LifeDecisionTraces {
  items: LifeDecisionTrace[]
}

export async function fetchLifeDecisionTraces(): Promise<LifeDecisionTraces> {
  return fetchJson<LifeDecisionTraces>('/life/decision-traces')
}
```

- [ ] **Step 5: Add daily dashboard panel**

In `dashboard/src/pages/life.tsx`, fetch decision traces with existing `Promise.all`, store in `const [decisionTraces, setDecisionTraces]`, and render `<MemoryInfluencePanel traces={decisionTraces?.items || []} />` immediately after `RuntimeStatusPanel`.

Component:

```tsx
function MemoryInfluencePanel({ traces }: { traces: LifeDecisionTrace[] }) {
  const recent = traces.slice(0, 5)
  return (
    <ModuleSection title="记忆影响" meta={recent.length ? `${recent.length} 次判断` : '暂无'}>
      {recent.length === 0 ? (
        <EmptyState text="还没有决策解释。下一次 Life tick 后会显示记忆如何参与判断。" />
      ) : (
        <div className="space-y-2">
          {recent.map(trace => (
            <div key={trace.id} className="rounded-md border border-stone-200 px-3 py-3 dark:border-neutral-800">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm font-semibold">
                  {trace.selected_reminder_type ? REMINDER_LABELS[trace.selected_reminder_type] || trace.selected_reminder_type : '本次无提醒'}
                </p>
                <span className="rounded-md bg-stone-100 px-2 py-1 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                  {formatDateTime(trace.tick_at)}
                </span>
              </div>
              <p className="mt-2 break-words text-sm text-neutral-700 dark:text-neutral-300">{trace.decision_reason || '没有记录决策原因。'}</p>
              <p className="mt-2 text-xs text-neutral-500">
                候选 {trace.candidates_json.length} 个 · 记忆命中 {trace.memory_influences_json.length} 条
                {trace.suppression_reason ? ` · 跳过：${trace.suppression_reason}` : ''}
              </p>
              {trace.memory_influences_json.length > 0 && (
                <div className="mt-2 space-y-1">
                  {trace.memory_influences_json.slice(0, 3).map((item, index) => (
                    <p key={`${trace.id}:memory:${index}`} className="break-words text-xs text-neutral-500">
                      {memoryEffectLabel(String(item.effect || 'context_only'))}：{String(item.content || item.memory_id || '')}
                    </p>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </ModuleSection>
  )
}
```

Add:

```ts
function memoryEffectLabel(value: string): string {
  if (value === 'copy_hint') return '影响文案'
  if (value === 'risk_hint') return '风险提示'
  if (value === 'strategy_hint') return '策略参考'
  return '仅作上下文'
}
```

- [ ] **Step 6: Run tests and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_life_api.py -q
cd dashboard && npx tsc --noEmit
git add api/routes/life.py dashboard/src/lib/lifeApi.ts dashboard/src/pages/life.tsx tests/test_life_api.py
git commit -m "Show Life memory influence traces"
```

Expected: API tests and TypeScript pass.

## Task 4: Flexible LLM Record Extraction

**Files:**
- Modify: `agents/life_intent_parser.py`
- Modify: `bridge/life_bridge.py`
- Test: `tests/test_life_intent_parser.py`, `tests/test_life_bridge.py`

- [ ] **Step 1: Write failing parser tests**

Add to `tests/test_life_intent_parser.py`:

```python
def test_parser_can_prefer_llm_for_ambiguous_life_record() -> None:
    calls = []

    def fallback(text, received_at):
        calls.append(text)
        return LifeIntent(
            intent="record_event",
            event_type="meal",
            occurred_at="2026-06-01T13:20:00+08:00",
            payload={
                "meal_slot": "lunch",
                "content": "面包和茉莉乌龙茶",
                "meal_time_precision": "exact",
            },
        )

    parser = LifeIntentParser(llm_fallback=fallback, prefer_llm_for_life_records=True)
    intent = parser.parse(
        "中午大概一点二十吃了点面包，后来还喝了茉莉乌龙茶",
        received_at=datetime.fromisoformat("2026-06-01T16:00:00+08:00"),
    )

    assert calls == ["中午大概一点二十吃了点面包，后来还喝了茉莉乌龙茶"]
    assert intent.event_type == "meal"
    assert intent.occurred_at == "2026-06-01T13:20:00+08:00"
    assert intent.payload["content"] == "面包和茉莉乌龙茶"


def test_parser_keeps_high_confidence_rules_before_llm() -> None:
    calls = []

    def fallback(text, received_at):
        calls.append(text)
        return LifeIntent(intent="unknown")

    parser = LifeIntentParser(llm_fallback=fallback, prefer_llm_for_life_records=True)
    intent = parser.parse(
        "我睡了",
        received_at=datetime.fromisoformat("2026-06-01T23:30:00+08:00"),
    )

    assert calls == []
    assert intent.event_type == "sleep_start"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_life_intent_parser.py::test_parser_can_prefer_llm_for_ambiguous_life_record tests/test_life_intent_parser.py::test_parser_keeps_high_confidence_rules_before_llm -q
```

Expected: constructor does not accept `prefer_llm_for_life_records`.

- [ ] **Step 3: Add parser option and ambiguous detection**

In `LifeIntentParser.__init__`:

```python
def __init__(
    self,
    llm_fallback: Callable[[str, datetime], LifeIntent | None] | None = None,
    *,
    prefer_llm_for_life_records: bool = False,
):
    self.llm_fallback = llm_fallback
    self.prefer_llm_for_life_records = prefer_llm_for_life_records
```

In `parse`:

```python
high_confidence = self._parse_deterministic(text, received_at, high_confidence_only=True)
if high_confidence.intent != "unknown":
    return high_confidence
if self.prefer_llm_for_life_records and self.llm_fallback is not None and _looks_like_life_record(text):
    fallback = self.llm_fallback(text, received_at)
    if fallback and fallback.intent != "unknown":
        return fallback
intent = self._parse_deterministic(text, received_at)
...
```

Adjust `_parse_deterministic` signature:

```python
def _parse_deterministic(self, text: str, received_at: datetime, *, high_confidence_only: bool = False) -> LifeIntent:
```

In high-confidence mode, return only unambiguous control or exact events:

```python
if text in {"我睡了", "睡了", "准备睡了"}: ...
if text in {"我醒了", "醒了", "起床了"}: ...
if text == "今天不练了": ...
if text == "撤销上一条": ...
if text in {"已活动", "活动了", "起来活动了"}: ...
if text in {"已运动", "运动了"}: ...
return LifeIntent(intent="unknown")
```

Add:

```python
def _looks_like_life_record(text: str) -> bool:
    return any(keyword in text for keyword in (
        "睡", "醒", "起床", "吃", "喝", "午饭", "午餐", "晚饭", "晚餐",
        "早餐", "早饭", "运动", "跑步", "健身", "八段锦", "散步", "快走",
    ))
```

- [ ] **Step 4: Strengthen LLM prompt contract**

Update `_LLM_SYSTEM_PROMPT` to include:

```text
当用户描述生活记录但表达不标准时，你要主动抽取最合理的结构化记录。
如果能从文本中读出明确或相对时间，不要使用 received_at 代替发生时间。
如果 meal 有内容但时间不明确，meal_time_precision 使用 missing，occurred_at 为空字符串。
如果只是模糊内容无法安全落库，输出 unknown。
不要因为没有完全匹配模板就输出 unknown；先尝试判断 sleep、meal、water、exercise。
```

- [ ] **Step 5: Enable option in bridge config**

In `bridge/life_bridge.py`, change parser construction:

```python
return LifeIntentParser(
    llm_fallback=fallback,
    prefer_llm_for_life_records=bool((cfg.get("life", {}) or {}).get("flexible_record_parser", True)),
)
```

Update `test_init_configures_life_intent_llm_fallback` in `tests/test_life_bridge.py` to assert:

```python
assert agent.parser.prefer_llm_for_life_records is True
```

- [ ] **Step 6: Run tests and commit**

Run:

```bash
.venv/bin/python -m pytest tests/test_life_intent_parser.py tests/test_life_bridge.py tests/test_life_agent.py -q
git add agents/life_intent_parser.py bridge/life_bridge.py tests/test_life_intent_parser.py tests/test_life_bridge.py
git commit -m "Use LLM fallback for flexible Life record extraction"
```

Expected: deterministic high-confidence cases still pass; ambiguous record-like messages can be handled by LLM fallback.

## Task 5: Final Verification And Runtime Restart

**Files:**
- No new source files unless previous tasks require small fixes.

- [ ] **Step 1: Run Life backend test suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_life_repo.py tests/test_life_memory.py tests/test_life_intent_parser.py tests/test_life_agent.py tests/test_life_bridge.py tests/test_life_api.py tests/test_life_scenarios.py tests/test_life_smoke_script.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend typecheck**

Run:

```bash
cd dashboard && npx tsc --noEmit
```

Expected: no TypeScript errors.

- [ ] **Step 3: Run diff check**

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 4: Restart Life backend**

Run:

```bash
lsof -tiTCP:8000 -sTCP:LISTEN | xargs kill
nohup .venv/bin/python -u -m uvicorn api.main:app --host 127.0.0.1 --port 8000 > /tmp/hermes-life-api.log 2>&1 &
```

Expected: API starts on port 8000.

- [ ] **Step 5: Verify runtime endpoints**

Run:

```bash
curl -sS http://localhost:8000/api/life/decision-traces
curl -sS http://localhost:8000/api/life/runtime
```

Expected: both return JSON and no 404.

- [ ] **Step 6: Final commit if needed**

If any uncommitted Life-only fixes remain:

```bash
git add <life-only-files>
git commit -m "Verify Life decision trace pipeline"
```

Expected: only Life files are staged; unrelated Canvas/RAG/Garden files remain untouched.

## Self-Review

- Spec coverage: Tasks 1-3 implement Life Memory Influence Trace storage, tick writing, API, and dashboard. Task 4 addresses the user's new flexible recording concern without changing persistence safety. Task 5 covers verification and runtime restart.
- Placeholder scan: No TBD/TODO placeholders remain in task steps.
- Type consistency: `life_decision_traces`, `context_json`, `candidates_json`, `memory_influences_json`, and `warnings_json` are consistent across schema, repo, API, and frontend.
- Scope check: The plan stays Life-only and does not touch Knowledge/Garden/RAG.

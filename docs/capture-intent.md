---
status: current
owner: knowledge-service
last_verified: 2026-11-18
verified_against: "3e6b974 + fix/kc-agents"
---

# CaptureIntent contract

`CaptureIntent` is the structured plan Capture builds before writing a draft.
It lives in `agents/capture_agent.py` and is stored as JSON in
`capture_drafts.capture_intent`.

There are two producers:

1. **Deterministic fallback** — `CaptureAgent._build_capture_intent()` derives
   the plan from the user request and source text.
2. **LLM planner** — `_build_capture_intent_with_planning()` asks the planner for
   a small JSON object and validates it in `_intent_from_capture_plan()`.

The planner may only set the keys documented below.  The editable-draft path
adds runtime keys such as `entry_mode`, `title_source`,
`editable_source_of_truth`, `pipeline_policy` and `folder_path`; those are not
part of the planner contract and must be preserved when present.

## Fields

| Field | Type | Allowed values | Default | Written by | Read by |
|---|---|---|---|---|---|
| `user_request` | string | any user instruction, unmodified | required | fallback, planner wrapper | prompt builders, full-text/raw intent checks |
| `material_intent` | string | `verbatim_full_text`, `enhanced_note`, `focused_note`, `refined_note` | required | deterministic detector | density/material-intent prompt rules |
| `task_type` | string | `capture`, `organize`, `rewrite`, `translate` | required | planner, fallback | draft prompt |
| `coverage` | string | `full_article`, `selected_sections` | required | planner, fallback | draft prompt; `selected_sections` clears `must_preserve` |
| `emphasis` | string[] | free strings; empty means no special focus | required | planner (`focus`) or fallback | draft prompt, preview blocks |
| `must_preserve` | string[] | `source_fidelity`, `main_thread`, `user_named_focus`, `source_layering` | required | fallback/planner wrapper | draft prompt |
| `output_shape` | string[] | `full_text`, `natural_draft` | required | fallback | `_is_full_text_save_intent`, bridge preview/full-text checks |
| `detail_level` | string | `brief`, `medium`, `deep`, `source_like` | `medium` | planner detector | density rules, detail-policy rule |
| `draft_guidance` | string | `let_model_decide`, `verbatim` | `let_model_decide` | fallback | carried into the draft prompt; no branch logic reads it |
| `compression` | string | `none`, `low`, `medium`, `high` | `medium` | planner detector | compression rules in the draft prompt |
| `preserve` | string[] | `original_wording`, `data`, `examples`, `methodology` | `[]` | planner, fallback | draft prompt |
| `plan_source` | string | `fallback`, `deterministic_full_text`, `llm` | `fallback` | builder | diagnostics and draft metadata; no behavior branch |
| `detail_policy` | string | free one-sentence guidance | `""` | planner | detail-policy rule in the draft prompt |
| `semantic_title` | string | free title or empty | `""` | planner | title selection and draft metadata |

### Field notes

- `material_intent` is not the user's topic.  It says how faithful the output
  should be: `verbatim_full_text` for literal saves, `enhanced_note` when the
  user wants discussion folded in, `focused_note` when they named parts, and
  `refined_note` otherwise.
- `coverage=selected_sections` means the user asked for specific parts.  The
  fallback/planner wrapper clears `must_preserve` in that case.
- `output_shape=["full_text"]` is the switch for “save the full text” behavior.
  Do not add a second boolean for the same decision.
- `compression=none` is only used by full-text saves.  The other levels are
  `low`, `medium`, `high`.
- `draft_guidance`, `plan_source` and `semantic_title` are currently diagnostic
  or prompt-carried; they are not independent control switches.  If you make
  one control behavior, add a test and update this table.

## Validation

`capture_intent_schema()` returns a JSON-schema-like description.  It is
intentionally `additionalProperties: true` because editable drafts add runtime
keys.

`validate_capture_intent(value)` returns a list of human-readable issues and
never mutates the input:

- missing required fields;
- enum values outside the allowed sets;
- list fields that are not lists of strings;
- constrained list items outside their allowed sets;
- unknown keys (reported as `unknown key preserved: ...`).

The Library draft PATCH route logs issues but still preserves the merged
object, so old clients do not lose data.  The LLM planner path logs issues and
falls back when the planner itself is unusable.

## Examples

Full-text save:

```json
{
  "user_request": "把这篇原文完整存下来",
  "material_intent": "verbatim_full_text",
  "task_type": "capture",
  "coverage": "full_article",
  "emphasis": [],
  "must_preserve": ["source_fidelity", "main_thread", "user_named_focus"],
  "output_shape": ["full_text"],
  "detail_level": "source_like",
  "draft_guidance": "verbatim",
  "compression": "none",
  "preserve": ["original_wording"],
  "plan_source": "deterministic_full_text",
  "detail_policy": "",
  "semantic_title": ""
}
```

Organize with named focus:

```json
{
  "user_request": "整理成笔记，重点保留方法论和案例",
  "material_intent": "focused_note",
  "task_type": "organize",
  "coverage": "selected_sections",
  "emphasis": ["方法论", "案例"],
  "must_preserve": [],
  "output_shape": ["natural_draft"],
  "detail_level": "deep",
  "draft_guidance": "let_model_decide",
  "compression": "medium",
  "preserve": ["examples", "methodology"],
  "plan_source": "llm",
  "detail_policy": "方法论部分展开，背景部分压缩。",
  "semantic_title": "可复用的方法论"
}
```

## Changing the contract

1. Add or remove a field in `CaptureIntent` and in this table.
2. Update `CAPTURE_INTENT_ENUMS`, `CAPTURE_INTENT_LIST_ENUMS` or
   `CAPTURE_INTENT_REQUIRED` if the type/allowed values change.
3. Add a test in `tests/test_capture_intent_contract.py`; the field-coverage
   test fails when a dataclass field is missing from this document.
4. Do not silently drop unknown keys from stored drafts.

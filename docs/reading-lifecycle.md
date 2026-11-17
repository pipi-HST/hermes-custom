---
status: current
owner: knowledge-service
last_verified: 2026-11-18
verified_against: "3e6b974 + 2026-11-18 parallel fix branches"
---

# Reading Session lifecycle

A Reading Session is a temporary discussion around one or more sources.  It is
not a Capture Draft and it does not write durable knowledge by itself.

## Flow

```text
start_reading_session
  -> reading_sessions + reading_evidence
  -> discuss_reading
  -> reading_turns
  -> summarize_reading_session
  -> reading_drafts (open)
  -> user confirms
  -> commit_reading_artifact
  -> one of three durable outputs
```

## Three commit outputs

`bridge/reading_bridge.py:_commit_reading_artifact()` dispatches on
`output_type`:

| output_type | Writes | Notes |
|---|---|---|
| `enhance_capture` | rewrites an existing Capture Note | Requires `target_capture_id`; writes a `capture_version_backups` row with before/after hashes before changing the file. |
| `knowledge_card` | `garden_cards` + graph refresh | Used when the discussion produced a reusable viewpoint/fact/method/risk. |
| `local_learning_note` / `discussion_note` | new `captures` row + Capture Note | An independent note, not a rewrite of the source. |

`target_capture_id` is only set when the session source is itself a capture and
the output is `enhance_capture`.  A multi-source answer path asks the user to
choose the target instead of guessing (`bridge/answer_bridge.py`).

## Confirmation

`commit_reading_artifact` requires the same confirmation gate as Capture:

- chat/agent path: a confirmation phrase such as “确认整理”;
- HTTP path: `POST /api/reading/drafts/{draft_id}/confirmation` then
  `POST /api/reading/drafts/{draft_id}/commit` with the single-use token.

An uncommitted `reading_draft` is not durable knowledge.  Deleting it does not
touch the source capture or card.

## Tables

| Table | Purpose |
|---|---|
| `reading_sessions` | session metadata and source reference |
| `reading_turns` | user/assistant discussion turns |
| `reading_evidence` | source excerpts used by the session |
| `reading_drafts` | reviewable output, `target_capture_id`, `output_type` |
| `capture_version_backups` | before/after file hash and backup path for `enhance_capture` |

## What it is not

- It is not a second Capture Draft pipeline.  Capture drafts go through
  `bridge/capture_bridge.py`.
- It is not durable until `commit_reading_artifact` succeeds.
- It does not write `captures` directly except through the three outputs above.

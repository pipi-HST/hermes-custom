"""Repository helpers for Life Assistant tables."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any


class LifeRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_event(
        self,
        *,
        event_type: str,
        occurred_at: str,
        ended_at: str | None = None,
        duration_minutes: int | None = None,
        source: str = "",
        raw_text: str = "",
        payload: dict[str, Any] | None = None,
        status: str = "confirmed",
    ) -> dict[str, Any]:
        event_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_events
               (id, event_type, occurred_at, ended_at, duration_minutes,
                source, raw_text, payload_json, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                event_type,
                occurred_at,
                ended_at,
                duration_minutes,
                source,
                raw_text,
                json.dumps(payload or {}, ensure_ascii=False),
                status,
            ),
        )
        self.conn.commit()
        return self.get_event(event_id) or {}

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM life_events WHERE id = ?", (event_id,)
        ).fetchone()
        return self._event_row_to_dict(row) if row else None

    def latest_user_event(
        self,
        *,
        now: str,
        window_minutes: int,
    ) -> dict[str, Any] | None:
        now_dt = _parse_datetime(now)
        rows = self.conn.execute(
            """SELECT * FROM life_events
               WHERE source = 'wechat'
               ORDER BY datetime(replace(substr(created_at, 1, 19), 'T', ' ')) DESC,
                        rowid DESC"""
        ).fetchall()
        for row in rows:
            item = self._event_row_to_dict(row)
            if not item:
                continue
            occurred = _parse_datetime(str(item["occurred_at"]))
            if occurred.date() != now_dt.date():
                continue
            payload = item.get("payload_json") or {}
            reference_text = str(payload.get("recorded_at") or item["occurred_at"])
            reference = _parse_datetime(reference_text)
            elapsed_minutes = abs(_seconds_between(reference, now_dt)) / 60
            if elapsed_minutes <= window_minutes:
                return item
        return None

    def upsert_meal_event(
        self,
        *,
        meal_date: str,
        meal_slot: str,
        occurred_at: str,
        source: str,
        raw_text: str,
        payload: dict[str, Any],
        status: str = "confirmed",
    ) -> dict[str, Any]:
        existing = self._meal_event_on_date(meal_date=meal_date, meal_slot=meal_slot)
        if existing:
            existing_payload = existing.get("payload_json") or {}
            update_mode = str(payload.get("meal_update_mode") or "")
            payload = {key: value for key, value in payload.items() if key != "meal_update_mode"}
            if update_mode == "append":
                existing_content = str(existing_payload.get("content") or "").strip()
                new_content = str(payload.get("content") or "").strip()
                if existing_content and new_content and new_content not in existing_content:
                    payload["content"] = f"{existing_content}、{new_content}"
                elif existing_content:
                    payload["content"] = existing_content
            if (
                payload.get("meal_time_precision") == "missing"
                and existing_payload.get("meal_time_precision") not in {None, "missing"}
            ):
                payload = {**payload, "meal_time_precision": existing_payload["meal_time_precision"]}
                occurred_at = str(existing["occurred_at"])
            if update_mode == "append":
                payload = {**existing_payload, **payload}
                raw_text = _append_raw_text(str(existing.get("raw_text") or ""), raw_text)
            return self.update_event(
                existing["id"],
                occurred_at=occurred_at,
                source=source,
                raw_text=raw_text,
                payload=payload,
                status=status,
            ) or {}
        payload = {key: value for key, value in payload.items() if key != "meal_update_mode"}
        return self.create_event(
            event_type="meal",
            occurred_at=occurred_at,
            source=source,
            raw_text=raw_text,
            payload=payload,
            status=status,
        )

    def latest_pending_meal(
        self,
        *,
        now: str,
        window_minutes: int,
        meal_slot: str = "",
    ) -> dict[str, Any] | None:
        now_dt = _parse_datetime(now)
        rows = self.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'meal' AND status NOT IN ('undone', 'superseded')
               ORDER BY datetime(replace(substr(updated_at, 1, 19), 'T', ' ')) DESC,
                        rowid DESC"""
        ).fetchall()
        for row in rows:
            item = self._event_row_to_dict(row)
            if not item:
                continue
            payload = item.get("payload_json") or {}
            if payload.get("meal_time_precision") != "missing":
                continue
            if meal_slot and payload.get("meal_slot") != meal_slot:
                continue
            recorded_at = str(payload.get("recorded_at") or item.get("created_at") or "")
            try:
                recorded = _parse_datetime(recorded_at)
            except ValueError:
                continue
            if recorded.date() != now_dt.date():
                continue
            elapsed_minutes = abs(_seconds_between(recorded, now_dt)) / 60
            if elapsed_minutes <= window_minutes:
                return item
        return None

    def _meal_event_on_date(self, *, meal_date: str, meal_slot: str) -> dict[str, Any] | None:
        rows = self.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'meal' AND status NOT IN ('undone', 'superseded')
               ORDER BY rowid DESC"""
        ).fetchall()
        for row in rows:
            item = self._event_row_to_dict(row)
            if not item:
                continue
            payload = item.get("payload_json") or {}
            if payload.get("meal_slot") != meal_slot:
                continue
            if _date_key(str(item.get("occurred_at") or "")) == meal_date:
                return item
        return None

    def meal_event_on_date(self, *, meal_date: str, meal_slot: str) -> dict[str, Any] | None:
        return self._meal_event_on_date(meal_date=meal_date, meal_slot=meal_slot)

    def update_event(
        self,
        event_id: str,
        *,
        event_type: str | None = None,
        occurred_at: str | None = None,
        ended_at: str | None = None,
        duration_minutes: int | None = None,
        source: str | None = None,
        raw_text: str | None = None,
        payload: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        updates: dict[str, Any] = {}
        if event_type is not None:
            updates["event_type"] = event_type
        if occurred_at is not None:
            updates["occurred_at"] = occurred_at
        if ended_at is not None:
            updates["ended_at"] = ended_at
        if duration_minutes is not None:
            updates["duration_minutes"] = duration_minutes
        if source is not None:
            updates["source"] = source
        if raw_text is not None:
            updates["raw_text"] = raw_text
        if payload is not None:
            updates["payload_json"] = json.dumps(payload, ensure_ascii=False)
        if status is not None:
            updates["status"] = status
        if not updates:
            return self.get_event(event_id)
        updates["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{key} = ?" for key in updates)
        self.conn.execute(
            f"UPDATE life_events SET {sets} WHERE id = ?",
            [*updates.values(), event_id],
        )
        self.conn.commit()
        return self.get_event(event_id)

    def create_reminder_log(
        self,
        *,
        reminder_type: str,
        scheduled_for: str,
        status: str,
        dedupe_key: str,
        rule_id: str = "",
        sent_at: str | None = None,
        payload: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        existing = self.conn.execute(
            "SELECT * FROM life_reminder_logs WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()
        if existing:
            return self._reminder_row_to_dict(existing)

        log_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_reminder_logs
               (id, rule_id, reminder_type, scheduled_for, sent_at, status,
                dedupe_key, error, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_id,
                rule_id,
                reminder_type,
                scheduled_for,
                sent_at,
                status,
                dedupe_key,
                error,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM life_reminder_logs WHERE id = ?", (log_id,)
        ).fetchone()
        return self._reminder_row_to_dict(row)

    def update_reminder_log(
        self,
        log_id: str,
        *,
        status: str | None = None,
        sent_at: str | None = None,
        error: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if status is not None:
            updates["status"] = status
        if sent_at is not None:
            updates["sent_at"] = sent_at
        if error is not None:
            updates["error"] = error
        if payload is not None:
            updates["payload_json"] = json.dumps(payload, ensure_ascii=False)
        if not updates:
            row = self.conn.execute(
                "SELECT * FROM life_reminder_logs WHERE id = ?",
                (log_id,),
            ).fetchone()
            return self._reminder_row_to_dict(row)
        updates["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{key} = ?" for key in updates)
        self.conn.execute(
            f"UPDATE life_reminder_logs SET {sets} WHERE id = ?",
            [*updates.values(), log_id],
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM life_reminder_logs WHERE id = ?",
            (log_id,),
        ).fetchone()
        return self._reminder_row_to_dict(row)

    def supersede_same_day_reminders(
        self,
        *,
        reminder_type: str,
        day: str,
        superseded_at: str,
        reason: str,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM life_reminder_logs
               WHERE reminder_type = ?
                 AND status IN ('scheduled', 'sending')
               ORDER BY scheduled_for ASC, rowid ASC""",
            (reminder_type,),
        ).fetchall()
        updated: list[dict[str, Any]] = []
        for row in rows:
            if _date_key(str(row["scheduled_for"])) != day:
                continue
            payload = _load_json(row["payload_json"], {})
            payload.update({"superseded_at": superseded_at, "superseded_reason": reason})
            updated.append(
                self.update_reminder_log(
                    row["id"],
                    status="superseded",
                    payload=payload,
                )
            )
        return updated

    def complete_same_day_reminders(
        self,
        *,
        reminder_type: str,
        day: str,
        completed_at: str,
        event_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM life_reminder_logs
               WHERE reminder_type = ?
                 AND status IN ('scheduled', 'sending')
               ORDER BY scheduled_for ASC, rowid ASC""",
            (reminder_type,),
        ).fetchall()
        updated: list[dict[str, Any]] = []
        for row in rows:
            if _date_key(str(row["scheduled_for"])) != day:
                continue
            payload = _load_json(row["payload_json"], {})
            payload.update({"completed_at": completed_at, "completed_by_event_id": event_id})
            updated.append(
                self.update_reminder_log(
                    row["id"],
                    status="completed",
                    payload=payload,
                )
            )
        return updated

    def create_interaction_context(
        self,
        *,
        context_type: str,
        target_type: str,
        target_id: str,
        expected_reply_kind: str,
        created_at: str,
        expires_at: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.resolve_contexts_for_target(
            context_type=context_type,
            target_type=target_type,
            target_id=target_id,
            resolved_at=created_at,
        )
        context_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_interaction_contexts
               (id, context_type, target_type, target_id, expected_reply_kind,
                status, payload_json, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
            (
                context_id,
                context_type,
                target_type,
                target_id,
                expected_reply_kind,
                json.dumps(payload or {}, ensure_ascii=False),
                created_at,
                expires_at,
            ),
        )
        self.conn.commit()
        return self.get_interaction_context(context_id) or {}

    def latest_active_context(
        self,
        *,
        context_type: str,
        now: str,
    ) -> dict[str, Any] | None:
        current = _parse_datetime(now)
        rows = self.conn.execute(
            """SELECT * FROM life_interaction_contexts
               WHERE context_type = ? AND status = 'active'
               ORDER BY datetime(replace(substr(created_at, 1, 19), 'T', ' ')) DESC,
                        rowid DESC""",
            (context_type,),
        ).fetchall()
        for row in rows:
            item = self._context_row_to_dict(row)
            if _parse_datetime(str(item["expires_at"])) > current:
                return item
            self.resolve_interaction_context(item["id"], resolved_at=now, status="expired")
        return None

    def get_interaction_context(self, context_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM life_interaction_contexts WHERE id = ?",
            (context_id,),
        ).fetchone()
        return self._context_row_to_dict(row) if row else None

    def resolve_interaction_context(
        self,
        context_id: str,
        *,
        resolved_at: str,
        status: str = "resolved",
    ) -> dict[str, Any] | None:
        self.conn.execute(
            """UPDATE life_interaction_contexts
               SET status = ?, resolved_at = ?
               WHERE id = ?""",
            (status, resolved_at, context_id),
        )
        self.conn.commit()
        return self.get_interaction_context(context_id)

    def resolve_contexts_for_target(
        self,
        *,
        context_type: str,
        target_type: str,
        target_id: str,
        resolved_at: str,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM life_interaction_contexts
               WHERE context_type = ?
                 AND target_type = ?
                 AND target_id = ?
                 AND status = 'active'""",
            (context_type, target_type, target_id),
        ).fetchall()
        updated: list[dict[str, Any]] = []
        for row in rows:
            updated_item = self.resolve_interaction_context(
                row["id"],
                resolved_at=resolved_at,
            )
            if updated_item:
                updated.append(updated_item)
        return updated

    def reconcile_sending_reminders(
        self,
        *,
        completed_at: str,
        delivery_error: str = "",
    ) -> list[dict[str, Any]]:
        completed = _parse_datetime(completed_at)
        status = "failed" if delivery_error else "sent"
        rows = self.conn.execute(
            """SELECT * FROM life_reminder_logs
               WHERE status = 'sending'
               ORDER BY scheduled_for ASC, rowid ASC"""
        ).fetchall()
        updated: list[dict[str, Any]] = []
        for row in rows:
            scheduled = _parse_datetime(row["scheduled_for"])
            if _seconds_between(scheduled, completed) < 0:
                continue
            updated.append(
                self.update_reminder_log(
                    row["id"],
                    status=status,
                    sent_at=completed_at if status == "sent" else None,
                    error=delivery_error,
                )
            )
        return updated

    def acquire_lock(
        self,
        *,
        name: str,
        owner: str,
        now: str,
        ttl_seconds: int,
    ) -> bool:
        now_dt = _parse_datetime(now)
        expires_at = (now_dt + timedelta(seconds=ttl_seconds)).isoformat()
        row = self.conn.execute(
            "SELECT * FROM life_locks WHERE name = ?",
            (name,),
        ).fetchone()
        if row and _parse_datetime(row["expires_at"]) > now_dt:
            return False
        if row:
            self.conn.execute(
                """UPDATE life_locks
                   SET owner = ?, expires_at = ?, updated_at = ?
                   WHERE name = ?""",
                (owner, expires_at, datetime.now().isoformat(), name),
            )
        else:
            self.conn.execute(
                """INSERT INTO life_locks (name, owner, expires_at)
                   VALUES (?, ?, ?)""",
                (name, owner, expires_at),
            )
        self.conn.commit()
        return True

    def release_lock(self, *, name: str, owner: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM life_locks WHERE name = ? AND owner = ?",
            (name, owner),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def create_daily_review(
        self,
        *,
        review_date: str,
        summary: str,
        metrics: dict[str, Any],
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.get_daily_review(review_date)
        if existing:
            return existing
        review_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_daily_reviews
               (id, review_date, summary, metrics_json, notes_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                review_id,
                review_date,
                summary,
                json.dumps(metrics, ensure_ascii=False),
                json.dumps(notes or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return self.get_daily_review(review_date) or {}

    def get_daily_review(self, review_date: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM life_daily_reviews WHERE review_date = ?",
            (review_date,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metrics_json"] = _load_json(result.get("metrics_json"), {})
        result["notes_json"] = _load_json(result.get("notes_json"), {})
        return result

    def create_weekly_review(
        self,
        *,
        week_start: str,
        week_end: str,
        summary: str,
        labels: dict[str, Any],
        patterns: dict[str, Any],
        recommendations: list[str],
        headline: str = "",
        report_text: str = "",
        evidence: list[dict[str, Any]] | None = None,
        delivery: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.get_weekly_review(week_start=week_start, week_end=week_end)
        if existing:
            updates: dict[str, Any] = {}
            if headline and not existing.get("headline"):
                updates["headline"] = headline
            if (report_text or summary) and not existing.get("report_text"):
                updates["report_text"] = report_text or summary
            if evidence and not existing.get("evidence_json"):
                updates["evidence_json"] = json.dumps(evidence, ensure_ascii=False)
            if delivery and not existing.get("delivery_json"):
                updates["delivery_json"] = json.dumps(delivery, ensure_ascii=False)
            if updates:
                assignments = ", ".join(f"{column} = ?" for column in updates)
                self.conn.execute(
                    f"""UPDATE life_weekly_reviews
                        SET {assignments}
                        WHERE week_start = ? AND week_end = ?""",
                    (*updates.values(), week_start, week_end),
                )
                self.conn.commit()
            return self.get_weekly_review(week_start=week_start, week_end=week_end) or existing
        review_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_weekly_reviews
               (id, week_start, week_end, summary, headline, report_text,
                labels_json, patterns_json, recommendations_json, evidence_json, delivery_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                review_id,
                week_start,
                week_end,
                summary,
                headline,
                report_text or summary,
                json.dumps(labels, ensure_ascii=False),
                json.dumps(patterns, ensure_ascii=False),
                json.dumps(recommendations, ensure_ascii=False),
                json.dumps(evidence or [], ensure_ascii=False),
                json.dumps(delivery or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return self.get_weekly_review(week_start=week_start, week_end=week_end) or {}

    def get_weekly_review(self, *, week_start: str, week_end: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT * FROM life_weekly_reviews
               WHERE week_start = ? AND week_end = ?""",
            (week_start, week_end),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["labels_json"] = _load_json(result.get("labels_json"), {})
        result["patterns_json"] = _load_json(result.get("patterns_json"), {})
        result["recommendations_json"] = _load_json(result.get("recommendations_json"), [])
        result["evidence_json"] = _load_json(result.get("evidence_json"), [])
        result["delivery_json"] = _load_json(result.get("delivery_json"), {})
        return result

    def create_long_term_decision(
        self,
        *,
        content: str,
        created_at: str,
        next_review_at: str,
        review_interval_days: int = 0,
        source: str = "wechat",
        status: str = "active",
    ) -> dict[str, Any]:
        decision_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_long_term_decisions
               (id, content, source, status, review_interval_days, next_review_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                decision_id,
                content,
                source,
                status,
                review_interval_days,
                next_review_at,
                created_at,
                created_at,
            ),
        )
        self.conn.commit()
        return self.get_long_term_decision(decision_id) or {}

    def find_open_long_term_decision_by_content(
        self,
        content: str,
        *,
        exclude_id: str = "",
        status: str = "",
    ) -> dict[str, Any] | None:
        normalized = content.strip()
        rows = self.conn.execute(
            """SELECT * FROM life_long_term_decisions
               WHERE content = ?
                 AND status IN ('active', 'pending_review_time')
               ORDER BY status ASC, updated_at DESC, created_at DESC, rowid DESC""",
            (normalized,),
        ).fetchall()
        for row in rows:
            item = dict(row)
            if exclude_id and item["id"] == exclude_id:
                continue
            if status and item["status"] != status:
                continue
            return item
        return None

    def get_long_term_decision(self, decision_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM life_long_term_decisions WHERE id = ?",
            (decision_id,),
        ).fetchone()
        return dict(row) if row else None

    def due_long_term_decision(self, *, now: str) -> dict[str, Any] | None:
        current = _parse_datetime(now)
        rows = self.conn.execute(
            """SELECT * FROM life_long_term_decisions
               WHERE status = 'active' AND next_review_at != ''
               ORDER BY next_review_at ASC, rowid ASC"""
        ).fetchall()
        for row in rows:
            item = dict(row)
            if _parse_datetime(str(item["next_review_at"])) <= current:
                return item
        return None

    def latest_pending_long_term_decision(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT * FROM life_long_term_decisions
               WHERE status = 'pending_review_time'
               ORDER BY updated_at DESC, created_at DESC, rowid DESC
               LIMIT 1"""
        ).fetchone()
        return dict(row) if row else None

    def latest_active_long_term_decision(self, *, now: str) -> dict[str, Any] | None:
        current = _parse_datetime(now)
        rows = self.conn.execute(
            """SELECT * FROM life_long_term_decisions
               WHERE status = 'active'
               ORDER BY next_review_at ASC, created_at DESC, rowid DESC"""
        ).fetchall()
        due: list[dict[str, Any]] = []
        active: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            active.append(item)
            if _parse_datetime(str(item["next_review_at"])) <= current:
                due.append(item)
        if due:
            return due[0]
        return active[-1] if active else None

    def update_long_term_decision(
        self,
        decision_id: str,
        *,
        content: str | None = None,
        status: str | None = None,
        next_review_at: str | None = None,
        review_interval_days: int | None = None,
        closed_at: str | None = None,
        updated_at: str | None = None,
    ) -> dict[str, Any] | None:
        updates: dict[str, Any] = {}
        if content is not None:
            updates["content"] = content
        if status is not None:
            updates["status"] = status
        if next_review_at is not None:
            updates["next_review_at"] = next_review_at
        if review_interval_days is not None:
            updates["review_interval_days"] = review_interval_days
        if closed_at is not None:
            updates["closed_at"] = closed_at
        updates["updated_at"] = updated_at or datetime.now().isoformat()
        sets = ", ".join(f"{key} = ?" for key in updates)
        self.conn.execute(
            f"UPDATE life_long_term_decisions SET {sets} WHERE id = ?",
            [*updates.values(), decision_id],
        )
        self.conn.commit()
        return self.get_long_term_decision(decision_id)

    def create_decision_review(
        self,
        *,
        decision_id: str,
        reviewed_at: str,
        review_note: str,
        outcome: str,
        next_review_at: str = "",
        summary: str = "",
    ) -> dict[str, Any]:
        review_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_decision_reviews
               (id, decision_id, reviewed_at, review_note, outcome, summary, next_review_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                review_id,
                decision_id,
                reviewed_at,
                review_note,
                outcome,
                summary,
                next_review_at,
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM life_decision_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        return dict(row or {})

    def list_long_term_decisions(self, *, include_closed: bool = False) -> list[dict[str, Any]]:
        if include_closed:
            rows = self.conn.execute(
                """SELECT * FROM life_long_term_decisions
                   ORDER BY status ASC, next_review_at ASC, created_at DESC"""
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM life_long_term_decisions
                   WHERE status IN ('active', 'pending_review_time')
                   ORDER BY next_review_at ASC, created_at DESC"""
            ).fetchall()
        return [self._decision_with_latest_review(dict(row)) for row in rows]

    def create_memory_observation(
        self,
        *,
        domain: str,
        observation_type: str,
        content: str,
        observed_at: str,
        source: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        observation_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_memory_observations
               (id, domain, observation_type, content, evidence_json, source, observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                observation_id,
                domain,
                observation_type,
                content,
                json.dumps(evidence or {}, ensure_ascii=False),
                source,
                observed_at,
            ),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM life_memory_observations WHERE id = ?",
            (observation_id,),
        ).fetchone()
        return self._memory_observation_row_to_dict(row)

    def list_memory_observations(
        self,
        *,
        domain: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if domain:
            rows = self.conn.execute(
                """SELECT * FROM life_memory_observations
                   WHERE domain = ?
                   ORDER BY observed_at DESC, rowid DESC
                   LIMIT ?""",
                (domain, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM life_memory_observations
                   ORDER BY observed_at DESC, rowid DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [self._memory_observation_row_to_dict(row) for row in rows]

    def upsert_memory_item(
        self,
        *,
        domain: str,
        memory_type: str,
        status: str,
        content: str,
        confidence: str,
        source: str,
        effective_from: str,
        evidence: dict[str, Any] | None = None,
        expires_at: str = "",
    ) -> dict[str, Any]:
        row = self.conn.execute(
            """SELECT * FROM life_memory_items
               WHERE domain = ?
                 AND memory_type = ?
                 AND source = ?
                 AND content = ?
               ORDER BY rowid DESC
               LIMIT 1""",
            (domain, memory_type, source, content),
        ).fetchone()
        now_text = datetime.now().isoformat()
        if row:
            item = self._memory_item_row_to_dict(row)
            if item["status"] == "blocked":
                return item
            self.conn.execute(
                """UPDATE life_memory_items
                   SET status = ?, confidence = ?, evidence_json = ?,
                       effective_from = ?, expires_at = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    status,
                    confidence,
                    json.dumps(evidence or {}, ensure_ascii=False),
                    effective_from,
                    expires_at,
                    now_text,
                    item["id"],
                ),
            )
            self.conn.commit()
            return self.get_memory_item(str(item["id"])) or item

        memory_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_memory_items
               (id, domain, memory_type, status, content, confidence,
                evidence_json, source, effective_from, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory_id,
                domain,
                memory_type,
                status,
                content,
                confidence,
                json.dumps(evidence or {}, ensure_ascii=False),
                source,
                effective_from,
                expires_at,
            ),
        )
        self.conn.commit()
        return self.get_memory_item(memory_id) or {}

    def get_memory_item(self, memory_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM life_memory_items WHERE id = ?",
            (memory_id,),
        ).fetchone()
        return self._memory_item_row_to_dict(row) if row else None

    def list_memory_items(
        self,
        *,
        statuses: list[str] | None = None,
        domains: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if statuses:
            clauses.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if domains:
            clauses.append(f"domain IN ({','.join('?' for _ in domains)})")
            params.extend(domains)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""SELECT * FROM life_memory_items
                {where}
                ORDER BY status ASC, updated_at DESC, rowid DESC
                LIMIT ?""",
            (*params, limit),
        ).fetchall()
        return [self._memory_item_row_to_dict(row) for row in rows]

    def update_memory_item_status(
        self,
        memory_id: str,
        *,
        status: str,
        updated_at: str | None = None,
    ) -> dict[str, Any] | None:
        self.conn.execute(
            """UPDATE life_memory_items
               SET status = ?, updated_at = ?
               WHERE id = ?""",
            (status, updated_at or datetime.now().isoformat(), memory_id),
        )
        self.conn.commit()
        return self.get_memory_item(memory_id)

    def record_memory_hit(
        self,
        *,
        memory_id: str,
        used_by: str,
        target_id: str,
        used_at: str,
        effect: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        hit_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO life_memory_hits
               (id, memory_id, used_by, target_id, used_at, effect_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                hit_id,
                memory_id,
                used_by,
                target_id,
                used_at,
                json.dumps(effect or {}, ensure_ascii=False),
            ),
        )
        self.conn.execute(
            """UPDATE life_memory_items
               SET last_used_at = ?, updated_at = ?
               WHERE id = ?""",
            (used_at, datetime.now().isoformat(), memory_id),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM life_memory_hits WHERE id = ?",
            (hit_id,),
        ).fetchone()
        return self._memory_hit_row_to_dict(row)

    def list_memory_hits(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM life_memory_hits
               ORDER BY used_at DESC, rowid DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [self._memory_hit_row_to_dict(row) for row in rows]

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
        row = self.conn.execute(
            "SELECT * FROM life_decision_traces WHERE id = ?",
            (trace_id,),
        ).fetchone()
        return self._decision_trace_row_to_dict(row)

    def list_decision_traces(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM life_decision_traces
               ORDER BY tick_at DESC, rowid DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [self._decision_trace_row_to_dict(row) for row in rows]

    def count_reminder_logs(self, reminder_type: str) -> int:
        row = self.conn.execute(
            "SELECT count(*) AS cnt FROM life_reminder_logs WHERE reminder_type = ?",
            (reminder_type,),
        ).fetchone()
        return int(row["cnt"] if row else 0)

    def _event_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["payload_json"] = _load_json(result.get("payload_json"), {})
        return result

    def _reminder_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any]:
        result = dict(row or {})
        result["payload_json"] = _load_json(result.get("payload_json"), {})
        return result

    def _context_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any]:
        result = dict(row or {})
        result["payload_json"] = _load_json(result.get("payload_json"), {})
        return result

    def _memory_item_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any]:
        result = dict(row or {})
        result["evidence_json"] = _load_json(result.get("evidence_json"), {})
        return result

    def _memory_observation_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any]:
        result = dict(row or {})
        result["evidence_json"] = _load_json(result.get("evidence_json"), {})
        return result

    def _memory_hit_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any]:
        result = dict(row or {})
        result["effect_json"] = _load_json(result.get("effect_json"), {})
        return result

    def _decision_trace_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any]:
        result = dict(row or {})
        result["context_json"] = _load_json(result.get("context_json"), {})
        result["candidates_json"] = _load_json(result.get("candidates_json"), [])
        result["memory_influences_json"] = _load_json(result.get("memory_influences_json"), [])
        result["warnings_json"] = _load_json(result.get("warnings_json"), [])
        return result

    def _decision_with_latest_review(self, decision: dict[str, Any]) -> dict[str, Any]:
        row = self.conn.execute(
            """SELECT * FROM life_decision_reviews
               WHERE decision_id = ?
               ORDER BY reviewed_at DESC, rowid DESC
               LIMIT 1""",
            (decision["id"],),
        ).fetchone()
        decision["latest_review"] = dict(row) if row else None
        return decision


def _load_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _append_raw_text(existing: str, addition: str) -> str:
    existing = existing.strip()
    addition = addition.strip()
    if not existing:
        return addition
    if not addition or addition in existing:
        return existing
    return f"{existing}；{addition}"


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.strptime(value[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")


def _seconds_between(start: datetime, end: datetime) -> float:
    start, end = _compatible_datetime_pair(start, end)
    return (end - start).total_seconds()


def _date_key(value: str) -> str:
    return _parse_datetime(value).date().isoformat()


def _compatible_datetime_pair(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    if _is_aware(left) == _is_aware(right):
        return left, right
    return left.replace(tzinfo=None), right.replace(tzinfo=None)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

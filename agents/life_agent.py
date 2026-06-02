"""Life Assistant agent core logic."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agents.life_intent_parser import LifeIntent, LifeIntentParser
from agents.life_memory import LifeMemoryAnalyzer
from db.life_repo import LifeRepo


@dataclass(frozen=True)
class ActivitySnapshot:
    idle_seconds: int | None
    available: bool = True
    locked: bool = False
    sleeping: bool = False
    display_off: bool = False
    logged_out: bool = False
    interruption_seconds: int | None = None
    interruption_source: str = ""
    interruption_started_at: str = ""
    interruption_ended_at: str = ""


@dataclass(frozen=True)
class LifeConfig:
    sedentary_threshold_minutes: int = 60
    sedentary_repeat_minutes: int = 20
    break_threshold_minutes: int = 6
    reminder_due_window_minutes: int = 15
    exercise_first_time: str = "10:30"
    exercise_repeat_hours: int = 2
    sleep_record_check_time: str = "13:00"
    meal_record_check_time: str = "21:30"
    sleep_reminder_time: str = "23:30"
    water_times: tuple[str, str] = ("10:00", "16:00")
    notifications_enabled: bool = False
    tick_lock_ttl_seconds: int = 50
    notification_rate_limit_cooldown_minutes: int = 120
    notification_rate_limit_max_cooldown_minutes: int = 360
    log_path: str = ""


class LifeAgent:
    def __init__(
        self,
        repo: LifeRepo,
        config: LifeConfig | None = None,
        parser: LifeIntentParser | None = None,
    ):
        self.repo = repo
        self.config = config or LifeConfig()
        self.parser = parser or LifeIntentParser()

    def record_life_event(
        self,
        message: str,
        *,
        received_at: str | None = None,
    ) -> dict[str, Any]:
        received = _parse_datetime(received_at) if received_at else _now()
        text = message.strip()
        intent = self.parser.parse(text, received_at=received)

        if intent.intent == "manual_status_query":
            return _rejected("第一版不支持手动查询，我会在定时复盘里总结这些数据。")

        if intent.intent == "health_consultation":
            return _rejected("我不能做医学诊断；如果症状持续、明显影响生活，建议咨询医生或专业人士。")

        if intent.intent == "sedentary_activity_reply":
            return _rejected(
                f"久坐警报不能靠回复解除；离开电脑超过{self.config.break_threshold_minutes}分钟后我会自动停止提醒。"
            )

        if intent.intent == "record_long_term_decision":
            return self._record_long_term_decision(intent, received)

        if intent.intent == "set_long_term_decision_review_time":
            return self._set_long_term_decision_review_time(intent, received)

        if intent.intent == "review_long_term_decision":
            return self._review_long_term_decision(intent, received)

        if intent.intent == "close_long_term_decision":
            return self._close_long_term_decision(intent, received)

        if intent.intent == "pause_sedentary":
            try:
                pause_until = _parse_datetime(intent.scheduled_for)
            except ValueError:
                return _rejected("这条我暂时不能作为生活记录保存。")
            self.repo.create_reminder_log(
                reminder_type="sedentary",
                scheduled_for=pause_until.isoformat(),
                status="paused",
                dedupe_key=f"sedentary_pause:{received.isoformat()}",
                payload={"duration_minutes": intent.duration_minutes},
            )
            return {
                **_reminder_updated(f"已暂停久坐警报到{_fmt_time(pause_until)}；坐姿统计仍会继续记录。"),
                "scheduled_for": pause_until.isoformat(),
            }

        if intent.intent == "skip_exercise":
            self.repo.create_reminder_log(
                reminder_type="exercise",
                scheduled_for=received.isoformat(),
                status="skipped",
                dedupe_key=f"exercise_skip:{received.date().isoformat()}",
                payload={"reason": "user_skip"},
            )
            return _reminder_updated("今天不练已记录，今天不会继续追运动提醒。")

        if intent.intent == "reschedule_exercise":
            try:
                scheduled_for = _parse_datetime(intent.scheduled_for)
            except ValueError:
                return _rejected("这条我暂时不能作为生活记录保存。")
            self.repo.supersede_same_day_reminders(
                reminder_type="exercise",
                day=scheduled_for.date().isoformat(),
                superseded_at=received.isoformat(),
                reason="user_reschedule",
            )
            payload = {"source": "user_reschedule", **intent.payload}
            self.repo.create_reminder_log(
                reminder_type="exercise",
                scheduled_for=scheduled_for.isoformat(),
                status="scheduled",
                dedupe_key=f"exercise_reschedule:{scheduled_for.isoformat()}",
                payload=payload,
            )
            exercise_type = payload.get("exercise_type", "运动")
            return {
                **_reminder_updated(f"已把{exercise_type}提醒改到{_fmt_time(scheduled_for)}。"),
                "scheduled_for": scheduled_for.isoformat(),
            }

        if intent.intent == "undo_latest":
            latest = self.repo.latest_user_event(
                now=received.isoformat(),
                window_minutes=30,
            )
            if not latest:
                return _rejected("没有找到 30 分钟内可撤销的生活记录。")
            updated = self.repo.update_event(latest["id"], status="undone")
            return {"status": "undone", "event": updated, "chat_preview": "已撤销上一条生活记录。"}

        if intent.intent == "modify_latest":
            return self._modify_latest_event(intent, received)

        if intent.intent == "record_event":
            return self._record_structured_event(intent, message, received)

        return _rejected("这条我暂时不能作为生活记录保存。")

    def reconcile_delivery_result(
        self,
        *,
        completed_at: str,
        delivery_error: str = "",
    ) -> dict[str, Any]:
        updated = self.repo.reconcile_sending_reminders(
            completed_at=completed_at,
            delivery_error=delivery_error,
        )
        return {
            "status": "reconciled",
            "updated": len(updated),
            "delivery_status": "failed" if delivery_error else "sent",
        }

    def _record_structured_event(
        self,
        intent: LifeIntent,
        message: str,
        received: datetime,
    ) -> dict[str, Any]:
        try:
            occurred = _parse_datetime(intent.occurred_at) if intent.occurred_at else received
        except ValueError:
            return _rejected("这条我暂时不能作为生活记录保存。")

        if intent.event_type == "sleep_start":
            event = self.repo.create_event(
                event_type="sleep_start",
                occurred_at=occurred.isoformat(),
                source="wechat",
                raw_text=message,
                payload=intent.payload or None,
            )
            paired_end = self._backfill_next_sleep_end(event)
            if paired_end and paired_end.get("duration_minutes") is not None:
                return _recorded(
                    event,
                    f"已记录睡觉时间：{_fmt_time(occurred)}，睡了{_fmt_duration(paired_end['duration_minutes'])}。",
                )
            return _recorded(event, f"已记录睡觉时间：{_fmt_time(occurred)}。")

        if intent.event_type == "sleep_end":
            start = self._latest_unmatched_sleep_start()
            duration = None
            if start:
                started_at = _parse_datetime(start["occurred_at"])
                duration = _duration_minutes_between(started_at, occurred)
            payload = {"wake_date": occurred.date().isoformat(), **intent.payload}
            event = self.repo.create_event(
                event_type="sleep_end",
                occurred_at=occurred.isoformat(),
                duration_minutes=duration,
                source="wechat",
                raw_text=message,
                payload=payload,
            )
            if duration is None:
                return _recorded(event, "已记录醒来时间；还缺睡觉时间，可以补一句大概几点睡。")
            return _recorded(event, f"已记录醒来时间：{_fmt_time(occurred)}，睡了{_fmt_duration(duration)}。")

        if intent.event_type == "sleep_session":
            try:
                ended_at = _parse_datetime(str(intent.payload.get("ended_at", "")))
            except ValueError:
                return _rejected("这条我暂时不能作为生活记录保存。")
            duration = _duration_minutes_between(occurred, ended_at)
            updated = self._update_existing_sleep_session(
                started_at=occurred,
                ended_at=ended_at,
                raw_text=message,
            )
            if updated:
                return {
                    "status": "modified",
                    "event": updated,
                    "chat_preview": f"已修正睡眠：{_fmt_time(occurred)} 到 {_fmt_time(ended_at)}，睡了{_fmt_duration(duration)}。",
                }
            self.repo.create_event(
                event_type="sleep_start",
                occurred_at=occurred.isoformat(),
                source="wechat",
                raw_text=message,
            )
            event = self.repo.create_event(
                event_type="sleep_end",
                occurred_at=ended_at.isoformat(),
                duration_minutes=duration,
                source="wechat",
                raw_text=message,
                payload={"wake_date": ended_at.date().isoformat()},
            )
            return _recorded(event, f"已记录睡眠：{_fmt_time(occurred)} 到 {_fmt_time(ended_at)}，睡了{_fmt_duration(duration)}。")

        if intent.event_type == "exercise":
            if intent.status != "completed_unknown":
                if intent.duration_minutes is None or "exercise_type" not in intent.payload:
                    return _rejected("这条我暂时不能作为生活记录保存。")
                pending_details = self.repo.latest_active_context(
                    context_type="exercise_details_missing",
                    now=received.isoformat(),
                )
                if pending_details and pending_details.get("target_type") == "life_event":
                    existing = self.repo.get_event(str(pending_details["target_id"]))
                    if existing and existing.get("event_type") == "exercise":
                        updated = self.repo.update_event(
                            existing["id"],
                            duration_minutes=intent.duration_minutes,
                            raw_text=_append_text(str(existing.get("raw_text") or ""), message),
                            payload=intent.payload or None,
                            status="recorded",
                        )
                        self.repo.resolve_interaction_context(
                            pending_details["id"],
                            resolved_at=received.isoformat(),
                        )
                        if updated:
                            self.repo.complete_same_day_reminders(
                                reminder_type="exercise",
                                day=occurred.date().isoformat(),
                                completed_at=occurred.isoformat(),
                                event_id=updated["id"],
                            )
                            return {
                                "status": "modified",
                                "event": updated,
                                "chat_preview": f"已补充运动：{intent.payload['exercise_type']} {intent.duration_minutes} 分钟。",
                            }
            event = self.repo.create_event(
                event_type="exercise",
                occurred_at=occurred.isoformat(),
                duration_minutes=intent.duration_minutes,
                source="wechat",
                raw_text=message,
                status=intent.status or "recorded",
                payload=intent.payload or None,
            )
            self.repo.complete_same_day_reminders(
                reminder_type="exercise",
                day=occurred.date().isoformat(),
                completed_at=occurred.isoformat(),
                event_id=event["id"],
            )
            if intent.status == "completed_unknown":
                self._open_context(
                    "exercise_details_missing",
                    target_type="life_event",
                    target_id=event["id"],
                    expected_reply_kind="exercise_details",
                    current=received,
                    ttl=timedelta(hours=12),
                )
                return _recorded(event, "已记录今天运动完成；可以之后补充类型和时长。")
            return _recorded(
                event,
                f"已记录运动：{intent.payload['exercise_type']} {intent.duration_minutes} 分钟。",
            )

        if intent.event_type == "meal":
            if "meal_slot" not in intent.payload or "content" not in intent.payload:
                return _rejected("这条我暂时不能作为生活记录保存。")
            payload = {**intent.payload, "recorded_at": received.isoformat()}
            is_append = payload.get("meal_update_mode") == "append"
            if intent.occurred_at:
                meal_date = occurred.date().isoformat()
            else:
                occurred = _date_anchor(received)
                meal_date = received.date().isoformat()
            event = self.repo.upsert_meal_event(
                meal_date=meal_date,
                meal_slot=str(payload["meal_slot"]),
                occurred_at=occurred.isoformat(),
                source="wechat",
                raw_text=message,
                payload=payload,
            )
            stored_payload = event.get("payload_json") or payload
            action = "已补充" if is_append else "已记录"
            if stored_payload.get("meal_time_precision") == "missing":
                self._open_context(
                    "meal_time_missing",
                    target_type="life_event",
                    target_id=event["id"],
                    expected_reply_kind="time",
                    current=received,
                    ttl=timedelta(hours=12),
                    payload={"meal_slot": stored_payload["meal_slot"]},
                )
                return _recorded(
                    event,
                    f"{action}{_meal_label(stored_payload['meal_slot'])}：{stored_payload['content']}。大概几点吃的？",
                )
            self.repo.resolve_contexts_for_target(
                context_type="meal_time_missing",
                target_type="life_event",
                target_id=event["id"],
                resolved_at=received.isoformat(),
            )
            return _recorded(
                event,
                f"{action}{_meal_label(stored_payload['meal_slot'])}：{stored_payload['content']}。",
            )

        if intent.event_type == "water":
            event = self.repo.create_event(
                event_type="water",
                occurred_at=occurred.isoformat(),
                source="wechat",
                raw_text=message,
            )
            return _recorded(event, f"已记录喝水：{_fmt_time(occurred)}。")

        return _rejected("这条我暂时不能作为生活记录保存。")

    def _modify_latest_event(self, intent: LifeIntent, received: datetime) -> dict[str, Any]:
        if "meal_occurred_at" in intent.payload:
            if intent.payload.get("explicit_change"):
                updated = self._modify_meal_time_by_slot(
                    str(intent.payload["meal_occurred_at"]),
                    received,
                    meal_slot=str(intent.payload.get("meal_slot") or ""),
                )
                if updated:
                    return updated
            return self._modify_pending_meal_time(
                str(intent.payload["meal_occurred_at"]),
                received,
                meal_slot=str(intent.payload.get("meal_slot") or ""),
                meal_time_period=str(intent.payload.get("meal_time_period") or ""),
            )

        latest = self.repo.latest_user_event(
            now=received.isoformat(),
            window_minutes=30,
        )
        if not latest:
            return _rejected("没有找到 30 分钟内可修改的生活记录。")

        change_text = str(intent.payload.get("change_text") or "").strip()
        if not change_text:
            return _rejected("这条修改指令不完整，暂时不能保存。")

        updated = self._updated_latest_meal(latest, change_text, received)
        if not updated:
            updated = self._updated_latest_exercise(latest, change_text, message_time=received)
        if not updated:
            updated = self._updated_latest_sleep(latest, change_text, message_time=received)
        if not updated:
            return _rejected("这条修改我暂时只支持改最近一条饮食、运动或睡眠记录。")

        payload = updated.get("payload_json") or {}
        if updated["event_type"] == "exercise":
            return {
                "status": "modified",
                "event": updated,
                "chat_preview": f"已把上一条改为运动：{payload.get('exercise_type', '运动')} {updated.get('duration_minutes') or 0} 分钟。",
            }
        if updated["event_type"] == "sleep_end":
            return {
                "status": "modified",
                "event": updated,
                "chat_preview": f"已修改上一条睡眠记录，睡了{_fmt_duration(int(updated.get('duration_minutes') or 0))}。",
            }
        return {
            "status": "modified",
            "event": updated,
            "chat_preview": f"已把上一条改为{_meal_label(payload['meal_slot'])}：{payload['content']}。",
        }

    def _record_long_term_decision(self, intent: LifeIntent, received: datetime) -> dict[str, Any]:
        content = str(intent.payload.get("content") or "").strip()
        if not content:
            return _rejected("这条长期决定内容不完整，暂时不能保存。")
        next_review_at = _parse_optional_datetime(intent.payload.get("next_review_at"))
        existing = self.repo.find_open_long_term_decision_by_content(content)
        if existing:
            if next_review_at:
                interval = _days_between(received, next_review_at)
                updated = self.repo.update_long_term_decision(
                    existing["id"],
                    status="active",
                    next_review_at=next_review_at.isoformat(),
                    review_interval_days=interval,
                    updated_at=received.isoformat(),
                )
                return {
                    "status": "decision_recorded",
                    "decision": updated,
                    "event": None,
                    "chat_preview": f"已更新这个长期决定的下次复盘时间：{_fmt_month_day_time(next_review_at)}。",
                }
            if existing["status"] == "pending_review_time":
                self._open_context(
                    "long_term_review_time_missing",
                    target_type="long_term_decision",
                    target_id=existing["id"],
                    expected_reply_kind="time",
                    current=received,
                    ttl=timedelta(days=7),
                    payload={"content": content},
                )
                return {
                    "status": "decision_needs_review_time",
                    "decision": existing,
                    "event": None,
                    "chat_preview": "这个长期决定已经记下了。下一次复盘是哪天几点？",
                }
            return {
                "status": "decision_recorded",
                "decision": existing,
                "event": None,
                "chat_preview": f"这个长期决定已经在记录里。下次复盘 {_fmt_month_day_time(_parse_datetime(existing['next_review_at']))}。",
            }
        if not next_review_at:
            decision = self.repo.create_long_term_decision(
                content=content,
                created_at=received.isoformat(),
                next_review_at="",
                review_interval_days=0,
                status="pending_review_time",
            )
            self._open_context(
                "long_term_review_time_missing",
                target_type="long_term_decision",
                target_id=decision["id"],
                expected_reply_kind="time",
                current=received,
                ttl=timedelta(days=7),
                payload={"content": content},
            )
            return {
                "status": "decision_needs_review_time",
                "decision": decision,
                "event": None,
                "chat_preview": f"已记下这个长期决定。下一次复盘是哪天几点？",
            }
        interval = _days_between(received, next_review_at)
        decision = self.repo.create_long_term_decision(
            content=content,
            created_at=received.isoformat(),
            next_review_at=next_review_at.isoformat(),
            review_interval_days=interval,
        )
        return {
            "status": "decision_recorded",
            "decision": decision,
            "event": None,
            "chat_preview": f"已记录长期决定：{content}。我会在{_fmt_month_day_time(next_review_at)}提醒你复盘。",
        }

    def _set_long_term_decision_review_time(self, intent: LifeIntent, received: datetime) -> dict[str, Any]:
        context = self.repo.latest_active_context(
            context_type="long_term_review_time_missing",
            now=received.isoformat(),
        )
        decision = None
        if context and context.get("target_type") == "long_term_decision":
            decision = self.repo.get_long_term_decision(str(context["target_id"]))
        if not decision:
            decision = self.repo.latest_pending_long_term_decision()
        if not decision:
            return _rejected("没有找到待补复盘时间的长期决定。")
        next_review_at = _parse_optional_datetime(intent.payload.get("next_review_at"))
        if not next_review_at:
            return _rejected("这条复盘时间我暂时没读懂，可以直接说具体日期和时间。")
        duplicate = self.repo.find_open_long_term_decision_by_content(
            str(decision["content"]),
            exclude_id=str(decision["id"]),
            status="active",
        )
        if duplicate:
            updated = self.repo.update_long_term_decision(
                duplicate["id"],
                next_review_at=next_review_at.isoformat(),
                review_interval_days=_days_between(received, next_review_at),
                updated_at=received.isoformat(),
            )
            self.repo.update_long_term_decision(
                decision["id"],
                status="superseded",
                updated_at=received.isoformat(),
            )
            if context:
                self.repo.resolve_interaction_context(context["id"], resolved_at=received.isoformat())
            return {
                "status": "decision_recorded",
                "decision": updated,
                "event": None,
                "chat_preview": f"已设置长期决定的下次复盘时间：{_fmt_month_day_time(next_review_at)}。",
            }
        updated = self.repo.update_long_term_decision(
            decision["id"],
            status="active",
            next_review_at=next_review_at.isoformat(),
            review_interval_days=_days_between(received, next_review_at),
            updated_at=received.isoformat(),
        )
        if context:
            self.repo.resolve_interaction_context(context["id"], resolved_at=received.isoformat())
        return {
            "status": "decision_recorded",
            "decision": updated,
            "event": None,
            "chat_preview": f"已设置长期决定的下次复盘时间：{_fmt_month_day_time(next_review_at)}。",
        }

    def _review_long_term_decision(self, intent: LifeIntent, received: datetime) -> dict[str, Any]:
        context = self.repo.latest_active_context(
            context_type="long_term_review_reply",
            now=received.isoformat(),
        )
        decision = None
        if context and context.get("target_type") == "long_term_decision":
            decision = self.repo.get_long_term_decision(str(context["target_id"]))
        if not decision:
            decision = self.repo.latest_active_long_term_decision(now=received.isoformat())
        if not decision:
            return _rejected("没有找到可复盘的长期决定。")
        review_note = str(intent.payload.get("review_note") or "").strip() or "继续有效"
        new_content = str(intent.payload.get("new_content") or "").strip()
        next_review_at = _parse_optional_datetime(intent.payload.get("next_review_at"))
        outcome = "adjusted" if new_content else "kept"
        review = self.repo.create_decision_review(
            decision_id=decision["id"],
            reviewed_at=received.isoformat(),
            review_note=review_note,
            outcome=outcome,
            next_review_at=next_review_at.isoformat() if next_review_at else "",
        )
        if not next_review_at:
            updated = self.repo.update_long_term_decision(
                decision["id"],
                content=new_content or None,
                status="pending_review_time",
                next_review_at="",
                review_interval_days=0,
                updated_at=received.isoformat(),
            )
            if context:
                self.repo.resolve_interaction_context(context["id"], resolved_at=received.isoformat())
            self._upsert_decision_review_memory(
                decision=updated,
                review=review,
                current=received,
            )
            self._open_context(
                "long_term_review_time_missing",
                target_type="long_term_decision",
                target_id=decision["id"],
                expected_reply_kind="time",
                current=received,
                ttl=timedelta(days=7),
                payload={"content": str(updated.get("content") or decision["content"])},
            )
            return {
                "status": "decision_needs_review_time",
                "decision": updated,
                "event": None,
                "chat_preview": "已记录这次复盘。下一次复盘是哪天几点？",
            }
        interval = _days_between(received, next_review_at)
        updated = self.repo.update_long_term_decision(
            decision["id"],
            content=new_content or None,
            status="active",
            next_review_at=next_review_at.isoformat(),
            review_interval_days=interval,
            updated_at=received.isoformat(),
        )
        if context:
            self.repo.resolve_interaction_context(context["id"], resolved_at=received.isoformat())
        display_content = str((updated or decision).get("content") or decision["content"])
        self._upsert_decision_review_memory(
            decision=updated or decision,
            review=review,
            current=received,
        )
        return {
            "status": "decision_review_recorded",
            "decision": updated,
            "event": None,
            "chat_preview": f"已复盘长期决定：{display_content}。下次复盘 {_fmt_month_day_time(next_review_at)}。",
        }

    def _close_long_term_decision(self, intent: LifeIntent, received: datetime) -> dict[str, Any]:
        context = self.repo.latest_active_context(
            context_type="long_term_review_reply",
            now=received.isoformat(),
        )
        decision = None
        if context and context.get("target_type") == "long_term_decision":
            decision = self.repo.get_long_term_decision(str(context["target_id"]))
        if not decision:
            decision = self.repo.latest_active_long_term_decision(now=received.isoformat())
        if not decision:
            return _rejected("没有找到可关闭的长期决定。")
        review_note = str(intent.payload.get("review_note") or "").strip() or "关闭这个决定"
        outcome = str(intent.payload.get("outcome") or "closed")
        if outcome not in {"completed", "abandoned", "closed"}:
            outcome = "closed"
        review = self.repo.create_decision_review(
            decision_id=decision["id"],
            reviewed_at=received.isoformat(),
            review_note=review_note,
            outcome=outcome,
        )
        updated = self.repo.update_long_term_decision(
            decision["id"],
            status="closed",
            closed_at=received.isoformat(),
            updated_at=received.isoformat(),
        )
        if context:
            self.repo.resolve_interaction_context(context["id"], resolved_at=received.isoformat())
        self._upsert_decision_review_memory(
            decision=updated or decision,
            review=review,
            current=received,
        )
        return {
            "status": "decision_closed",
            "decision": updated,
            "event": None,
            "chat_preview": f"已关闭长期决定：{decision['content']}。",
        }

    def _upsert_decision_review_memory(
        self,
        *,
        decision: dict[str, Any] | None,
        review: dict[str, Any],
        current: datetime,
    ) -> None:
        if not decision or not review:
            return
        content = str(decision.get("content") or "").strip()
        note = str(review.get("review_note") or "").strip()
        if not content or not note:
            return
        outcome = str(review.get("outcome") or "")
        memory_text = f"长期决定“{content}”最近复盘结论：{note}"
        self.repo.upsert_memory_item(
            domain="life.decision",
            memory_type="decision_review",
            status="active" if str(decision.get("status") or "") != "closed" else "archived",
            content=memory_text,
            confidence="high",
            source="long_term_decision_review",
            effective_from=current.isoformat(),
            evidence={
                "decision_id": decision.get("id"),
                "review_id": review.get("id"),
                "outcome": outcome,
                "next_review_at": review.get("next_review_at") or decision.get("next_review_at") or "",
            },
        )

    def _modify_pending_meal_time(
        self,
        occurred_at_text: str,
        received: datetime,
        *,
        meal_slot: str = "",
        meal_time_period: str = "",
    ) -> dict[str, Any]:
        try:
            occurred = _parse_datetime(occurred_at_text)
        except ValueError:
            return _rejected("这条修改指令不完整，暂时不能保存。")
        context = self.repo.latest_active_context(
            context_type="meal_time_missing",
            now=received.isoformat(),
        )
        latest = None
        if context and context.get("target_type") == "life_event":
            candidate = self.repo.get_event(str(context["target_id"]))
            payload = candidate.get("payload_json") if candidate else {}
            if candidate and (not meal_slot or payload.get("meal_slot") == meal_slot):
                latest = candidate
        if not latest:
            latest = self.repo.latest_pending_meal(
                now=received.isoformat(),
                window_minutes=720 if meal_slot else 60,
                meal_slot=meal_slot,
            )
        if not latest:
            if meal_slot:
                return _rejected("没有找到当天待补时间的这顿饮食记录。")
            return _rejected("没有找到 60 分钟内待补时间的饮食记录。")
        payload = dict(latest.get("payload_json") or {})
        occurred = _disambiguate_meal_time(
            occurred,
            meal_slot=str(payload.get("meal_slot") or meal_slot),
            time_period=meal_time_period,
        )
        payload["meal_time_precision"] = "exact"
        updated = self.repo.update_event(
            latest["id"],
            occurred_at=occurred.isoformat(),
            payload=payload,
        )
        if not updated:
            return _rejected("这条修改指令不完整，暂时不能保存。")
        if context:
            self.repo.resolve_interaction_context(context["id"], resolved_at=received.isoformat())
        label = _meal_label(str(payload.get("meal_slot") or ""))
        return {
            "status": "modified",
            "event": updated,
            "chat_preview": f"已补充{label}时间：{_fmt_time(occurred)}。",
        }

    def _modify_meal_time_by_slot(
        self,
        occurred_at_text: str,
        received: datetime,
        *,
        meal_slot: str,
    ) -> dict[str, Any] | None:
        if not meal_slot:
            return None
        try:
            occurred = _parse_datetime(occurred_at_text)
        except ValueError:
            return None
        latest = self.repo.meal_event_on_date(
            meal_date=received.date().isoformat(),
            meal_slot=meal_slot,
        )
        if not latest:
            return None
        payload = dict(latest.get("payload_json") or {})
        payload["meal_time_precision"] = "exact"
        payload["meal_slot_inferred"] = False
        updated = self.repo.update_event(
            latest["id"],
            occurred_at=occurred.isoformat(),
            payload=payload,
        )
        if not updated:
            return None
        self.repo.resolve_contexts_for_target(
            context_type="meal_time_missing",
            target_type="life_event",
            target_id=latest["id"],
            resolved_at=received.isoformat(),
        )
        return {
            "status": "modified",
            "event": updated,
            "chat_preview": f"已修改{_meal_label(meal_slot)}时间：{_fmt_time(occurred)}。",
        }

    def _update_existing_sleep_session(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
        raw_text: str,
    ) -> dict[str, Any] | None:
        rows = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'sleep_end'
                 AND status NOT IN ('undone', 'superseded')
               ORDER BY occurred_at DESC, rowid DESC"""
        ).fetchall()
        wake_date = ended_at.date().isoformat()
        same_wake_date: list[dict[str, Any]] = []
        for row in rows:
            item = self.repo._event_row_to_dict(row)
            if not item:
                continue
            payload = item.get("payload_json") or {}
            row_wake_date = str(payload.get("wake_date") or "")
            if not row_wake_date:
                row_wake_date = _parse_datetime(str(item["occurred_at"])).date().isoformat()
            if row_wake_date == wake_date:
                same_wake_date.append(item)
        if len(same_wake_date) != 1:
            return None
        sleep_end = same_wake_date[0]
        existing_ended_at = _parse_datetime(str(sleep_end["occurred_at"]))
        if abs(_seconds_between(existing_ended_at, ended_at)) > 3 * 60 * 60:
            return None
        start_row = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'sleep_start'
                 AND status NOT IN ('undone', 'superseded')
                 AND occurred_at < ?
               ORDER BY occurred_at DESC, rowid DESC
               LIMIT 1""",
            (sleep_end["occurred_at"],),
        ).fetchone()
        if not start_row:
            return None
        sleep_start = self.repo._event_row_to_dict(start_row)
        if not sleep_start:
            return None
        self.repo.update_event(
            sleep_start["id"],
            occurred_at=started_at.isoformat(),
            raw_text=_append_text(str(sleep_start.get("raw_text") or ""), raw_text),
        )
        return self.repo.update_event(
            sleep_end["id"],
            occurred_at=ended_at.isoformat(),
            duration_minutes=_duration_minutes_between(started_at, ended_at),
            raw_text=_append_text(str(sleep_end.get("raw_text") or ""), raw_text),
            payload={"wake_date": wake_date},
        )

    def _updated_latest_meal(
        self,
        latest: dict[str, Any],
        change_text: str,
        received: datetime,
    ) -> dict[str, Any] | None:
        if latest["event_type"] != "meal":
            return None

        slot = _meal_slot_from_text(change_text)
        if slot and "吃" not in change_text:
            payload = dict(latest.get("payload_json") or {})
            if not payload.get("content"):
                return None
            payload["meal_slot"] = slot
            payload["meal_slot_inferred"] = False
            return self.repo.update_event(latest["id"], payload=payload)

        parsed = self.parser.parse(change_text, received_at=received)
        if parsed.intent != "record_event" or parsed.event_type != "meal":
            return None
        if "meal_slot" not in parsed.payload or "content" not in parsed.payload:
            return None
        return self.repo.update_event(latest["id"], payload=parsed.payload)

    def _updated_latest_exercise(
        self,
        latest: dict[str, Any],
        change_text: str,
        *,
        message_time: datetime,
    ) -> dict[str, Any] | None:
        if latest["event_type"] != "exercise":
            return None
        parsed = self.parser.parse(change_text, received_at=message_time)
        if parsed.intent != "record_event" or parsed.event_type != "exercise":
            return None
        if parsed.duration_minutes is None or "exercise_type" not in parsed.payload:
            return None
        return self.repo.update_event(
            latest["id"],
            duration_minutes=parsed.duration_minutes,
            payload=parsed.payload,
            status="recorded",
        )

    def _updated_latest_sleep(
        self,
        latest: dict[str, Any],
        change_text: str,
        *,
        message_time: datetime,
    ) -> dict[str, Any] | None:
        if latest["event_type"] not in {"sleep_start", "sleep_end"}:
            return None
        parsed = self.parser.parse(change_text, received_at=message_time)
        if parsed.intent != "record_event" or parsed.event_type != "sleep_session":
            return None
        try:
            started_at = _parse_datetime(parsed.occurred_at)
            ended_at = _parse_datetime(str(parsed.payload.get("ended_at", "")))
        except ValueError:
            return None
        return self._update_existing_sleep_session(
            started_at=started_at,
            ended_at=ended_at,
            raw_text=change_text,
        )

    def life_tick(
        self,
        *,
        now: str | None = None,
        activity: ActivitySnapshot | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        current = _parse_datetime(now) if now else _now()
        activity = activity or ActivitySnapshot(idle_seconds=None, available=False)
        lock_owner = f"life_tick:{uuid.uuid4().hex[:8]}"
        if not self.repo.acquire_lock(
            name="life_tick",
            owner=lock_owner,
            now=current.isoformat(),
            ttl_seconds=self.config.tick_lock_ttl_seconds,
        ):
            result = {
                "status": "locked",
                "selected": None,
                "chat_preview": "Life Assistant tick 正在运行，本次跳过。",
                "warnings": ["life_tick_lock_held"],
                "meta": {"should_notify": False, "dry_run": dry_run},
            }
            self._write_tick_log(
                current=current,
                activity=activity,
                candidates=[],
                selected=None,
                warnings=result["warnings"],
                dry_run=dry_run,
                should_notify=False,
                status="locked",
            )
            self._record_decision_trace(
                current=current,
                activity=activity,
                status="locked",
                candidates=[],
                selected=None,
                selected_reminder_id="",
                memory_influences=[],
                warnings=result["warnings"],
                suppression_reason="life_tick_lock_held",
            )
            return result

        try:
            warnings: list[str] = []
            candidates: list[dict[str, Any]] = []
            self._mark_missed_reviews(current)
            try:
                LifeMemoryAnalyzer(self.repo).refresh(now=current.isoformat())
            except Exception:
                warnings.append("life_memory_refresh_failed")
            if self._has_fresh_pending_knowledge_choice(current):
                warnings.append("knowledge_workflow_active")
                result = {
                    "status": "idle",
                    "selected": None,
                    "chat_preview": "Life Assistant 本次 tick 无需提醒。",
                    "warnings": warnings,
                    "meta": {"should_notify": False, "dry_run": dry_run},
                }
                self._write_tick_log(
                    current=current,
                    activity=activity,
                    candidates=[],
                    selected=None,
                    warnings=warnings,
                    dry_run=dry_run,
                    should_notify=False,
                    status="idle",
                )
                self._record_decision_trace(
                    current=current,
                    activity=activity,
                    status="idle",
                    candidates=[],
                    selected=None,
                    selected_reminder_id="",
                    memory_influences=[],
                    warnings=warnings,
                    suppression_reason="knowledge_workflow_active",
                )
                return result

            if activity.available:
                sedentary = self._sedentary_candidate(current, activity)
                if sedentary:
                    candidates.append(sedentary)
            else:
                warnings.append("mac_activity_unavailable")

            long_term_decision_review = self._long_term_decision_review_candidate(current)
            if long_term_decision_review:
                candidates.append(long_term_decision_review)
            exercise = self._exercise_candidate(current)
            if exercise:
                candidates.append(exercise)
            sleep = self._sleep_candidate(current)
            if sleep:
                candidates.append(sleep)
            water = self._water_candidate(current)
            if water:
                candidates.append(water)
            sleep_record = self._sleep_record_candidate(current)
            if sleep_record:
                candidates.append(sleep_record)
            meal_record = self._meal_record_candidate(current)
            if meal_record:
                candidates.append(meal_record)
            daily_review = self._daily_review_candidate(current)
            if daily_review:
                candidates.append(daily_review)
            weekly_review = self._weekly_review_candidate(current)
            if weekly_review:
                candidates.append(weekly_review)

            self._apply_memory_strategy_to_candidates(candidates, current)
            candidates.sort(key=lambda item: item["priority"])
            selected = candidates[0] if candidates else None
            if selected and self._notification_rate_limited(current):
                warnings.append("notification_rate_limited")
                selected = None
            should_notify = bool(selected and self.config.notifications_enabled and not dry_run)
            selected_reminder_id = ""
            memory_pack: list[dict[str, Any]] = []
            strategy_influences = list((selected or {}).get("memory_influences") or [])
            if selected:
                self._store_selected_review(selected)
                memory_pack = self._memory_pack_for_reminder(str(selected["reminder_type"]))
                if memory_pack:
                    selected = {
                        **selected,
                        "message": self._message_with_memory(
                            str(selected["message"]),
                            memory_pack,
                        ),
                    }
                payload = {"dry_run": dry_run, **(selected.get("payload") or {})}
                if memory_pack:
                    payload["memory_items"] = memory_pack
                if strategy_influences:
                    payload["strategy_memory_items"] = strategy_influences
                log = self.repo.create_reminder_log(
                    reminder_type=selected["reminder_type"],
                    scheduled_for=current.isoformat(),
                    status="sending" if should_notify else "scheduled",
                    dedupe_key=selected["dedupe_key"],
                    payload=payload,
                )
                selected_reminder_id = str(log["id"])
                for memory in memory_pack:
                    self.repo.record_memory_hit(
                        memory_id=str(memory["id"]),
                        used_by="life_tick",
                        target_id=str(log["id"]),
                        used_at=current.isoformat(),
                        effect={
                            "reminder_type": selected["reminder_type"],
                            "effect": "message_context",
                        },
                    )
                for influence in strategy_influences:
                    memory_id = str(influence.get("memory_id") or "")
                    if not memory_id:
                        continue
                    self.repo.record_memory_hit(
                        memory_id=memory_id,
                        used_by="life_tick",
                        target_id=str(log["id"]),
                        used_at=current.isoformat(),
                        effect={
                            "reminder_type": selected["reminder_type"],
                            "effect": str(influence.get("effect") or "strategy_context"),
                        },
                    )
                if selected["reminder_type"] == "long_term_decision_review":
                    decision_id = str((selected.get("payload") or {}).get("decision_id") or "")
                    if decision_id:
                        self._open_context(
                            "long_term_review_reply",
                            target_type="long_term_decision",
                            target_id=decision_id,
                            expected_reply_kind="decision_review",
                            current=current,
                            ttl=timedelta(days=7),
                        )
            result = {
                "status": "selected" if selected else "idle",
                "selected": selected,
                "chat_preview": selected["message"] if selected else "Life Assistant 本次 tick 无需提醒。",
                "warnings": warnings,
                "meta": {"should_notify": should_notify, "dry_run": dry_run},
            }
            self._write_tick_log(
                current=current,
                activity=activity,
                candidates=candidates,
                selected=selected,
                warnings=warnings,
                dry_run=dry_run,
                should_notify=should_notify,
                status=result["status"],
            )
            trace_warnings = list(warnings)
            self._record_decision_trace(
                current=current,
                activity=activity,
                status=str(result["status"]),
                candidates=candidates,
                selected=selected,
                selected_reminder_id=selected_reminder_id,
                memory_influences=self._memory_influences_from_pack(
                    memory_pack,
                    str(selected["reminder_type"]) if selected else "",
                ) + strategy_influences,
                warnings=trace_warnings,
                suppression_reason="notification_rate_limited" if "notification_rate_limited" in warnings else "",
            )
            return result
        finally:
            self.repo.release_lock(name="life_tick", owner=lock_owner)

    def _memory_pack_for_reminder(self, reminder_type: str) -> list[dict[str, Any]]:
        domains = _memory_domains_for_reminder(reminder_type)
        if not domains:
            return []
        items = self.repo.list_memory_items(
            statuses=["active", "temporary", "weak"],
            domains=domains,
            limit=5,
        )
        return [
            {
                "id": item["id"],
                "domain": item["domain"],
                "memory_type": item["memory_type"],
                "status": item["status"],
                "content": item["content"],
                "confidence": item["confidence"],
                "source": item["source"],
            }
            for item in items
            if not _is_strategy_memory_item(item)
        ]

    def _message_with_memory(self, message: str, memory_pack: list[dict[str, Any]]) -> str:
        memory_text = _memory_copy_text(memory_pack)
        if not memory_text:
            return message
        return f"{message} 参考记忆：{memory_text}"

    def _apply_memory_strategy_to_candidates(
        self,
        candidates: list[dict[str, Any]],
        current: datetime,
    ) -> None:
        if not candidates:
            return
        memories = self._active_strategy_memories(current)
        if not memories:
            return
        for candidate in candidates:
            reminder_type = str(candidate.get("reminder_type") or "")
            domains = _strategy_domains_for_reminder(reminder_type)
            memory = next((item for item in memories if str(item.get("domain") or "") in domains), None)
            if not memory:
                continue
            original_priority = int(candidate.get("priority") or 0)
            adjusted_priority = original_priority if reminder_type == "sedentary" else max(0, original_priority - 1)
            candidate["priority"] = adjusted_priority
            influence = {
                "memory_id": str(memory.get("id") or ""),
                "domain": str(memory.get("domain") or ""),
                "memory_type": str(memory.get("memory_type") or ""),
                "status": str(memory.get("status") or ""),
                "content": str(memory.get("content") or ""),
                "effect": "strategy_context",
                "reminder_type": reminder_type,
                "priority_before": original_priority,
                "priority_after": adjusted_priority,
            }
            candidate.setdefault("memory_influences", []).append(influence)
            candidate["strategy_memory_id"] = influence["memory_id"]

    def _active_strategy_memories(self, current: datetime) -> list[dict[str, Any]]:
        items = self.repo.list_memory_items(
            statuses=["active", "temporary", "weak"],
            domains=["life.sleep", "life.meal", "life.water", "life.sedentary", "life.exercise"],
            limit=20,
        )
        return [
            item
            for item in items
            if _is_strategy_memory_item(item)
            and _memory_item_active_at(item, current)
        ]

    def _store_selected_review(self, selected: dict[str, Any]) -> None:
        if selected["reminder_type"] == "daily_review":
            review = selected.get("review") or {}
            self.repo.create_daily_review(
                review_date=str(review["review_date"]),
                summary=str(review["summary"]),
                metrics=review.get("metrics") or {},
                notes=review.get("notes") or {},
            )
        if selected["reminder_type"] == "weekly_review":
            review = selected.get("review") or {}
            self.repo.create_weekly_review(
                week_start=str(review["week_start"]),
                week_end=str(review["week_end"]),
                summary=str(review["summary"]),
                labels=review.get("labels") or {},
                patterns=review.get("patterns") or {},
                recommendations=list(review.get("recommendations") or []),
                headline=str(review.get("headline") or ""),
                report_text=str(review.get("report_text") or review.get("summary") or ""),
                evidence=list(review.get("evidence") or []),
                delivery=review.get("delivery") or {},
            )
            strategy = review.get("strategy_memory") or {}
            if strategy:
                self.repo.upsert_memory_item(
                    domain=str(strategy["domain"]),
                    memory_type="stage_goal",
                    status="temporary",
                    content=str(strategy["content"]),
                    confidence="medium",
                    source="weekly_review",
                    effective_from=str(strategy["effective_from"]),
                    expires_at=str(strategy["expires_at"]),
                    evidence=strategy.get("evidence") or {},
                )

    def _open_context(
        self,
        context_type: str,
        *,
        target_type: str,
        target_id: str,
        expected_reply_kind: str,
        current: datetime,
        ttl: timedelta,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.repo.create_interaction_context(
            context_type=context_type,
            target_type=target_type,
            target_id=target_id,
            expected_reply_kind=expected_reply_kind,
            created_at=current.isoformat(),
            expires_at=(current + ttl).isoformat(),
            payload=payload,
        )

    def _mark_missed_reviews(self, current: datetime) -> None:
        self._ensure_review_rows_for_existing_logs()

        daily_review_date = (current - timedelta(days=1)).date().isoformat()
        daily_cutoff = current.replace(hour=2, minute=0, second=0, microsecond=0)
        daily_dedupe_key = f"daily_review:{daily_review_date}"
        if (
            current >= daily_cutoff
            and not self.repo.get_daily_review(daily_review_date)
            and not self._has_any_reminder_log(daily_dedupe_key)
        ):
            self.repo.create_reminder_log(
                reminder_type="daily_review",
                scheduled_for=daily_cutoff.isoformat(),
                status="missed",
                dedupe_key=daily_dedupe_key,
                payload={"reason": "blocked_until_cutoff"},
            )

        if current.weekday() != 6:
            return
        weekly_cutoff = current.replace(hour=23, minute=30, second=0, microsecond=0)
        if current < weekly_cutoff:
            return
        week_start, week_end = _week_bounds(current)
        weekly_dedupe_key = f"weekly_review:{week_start}:{week_end}"
        if self.repo.get_weekly_review(week_start=week_start, week_end=week_end):
            return
        if self._has_any_reminder_log(weekly_dedupe_key):
            return
        self.repo.create_reminder_log(
            reminder_type="weekly_review",
            scheduled_for=weekly_cutoff.isoformat(),
            status="missed",
            dedupe_key=weekly_dedupe_key,
            payload={"reason": "blocked_until_cutoff"},
        )

    def _ensure_review_rows_for_existing_logs(self) -> None:
        rows = self.repo.conn.execute(
            """SELECT reminder_type, dedupe_key FROM life_reminder_logs
               WHERE reminder_type IN ('daily_review', 'weekly_review')
                 AND status IN ('scheduled', 'sending', 'sent')
               ORDER BY scheduled_for ASC"""
        ).fetchall()
        for row in rows:
            reminder_type = row["reminder_type"]
            dedupe_key = str(row["dedupe_key"])
            if reminder_type == "daily_review":
                review_date = dedupe_key.replace("daily_review:", "", 1)
                if not review_date or self.repo.get_daily_review(review_date):
                    continue
                review = self._build_daily_review(review_date)
                self.repo.create_daily_review(
                    review_date=review_date,
                    summary=review["summary"],
                    metrics=review["metrics"],
                    notes=review["notes"],
                )
            elif reminder_type == "weekly_review":
                parts = dedupe_key.split(":")
                if len(parts) != 3:
                    continue
                _, week_start, week_end = parts
                if self.repo.get_weekly_review(week_start=week_start, week_end=week_end):
                    continue
                review = self._build_weekly_review(week_start, week_end)
                self.repo.create_weekly_review(
                    week_start=week_start,
                    week_end=week_end,
                    summary=review["summary"],
                    labels=review["labels"],
                    patterns=review["patterns"],
                    recommendations=review["recommendations"],
                    headline=review["headline"],
                    report_text=review["report_text"],
                    evidence=review["evidence"],
                    delivery=review["delivery"],
                )

    def _write_tick_log(
        self,
        *,
        current: datetime,
        activity: ActivitySnapshot,
        candidates: list[dict[str, Any]],
        selected: dict[str, Any] | None,
        warnings: list[str],
        dry_run: bool,
        should_notify: bool,
        status: str,
    ) -> None:
        if not self.config.log_path:
            return
        entry = {
            "tick_time": current.isoformat(),
            "status": status,
            "idle_seconds": activity.idle_seconds,
            "activity_available": activity.available,
            "interruption_seconds": activity.interruption_seconds,
            "interruption_source": activity.interruption_source,
            "interruption_started_at": activity.interruption_started_at,
            "interruption_ended_at": activity.interruption_ended_at,
            "sedentary_state": "active" if self._active_sitting_window() else "inactive",
            "due_candidates": [item["reminder_type"] for item in candidates],
            "selected_reminder": selected.get("reminder_type") if selected else "",
            "should_notify": should_notify,
            "dry_run": dry_run,
            "warnings": warnings,
        }
        try:
            path = Path(self.config.log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            return

    def _record_decision_trace(
        self,
        *,
        current: datetime,
        activity: ActivitySnapshot,
        status: str,
        candidates: list[dict[str, Any]],
        selected: dict[str, Any] | None,
        selected_reminder_id: str,
        memory_influences: list[dict[str, Any]],
        warnings: list[str],
        suppression_reason: str = "",
    ) -> None:
        try:
            self.repo.create_decision_trace(
                tick_at=current.isoformat(),
                status=status,
                selected_reminder_type=str(selected.get("reminder_type") or "") if selected else "",
                selected_reminder_id=selected_reminder_id,
                context=self._trace_context(current, activity),
                candidates=self._trace_candidates(candidates),
                memory_influences=memory_influences,
                decision_reason=_decision_reason(selected, warnings),
                suppression_reason=suppression_reason,
                warnings=warnings,
            )
        except Exception:
            warnings.append("life_decision_trace_failed")

    def _trace_context(self, current: datetime, activity: ActivitySnapshot) -> dict[str, Any]:
        return {
            "date": current.date().isoformat(),
            "idle_seconds": activity.idle_seconds,
            "activity_available": activity.available,
            "locked": activity.locked,
            "sleeping": activity.sleeping,
            "display_off": activity.display_off,
            "logged_out": activity.logged_out,
            "interruption_seconds": activity.interruption_seconds,
            "interruption_source": activity.interruption_source,
            "sedentary_state": "active" if self._active_sitting_window() else "inactive",
            "notifications_enabled": self.config.notifications_enabled,
        }

    def _trace_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "reminder_type": str(item.get("reminder_type") or ""),
                "priority": item.get("priority"),
                "reason": _default_candidate_reason(str(item.get("reminder_type") or "")),
                "dedupe_key": str(item.get("dedupe_key") or ""),
                "strategy_memory_id": str(item.get("strategy_memory_id") or ""),
            }
            for item in candidates
        ]

    def _memory_influences_from_pack(
        self,
        memory_pack: list[dict[str, Any]],
        reminder_type: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "memory_id": str(item.get("id") or ""),
                "domain": str(item.get("domain") or ""),
                "memory_type": str(item.get("memory_type") or ""),
                "status": str(item.get("status") or ""),
                "content": str(item.get("content") or ""),
                "effect": "message_context",
                "reminder_type": reminder_type,
            }
            for item in memory_pack
        ]

    def _latest_unmatched_sleep_start(self) -> dict[str, Any] | None:
        row = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'sleep_start'
               ORDER BY occurred_at DESC, rowid DESC
               LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        end = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'sleep_end' AND occurred_at > ?
               ORDER BY occurred_at ASC LIMIT 1""",
            (row["occurred_at"],),
        ).fetchone()
        return None if end else self.repo._event_row_to_dict(row)

    def _backfill_next_sleep_end(self, sleep_start: dict[str, Any]) -> dict[str, Any] | None:
        row = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'sleep_end'
                 AND duration_minutes IS NULL
                 AND occurred_at > ?
               ORDER BY occurred_at ASC, rowid ASC
               LIMIT 1""",
            (sleep_start["occurred_at"],),
        ).fetchone()
        if not row:
            return None
        sleep_end = self.repo._event_row_to_dict(row)
        started_at = _parse_datetime(sleep_start["occurred_at"])
        ended_at = _parse_datetime(sleep_end["occurred_at"])
        if started_at.date() != ended_at.date():
            return None
        duration = _duration_minutes_between(started_at, ended_at)
        return self.repo.update_event(sleep_end["id"], duration_minutes=duration)

    def _sedentary_candidate(
        self,
        current: datetime,
        activity: ActivitySnapshot,
    ) -> dict[str, Any] | None:
        active_window = self._split_cross_midnight_sitting_window(current)
        if activity.locked or activity.logged_out:
            if active_window:
                break_at = self._mac_interruption_break_time(active_window, current, activity)
                if break_at:
                    source = activity.interruption_source or "mac"
                    resolution_source = f"mac_interrupted:{source}"
                    if self._should_supersede_sleep_like_overnight_split(active_window, resolution_source):
                        self._supersede_sitting_window(
                            active_window,
                            reason="sleep_like_overnight_split_window",
                            current=current,
                        )
                    else:
                        self._close_sitting_window(active_window, break_at, resolution_source)
            return None
        if activity.sleeping or activity.display_off:
            if active_window:
                break_at = self._mac_interruption_break_time(active_window, current, activity)
                if break_at:
                    source = activity.interruption_source or "mac"
                    resolution_source = f"mac_interrupted:{source}"
                    if self._should_supersede_sleep_like_overnight_split(active_window, resolution_source):
                        self._supersede_sitting_window(
                            active_window,
                            reason="sleep_like_overnight_split_window",
                            current=current,
                        )
                    else:
                        self._close_sitting_window(active_window, break_at, resolution_source)
                return None
        if activity.idle_seconds is None:
            return None
        if activity.idle_seconds > self.config.break_threshold_minutes * 60:
            if active_window:
                break_at = self._idle_break_time(active_window, current, activity.idle_seconds)
                if break_at:
                    self._close_sitting_window(active_window, break_at, "idle_break")
            return None

        if not active_window:
            self.repo.create_event(
                event_type="sedentary_start",
                occurred_at=current.isoformat(),
                source="mac_activity",
                status="active",
                payload={"idle_seconds": activity.idle_seconds},
            )
            return None

        started_at = _parse_datetime(active_window["occurred_at"])
        sitting_minutes = _duration_minutes_between(started_at, current)
        if sitting_minutes < self.config.sedentary_threshold_minutes:
            return None
        if self._is_sedentary_paused(current):
            return None
        if self._has_recent_reminder_log("sedentary", current, minutes=self.config.sedentary_repeat_minutes):
            return None
        retry_payload = self._short_retry_payload("sedentary", current)
        if retry_payload:
            return {
                "priority": 1,
                "reminder_type": "sedentary",
                "dedupe_key": f"sedentary:{current.strftime('%Y-%m-%dT%H:%M')}",
                "message": (
                    f"久坐警报：起来活动一下，离开电脑超过{self.config.break_threshold_minutes}分钟后我会停止提醒。"
                ),
                "payload": retry_payload,
            }

        return {
            "priority": 1,
            "reminder_type": "sedentary",
            "dedupe_key": f"sedentary:{current.strftime('%Y-%m-%dT%H:%M')}",
            "message": (
                f"久坐警报：起来活动一下，离开电脑超过{self.config.break_threshold_minutes}分钟后我会停止提醒。"
            ),
            "payload": {"retry_count": 0},
        }

    def _active_sitting_window(self) -> dict[str, Any] | None:
        row = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE event_type = 'sedentary_start' AND status = 'active'
               ORDER BY occurred_at DESC, rowid DESC
               LIMIT 1"""
        ).fetchone()
        return self.repo._event_row_to_dict(row) if row else None

    def _split_cross_midnight_sitting_window(self, current: datetime) -> dict[str, Any] | None:
        active_window = self._active_sitting_window()
        if not active_window:
            return None

        started_at = _parse_datetime(active_window["occurred_at"])
        while started_at.date() < current.date():
            split_at = _next_midnight(started_at)
            duration_minutes = _duration_minutes_between(started_at, split_at)
            self.repo.update_event(
                active_window["id"],
                ended_at=split_at.isoformat(),
                duration_minutes=duration_minutes,
                status="day_split",
            )
            active_window = self.repo.create_event(
                event_type="sedentary_start",
                occurred_at=split_at.isoformat(),
                source="mac_activity",
                status="active",
                payload={"split_from": active_window["id"]},
            )
            started_at = split_at
        return active_window

    def _is_sedentary_paused(self, current: datetime) -> bool:
        rows = self.repo.conn.execute(
            """SELECT scheduled_for FROM life_reminder_logs
               WHERE reminder_type = 'sedentary' AND status = 'paused'
               ORDER BY scheduled_for DESC"""
        ).fetchall()
        return any(_parse_datetime(row["scheduled_for"]) > current for row in rows)

    def _close_sitting_window(
        self,
        active_window: dict[str, Any],
        current: datetime,
        resolution_source: str,
    ) -> None:
        started_at = _parse_datetime(active_window["occurred_at"])
        if _seconds_between(started_at, current) < 0:
            return
        duration_minutes = _duration_minutes_between(started_at, current)
        self.repo.update_event(
            active_window["id"],
            ended_at=current.isoformat(),
            duration_minutes=duration_minutes,
            status="auto_resolved",
        )
        self.repo.create_event(
            event_type="sedentary_break",
            occurred_at=current.isoformat(),
            duration_minutes=duration_minutes,
            source="mac_activity",
            payload={
                "sitting_window_id": active_window["id"],
                "resolution_source": resolution_source,
            },
        )

    def _supersede_sitting_window(
        self,
        active_window: dict[str, Any],
        *,
        reason: str,
        current: datetime,
    ) -> None:
        payload = dict(active_window.get("payload_json") or {})
        payload.update(
            {
                "superseded_reason": reason,
                "superseded_at": current.isoformat(),
            }
        )
        self.repo.update_event(
            active_window["id"],
            ended_at=current.isoformat(),
            status="superseded",
            payload=payload,
        )

    def _should_supersede_sleep_like_overnight_split(
        self,
        active_window: dict[str, Any],
        resolution_source: str,
    ) -> bool:
        payload = active_window.get("payload_json") or {}
        if not payload.get("split_from"):
            return False
        try:
            started_at = _parse_datetime(str(active_window["occurred_at"]))
        except ValueError:
            return False
        if started_at.hour != 0 or started_at.minute != 0:
            return False
        return resolution_source in {"mac_interrupted:sleep", "mac_interrupted:display_off"}

    def _idle_break_time(
        self,
        active_window: dict[str, Any],
        current: datetime,
        idle_seconds: int,
    ) -> datetime | None:
        started_at = _parse_datetime(active_window["occurred_at"])
        if _seconds_between(started_at, current) < 0:
            return None
        break_at = current - timedelta(seconds=idle_seconds)
        break_at = break_at + timedelta(minutes=self.config.break_threshold_minutes)
        if _seconds_between(started_at, break_at) < 0:
            return started_at
        return break_at

    def _mac_interruption_break_time(
        self,
        active_window: dict[str, Any],
        current: datetime,
        activity: ActivitySnapshot,
    ) -> datetime | None:
        interruption_seconds = activity.interruption_seconds
        if interruption_seconds is None:
            return None
        if interruption_seconds < self.config.break_threshold_minutes * 60:
            return None
        started_at = _parse_datetime(active_window["occurred_at"])
        if _seconds_between(started_at, current) < 0:
            return None
        break_at = current - timedelta(seconds=interruption_seconds)
        if activity.interruption_started_at:
            try:
                break_at = _parse_datetime(activity.interruption_started_at)
            except ValueError:
                return None
        break_at = break_at + timedelta(minutes=self.config.break_threshold_minutes)
        if _seconds_between(started_at, break_at) <= 0:
            return None
        return break_at

    def _exercise_candidate(self, current: datetime) -> dict[str, Any] | None:
        if current.strftime("%H:%M") < self.config.exercise_first_time:
            return None
        if self._has_event_on_day("exercise", current):
            return None
        if self._has_reminder_log_on_day("exercise", "skipped", current):
            return None
        priority = 0 if self._has_sedentary_reminder_since_exercise_due(current) else 2
        retry_payload = self._short_retry_payload("exercise", current)
        if retry_payload:
            return {
                "priority": priority,
                "reminder_type": "exercise",
                "dedupe_key": f"exercise:{current.strftime('%Y-%m-%dT%H:%M')}",
                "message": "运动提醒：今天找个时间动一动吧。回复已运动、今天不练了，或告诉我具体时间。",
                "payload": retry_payload,
            }
        scheduled_log = self._latest_exercise_reminder_log(current)
        if scheduled_log:
            scheduled = _parse_datetime(scheduled_log["scheduled_for"])
            if current < scheduled:
                return None
            payload = scheduled_log.get("payload_json") or {}
            if scheduled_log.get("status") == "failed":
                next_repeat = scheduled + timedelta(hours=self.config.exercise_repeat_hours)
                if current < next_repeat:
                    return None
            if payload.get("source") != "user_reschedule":
                next_repeat = scheduled + timedelta(hours=self.config.exercise_repeat_hours)
                if current < next_repeat:
                    return None
        return {
            "priority": priority,
            "reminder_type": "exercise",
            "dedupe_key": f"exercise:{current.strftime('%Y-%m-%dT%H:%M')}",
            "message": "运动提醒：今天找个时间动一动吧。回复已运动、今天不练了，或告诉我具体时间。",
            "payload": {"retry_count": 0},
        }

    def _sleep_candidate(self, current: datetime) -> dict[str, Any] | None:
        due_at = _due_time_on_day(
            current,
            self.config.sleep_reminder_time,
            window_minutes=self.config.reminder_due_window_minutes,
        )
        if not due_at:
            return None
        dedupe_key = f"sleep:{due_at.strftime('%Y-%m-%d')}"
        if self._has_any_reminder_log(dedupe_key):
            return None
        return {
            "priority": 3,
            "reminder_type": "sleep",
            "dedupe_key": dedupe_key,
            "message": "睡眠提醒：差不多可以准备睡了。",
        }

    def _water_candidate(self, current: datetime) -> dict[str, Any] | None:
        due_at = None
        for water_time in self.config.water_times:
            due_at = _due_time_on_day(
                current,
                water_time,
                window_minutes=self.config.reminder_due_window_minutes,
            )
            if due_at:
                break
        if not due_at:
            return None
        if self._has_recent_event("water", current, minutes=60):
            return None
        dedupe_key = f"water:{due_at.strftime('%Y-%m-%dT%H:%M')}"
        if self._has_any_reminder_log(dedupe_key):
            return None
        return {
            "priority": 4,
            "reminder_type": "water",
            "dedupe_key": dedupe_key,
            "message": "喝水提醒：白天补点水。",
        }

    def _sleep_record_candidate(self, current: datetime) -> dict[str, Any] | None:
        due_at = _due_time_on_day(
            current,
            self.config.sleep_record_check_time,
            window_minutes=self.config.reminder_due_window_minutes,
        )
        if not due_at:
            return None
        wake_day = current.date().isoformat()
        if self._has_sleep_duration_for_wake_day(wake_day):
            return None
        message = "睡眠补记：昨晚大概几点睡、今天几点醒？我会帮你算睡眠时长。"
        if self._has_sleep_end_without_duration_for_wake_day(wake_day):
            message = "睡眠补记：已看到醒来记录，还缺睡觉时间。昨晚大概几点睡？"
        dedupe_key = f"sleep_record:{wake_day}"
        if self._has_any_reminder_log(dedupe_key):
            return None
        return {
            "priority": 7,
            "reminder_type": "sleep_record",
            "dedupe_key": dedupe_key,
            "message": message,
        }

    def _meal_record_candidate(self, current: datetime) -> dict[str, Any] | None:
        due_at = _due_time_on_day(
            current,
            self.config.meal_record_check_time,
            window_minutes=self.config.reminder_due_window_minutes,
        )
        if not due_at:
            return None
        meal_day = current.date().isoformat()
        if self._has_meal_on_date(meal_day):
            return None
        dedupe_key = f"meal_record:{meal_day}"
        if self._has_any_reminder_log(dedupe_key):
            return None
        return {
            "priority": 7,
            "reminder_type": "meal",
            "dedupe_key": dedupe_key,
            "message": "饮食补记：今天吃了什么？大概几点吃的？",
        }

    def _daily_review_candidate(self, current: datetime) -> dict[str, Any] | None:
        due_at = _due_time_on_day(
            current,
            "00:00",
            window_minutes=self.config.reminder_due_window_minutes,
        )
        if not due_at:
            return None
        review_date = (due_at - timedelta(days=1)).date().isoformat()
        if self.repo.get_daily_review(review_date):
            return None
        dedupe_key = f"daily_review:{review_date}"
        if self._has_any_reminder_log(dedupe_key):
            return None
        review = self._build_daily_review(review_date)
        return {
            "priority": 5,
            "reminder_type": "daily_review",
            "dedupe_key": dedupe_key,
            "message": f"每日轻复盘：{review['summary']}",
            "review": review,
        }

    def _weekly_review_candidate(self, current: datetime) -> dict[str, Any] | None:
        if current.weekday() != 6:
            return None
        due_at = _due_time_on_day(
            current,
            "21:30",
            window_minutes=self.config.reminder_due_window_minutes,
        )
        if not due_at:
            return None
        week_start, week_end = _week_bounds(current)
        if self.repo.get_weekly_review(week_start=week_start, week_end=week_end):
            return None
        dedupe_key = f"weekly_review:{week_start}:{week_end}"
        if self._has_any_reminder_log(dedupe_key):
            return None
        review = self._build_weekly_review(week_start, week_end)
        return {
            "priority": 6,
            "reminder_type": "weekly_review",
            "dedupe_key": dedupe_key,
            "message": f"周复盘：{review['report_text']}",
            "review": review,
        }

    def _long_term_decision_review_candidate(self, current: datetime) -> dict[str, Any] | None:
        decision = self.repo.due_long_term_decision(now=current.isoformat())
        if not decision:
            return None
        due_at = _parse_datetime(str(decision["next_review_at"]))
        dedupe_key = f"long_term_decision_review:{decision['id']}:{due_at.date().isoformat()}"
        if self._has_any_reminder_log(dedupe_key):
            return None
        return {
            "priority": 2,
            "reminder_type": "long_term_decision_review",
            "dedupe_key": dedupe_key,
            "message": (
                f"长期决定复盘：你之前决定“{decision['content']}”。"
                "现在是否还成立？可以回复“继续有效”“调整为：...”或“关闭这个决定”。"
            ),
            "payload": {"decision_id": decision["id"]},
        }

    def _build_daily_review(self, review_date: str) -> dict[str, Any]:
        metrics = self._daily_metrics(review_date)
        parts = [
            f"睡眠{_fmt_duration(metrics['sleep_minutes'])}" if metrics["sleep_minutes"] else "睡眠未记录",
            f"运动{metrics['exercise_count']}次" if metrics["exercise_count"] else "运动未记录",
            f"久坐{_fmt_duration(metrics['sedentary_over_limit_minutes'])}" if metrics["sedentary_over_limit_minutes"] else "久坐未超60分钟",
            f"饮食记录{metrics['meal_count']}条" if metrics["meal_count"] else "饮食未记录",
            f"喝水{metrics['water_count']}次" if metrics["water_count"] else "喝水未记录",
        ]
        if metrics["active_sitting_open"]:
            parts.append("当前还有未结束的坐姿窗口")
        return {
            "review_date": review_date,
            "summary": "；".join(parts) + "。",
            "metrics": metrics,
            "notes": {"generated_by": "life_agent_rules"},
        }

    def _build_weekly_review(self, week_start: str, week_end: str) -> dict[str, Any]:
        days = _date_range(week_start, week_end)
        daily_metrics = [self._daily_metrics(day) for day in days]
        exercise_days = sum(1 for item in daily_metrics if item["exercise_count"])
        total_sleep = sum(item["sleep_minutes"] for item in daily_metrics)
        sleep_days = sum(1 for item in daily_metrics if item["sleep_minutes"])
        total_sedentary = sum(item["sedentary_over_limit_minutes"] for item in daily_metrics)
        meal_days = sum(1 for item in daily_metrics if item["meal_count"])
        water_days = sum(1 for item in daily_metrics if item["water_count"])
        total_water = sum(item["water_count"] for item in daily_metrics)
        avg_sleep = round(total_sleep / sleep_days) if sleep_days else 0
        missing = {
            "sleep_days": 7 - sleep_days,
            "exercise_days": 7 - exercise_days,
            "meal_days": 7 - meal_days,
            "water_days": 7 - water_days,
        }
        memory_items = self._weekly_memory_items()
        labels = {
            "sleep": "有记录" if sleep_days else "缺记录",
            "exercise": "有运动" if exercise_days else "缺运动",
            "sedentary": "偏高" if total_sedentary >= 60 else "可控",
            "meals": "有记录" if meal_days else "缺记录",
            "water": "有记录" if water_days else "缺记录",
        }
        patterns = {
            "sleep_days": sleep_days,
            "exercise_days": exercise_days,
            "total_sedentary_minutes": total_sedentary,
            "meal_record_days": meal_days,
            "water_days": water_days,
            "water_count": total_water,
            "missing": missing,
            "memory_items": memory_items,
        }
        recommendations = self._weekly_recommendations(
            sleep_days=sleep_days,
            exercise_days=exercise_days,
            total_sedentary=total_sedentary,
            meal_days=meal_days,
            water_days=water_days,
        )
        evidence = self._weekly_evidence(week_start, week_end)
        headline = self._weekly_headline(
            sleep_days=sleep_days,
            exercise_days=exercise_days,
            total_sedentary=total_sedentary,
            meal_days=meal_days,
        )
        summary = (
            f"本周睡眠记录{sleep_days}天"
            f"{f'，平均{_fmt_duration(avg_sleep)}' if avg_sleep else ''}；"
            f"运动{exercise_days}天；久坐{_fmt_duration(total_sedentary)}；"
            f"饮食记录覆盖{meal_days}天。"
        )
        report_text = self._weekly_report_text(
            headline=headline,
            sleep_days=sleep_days,
            avg_sleep=avg_sleep,
            exercise_days=exercise_days,
            total_sedentary=total_sedentary,
            meal_days=meal_days,
            water_days=water_days,
            total_water=total_water,
            missing=missing,
            memory_items=memory_items,
            evidence=evidence,
            recommendations=recommendations,
        )
        strategy_memory = self._weekly_strategy_memory(
            week_start=week_start,
            week_end=week_end,
            recommendation=recommendations[0] if recommendations else "",
            sleep_days=sleep_days,
            exercise_days=exercise_days,
            total_sedentary=total_sedentary,
            meal_days=meal_days,
            water_days=water_days,
        )
        return {
            "week_start": week_start,
            "week_end": week_end,
            "summary": summary,
            "headline": headline,
            "report_text": report_text,
            "labels": labels,
            "patterns": patterns,
            "recommendations": recommendations,
            "evidence": evidence,
            "strategy_memory": strategy_memory,
            "delivery": {"wechat": "pending", "feishu": "pending"},
        }

    def _weekly_strategy_memory(
        self,
        *,
        week_start: str,
        week_end: str,
        recommendation: str,
        sleep_days: int,
        exercise_days: int,
        total_sedentary: int,
        meal_days: int,
        water_days: int,
    ) -> dict[str, Any]:
        if total_sedentary >= 180:
            domain = "life.sedentary"
            reminder_type = "sedentary"
        elif exercise_days < 3:
            domain = "life.exercise"
            reminder_type = "exercise"
        elif sleep_days <= 2:
            domain = "life.sleep"
            reminder_type = "sleep_record"
        elif meal_days <= 2:
            domain = "life.meal"
            reminder_type = "meal"
        elif water_days <= 2:
            domain = "life.water"
            reminder_type = "water"
        else:
            domain = "life.sedentary"
            reminder_type = "sedentary"
        start = f"{week_end}T21:30:00+08:00"
        expires_at = (_parse_datetime(f"{week_end}T23:59:59+08:00") + timedelta(days=7)).isoformat()
        suggestion = recommendation or "下周维持记录，并优先减少连续久坐。"
        return {
            "domain": domain,
            "reminder_type": reminder_type,
            "content": f"下周策略：{suggestion}",
            "effective_from": start,
            "expires_at": expires_at,
            "evidence": {
                "week_start": week_start,
                "week_end": week_end,
                "target_reminder_type": reminder_type,
                "sleep_days": sleep_days,
                "exercise_days": exercise_days,
                "total_sedentary_minutes": total_sedentary,
                "meal_days": meal_days,
                "water_days": water_days,
            },
        }

    def _weekly_headline(
        self,
        *,
        sleep_days: int,
        exercise_days: int,
        total_sedentary: int,
        meal_days: int,
    ) -> str:
        if total_sedentary >= 180:
            return "久坐压力偏高，优先减少连续坐姿"
        if sleep_days <= 2:
            return "睡眠记录偏少，先把作息数据补齐"
        if exercise_days == 0:
            return "运动缺口明显，需要固定一次活动"
        if meal_days <= 2:
            return "饮食记录偏少，先提高记录完整度"
        return "本周生活节律基本可控"

    def _weekly_recommendations(
        self,
        *,
        sleep_days: int,
        exercise_days: int,
        total_sedentary: int,
        meal_days: int,
        water_days: int,
    ) -> list[str]:
        if total_sedentary >= 180:
            return ["下周先把连续坐姿拆开，优先在久坐提醒后真实离座。"]
        if exercise_days < 3:
            return ["下周先固定一次最容易执行的运动，不追求强度。"]
        if sleep_days <= 2:
            return ["下周优先补齐睡眠记录，先看清作息节律。"]
        if meal_days <= 2:
            return ["下周先稳定记录饮食时间和内容，不急着评价吃得好不好。"]
        if water_days <= 2:
            return ["下周继续保持两次喝水记录，让白天补水更可见。"]
        return ["下周维持当前节律，优先减少连续久坐。"]

    def _weekly_memory_items(self) -> list[dict[str, Any]]:
        items = self.repo.list_memory_items(
            statuses=["active", "temporary", "weak"],
            domains=["life.sleep", "life.meal", "life.water", "life.sedentary", "life.copy"],
            limit=8,
        )
        return [item for item in items if _is_user_facing_memory_item(item) and not _is_strategy_memory_item(item)][:3]

    def _weekly_report_text(
        self,
        *,
        headline: str,
        sleep_days: int,
        avg_sleep: int,
        exercise_days: int,
        total_sedentary: int,
        meal_days: int,
        water_days: int,
        total_water: int,
        missing: dict[str, int],
        memory_items: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        recommendations: list[str],
    ) -> str:
        sleep_text = (
            f"记录{sleep_days}天，平均{_fmt_duration(avg_sleep)}，可初步观察作息节律"
            if sleep_days >= 3 and avg_sleep
            else f"只记录{sleep_days}天，证据不足，先不判断睡眠习惯"
        )
        meal_text = (
            f"饮食覆盖{meal_days}天，可以开始看时间和内容"
            if meal_days >= 3
            else f"饮食只覆盖{meal_days}天，先补齐记录再评价结构"
        )
        exercise_text = (
            f"运动{exercise_days}天，节奏偏少"
            if exercise_days < 3
            else f"运动{exercise_days}天，有延续性"
        )
        sedentary_text = (
            f"久坐超标{_fmt_duration(total_sedentary)}，连续坐姿是本周主要压力"
            if total_sedentary >= 180
            else f"久坐超标{_fmt_duration(total_sedentary)}，仍需留意连续坐姿"
            if total_sedentary
            else "未见超过60分钟窗口"
        )
        water_text = f"喝水{total_water}次，覆盖{water_days}天" if total_water else "喝水缺记录"
        missing_text = "、".join(
            text for text in [
                f"睡眠缺{missing['sleep_days']}天" if missing["sleep_days"] else "",
                f"运动缺{missing['exercise_days']}天" if missing["exercise_days"] else "",
                f"饮食缺{missing['meal_days']}天" if missing["meal_days"] else "",
                f"喝水缺{missing['water_days']}天" if missing["water_days"] else "",
            ]
            if text
        ) or "关键记录基本完整"
        memory_text = "；".join(str(item.get("content") or "") for item in memory_items[:2])
        evidence_text = "；".join(str(item.get("text") or "") for item in evidence[:2] if item.get("text"))
        suggestion_text = recommendations[0] if recommendations else "下周继续保持记录。"
        text = (
            f"本周总判断：{headline}。\n"
            f"睡眠：{sleep_text}。\n"
            f"饮食：{meal_text}；{water_text}。\n"
            f"运动：{exercise_text}。\n"
            f"久坐：{sedentary_text}。\n"
            f"{f'记忆：{memory_text}。' if memory_text else ''}"
            f"{f'证据：{evidence_text}。' if evidence_text else ''}\n"
            f"下周最重要建议：{suggestion_text}\n"
            f"需补记：{missing_text}。"
        )
        return text[:600]

    def _weekly_evidence(self, week_start: str, week_end: str) -> list[dict[str, Any]]:
        rows = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE status NOT IN ('undone', 'superseded')
               ORDER BY occurred_at ASC, rowid ASC"""
        ).fetchall()
        evidence: list[dict[str, Any]] = []
        for row in rows:
            event = self.repo._event_row_to_dict(row)
            if not event:
                continue
            event_day = _date_key(str(event.get("occurred_at") or ""))
            if not (week_start <= event_day <= week_end):
                continue
            if event["event_type"] == "sedentary_start" and int(event.get("duration_minutes") or 0) >= self.config.sedentary_threshold_minutes:
                evidence.append({
                    "type": "sedentary",
                    "date": event_day,
                    "text": f"{event_day[5:]} 久坐{_fmt_duration(int(event.get('duration_minutes') or 0))}",
                })
            elif event["event_type"] == "meal" and event.get("raw_text"):
                evidence.append({
                    "type": "meal",
                    "date": event_day,
                    "text": f"{event_day[5:]} {event['raw_text']}",
                })
            elif event["event_type"] == "exercise":
                payload = event.get("payload_json") or {}
                kind = str(payload.get("exercise_type") or "运动")
                evidence.append({
                    "type": "exercise",
                    "date": event_day,
                    "text": f"{event_day[5:]} {kind}{_fmt_duration(int(event.get('duration_minutes') or 0)) if event.get('duration_minutes') else ''}",
                })
            if len(evidence) >= 5:
                break
        return evidence

    def _daily_metrics(self, day: str) -> dict[str, Any]:
        events = self.repo.conn.execute(
            """SELECT * FROM life_events
               WHERE status NOT IN ('undone', 'superseded')
               ORDER BY occurred_at ASC, rowid ASC"""
        ).fetchall()
        sleep_minutes = 0
        exercise_count = 0
        exercise_minutes = 0
        meal_count = 0
        water_count = 0
        sitting_total = 0
        sedentary_over_limit = 0
        active_sitting_open = False
        for row in events:
            event = self.repo._event_row_to_dict(row)
            if not event:
                continue
            event_day = _date_key(str(event.get("occurred_at") or ""))
            payload = event.get("payload_json") or {}
            if event["event_type"] == "sleep_end":
                wake_date = str(payload.get("wake_date") or event_day)
                if wake_date == day:
                    sleep_minutes += int(event.get("duration_minutes") or 0)
            elif event_day == day and event["event_type"] == "exercise":
                exercise_count += 1
                exercise_minutes += int(event.get("duration_minutes") or 0)
            elif event_day == day and event["event_type"] == "meal":
                meal_count += 1
            elif event_day == day and event["event_type"] == "water":
                water_count += 1
            elif event_day == day and event["event_type"] == "sedentary_start":
                if event.get("status") == "active":
                    active_sitting_open = True
                    continue
                duration = int(event.get("duration_minutes") or 0)
                sitting_total += duration
                if duration >= self.config.sedentary_threshold_minutes:
                    sedentary_over_limit += duration
        return {
            "sleep_minutes": sleep_minutes,
            "exercise_count": exercise_count,
            "exercise_minutes": exercise_minutes,
            "meal_count": meal_count,
            "water_count": water_count,
            "sitting_total_minutes": sitting_total,
            "sedentary_over_limit_minutes": sedentary_over_limit,
            "active_sitting_open": active_sitting_open,
        }

    def _has_event_on_day(self, event_type: str, current: datetime) -> bool:
        rows = self.repo.conn.execute(
            "SELECT occurred_at FROM life_events WHERE event_type = ?",
            (event_type,),
        ).fetchall()
        return any(_parse_datetime(row["occurred_at"]).date() == current.date() for row in rows)

    def _has_sleep_duration_for_wake_day(self, wake_day: str) -> bool:
        rows = self.repo.conn.execute(
            """SELECT occurred_at, duration_minutes, payload_json FROM life_events
               WHERE event_type = 'sleep_end'
                 AND status NOT IN ('undone', 'superseded')"""
        ).fetchall()
        for row in rows:
            event = self.repo._event_row_to_dict(row)
            payload = event.get("payload_json") or {}
            event_day = str(payload.get("wake_date") or _date_key(str(event.get("occurred_at") or "")))
            if event_day == wake_day and int(event.get("duration_minutes") or 0) > 0:
                return True
        return False

    def _has_sleep_end_without_duration_for_wake_day(self, wake_day: str) -> bool:
        rows = self.repo.conn.execute(
            """SELECT occurred_at, duration_minutes, payload_json FROM life_events
               WHERE event_type = 'sleep_end'
                 AND status NOT IN ('undone', 'superseded')"""
        ).fetchall()
        for row in rows:
            event = self.repo._event_row_to_dict(row)
            payload = event.get("payload_json") or {}
            event_day = str(payload.get("wake_date") or _date_key(str(event.get("occurred_at") or "")))
            if event_day == wake_day and int(event.get("duration_minutes") or 0) <= 0:
                return True
        return False

    def _has_meal_on_date(self, meal_day: str) -> bool:
        rows = self.repo.conn.execute(
            """SELECT occurred_at FROM life_events
               WHERE event_type = 'meal'
                 AND status NOT IN ('undone', 'superseded')"""
        ).fetchall()
        return any(_date_key(str(row["occurred_at"])) == meal_day for row in rows)

    def _has_reminder_log_on_day(
        self,
        reminder_type: str,
        status: str,
        current: datetime,
    ) -> bool:
        rows = self.repo.conn.execute(
            """SELECT scheduled_for FROM life_reminder_logs
               WHERE reminder_type = ? AND status = ?""",
            (reminder_type, status),
        ).fetchall()
        return any(_parse_datetime(row["scheduled_for"]).date() == current.date() for row in rows)

    def _latest_exercise_reminder_log(self, current: datetime) -> dict[str, Any] | None:
        rows = self.repo.conn.execute(
            """SELECT * FROM life_reminder_logs
               WHERE reminder_type = 'exercise' AND status IN ('scheduled', 'sending', 'sent', 'failed')
               ORDER BY scheduled_for DESC, rowid DESC"""
        ).fetchall()
        for row in rows:
            scheduled = _parse_datetime(row["scheduled_for"])
            if scheduled.date() == current.date():
                return self.repo._reminder_row_to_dict(row)
        return None

    def _has_sedentary_reminder_since_exercise_due(self, current: datetime) -> bool:
        hour, minute = [int(part) for part in self.config.exercise_first_time.split(":", 1)]
        due_at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        rows = self.repo.conn.execute(
            """SELECT scheduled_for FROM life_reminder_logs
               WHERE reminder_type = 'sedentary'
                 AND status IN ('scheduled', 'sending', 'sent', 'failed')"""
        ).fetchall()
        for row in rows:
            scheduled = _parse_datetime(row["scheduled_for"])
            if due_at <= scheduled <= current:
                return True
        return False

    def _short_retry_payload(self, reminder_type: str, current: datetime) -> dict[str, Any] | None:
        row = self.repo.conn.execute(
            """SELECT * FROM life_reminder_logs
               WHERE reminder_type = ? AND status = 'failed'
               ORDER BY scheduled_for DESC, rowid DESC
               LIMIT 1""",
            (reminder_type,),
        ).fetchone()
        if not row:
            return None
        item = self.repo._reminder_row_to_dict(row)
        scheduled = _parse_datetime(item["scheduled_for"])
        if scheduled.date() != current.date():
            return None
        elapsed_seconds = _seconds_between(scheduled, current)
        if elapsed_seconds < 60 or elapsed_seconds > 5 * 60:
            return None
        retry_count = int((item.get("payload_json") or {}).get("retry_count") or 0)
        if retry_count >= 2:
            return None
        return {
            "retry_count": retry_count + 1,
            "retry_of": item["id"],
        }

    def _has_recent_event(
        self,
        event_type: str,
        current: datetime,
        *,
        minutes: int,
    ) -> bool:
        rows = self.repo.conn.execute(
            "SELECT occurred_at FROM life_events WHERE event_type = ?",
            (event_type,),
        ).fetchall()
        for row in rows:
            occurred = _parse_datetime(row["occurred_at"])
            elapsed_seconds = _seconds_between(occurred, current)
            if 0 <= elapsed_seconds <= minutes * 60:
                return True
        return False

    def _has_recent_reminder_log(
        self,
        reminder_type: str,
        current: datetime,
        *,
        minutes: int,
    ) -> bool:
        rows = self.repo.conn.execute(
            """SELECT scheduled_for FROM life_reminder_logs
               WHERE reminder_type = ? AND status IN ('scheduled', 'sending', 'sent', 'failed')""",
            (reminder_type,),
        ).fetchall()
        for row in rows:
            scheduled = _parse_datetime(row["scheduled_for"])
            elapsed_seconds = _seconds_between(scheduled, current)
            if 0 <= elapsed_seconds < minutes * 60:
                return True
        return False

    def _notification_rate_limited(self, current: datetime) -> bool:
        rows = self.repo.conn.execute(
            """SELECT scheduled_for, error FROM life_reminder_logs
               WHERE status = 'failed'
               ORDER BY scheduled_for DESC, rowid DESC"""
        ).fetchall()
        rate_limit_failures: list[datetime] = []
        for row in rows:
            error = str(row["error"] or "")
            if not _is_rate_limit_error(error):
                continue
            scheduled = _parse_datetime(row["scheduled_for"])
            if scheduled <= current:
                rate_limit_failures.append(scheduled)
        if not rate_limit_failures:
            return False
        latest = rate_limit_failures[0]
        cutoff = latest - timedelta(hours=6)
        recent_count = sum(1 for scheduled in rate_limit_failures if scheduled >= cutoff)
        cooldown_minutes = rate_limit_cooldown_minutes(
            recent_count,
            base_minutes=self.config.notification_rate_limit_cooldown_minutes,
            max_minutes=self.config.notification_rate_limit_max_cooldown_minutes,
        )
        elapsed_seconds = _seconds_between(latest, current)
        if 0 <= elapsed_seconds < cooldown_minutes * 60:
            return True
        return False

    def _has_active_reminder_log(self, dedupe_key: str) -> bool:
        row = self.repo.conn.execute(
            """SELECT id FROM life_reminder_logs
               WHERE dedupe_key = ? AND status IN ('scheduled', 'sending')
               LIMIT 1""",
            (dedupe_key,),
        ).fetchone()
        return row is not None

    def _has_any_reminder_log(self, dedupe_key: str) -> bool:
        row = self.repo.conn.execute(
            "SELECT id FROM life_reminder_logs WHERE dedupe_key = ? LIMIT 1",
            (dedupe_key,),
        ).fetchone()
        return row is not None

    def _has_fresh_pending_knowledge_choice(self, current: datetime) -> bool:
        row = self.repo.conn.execute(
            """SELECT created_at, expires_at FROM pending_questions
               WHERE status = 'pending' AND agent_type IN ('theme', 'garden')
               ORDER BY datetime(replace(substr(created_at, 1, 19), 'T', ' ')) DESC
               LIMIT 1"""
        ).fetchone()
        if not row:
            return False
        try:
            created_at = _parse_datetime(row["created_at"])
            expires_at = _parse_datetime(row["expires_at"])
        except ValueError:
            return False
        if current > expires_at:
            return False
        return 0 <= _seconds_between(created_at, current) <= 5 * 60


def _recorded(event: dict[str, Any], chat_preview: str) -> dict[str, Any]:
    return {"status": "recorded", "event": event, "chat_preview": chat_preview}


def _rejected(chat_preview: str) -> dict[str, Any]:
    return {"status": "rejected", "event": None, "chat_preview": chat_preview}


def _reminder_updated(chat_preview: str) -> dict[str, Any]:
    return {"status": "reminder_updated", "event": None, "chat_preview": chat_preview}


def _default_candidate_reason(reminder_type: str) -> str:
    return {
        "sedentary": "连续坐姿达到提醒条件",
        "long_term_decision_review": "长期决定到达复盘时间",
        "exercise": "今日运动提醒到达可触发窗口",
        "sleep": "早睡提醒到达设定时间",
        "water": "喝水提醒到达设定时间",
        "sleep_record": "睡眠记录缺失且到达补记时间",
        "meal": "饮食记录缺失且到达补记时间",
        "daily_review": "每日复盘到达生成时间",
        "weekly_review": "每周复盘到达生成时间",
    }.get(reminder_type, "候选提醒达到触发条件")


def _decision_reason(selected: dict[str, Any] | None, warnings: list[str]) -> str:
    if selected:
        reminder_type = str(selected.get("reminder_type") or "")
        return _default_candidate_reason(reminder_type)
    if "notification_rate_limited" in warnings:
        return "当前命中通知限流冷却，本次不发送提醒。"
    if "knowledge_workflow_active" in warnings:
        return "Knowledge 流程正在等待用户选择，本次 Life tick 不打扰。"
    if "life_tick_lock_held" in warnings:
        return "已有 Life tick 在运行，本次跳过。"
    return "本次没有符合条件的 Life 提醒。"


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _parse_datetime(value.strip())
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now().astimezone()


def _fmt_time(value: datetime) -> str:
    return value.strftime("%H:%M")


def _fmt_month_day_time(value: datetime) -> str:
    return value.strftime("%m-%d %H:%M")


def _fmt_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}小时{mins}分钟"
    if hours:
        return f"{hours}小时"
    return f"{mins}分钟"


def _duration_minutes_between(start: datetime, end: datetime) -> int:
    return max(0, round(_seconds_between(start, end) / 60))


def _days_between(start: datetime, end: datetime) -> int:
    return max(1, round(_seconds_between(start, end) / 86400))


def _date_anchor(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _append_text(existing: str, addition: str) -> str:
    existing = existing.strip()
    addition = addition.strip()
    if not existing:
        return addition
    if not addition or addition in existing:
        return existing
    return f"{existing}；{addition}"


def _next_midnight(value: datetime) -> datetime:
    return _date_anchor(value) + timedelta(days=1)


def _week_bounds(value: datetime) -> tuple[str, str]:
    week_start = value.date() - timedelta(days=value.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start.isoformat(), week_end.isoformat()


def _date_range(start: str, end: str) -> list[str]:
    start_date = datetime.fromisoformat(start).date()
    end_date = datetime.fromisoformat(end).date()
    days: list[str] = []
    current = start_date
    while current <= end_date:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _date_key(value: str) -> str:
    return _parse_datetime(value).date().isoformat()


def _seconds_between(start: datetime, end: datetime) -> float:
    start, end = _compatible_datetime_pair(start, end)
    return (end - start).total_seconds()


def _compatible_datetime_pair(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    if _is_aware(left) == _is_aware(right):
        return left, right
    return left.replace(tzinfo=None), right.replace(tzinfo=None)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _is_rate_limit_error(error: str) -> bool:
    normalized = error.lower().replace("_", " ").replace("-", " ")
    return "rate limit" in normalized or "too many requests" in normalized or "ret= 2" in normalized


def rate_limit_cooldown_minutes(
    failure_count: int,
    *,
    base_minutes: int = 120,
    max_minutes: int = 360,
) -> int:
    if failure_count <= 1:
        return base_minutes
    return min(max_minutes, base_minutes * (2 ** (failure_count - 1)))


def _due_time_on_day(current: datetime, time_text: str, *, window_minutes: int) -> datetime | None:
    hour, minute = [int(part) for part in time_text.split(":", 1)]
    due_at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due_at <= current <= due_at + timedelta(minutes=window_minutes):
        return due_at
    return None


def _meal_label(slot: str) -> str:
    return {
        "breakfast": "早餐",
        "lunch": "午餐",
        "dinner": "晚餐",
        "late_snack": "夜宵",
        "snack": "加餐",
    }.get(slot, "饮食")


def _meal_slot_from_text(text: str) -> str:
    for keyword, slot in {
        "早餐": "breakfast",
        "早饭": "breakfast",
        "午餐": "lunch",
        "午饭": "lunch",
        "晚餐": "dinner",
        "晚饭": "dinner",
        "夜宵": "late_snack",
        "加餐": "snack",
    }.items():
        if keyword in text:
            return slot
    return ""


def _disambiguate_meal_time(value: datetime, *, meal_slot: str, time_period: str) -> datetime:
    if time_period:
        return value
    if meal_slot == "dinner" and 1 <= value.hour <= 10:
        return value + timedelta(hours=12)
    if meal_slot == "lunch" and 1 <= value.hour <= 3:
        return value + timedelta(hours=12)
    return value


def _memory_domains_for_reminder(reminder_type: str) -> list[str]:
    if reminder_type == "exercise":
        return ["life.exercise", "life.copy"]
    if reminder_type in {"sleep", "sleep_record"}:
        return ["life.sleep", "life.copy"]
    if reminder_type == "meal":
        return ["life.meal", "life.copy"]
    if reminder_type == "water":
        return ["life.water", "life.copy"]
    if reminder_type == "sedentary":
        return ["life.sedentary", "life.copy"]
    if reminder_type == "long_term_decision_review":
        return ["life.decision", "life.copy"]
    if reminder_type in {"daily_review", "weekly_review"}:
        return ["life.sleep", "life.meal", "life.water", "life.sedentary", "life.exercise", "life.decision", "life.copy"]
    return ["life.copy"]


def _strategy_domains_for_reminder(reminder_type: str) -> list[str]:
    if reminder_type == "exercise":
        return ["life.exercise"]
    if reminder_type in {"sleep", "sleep_record"}:
        return ["life.sleep"]
    if reminder_type == "meal":
        return ["life.meal"]
    if reminder_type == "water":
        return ["life.water"]
    if reminder_type == "sedentary":
        return ["life.sedentary"]
    return []


def _memory_copy_text(memory_pack: list[dict[str, Any]]) -> str:
    for item in memory_pack:
        if not _is_user_facing_memory_item(item):
            continue
        domain = str(item.get("domain") or "")
        if domain == "life.copy":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            return content[:42]
    for item in memory_pack:
        if not _is_user_facing_memory_item(item):
            continue
        content = str(item.get("content") or "").strip()
        if content:
            return content[:42]
    return ""


def _is_user_facing_memory_item(item: dict[str, Any]) -> bool:
    if str(item.get("source") or "") == "reminder_feedback":
        return False
    return True


def _is_strategy_memory_item(item: dict[str, Any]) -> bool:
    return str(item.get("memory_type") or "") == "stage_goal" and str(item.get("source") or "") == "weekly_review"


def _memory_item_active_at(item: dict[str, Any], current: datetime) -> bool:
    effective_from = str(item.get("effective_from") or "")
    expires_at = str(item.get("expires_at") or "")
    try:
        if effective_from and _parse_datetime(effective_from) > current:
            return False
        if expires_at and _parse_datetime(expires_at) < current:
            return False
    except ValueError:
        return False
    return True

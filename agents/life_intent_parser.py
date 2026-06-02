"""Structured intent parsing for Life Assistant messages."""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable


@dataclass(frozen=True)
class LifeIntent:
    intent: str
    event_type: str = ""
    occurred_at: str = ""
    duration_minutes: int | None = None
    status: str = ""
    scheduled_for: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class LifeIntentParser:
    def __init__(
        self,
        llm_fallback: Callable[[str, datetime], LifeIntent | None] | None = None,
        *,
        prefer_llm_for_life_records: bool = False,
    ):
        self.llm_fallback = llm_fallback
        self.prefer_llm_for_life_records = prefer_llm_for_life_records

    def parse(self, message: str, *, received_at: datetime) -> LifeIntent:
        text = message.strip()
        if _looks_like_long_term_decision(text) and self.llm_fallback is not None:
            fallback = self.llm_fallback(text, received_at)
            if fallback and fallback.intent != "unknown":
                return fallback
        intent = self._parse_deterministic(text, received_at)
        if self._should_prefer_llm_for_life_record(text, intent):
            fallback = self.llm_fallback(text, received_at)
            if fallback and fallback.intent != "unknown":
                return fallback
        if intent.intent != "unknown" or self.llm_fallback is None:
            return intent
        fallback = self.llm_fallback(text, received_at)
        return fallback or intent

    def _should_prefer_llm_for_life_record(self, text: str, intent: LifeIntent) -> bool:
        return bool(
            self.prefer_llm_for_life_records
            and self.llm_fallback is not None
            and _looks_like_life_record(text)
            and not _is_high_confidence_intent(intent)
        )

    def _parse_deterministic(self, text: str, received_at: datetime) -> LifeIntent:
        decision = _parse_explicit_long_term_decision(text)
        if decision:
            return LifeIntent(
                intent="record_long_term_decision",
                payload={"content": decision},
            )

        decision_reply = _parse_long_term_decision_reply(text, received_at)
        if decision_reply:
            return decision_reply

        review_time = _parse_long_term_review_time_update(text, received_at)
        if review_time:
            return LifeIntent(
                intent="set_long_term_decision_review_time",
                payload={"next_review_at": review_time.isoformat()},
            )

        if _is_health_consultation(text):
            return LifeIntent(intent="health_consultation")

        if _is_manual_status_query(text):
            return LifeIntent(intent="manual_status_query")

        modification = _parse_latest_modification(text)
        if modification:
            return LifeIntent(intent="modify_latest", payload={"change_text": modification})

        sedentary_pause = _parse_sedentary_pause(text, received_at)
        if sedentary_pause:
            return LifeIntent(
                intent="pause_sedentary",
                scheduled_for=sedentary_pause["pause_until"].isoformat(),
                duration_minutes=sedentary_pause["duration_minutes"],
            )

        if text == "今天不练了":
            return LifeIntent(intent="skip_exercise")

        exercise_schedule = _parse_exercise_schedule(text, received_at)
        if exercise_schedule:
            return LifeIntent(
                intent="reschedule_exercise",
                scheduled_for=exercise_schedule["scheduled_for"].isoformat(),
                payload=exercise_schedule.get("payload", {}),
            )

        if text == "撤销上一条":
            return LifeIntent(intent="undo_latest")

        if text in {"已活动", "活动了", "起来活动了"}:
            return LifeIntent(intent="sedentary_activity_reply")

        meal_time_update = _parse_meal_time_update(text, received_at)
        if meal_time_update:
            payload = {
                "change_text": text,
                "meal_occurred_at": meal_time_update["occurred_at"].isoformat(),
                "meal_time_period": meal_time_update.get("time_period", ""),
            }
            if meal_time_update.get("meal_slot"):
                payload["meal_slot"] = meal_time_update["meal_slot"]
            if meal_time_update.get("explicit_change"):
                payload["explicit_change"] = True
            return LifeIntent(
                intent="modify_latest",
                payload=payload,
            )

        sleep_session = _parse_sleep_session(text, received_at)
        if sleep_session:
            return LifeIntent(
                intent="record_event",
                event_type="sleep_session",
                occurred_at=sleep_session["started_at"].isoformat(),
                payload={"ended_at": sleep_session["ended_at"].isoformat()},
            )

        if text in {"我睡了", "睡了", "准备睡了"}:
            return LifeIntent(
                intent="record_event",
                event_type="sleep_start",
                occurred_at=received_at.isoformat(),
            )

        sleep_start_time = _parse_sleep_start_time(text, received_at)
        if sleep_start_time:
            return LifeIntent(
                intent="record_event",
                event_type="sleep_start",
                occurred_at=sleep_start_time.isoformat(),
            )

        wake_time = _parse_wake_time(text, received_at)
        if text in {"我醒了", "醒了", "起床了"} or wake_time:
            occurred = wake_time or received_at
            return LifeIntent(
                intent="record_event",
                event_type="sleep_end",
                occurred_at=occurred.isoformat(),
                payload={"wake_date": occurred.date().isoformat()},
            )

        if text in {"已运动", "运动了"}:
            return LifeIntent(
                intent="record_event",
                event_type="exercise",
                occurred_at=received_at.isoformat(),
                status="completed_unknown",
                payload={"completion": "completed_unknown"},
            )

        exercise = _parse_exercise(text)
        if exercise:
            return LifeIntent(
                intent="record_event",
                event_type="exercise",
                occurred_at=received_at.isoformat(),
                duration_minutes=exercise["duration_minutes"],
                payload={"exercise_type": exercise["exercise_type"]},
            )

        meal = _parse_meal(text, received_at)
        if meal:
            occurred_at = str(meal.pop("occurred_at", ""))
            return LifeIntent(
                intent="record_event",
                event_type="meal",
                occurred_at=occurred_at,
                payload=meal,
            )

        if _is_water(text):
            return LifeIntent(
                intent="record_event",
                event_type="water",
                occurred_at=received_at.isoformat(),
            )

        return LifeIntent(intent="unknown")


class LifeIntentLLMFallback:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 10,
        post_json: Any = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.post_json = post_json or _post_json

    def __call__(self, text: str, received_at: datetime) -> LifeIntent | None:
        if not self.api_key:
            return None
        try:
            response = self.post_json(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "message": text,
                                    "received_at": received_at.isoformat(),
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    "temperature": 0,
                    "max_tokens": 500,
                },
                timeout=self.timeout_seconds,
            )
            content = response["choices"][0]["message"]["content"]
            parsed = _loads_json_object(content)
            return _intent_from_mapping(parsed)
        except Exception:
            return None


_LLM_SYSTEM_PROMPT = """你是 Life Assistant 的意图解析器，只能输出 JSON 对象。
把用户消息解析为:
intent: record_event | record_long_term_decision | set_long_term_decision_review_time | review_long_term_decision | close_long_term_decision | skip_exercise | reschedule_exercise | pause_sedentary | undo_latest | modify_latest | manual_status_query | health_consultation | unknown
event_type: sleep_start | sleep_end | exercise | meal | water | ""
occurred_at/scheduled_for: ISO 8601 字符串；没有就空字符串。
duration_minutes: 整数或 null。
status: completed_unknown 或空字符串。
payload: 对象。meal 使用 meal_slot/content；exercise 使用 exercise_type。
你要做语义判断，不要只按关键词机械分类；如果明显不是生活记录或提醒/复盘控制，输出 unknown。
不要臆造发生时间。饮食只有餐别或内容、没有明确几点时，occurred_at 留空，payload.meal_time_precision="missing"。
长期决定/长期决策用 record_long_term_decision，payload 至少包含 content；如果用户指定下次复盘时间，额外给 next_review_at。不要默认 30 天。
用户只是在补充长期决定的下次复盘时间时，用 set_long_term_decision_review_time，payload 包含 next_review_at。
长期决定复盘回复用 review_long_term_decision，payload 包含 review_note；如果用户调整决定，额外给 new_content；如果用户指定下一次复盘时间，额外给 next_review_at。用户明确结束/关闭/废弃/完成这个长期决定时用 close_long_term_decision；完成时 payload.outcome="completed"，废弃/放弃时 payload.outcome="abandoned"。
water 只表示明确记录补水或回应喝水提醒；带具体饮品名称、口味、咖啡因、酒精、汤羹，或跟一餐连续出现的“还/又/另外喝了”，优先解析为 meal 的饮品内容。
不要输出解释。不要把久坐提醒解析成可由回复解除的意图。"""


def _loads_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM parser response must be a JSON object")
    return parsed


def _intent_from_mapping(data: dict[str, Any]) -> LifeIntent:
    intent = str(data.get("intent") or "unknown")
    event_type = str(data.get("event_type") or "")
    duration = data.get("duration_minutes")
    if not isinstance(duration, int):
        duration = None
    payload = data.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    return LifeIntent(
        intent=intent,
        event_type=event_type,
        occurred_at=str(data.get("occurred_at") or ""),
        duration_minutes=duration,
        status=str(data.get("status") or ""),
        scheduled_for=str(data.get("scheduled_for") or ""),
        payload=payload,
    )


def _is_high_confidence_intent(intent: LifeIntent) -> bool:
    if intent.intent == "unknown":
        return False
    if intent.intent != "record_event":
        return True
    if intent.event_type in {"sleep_start", "sleep_end", "sleep_session", "water"}:
        return True
    if intent.event_type == "exercise":
        return bool(intent.duration_minutes or intent.status == "completed_unknown")
    if intent.event_type != "meal":
        return False
    precision = str(intent.payload.get("meal_time_precision") or "")
    return bool(
        precision == "exact"
        and not intent.payload.get("meal_slot_inferred")
        and not intent.payload.get("meal_update_mode")
    )


def _looks_like_life_record(text: str) -> bool:
    if not text:
        return False
    markers = (
        "睡",
        "醒",
        "起床",
        "吃",
        "喝",
        "饮",
        "运动",
        "跑步",
        "健身",
        "快走",
        "散步",
        "骑车",
        "游泳",
        "训练",
        "八段锦",
        "练",
        "早餐",
        "早饭",
        "午餐",
        "午饭",
        "晚餐",
        "晚饭",
        "夜宵",
        "加餐",
        "补了",
        "补记",
    )
    return any(marker in text for marker in markers)


def _post_json(url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float) -> dict[str, Any]:
    import httpx

    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json=json)
        response.raise_for_status()
        return response.json()


def _parse_exercise(text: str) -> dict[str, Any] | None:
    match = re.search(r"([\u4e00-\u9fa5A-Za-z]{1,12})\s*(\d+)\s*(分钟|分|小时)", text)
    if not match:
        return None
    exercise_type = match.group(1).strip()
    exercise_markers = ("跑步", "健身", "快走", "散步", "骑车", "游泳", "训练", "运动", "八段锦")
    if not any(marker in exercise_type for marker in exercise_markers):
        return None
    duration = int(match.group(2))
    if match.group(3) == "小时":
        duration *= 60
    return {"exercise_type": exercise_type, "duration_minutes": duration}


def _parse_explicit_long_term_decision(text: str) -> str:
    match = re.match(r"^(长期决定|长期决策|记一个长期决定|记一个长期决策)\s*[：:]\s*(.+)$", text)
    return match.group(2).strip() if match else ""


def _looks_like_long_term_decision(text: str) -> bool:
    return bool(re.match(r"^(长期决定|长期决策|记一个长期决定|记一个长期决策)\s*[：:]", text))


def _parse_long_term_decision_reply(text: str, received: datetime) -> LifeIntent | None:
    if text == "继续有效" or text.startswith("继续有效"):
        payload: dict[str, Any] = {"review_note": text}
        next_review_at = _parse_long_term_review_time_update(text, received)
        if next_review_at:
            payload["next_review_at"] = next_review_at.isoformat()
        return LifeIntent(
            intent="review_long_term_decision",
            payload=payload,
        )
    adjust = re.match(r"^调整为\s*[：:]\s*(.+)$", text)
    if adjust:
        content = adjust.group(1).strip()
        next_review_at = _parse_long_term_review_time_update(text, received)
        payload = {"review_note": text, "new_content": content}
        if next_review_at:
            payload["next_review_at"] = next_review_at.isoformat()
        return LifeIntent(
            intent="review_long_term_decision",
            payload=payload,
        )
    if text == "关闭这个决定":
        return LifeIntent(intent="close_long_term_decision", payload={"review_note": text})
    if "决定" in text and any(keyword in text for keyword in ("完成", "做完", "达成")):
        return LifeIntent(
            intent="close_long_term_decision",
            payload={"review_note": text, "outcome": "completed"},
        )
    if "决定" in text and any(keyword in text for keyword in ("放弃", "废弃", "不做了")):
        return LifeIntent(
            intent="close_long_term_decision",
            payload={"review_note": text, "outcome": "abandoned"},
        )
    review = re.match(r"^复盘\s*[：:]\s*(.+)$", text)
    if review:
        return LifeIntent(
            intent="review_long_term_decision",
            payload={"review_note": review.group(1).strip()},
        )
    return None


def _parse_long_term_review_time_update(text: str, received: datetime) -> datetime | None:
    if "复盘" not in text:
        return None
    parsed_time = _parse_clock_time(text, received)
    if not parsed_time:
        return None

    explicit_year = re.search(
        r"([0-9]{4})\s*年\s*([0-9一二两三四五六七八九十]{1,3})\s*月\s*([0-9一二两三四五六七八九十]{1,3})\s*(号|日)",
        text,
    )
    if explicit_year:
        year = int(explicit_year.group(1))
        month = _parse_number(explicit_year.group(2))
        day = _parse_number(explicit_year.group(3))
        return _replace_date(parsed_time, year=year, month=month, day=day)

    explicit_month = re.search(
        r"([0-9一二两三四五六七八九十]{1,3})\s*月\s*([0-9一二两三四五六七八九十]{1,3})\s*(号|日)",
        text,
    )
    if explicit_month:
        month = _parse_number(explicit_month.group(1))
        day = _parse_number(explicit_month.group(2))
        candidate = _replace_date(parsed_time, year=received.year, month=month, day=day)
        if candidate and candidate <= received:
            candidate = _replace_date(parsed_time, year=received.year + 1, month=month, day=day)
        return candidate

    next_month = re.search(
        r"下个月\s*([0-9一二两三四五六七八九十]{1,3})\s*(号|日)",
        text,
    )
    if next_month:
        day = _parse_number(next_month.group(1))
        year = received.year + (1 if received.month == 12 else 0)
        month = 1 if received.month == 12 else received.month + 1
        return _replace_date(parsed_time, year=year, month=month, day=day)

    return parsed_time if parsed_time > received else None


def _replace_date(base: datetime, *, year: int, month: int | None, day: int | None) -> datetime | None:
    if month is None or day is None:
        return None
    try:
        return base.replace(year=year, month=month, day=day)
    except ValueError:
        return None


def _parse_exercise_schedule(text: str, received: datetime) -> dict[str, Any] | None:
    is_reminder_request = "提醒我运动" in text
    exercise_markers = ("运动", "跑步", "健身", "快走", "散步", "骑车", "游泳", "练", "训练")
    has_exercise_marker = any(keyword in text for keyword in exercise_markers)
    is_planned_exercise = any(keyword in text for keyword in ("打算", "计划", "准备")) and has_exercise_marker
    scheduled_for = _parse_clock_time(text, received)
    is_timed_exercise = has_exercise_marker and scheduled_for is not None
    if not is_reminder_request and not is_planned_exercise and not is_timed_exercise:
        return None
    if not scheduled_for:
        return None
    payload: dict[str, Any] = {}
    exercise_type = _parse_planned_exercise_type(text)
    if exercise_type:
        payload["exercise_type"] = exercise_type
    return {"scheduled_for": scheduled_for, "payload": payload}


def _parse_planned_exercise_type(text: str) -> str:
    match = re.search(r"练\s*([^。，,.！!\s]+)", text)
    return match.group(1) if match else ""


def _parse_sedentary_pause(text: str, received: datetime) -> dict[str, Any] | None:
    if "暂停久坐警报" not in text:
        return None
    match = re.search(r"([0-9一二两三四五六七八九十]{1,3})\s*(小时|分钟|分)", text)
    if match:
        amount = _parse_number(match.group(1))
        if amount is None:
            return None
        duration = amount * 60 if match.group(2) == "小时" else amount
    elif "今天" in text:
        end_of_day = received.replace(hour=23, minute=59, second=0, microsecond=0)
        duration = max(1, round((end_of_day - received).total_seconds() / 60))
        return {"pause_until": end_of_day, "duration_minutes": duration}
    else:
        return None
    return {
        "pause_until": received + timedelta(minutes=duration),
        "duration_minutes": duration,
    }


def _parse_latest_modification(text: str) -> str:
    match = re.match(r"^上一条改成\s*(.+)$", text)
    return match.group(1).strip() if match else ""


def _parse_meal_time_update(text: str, received: datetime) -> dict[str, Any] | None:
    parsed = _parse_clock_time(text, received)
    if not parsed:
        return None
    explicit_change = any(keyword in text for keyword in ("改成", "改到", "改为", "不是"))
    cleaned = _strip_meal_context(_strip_clock_time(text))
    cleaned = re.sub(r"^(时间是|时间|时间改成|时间改到|时间改为|大概|差不多|应该是|是|在)\s*", "", cleaned).strip()
    cleaned = re.sub(r"(时间是|时间|吃的|吃了|吃|的时候|左右|吧|是|。|，|,|\.)+$", "", cleaned).strip()
    cleaned = re.sub(r"^(改成|改到|改为)\s*", "", cleaned).strip()
    if cleaned:
        return None
    return {
        "occurred_at": parsed,
        "meal_slot": _explicit_meal_slot(text),
        "time_period": _clock_time_period(text),
        "explicit_change": explicit_change,
    }


def _parse_sleep_start_time(text: str, received: datetime) -> datetime | None:
    if "睡" not in text or "睡到" in text:
        return None
    parsed = _parse_clock_time(text, received)
    if parsed and parsed > received + timedelta(minutes=5):
        parsed = parsed - timedelta(days=1)
    return parsed


def _parse_sleep_session(text: str, received: datetime) -> dict[str, datetime] | None:
    if "睡" not in text or not any(keyword in text for keyword in ("醒", "起床")):
        return None
    sleep_index = text.find("睡")
    start = _parse_clock_time(text[: sleep_index + 1], received)
    end = _parse_clock_time(text[sleep_index + 1 :], received)
    if not start or not end:
        return None
    if start > end:
        start = start - timedelta(days=1)
    return {"started_at": start, "ended_at": end}


def _parse_wake_time(text: str, received: datetime) -> datetime | None:
    if not any(keyword in text for keyword in ("醒", "起床", "睡到")):
        return None
    parsed = _parse_clock_time(text, received)
    if parsed and parsed > received + timedelta(minutes=5):
        parsed = parsed - timedelta(days=1)
    return parsed


def _parse_clock_time(text: str, received: datetime) -> datetime | None:
    match = re.search(
        r"(凌晨|早上|上午|中午|下午|晚上|今晚)?\s*([0-9一二两三四五六七八九十]{1,3})\s*点\s*(半|([0-9一二两三四五六七八九十]{1,3})\s*分?)?",
        text,
    )
    if not match:
        return None
    period = match.group(1) or ""
    hour = _parse_number(match.group(2))
    minute = 30 if match.group(3) == "半" else _parse_number(match.group(4) or "0")
    if hour is None or minute is None:
        return None
    if period in {"下午", "晚上", "今晚"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if period in {"凌晨", "早上", "上午"} and hour == 12:
        hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return received.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _clock_time_period(text: str) -> str:
    match = re.search(r"(凌晨|早上|上午|中午|下午|晚上|今晚)?\s*[0-9一二两三四五六七八九十]{1,3}\s*点", text)
    return match.group(1) or "" if match else ""


def _parse_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if text in digits:
        return digits[text]
    if text == "十":
        return 10
    if text.startswith("十") and len(text) == 2 and text[1] in digits:
        return 10 + digits[text[1]]
    if text.endswith("十") and len(text) == 2 and text[0] in digits:
        return digits[text[0]] * 10
    if "十" in text:
        left, right = text.split("十", 1)
        if left in digits and right in digits:
            return digits[left] * 10 + digits[right]
    return None


def _parse_meal(text: str, received: datetime) -> dict[str, Any] | None:
    meal_context_slots = {
        "早上": "breakfast",
        "上午": "breakfast",
        "中午": "lunch",
        "下午": "snack",
        "晚上": "dinner",
    }
    slot = _explicit_meal_slot(text)
    if not slot:
        for keyword, value in meal_context_slots.items():
            if keyword in text and ("吃" in text or "喝" in text):
                slot = value
                break
    has_drink_verb = "喝" in text or "饮" in text
    if _is_water(text):
        return None
    if "吃" not in text and not has_drink_verb and not slot:
        return None
    has_explicit_slot = bool(slot)
    occurred_at = _parse_clock_time(text, received)
    time_period = _clock_time_period(text)
    meal_time_precision = "exact" if occurred_at else "missing"
    if not occurred_at and any(keyword in text for keyword in ("刚", "刚刚", "才")):
        occurred_at = received
        meal_time_precision = "inferred_current"
    if not occurred_at and not has_explicit_slot:
        occurred_at = received
        meal_time_precision = "inferred_current"
    if occurred_at and slot:
        occurred_at = _disambiguate_meal_time(
            occurred_at,
            meal_slot=slot,
            time_period=time_period,
        )
    meal_text = _strip_meal_context(_strip_clock_time(text))
    append_to_existing = bool(re.match(r"^(还|又|另外|再|也)\s*", meal_text))
    meal_text = re.sub(r"^(还|又|另外|再|也)\s*", "", meal_text).strip()
    meal_item_kind = "beverage" if has_drink_verb and "吃" not in meal_text else ""
    content = re.sub(r"^(吃了?|喝了?|饮了?)\s*", "", meal_text).strip()
    content = re.sub(r"^一\s*(杯|瓶|碗|口)\s*", "", content).strip()
    content = _clean_meal_content(content)
    if not content:
        return None
    inferred = False
    if not slot:
        slot = _infer_meal_slot(received)
        inferred = True
    result: dict[str, Any] = {
        "meal_slot": slot,
        "meal_slot_inferred": inferred,
        "content": content,
        "meal_time_precision": meal_time_precision,
    }
    if meal_item_kind:
        result["meal_item_kind"] = meal_item_kind
    if append_to_existing:
        result["meal_update_mode"] = "append"
    if occurred_at:
        result["occurred_at"] = occurred_at.isoformat()
    return result


def _strip_clock_time(text: str) -> str:
    return re.sub(
        r"(凌晨|早上|上午|中午|下午|晚上)?\s*[0-9一二两三四五六七八九十]{1,3}\s*点\s*(半|[0-9一二两三四五六七八九十]{1,3}\s*分?)?",
        "",
        text,
        count=1,
    ).strip()


def _strip_meal_context(text: str) -> str:
    return re.sub(
        r"^(早饭|早餐|午饭|午餐|晚饭|晚餐|夜宵|加餐|早上|上午|中午|下午|晚上)\s*",
        "",
        text,
        count=1,
    ).strip()


def _clean_meal_content(text: str) -> str:
    return re.sub(
        r"[，,。.\s]*(大概|差不多|左右|前后)?\s*(吃的|吃了|吃|喝的|喝了|喝|饮的|饮了|饮)$",
        "",
        text,
    ).strip()


def _disambiguate_meal_time(value: datetime, *, meal_slot: str, time_period: str) -> datetime:
    if time_period:
        return value
    if meal_slot == "dinner" and 1 <= value.hour <= 10:
        return value + timedelta(hours=12)
    if meal_slot == "lunch" and 1 <= value.hour <= 3:
        return value + timedelta(hours=12)
    return value


def _explicit_meal_slot(text: str) -> str:
    explicit_slots = {
        "早饭": "breakfast",
        "早餐": "breakfast",
        "午饭": "lunch",
        "午餐": "lunch",
        "晚饭": "dinner",
        "晚餐": "dinner",
        "夜宵": "late_snack",
        "加餐": "snack",
    }
    for keyword, value in explicit_slots.items():
        if keyword in text:
            return value
    return ""


def _infer_meal_slot(received: datetime) -> str:
    minutes = received.hour * 60 + received.minute
    if 5 * 60 <= minutes < 10 * 60 + 30:
        return "breakfast"
    if 10 * 60 + 30 <= minutes < 15 * 60:
        return "lunch"
    if 17 * 60 <= minutes < 22 * 60:
        return "dinner"
    if minutes >= 22 * 60 or minutes < 5 * 60:
        return "late_snack"
    return "snack"


def _is_water(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.strip())
    if normalized in {"喝水", "喝水了", "喝了水", "饮水"}:
        return True
    return bool(re.fullmatch(r"(刚刚?|才)?(喝|饮)了?(一?(杯|瓶|口))?(白开水|矿泉水|温水|凉水|水)", normalized))


def _is_manual_status_query(text: str) -> bool:
    return any(keyword in text for keyword in ("坐了多久", "状态怎么样", "今天状态"))


def _is_health_consultation(text: str) -> bool:
    if not (text.startswith("健康：") or text.startswith("健康:")):
        return False
    return any(keyword in text for keyword in ("身体", "问题", "病", "疼", "不舒服", "睡不好", "是不是"))

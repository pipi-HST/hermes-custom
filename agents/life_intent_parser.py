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
    ):
        self.llm_fallback = llm_fallback

    def parse(self, message: str, *, received_at: datetime) -> LifeIntent:
        text = message.strip()
        intent = self._parse_deterministic(text, received_at)
        if intent.intent != "unknown" or self.llm_fallback is None:
            return intent
        fallback = self.llm_fallback(text, received_at)
        return fallback or intent

    def _parse_deterministic(self, text: str, received_at: datetime) -> LifeIntent:
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
            return LifeIntent(
                intent="record_event",
                event_type="meal",
                occurred_at=received_at.isoformat(),
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
intent: record_event | skip_exercise | reschedule_exercise | pause_sedentary | undo_latest | modify_latest | manual_status_query | health_consultation | unknown
event_type: sleep_start | sleep_end | exercise | meal | water | ""
occurred_at/scheduled_for: ISO 8601 字符串；没有就空字符串。
duration_minutes: 整数或 null。
status: completed_unknown 或空字符串。
payload: 对象。meal 使用 meal_slot/content；exercise 使用 exercise_type。
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


def _post_json(url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float) -> dict[str, Any]:
    import httpx

    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json=json)
        response.raise_for_status()
        return response.json()


def _parse_exercise(text: str) -> dict[str, Any] | None:
    match = re.search(r"(跑步|健身|快走|散步|骑车|游泳|训练|运动|练)\s*(\d+)\s*(分钟|分|小时)", text)
    if not match:
        return None
    duration = int(match.group(2))
    if match.group(3) == "小时":
        duration *= 60
    return {"exercise_type": match.group(1), "duration_minutes": duration}


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
        r"(凌晨|早上|上午|中午|下午|晚上)?\s*([0-9一二两三四五六七八九十]{1,3})\s*点\s*(半|([0-9一二两三四五六七八九十]{1,3})\s*分?)?",
        text,
    )
    if not match:
        return None
    period = match.group(1) or ""
    hour = _parse_number(match.group(2))
    minute = 30 if match.group(3) == "半" else _parse_number(match.group(4) or "0")
    if hour is None or minute is None:
        return None
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if period in {"凌晨", "早上", "上午"} and hour == 12:
        hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return received.replace(hour=hour, minute=minute, second=0, microsecond=0)


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
    slot = ""
    for keyword, value in explicit_slots.items():
        if keyword in text:
            slot = value
            break
    if "吃" not in text and not slot:
        return None
    content = re.sub(r"^(早饭|早餐|午饭|午餐|晚饭|晚餐|夜宵|加餐)?\s*吃了?", "", text).strip()
    if not content:
        return None
    inferred = False
    if not slot:
        slot = _infer_meal_slot(received)
        inferred = True
    return {"meal_slot": slot, "meal_slot_inferred": inferred, "content": content}


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
    return text in {"喝水", "喝水了", "喝了水", "饮水"}


def _is_manual_status_query(text: str) -> bool:
    return any(keyword in text for keyword in ("坐了多久", "状态怎么样", "今天状态"))


def _is_health_consultation(text: str) -> bool:
    if not (text.startswith("健康：") or text.startswith("健康:")):
        return False
    return any(keyword in text for keyword in ("身体", "问题", "病", "疼", "不舒服", "睡不好", "是不是"))

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
        if _is_manual_status_query(text):
            return LifeIntent(intent="manual_status_query")

        if text == "今天不练了":
            return LifeIntent(intent="skip_exercise")

        scheduled_for = _parse_exercise_reschedule(text, received_at)
        if scheduled_for:
            return LifeIntent(
                intent="reschedule_exercise",
                scheduled_for=scheduled_for.isoformat(),
            )

        if text == "撤销上一条":
            return LifeIntent(intent="undo_latest")

        if text in {"我睡了", "睡了", "准备睡了"}:
            return LifeIntent(
                intent="record_event",
                event_type="sleep_start",
                occurred_at=received_at.isoformat(),
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
intent: record_event | skip_exercise | reschedule_exercise | undo_latest | manual_status_query | unknown
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


def _parse_exercise_reschedule(text: str, received: datetime) -> datetime | None:
    if "提醒我运动" not in text:
        return None
    match = re.search(r"(上午|中午|下午|晚上)?\s*(\d{1,2})\s*点", text)
    if not match:
        return None
    hour = int(match.group(2))
    period = match.group(1) or ""
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    return received.replace(hour=hour, minute=0, second=0, microsecond=0)


def _parse_wake_time(text: str, received: datetime) -> datetime | None:
    if not any(keyword in text for keyword in ("醒", "起床", "睡到")):
        return None
    parsed = _parse_clock_time(text, received)
    if parsed and parsed > received + timedelta(minutes=5):
        parsed = parsed - timedelta(days=1)
    return parsed


def _parse_clock_time(text: str, received: datetime) -> datetime | None:
    match = re.search(r"(凌晨|早上|上午|中午|下午|晚上)?\s*(\d{1,2})\s*点\s*(半|(\d{1,2})\s*分?)?", text)
    if not match:
        return None
    period = match.group(1) or ""
    hour = int(match.group(2))
    minute = 30 if match.group(3) == "半" else int(match.group(4) or 0)
    if period in {"下午", "晚上"} and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if period in {"凌晨", "早上", "上午"} and hour == 12:
        hour = 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return received.replace(hour=hour, minute=minute, second=0, microsecond=0)


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

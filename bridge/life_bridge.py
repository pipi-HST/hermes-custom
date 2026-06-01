"""Life Assistant bridge functions for Hermes."""

from __future__ import annotations

import sys
import subprocess
import re
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from agents.life_agent import ActivitySnapshot, LifeAgent, LifeConfig
from agents.life_intent_parser import LifeIntentLLMFallback, LifeIntentParser
from bridge.result_envelope import artifact_ref, success_envelope
from db.life_repo import LifeRepo
from db.life_schema import init_life_db

_agent: LifeAgent | None = None
_db_path: str | None = None
_config_path = Path(__file__).parent.parent / "config.yaml"


def _init() -> LifeAgent:
    global _agent, _db_path
    if _agent is None:
        if not _db_path:
            _db_path = _load_db_path(_config_path)
        conn = init_life_db(_db_path)
        _agent = LifeAgent(
            LifeRepo(conn),
            config=_load_life_config(_config_path),
            parser=_load_life_parser(_config_path),
        )
    return _agent


def _load_db_path(config_path: Path) -> str:
    with config_path.open() as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("database", {}).get("path", "./data/captures.db")


def _load_life_config(config_path: Path) -> LifeConfig:
    with config_path.open() as f:
        cfg = yaml.safe_load(f) or {}
    life_cfg = cfg.get("life", {}) or {}
    default_log_path = Path(__file__).parent.parent / "data" / "life.log"
    return LifeConfig(
        notifications_enabled=bool(life_cfg.get("notifications_enabled", False)),
        sedentary_repeat_minutes=int(life_cfg.get("sedentary_repeat_minutes", LifeConfig.sedentary_repeat_minutes)),
        break_threshold_minutes=int(life_cfg.get("break_threshold_minutes", LifeConfig.break_threshold_minutes)),
        sleep_record_check_time=str(life_cfg.get("sleep_record_check_time", LifeConfig.sleep_record_check_time)),
        meal_record_check_time=str(life_cfg.get("meal_record_check_time", LifeConfig.meal_record_check_time)),
        log_path=str(life_cfg.get("log_path") or default_log_path),
    )


def _load_life_parser(config_path: Path) -> LifeIntentParser:
    with config_path.open() as f:
        cfg = yaml.safe_load(f) or {}
    llm_cfg = cfg.get("llm", {}) or {}
    life_cfg = cfg.get("life", {}) or {}
    api_key = llm_cfg.get("api_key", "")
    fallback = None
    if api_key:
        fallback = LifeIntentLLMFallback(
            api_key=api_key,
            model=life_cfg.get("intent_model", "deepseek-v4-flash"),
            base_url=llm_cfg.get("base_url", "https://api.deepseek.com"),
        )
    return LifeIntentParser(
        llm_fallback=fallback,
        prefer_llm_for_life_records=bool(life_cfg.get("prefer_llm_for_life_records", True)),
    )


def record_life_event(message: str, received_at: str = "") -> dict:
    """Record a supported Life Assistant event from Hermes routing."""
    agent = _init()
    result = agent.record_life_event(message, received_at=received_at or None)
    event = result.get("event")
    artifacts = []
    if event:
        artifacts.append(
            artifact_ref(
                type="life_event",
                id=event.get("id", ""),
                title=event.get("event_type", ""),
                status=event.get("status", ""),
            )
        )
    return success_envelope(
        agent="life",
        intent="record_life_event",
        chat_preview=result["chat_preview"],
        durable_artifacts=artifacts,
        meta={
            "should_notify": False,
            "record_status": result["status"],
            "event_id": event.get("id", "") if event else "",
            "event_type": event.get("event_type", "") if event else "",
        },
    )


def life_tick(now: str = "", dry_run: bool = True) -> dict:
    """Run one Life Assistant scheduler tick."""
    agent = _init()
    result = agent.life_tick(
        now=now or None,
        activity=_read_mac_activity(),
        dry_run=dry_run,
    )
    selected = result.get("selected") or {}
    return success_envelope(
        agent="life",
        intent="life_tick",
        chat_preview=result["chat_preview"],
        warnings=result.get("warnings", []),
        meta={
            **result.get("meta", {}),
            "selected_reminder_type": selected.get("reminder_type", ""),
        },
    )


def reconcile_life_delivery(completed_at: str, delivery_error: str = "") -> dict:
    """Reconcile the previous cron delivery result into Life reminder logs."""
    agent = _init()
    result = agent.reconcile_delivery_result(
        completed_at=completed_at,
        delivery_error=delivery_error or "",
    )
    return success_envelope(
        agent="life",
        intent="reconcile_life_delivery",
        chat_preview="Life Assistant delivery state reconciled.",
        meta={
            "should_notify": False,
            **result,
        },
    )


def life_memory_review() -> dict:
    """Review active Life Assistant memories."""
    agent = _init()
    items = agent.repo.list_memory_items(
        statuses=["active", "temporary", "weak"],
        limit=50,
    )
    if not items:
        preview = "目前还没有形成可用的 Life 记忆。"
    else:
        preview = "\n".join(f"- {item['content']}" for item in items[:10])
    return success_envelope(
        agent="life",
        intent="life_memory_review",
        chat_preview=preview,
        meta={
            "should_notify": False,
            "count": len(items),
            "items": items,
        },
    )


def life_memory_forget(query: str, mode: str = "blocked") -> dict:
    """Disable Life memories matching a user-provided query."""
    agent = _init()
    normalized = query.strip()
    if not normalized:
        return success_envelope(
            agent="life",
            intent="life_memory_forget",
            chat_preview="没有提供要禁用的 Life 记忆关键词。",
            warnings=["life_memory_forget_empty_query"],
            meta={"should_notify": False, "updated": 0},
        )
    status = "archived" if mode == "archived" else "blocked"
    candidates = agent.repo.list_memory_items(limit=200)
    updated = []
    for item in candidates:
        haystack = " ".join(
            [
                str(item.get("domain") or ""),
                str(item.get("memory_type") or ""),
                str(item.get("content") or ""),
            ]
        )
        if normalized not in haystack:
            continue
        changed = agent.repo.update_memory_item_status(item["id"], status=status)
        if changed:
            updated.append(changed)
    preview = (
        f"已禁用 {len(updated)} 条 Life 记忆。"
        if updated
        else "没有找到匹配的 Life 记忆。"
    )
    return success_envelope(
        agent="life",
        intent="life_memory_forget",
        chat_preview=preview,
        meta={
            "should_notify": False,
            "updated": len(updated),
            "items": updated,
        },
    )


def _read_mac_activity() -> ActivitySnapshot:
    interruption = _read_recent_mac_interruption()
    try:
        output = subprocess.check_output(
            ["ioreg", "-c", "IOHIDSystem"],
            text=True,
            timeout=2,
        )
    except (subprocess.SubprocessError, FileNotFoundError, TimeoutError):
        return ActivitySnapshot(idle_seconds=None, available=False)
    marker = '"HIDIdleTime" = '
    for line in output.splitlines():
        if marker not in line:
            continue
        raw_value = line.split(marker, 1)[1].strip()
        try:
            # HIDIdleTime is reported in nanoseconds.
            source, seconds, started_at, ended_at = interruption or ("", None, "", "")
            return ActivitySnapshot(
                idle_seconds=int(raw_value) // 1_000_000_000,
                sleeping=source == "sleep",
                display_off=source == "display_off",
                interruption_seconds=seconds,
                interruption_source=source,
                interruption_started_at=started_at,
                interruption_ended_at=ended_at,
            )
        except ValueError:
            return ActivitySnapshot(idle_seconds=None, available=False)
    return ActivitySnapshot(idle_seconds=None, available=False)


def _read_recent_mac_interruption() -> tuple[str, int, str, str] | None:
    try:
        output = subprocess.check_output(
            ["pmset", "-g", "log"],
            text=True,
            timeout=3,
        )
    except (subprocess.SubprocessError, FileNotFoundError, TimeoutError):
        return None
    return _recent_mac_interruption(output, now=_now())


def _now() -> datetime:
    return datetime.now().astimezone()


def _recent_mac_interruption(log_output: str, *, now: datetime) -> tuple[str, int, str, str] | None:
    latest: tuple[str, int, datetime, datetime] | None = None
    open_start: tuple[str, datetime] | None = None
    for line in log_output.splitlines():
        timestamp = _parse_pmset_line_time(line, now=now)
        if timestamp is None:
            continue
        source = _pmset_start_source(line)
        if source:
            open_start = (source, timestamp)
            continue
        end_source = _pmset_end_source(line)
        if not end_source or not open_start:
            continue
        start_source, started_at = open_start
        if end_source != start_source:
            continue
        duration = int((timestamp - started_at).total_seconds())
        open_start = None
        if duration < LifeConfig.break_threshold_minutes * 60:
            continue
        if int((now - timestamp).total_seconds()) > 30 * 60:
            continue
        latest = (start_source, duration, started_at, timestamp)
    if not latest:
        return None
    return latest[0], latest[1], latest[2].isoformat(), latest[3].isoformat()


def _parse_pmset_line_time(line: str, *, now: datetime) -> datetime | None:
    match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?: ([+-]\d{4}))?", line)
    if not match:
        return None
    time_text, offset = match.groups()
    return _parse_pmset_time(f"{time_text} {offset}" if offset else time_text, now=now)


def _parse_pmset_time(value: str, *, now: datetime | None = None) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return parsed.replace(tzinfo=(now or _now()).tzinfo)


def _pmset_start_source(line: str) -> str:
    if "Entering Sleep state" in line or re.search(r"\bSleep\b", line):
        return "sleep"
    if "Display is turned off" in line:
        return "display_off"
    return ""


def _pmset_end_source(line: str) -> str:
    if "Wake from" in line or re.search(r"\bWake\b", line):
        return "sleep"
    if "Display is turned on" in line:
        return "display_off"
    return ""

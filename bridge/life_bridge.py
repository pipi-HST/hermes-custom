"""Life Assistant bridge functions for Hermes."""

from __future__ import annotations

import sys
import subprocess
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
        log_path=str(life_cfg.get("log_path") or default_log_path),
    )


def _load_life_parser(config_path: Path) -> LifeIntentParser:
    with config_path.open() as f:
        cfg = yaml.safe_load(f) or {}
    llm_cfg = cfg.get("llm", {}) or {}
    api_key = llm_cfg.get("api_key", "")
    fallback = None
    if api_key:
        fallback = LifeIntentLLMFallback(
            api_key=api_key,
            model=(cfg.get("life", {}) or {}).get("intent_model", "deepseek-v4-flash"),
            base_url=llm_cfg.get("base_url", "https://api.deepseek.com"),
        )
    return LifeIntentParser(llm_fallback=fallback)


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


def _read_mac_activity() -> ActivitySnapshot:
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
            return ActivitySnapshot(idle_seconds=int(raw_value) // 1_000_000_000)
        except ValueError:
            return ActivitySnapshot(idle_seconds=None, available=False)
    return ActivitySnapshot(idle_seconds=None, available=False)

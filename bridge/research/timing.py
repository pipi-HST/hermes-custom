"""Research stage timing helpers."""
from __future__ import annotations

import time
from typing import Any


STAGE_TIMING_KEYS = (
    "search_ms",
    "extract_ms",
    "critic_ms",
    "initial_critic_ms",
    "minimal_critic_ms",
    "batch_critic_ms",
    "broad_retry_ms",
    "repair_critic_ms",
    "limited_summary_adoption_ms",
    "fallback_ms",
    "synthesis_ms",
)


def _empty_stage_timings() -> dict[str, int]:
    return {key: 0 for key in STAGE_TIMING_KEYS}


def _normalized_stage_timings(stage_timings: dict[str, Any] | None) -> dict[str, int]:
    normalized = _empty_stage_timings()
    if not isinstance(stage_timings, dict):
        return normalized
    for key in STAGE_TIMING_KEYS:
        try:
            normalized[key] = max(int(stage_timings.get(key) or 0), 0)
        except (TypeError, ValueError):
            normalized[key] = 0
    return normalized


def _ensure_stage_timings(provider_status: dict[str, Any]) -> dict[str, int]:
    stage_timings = _normalized_stage_timings(provider_status.get("stage_timings"))
    provider_status["stage_timings"] = stage_timings
    return stage_timings


def _add_stage_timing(stage_timings: dict[str, Any] | None, key: str, started: float) -> None:
    if stage_timings is None or key not in STAGE_TIMING_KEYS:
        return
    elapsed_ms = max(int((time.monotonic() - started) * 1000), 0)
    try:
        current = int(stage_timings.get(key) or 0)
    except (TypeError, ValueError):
        current = 0
    stage_timings[key] = current + elapsed_ms

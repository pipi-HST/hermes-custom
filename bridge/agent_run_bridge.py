"""Read-only bridge for recent Hermes Agent run ledger entries."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.capture_agent import load_config
from bridge.result_envelope import success_envelope
from db.agent_run_repo import AgentRunRepo
from db.schema import init_db


def get_agent_run_status(limit: int = 5) -> dict[str, Any]:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = AgentRunRepo(init_db(config.db_path))
    max_limit = max(1, min(int(limit or 5), 10))
    runs = [dict(row) for row in repo.latest(limit=max_limit)]
    return success_envelope(
        agent="system",
        intent="agent_run_status",
        chat_preview=_preview(runs),
        meta={"run_count": len(runs), "runs": runs},
    )


def _preview(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "最近还没有记录到 Hermes Agent 运行。"
    lines = [f"最近 {len(runs)} 次 Agent 运行："]
    for index, run in enumerate(runs, 1):
        evidence = _json_obj(run.get("evidence_json"), {})
        output = _json_obj(run.get("output_json"), {})
        summary_parts = []
        for key in (
            "result_count",
            "source_count",
            "draft_id",
            "card_id",
            "scanned_count",
            "pending_questions_created",
        ):
            value = evidence.get(key)
            if value not in (None, "", []):
                summary_parts.append(f"{key}={value}")
        summary = f" ({', '.join(summary_parts)})" if summary_parts else ""
        lines.append(
            f"{index}. {run.get('agent')}/{run.get('intent')} "
            f"{run.get('status')}{summary}"
        )
        for source_index, source in enumerate(_source_lines(output, evidence), 1):
            lines.append(f"   [{source_index}] {source}")
    return "\n".join(lines)


def _json_obj(text: Any, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except (json.JSONDecodeError, TypeError):
        return default
    return value if isinstance(value, dict) else default


def _source_lines(output: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    sources = output.get("sources")
    if isinstance(sources, list) and sources:
        lines = []
        for source in sources[:5]:
            if not isinstance(source, dict):
                continue
            title = str(source.get("title") or "").strip()
            section = str(source.get("section") or "").strip()
            if not title:
                continue
            lines.append(f"{title} / {section}" if section else title)
        return lines

    titles = evidence.get("source_titles")
    if isinstance(titles, list):
        return [str(title) for title in titles[:5] if str(title)]
    return []

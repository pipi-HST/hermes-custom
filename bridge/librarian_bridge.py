"""Librarian bridge — audit and safely repair the Obsidian vault."""
from __future__ import annotations

import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.capture_agent import load_config
from agents.library_agent import LibraryAgent
from agents.outliner import Outliner
from bridge.result_envelope import artifact_ref, next_action, success_envelope


def run_librarian_audit(*, apply: bool = False) -> dict[str, Any]:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    outliner = Outliner(
        api_key=config.llm_api_key,
        model="deepseek-v4-flash",
        base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    agent = LibraryAgent(
        config.vault_path,
        outliner=outliner,
        db_path=config.db_path,
    )
    database_sync = agent.maintain_database_sync(apply=apply)
    outlines = agent.maintain_outlines(apply=apply)
    property_gaps = agent.audit_property_gaps()
    summary = _summarize(database_sync, outlines, property_gaps, apply=apply)
    report_path = _write_librarian_report(
        db_path=config.db_path,
        payload={
            "dry_run": not apply,
            "summary": summary,
            "database_sync": {
                **database_sync,
                "raw_report_path": database_sync.get("report_path") or "",
            },
            "outlines": outlines,
            "property_gaps": property_gaps,
        },
    )

    actions = []
    if not apply and summary["needs_confirmation"]:
        actions.append(
            next_action(
                label="执行安全修复",
                intent="apply_librarian_repair",
                payload={},
                requires_confirmation=True,
            )
        )

    artifacts = []
    if report_path:
        artifacts.append(
            artifact_ref(
                type="librarian_report",
                id=Path(report_path).name,
                title="Librarian repair report",
                path=report_path,
                status="dry_run" if not apply else "applied",
            )
        )

    return success_envelope(
        agent="librarian",
        intent="apply_librarian_repair" if apply else "audit_library",
        chat_preview=_chat_preview(summary, report_path, apply=apply),
        durable_artifacts=artifacts,
        next_actions=actions,
        meta={
            "dry_run": not apply,
            "database_sync": summary["database_sync"],
            "outlines": summary["outlines"],
            "property_gaps": summary["property_gaps"],
            "report_path": report_path,
            "backup_path": str(database_sync.get("backup_path") or ""),
        },
    )


def _write_librarian_report(*, db_path: str, payload: dict[str, Any]) -> str:
    report_dir = Path(db_path).expanduser().parent / "repair_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"librarian-{timestamp}.json"
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return str(report_path)


def _summarize(
    database_sync: dict[str, Any],
    outlines: dict[str, Any],
    property_gaps: dict[str, Any],
    *,
    apply: bool,
) -> dict[str, Any]:
    planned_actions = database_sync.get("planned_actions") or []
    before = database_sync.get("before") or {}
    after = database_sync.get("after") or {}
    outline_needs = int(outlines.get("needs_outline") or 0)
    outline_updated = int(outlines.get("updated") or 0)
    action_count = len(planned_actions)
    needs_confirmation = action_count > 0 or outline_needs > 0
    property_gap_count = int(property_gaps.get("capture_gap_count") or 0) + int(
        property_gaps.get("raw_gap_count") or 0
    )
    return {
        "needs_confirmation": needs_confirmation,
        "has_property_gaps": property_gap_count > 0,
        "database_sync": {
            "capture_count": int(after.get("captures") or before.get("captures") or 0),
            "planned_action_count": action_count,
            "missing_capture_paths": int(after.get("missing_capture_paths") or 0),
            "duplicate_capture_paths": int(after.get("duplicate_capture_paths") or 0),
            "open_draft_duplicate_groups": int(after.get("open_draft_duplicate_groups") or 0),
            "action_types": _count_types(planned_actions),
        },
        "outlines": {
            "scanned": int(outlines.get("scanned") or 0),
            "needs_outline": outline_needs,
            "updated": outline_updated,
            "errors": int(outlines.get("errors") or 0),
        },
        "property_gaps": {
            "capture_notes_scanned": int(property_gaps.get("capture_notes_scanned") or 0),
            "raw_notes_scanned": int(property_gaps.get("raw_notes_scanned") or 0),
            "capture_gap_count": int(property_gaps.get("capture_gap_count") or 0),
            "raw_gap_count": int(property_gaps.get("raw_gap_count") or 0),
            "capture_gaps": property_gaps.get("capture_gaps") or [],
            "raw_gaps": property_gaps.get("raw_gaps") or [],
        },
        "applied": apply,
    }


def _count_types(actions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions:
        action_type = str(action.get("type") or "unknown")
        counts[action_type] = counts.get(action_type, 0) + 1
    return counts


def _chat_preview(summary: dict[str, Any], report_path: str, *, apply: bool) -> str:
    db = summary["database_sync"]
    outlines = summary["outlines"]
    props = summary["property_gaps"]
    lines = ["Librarian 已执行安全修复。" if apply else "Librarian 审计完成。"]
    if not summary["needs_confirmation"] and not summary["has_property_gaps"] and not apply:
        lines.append("知识库当前干净：没有待处理的 DB/vault 断链、重复草稿或长文目录缺口。")
    else:
        lines.append(
            "DB/vault: "
            f"{db['planned_action_count']} 个待处理项, "
            f"missing={db['missing_capture_paths']}, "
            f"duplicate_path={db['duplicate_capture_paths']}, "
            f"open_draft_duplicates={db['open_draft_duplicate_groups']}。"
        )
        lines.append(
            "Outline: "
            f"扫描 {outlines['scanned']} 篇, "
            f"缺失 {outlines['needs_outline']} 篇, "
            f"已补 {outlines['updated']} 篇, "
            f"错误 {outlines['errors']}。"
        )
        lines.append(
            "Properties: "
            f"capture 缺口 {props['capture_gap_count']} 篇, "
            f"raw 缺口 {props['raw_gap_count']} 篇（只报告，不自动改）。"
        )
    if report_path:
        lines.append(f"报告: {report_path}")
    if apply:
        lines.append("已执行；如不满意，可用备份回退。")
    elif summary["needs_confirmation"]:
        lines.append("待确认修复。确认后我再执行安全修复。")
    return "\n".join(lines)

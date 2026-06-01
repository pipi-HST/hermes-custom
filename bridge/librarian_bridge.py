"""Librarian bridge — audit and safely repair the Obsidian vault."""
from __future__ import annotations

import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.capture_agent import load_config
from agents.embedder import Embedder
from agents.library_agent import LibraryAgent
from agents.outliner import Outliner
from bridge.file_artifacts import write_markdown_export
from bridge.result_envelope import artifact_ref, next_action, success_envelope
from bridge.run_ledger import run_with_ledger
from db.schema import init_db


def run_librarian_audit(*, apply: bool = False, export_file: bool = False) -> dict[str, Any]:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    conn = init_db(config.db_path)
    return run_with_ledger(
        conn,
        agent="librarian",
        intent="apply_librarian_repair" if apply else "audit_library",
        trigger="wechat",
        input_ref={"apply": apply, "export_file": export_file},
        operation=lambda: _run_librarian_audit(config, apply=apply, export_file=export_file),
    )


def _run_librarian_audit(config, *, apply: bool = False, export_file: bool = False) -> dict[str, Any]:
    outliner = Outliner(
        api_key=config.llm_api_key,
        model="deepseek-v4-flash",
        base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    agent = LibraryAgent(
        config.vault_path,
        outliner=outliner,
        db_path=config.db_path,
        embedder=Embedder(api_key=getattr(config, "qwen_api_key", "")),
    )
    database_sync = agent.maintain_database_sync(apply=apply)
    outlines = agent.maintain_outlines(apply=apply)
    property_gaps = agent.maintain_property_gaps(apply=apply)
    embedding_index = agent.maintain_embedding_index(apply=apply)
    summary = _summarize(
        database_sync,
        outlines,
        property_gaps,
        embedding_index,
        apply=apply,
    )
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
            "embedding_index": embedding_index,
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
    if export_file:
        artifacts.append(
            _librarian_markdown_report_artifact(
                summary=summary,
                json_report_path=report_path,
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
            "embedding_index": summary["embedding_index"],
            "report_path": report_path,
            "backup_path": str(database_sync.get("backup_path") or ""),
            "export_file": export_file,
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


def _librarian_markdown_report_artifact(
    *,
    summary: dict[str, Any],
    json_report_path: str,
    export_dir: Path | None = None,
) -> dict[str, Any]:
    db = summary.get("database_sync") or {}
    outlines = summary.get("outlines") or {}
    props = summary.get("property_gaps") or {}
    embedding = summary.get("embedding_index") or {}
    lines = [
        "# Library 审计与修复报告",
        "",
        "## 总览",
        "",
        f"- 模式：{'已执行修复' if summary.get('applied') else '仅审计'}",
        f"- Capture 数量：{db.get('capture_count', 0)}",
        f"- DB/vault 待处理项：{db.get('planned_action_count', 0)}",
        f"- JSON 明细：{json_report_path or '无'}",
        "",
        "## Outline",
        "",
        f"- 扫描：{outlines.get('scanned', 0)}",
        f"- 缺失：{outlines.get('needs_outline', 0)}",
        f"- 已补：{outlines.get('updated', 0)}",
        f"- 错误：{outlines.get('errors', 0)}",
        "",
        "## Properties",
        "",
        f"- 计划补齐：{props.get('planned_action_count', 0)}",
        f"- 已补齐：{props.get('applied_action_count', 0)}",
        "",
        "## Embedding",
        "",
        f"- 扫描：{embedding.get('scanned', 0)}",
        f"- 缺失或过期：{embedding.get('missing_or_stale', 0)}",
        f"- 已更新：{embedding.get('updated', 0)}",
        f"- 错误：{embedding.get('errors', 0)}",
    ]
    base_dir = export_dir or (Path(__file__).parent.parent / "data" / "exports" / "library")
    path = write_markdown_export(
        base_dir=base_dir,
        stem="library-audit-report",
        content="\n".join(lines),
    )
    return artifact_ref(
        type="librarian_markdown_report",
        id=Path(path).stem,
        title="Library 审计与修复报告",
        path=path,
        status="created",
    )


def _summarize(
    database_sync: dict[str, Any],
    outlines: dict[str, Any],
    property_gaps: dict[str, Any],
    embedding_index: dict[str, Any],
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
    property_after = property_gaps.get("after") or {}
    property_action_count = int(property_gaps.get("planned_action_count") or 0)
    property_gap_count = int(property_after.get("capture_gap_count") or 0) + int(
        property_after.get("raw_gap_count") or 0
    )
    if apply:
        property_action_count = 0
    embedding_missing = int(embedding_index.get("missing_or_stale") or 0)
    embedding_errors = int(embedding_index.get("errors") or 0)
    return {
        "needs_confirmation": (
            needs_confirmation
            or property_action_count > 0
            or (embedding_missing > 0 and not apply)
        ),
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
            "planned_action_count": int(property_gaps.get("planned_action_count") or 0),
            "applied_action_count": int(property_gaps.get("applied_action_count") or 0),
            "before": property_gaps.get("before") or {},
            "after": property_after,
        },
        "embedding_index": {
            "scanned": int(embedding_index.get("scanned") or 0),
            "missing_or_stale": embedding_missing,
            "updated": int(embedding_index.get("updated") or 0),
            "errors": embedding_errors,
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
    embedding = summary["embedding_index"]
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
        after = props["after"]
        if props["planned_action_count"] or props["applied_action_count"]:
            property_action_text = (
                f"已自动补 {props['applied_action_count']} 项"
                if apply
                else f"可自动补 {props['planned_action_count']} 项"
            )
        else:
            property_action_text = "没有可机械补齐项"
        lines.append(
            "Properties: "
            f"{property_action_text}; "
            f"剩余 capture 缺口 {int(after.get('capture_gap_count') or 0)} 篇, "
            f"raw 缺口 {int(after.get('raw_gap_count') or 0)} 篇。"
        )
        if embedding["missing_or_stale"] or embedding["updated"] or embedding["errors"]:
            index_action_text = (
                f"已更新 {embedding['updated']} 条"
                if apply
                else f"缺失/过期 {embedding['missing_or_stale']} 条"
            )
            lines.append(
                "Embedding 索引: "
                f"扫描 {embedding['scanned']} 篇, "
                f"{index_action_text}, "
                f"错误 {embedding['errors']}。"
            )
        if summary["has_property_gaps"]:
            lines.append("剩余是判断字段或没有可推导来源的字段，不影响使用，不需要手动处理。")
    if report_path:
        lines.append(f"报告: {report_path}")
    if apply:
        lines.append("已执行；如不满意，可用备份回退。")
    elif summary["needs_confirmation"]:
        lines.append("待确认修复。确认后我再执行安全修复。")
    return "\n".join(lines)

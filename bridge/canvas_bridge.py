"""Bridge for generating Knowledge Canvas relationship maps."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.canvas_agent import (
    CanvasBuildInput,
    CanvasItem,
    CanvasStructure,
    CanvasStructureGroup,
    CanvasTheme,
    build_obsidian_canvas,
    safe_canvas_filename,
)
from agents.capture_agent import load_config
from agents.garden_structure_agent import GardenStructureAgent
from agents.markdown_writer import read_note
from bridge.result_envelope import artifact_ref, error_envelope, success_envelope
from bridge.run_ledger import run_with_ledger
from db.repo import CaptureRepo
from db.schema import init_db


def generate_canvas(
    *,
    theme: str = "",
    question_id: str = "",
    capture_ids: list[str] | None = None,
    garden_card_ids: list[str] | None = None,
    db_path: str = "",
    vault_path: str = "",
    export_file: bool = False,
) -> dict[str, Any]:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    resolved_db_path = db_path or config.db_path
    resolved_vault_path = vault_path or config.vault_path
    conn = init_db(resolved_db_path)
    repo = CaptureRepo(conn)
    return run_with_ledger(
        conn,
        agent="canvas",
        intent="generate_canvas",
        trigger="wechat",
        input_ref={
            "theme": theme,
            "question_id": question_id,
            "capture_ids": capture_ids or [],
            "garden_card_ids": garden_card_ids or [],
            "export_file": export_file,
        },
        operation=lambda: _generate_canvas(
            repo,
            Path(resolved_vault_path).expanduser(),
            config=config,
            theme=theme,
            question_id=question_id,
            capture_ids=capture_ids or [],
            garden_card_ids=garden_card_ids or [],
            export_file=export_file,
        ),
    )


def _generate_canvas(
    repo: CaptureRepo,
    vault_path: Path,
    *,
    config,
    theme: str,
    question_id: str,
    capture_ids: list[str],
    garden_card_ids: list[str],
    export_file: bool = False,
) -> dict[str, Any]:
    question_capture_ids = _capture_ids_for_question(repo, question_id)
    resolved_capture_ids = _dedupe(capture_ids + question_capture_ids)
    wants_garden_cluster = _should_build_garden_cluster(theme, garden_card_ids, resolved_capture_ids, question_id)
    theme_node = None if wants_garden_cluster else _resolve_theme(vault_path, theme)
    canvas_mode = "theme_canvas"
    garden_cards: list[dict[str, Any]] = []

    if not theme_node and wants_garden_cluster:
        canvas_mode = "garden_cluster"
        garden_cards = _resolve_garden_cards(
            repo,
            garden_card_ids=garden_card_ids,
            capture_ids=[],
            include_all=not garden_card_ids,
        )
        if not garden_cards:
            return error_envelope(
                agent="canvas",
                intent="generate_canvas",
                error="还没有 Garden 卡可以生成聚类画布。",
                chat_preview="还没有 Garden 卡可以生成聚类画布。可以先让 Garden Agent 从相关材料里生成几张知识卡片，再生成 Canvas。",
                meta={"canvas_mode": canvas_mode},
            )
        resolved_capture_ids = _dedupe(resolved_capture_ids + _capture_ids_from_cards(garden_cards))
        theme_node = CanvasTheme(
            title="Garden Card 聚类",
            file_path="",
            summary="这张画布以 Garden 知识卡片为主角，展示卡片之间的聚类关系和底层来源材料。",
        )
    if not theme_node:
        return error_envelope(
            agent="canvas",
            intent="generate_canvas",
            error="没有找到匹配的 Theme/MOC。可以先扫描主题，或直接说清楚要用哪几篇笔记生成画板。",
            chat_preview="没有找到匹配的 Theme/MOC。可以先扫描主题，或直接说清楚要用哪几篇笔记生成画板。",
            meta={"theme": theme, "question_id": question_id},
        )
    if not resolved_capture_ids and theme_node.file_path:
        resolved_capture_ids = _capture_ids_from_theme_moc(repo, vault_path, theme_node.file_path)

    if not garden_cards:
        garden_cards = _resolve_garden_cards(
            repo,
            garden_card_ids=garden_card_ids,
            capture_ids=resolved_capture_ids,
        )
    if not resolved_capture_ids:
        resolved_capture_ids = _capture_ids_from_cards(garden_cards)
    captures, missing_capture_ids = _resolve_captures(repo, vault_path, resolved_capture_ids)
    garden_items = _garden_items(garden_cards)
    warnings: list[str] = []
    if missing_capture_ids:
        warnings.append(f"已跳过 {len(missing_capture_ids)} 条缺失文件的来源笔记")
    if not garden_items:
        warnings.append("暂时没有找到关联的 Garden 卡")

    structure = None
    structure_status = "fallback"
    if len(garden_items) >= 2:
        planner = GardenStructureAgent(
            llm_api_key=config.llm_api_key,
            llm_model=config.llm_model,
            llm_base_url=config.llm_base_url or "https://api.deepseek.com",
        )
        plan = planner.plan(theme_node.title, garden_cards)
        if plan.get("status") != "structured":
            reason = str(plan.get("reason") or "这些卡只是同主题，没有形成清晰结构。")
            return error_envelope(
                agent="canvas",
                intent="generate_canvas",
                error=reason,
                chat_preview="\n".join([
                    "这组 Garden Card 暂时不适合生成 Canvas。",
                    "",
                    reason,
                    "",
                    "这种情况用 Theme/MOC 单向整理就够了；等材料之间形成更明确的支撑、方法、风险或问题关系，再生成画板会更有用。",
                ]),
                warnings=warnings,
                meta={
                    "theme": theme_node.title,
                    "capture_count": len(captures),
                    "garden_count": len(garden_items),
                    "structure_status": "not_worth_canvas",
                },
            )
        structure_status = "structured"
        structure = _canvas_structure_from_plan(plan)

    built = build_obsidian_canvas(
        CanvasBuildInput(
            title=theme_node.title,
            theme=theme_node,
            captures=captures,
            garden_cards=garden_items,
            structure=structure,
        )
    )
    warnings.extend(built.warnings)
    canvas_path = _write_canvas(vault_path, theme_node.title, built.json_text)
    rel_path = canvas_path.relative_to(vault_path).as_posix()
    artifacts = [
        artifact_ref(
            type="obsidian_canvas",
            id=canvas_path.stem,
            title=canvas_path.stem,
            path=rel_path,
            status="created",
        )
    ]
    if export_file:
        artifacts.append(_canvas_file_artifact(canvas_path))
    chat_preview = _canvas_preview(
        title=canvas_path.stem,
        rel_path=rel_path,
        capture_count=len(captures),
        garden_count=len(garden_items),
        canvas_mode=canvas_mode,
    )
    return success_envelope(
        agent="canvas",
        intent="generate_canvas",
        chat_preview=chat_preview,
        durable_artifacts=artifacts,
        warnings=warnings,
        meta={
            "path": rel_path,
            "file_path": str(canvas_path.resolve()) if export_file else "",
            "theme": theme_node.title,
            "capture_count": len(captures),
            "garden_count": len(garden_items),
            "node_count": built.node_count,
            "edge_count": built.edge_count,
            "structure_status": structure_status,
            "export_file": export_file,
            "canvas_mode": canvas_mode,
        },
    )


def _should_build_garden_cluster(
    theme: str,
    garden_card_ids: list[str],
    capture_ids: list[str],
    question_id: str,
) -> bool:
    if garden_card_ids:
        return True
    query = _norm(theme)
    if query and any(keyword in query for keyword in ["garden", "知识卡片", "花园", "卡片聚类"]):
        return True
    return not query and not capture_ids and not question_id


def _canvas_file_artifact(canvas_path: Path) -> dict[str, Any]:
    return artifact_ref(
        type="obsidian_canvas_file",
        id=canvas_path.stem,
        title=canvas_path.stem,
        path=str(canvas_path.resolve()),
        status="created",
    )


def _resolve_theme(vault_path: Path, theme: str) -> CanvasTheme | None:
    candidates = sorted((vault_path / "themes").glob("*.md"))
    query = _norm(theme)
    for path in candidates:
        rel_path = path.relative_to(vault_path).as_posix()
        note = read_note(vault_path, rel_path)
        frontmatter = note[0] if note else {}
        body = note[1] if note else ""
        title = str(frontmatter.get("title") or path.stem)
        if not query or query in _norm(title) or query in _norm(path.stem):
            return CanvasTheme(
                title=title,
                file_path=rel_path,
                summary=_first_plain_paragraph(body),
            )
    return None


def _capture_ids_for_question(repo: CaptureRepo, question_id: str) -> list[str]:
    if not question_id:
        return []
    row = repo.conn.execute(
        "SELECT related_capture_ids FROM pending_questions WHERE id = ?",
        (question_id,),
    ).fetchone()
    if not row:
        return []
    return _json_list(row["related_capture_ids"])


def _capture_ids_from_theme_moc(repo: CaptureRepo, vault_path: Path, theme_file: str) -> list[str]:
    note = read_note(vault_path, theme_file)
    if not note:
        return []
    _frontmatter, body = note
    linked_titles = _wiki_link_titles(body)
    if not linked_titles:
        return []

    capture_lookup: dict[str, str] = {}
    for row in repo.list_all(limit=10000):
        rel_path = row.get("file_path") or ""
        if not rel_path:
            continue
        title = _note_title(vault_path, rel_path) or Path(rel_path).stem
        capture_lookup[_norm(title)] = row["id"]
        capture_lookup[_norm(Path(rel_path).stem)] = row["id"]

    capture_ids: list[str] = []
    for title in linked_titles:
        capture_id = capture_lookup.get(_norm(title))
        if capture_id:
            capture_ids.append(capture_id)
    return _dedupe(capture_ids)


def _wiki_link_titles(text: str) -> list[str]:
    titles: list[str] = []
    for match in re.finditer(r"\[\[([^\]]+)\]\]", text or ""):
        target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        if target:
            titles.append(target)
    return titles


def _resolve_captures(
    repo: CaptureRepo,
    vault_path: Path,
    capture_ids: list[str],
) -> tuple[list[CanvasItem], list[str]]:
    captures: list[CanvasItem] = []
    missing: list[str] = []
    for capture_id in capture_ids:
        row = repo.get_by_id(capture_id)
        if not row or not row.get("file_path"):
            missing.append(capture_id)
            continue
        rel_path = row["file_path"]
        if not (vault_path / rel_path).exists():
            missing.append(capture_id)
            continue
        note = read_note(vault_path, rel_path)
        frontmatter = note[0] if note else {}
        body = note[1] if note else ""
        title = str(frontmatter.get("title") or "") or Path(rel_path).stem
        summary = str(frontmatter.get("story") or "") or _first_plain_paragraph(body)
        captures.append(CanvasItem(id=capture_id, title=title, file_path=rel_path, summary=summary))
    return captures, missing


def _resolve_garden_cards(
    repo: CaptureRepo,
    *,
    garden_card_ids: list[str],
    capture_ids: list[str],
    include_all: bool = False,
) -> list[dict[str, Any]]:
    if include_all:
        return [dict(row) for row in repo.conn.execute("SELECT * FROM garden_cards").fetchall()]
    if garden_card_ids:
        return [
            dict(row)
            for row in repo.conn.execute(
                "SELECT * FROM garden_cards WHERE id IN (%s)" % ",".join("?" for _ in garden_card_ids),
                garden_card_ids,
            ).fetchall()
        ]
    if not capture_ids:
        return []
    rows = [dict(row) for row in repo.conn.execute("SELECT * FROM garden_cards").fetchall()]
    capture_set = set(capture_ids)
    return [
        row for row in rows
        if capture_set & set(_json_list(row.get("capture_ids")))
    ]


def _garden_items(rows: list[dict[str, Any]]) -> list[CanvasItem]:
    items: list[CanvasItem] = []
    for row in rows:
        card_id = str(row.get("id") or "")
        title = str(row.get("title") or card_id or "未命名")
        items.append(
            CanvasItem(
                id=card_id,
                title=title,
                file_path=f"garden/{_safe_note_stem(title, card_id)}.md",
                kind=str(row.get("card_kind") or ""),
                summary=_garden_card_summary(row),
                source_ids=_json_list(row.get("capture_ids")),
            )
        )
    return items


def _garden_card_summary(row: dict[str, Any]) -> str:
    title = str(row.get("title") or "").strip()
    one_liner = str(row.get("one_liner") or "").strip()
    if one_liner and _norm(one_liner) != _norm(title):
        return one_liner

    claims = []
    for argument in _json_list_of_dicts(row.get("core_arguments")):
        claim = str(argument.get("claim") or "").strip()
        if claim:
            claims.append(claim)
    if claims:
        return "；".join(claims[:2])
    return one_liner


def _canvas_structure_from_plan(plan: dict[str, Any]) -> CanvasStructure:
    return CanvasStructure(
        core_claim=str(plan.get("core_claim") or ""),
        groups=[
            CanvasStructureGroup(
                role=str(group.get("role") or ""),
                title=str(group.get("title") or ""),
                card_ids=[str(card_id) for card_id in group.get("card_ids") or []],
                reason=str(group.get("reason") or ""),
            )
            for group in plan.get("groups") or []
            if isinstance(group, dict)
        ],
        evidence_ids=[str(item) for item in plan.get("evidence_ids") or []],
    )


def _capture_ids_from_cards(rows: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for row in rows:
        ids.extend(_json_list(row.get("capture_ids")))
    return _dedupe(ids)


def _write_canvas(vault_path: Path, title: str, json_text: str) -> Path:
    canvas_dir = vault_path / "canvas"
    canvas_dir.mkdir(parents=True, exist_ok=True)
    target = canvas_dir / safe_canvas_filename(title)
    if target.exists() and not _is_hermes_canvas(target):
        target = canvas_dir / safe_canvas_filename(f"{title}-Hermes").replace("-Hermes-关系图", "-关系图-Hermes")
    target.write_text(json_text + "\n", encoding="utf-8")
    return target


def _is_hermes_canvas(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    metadata = payload.get("metadata") if isinstance(payload, dict) else None
    return isinstance(metadata, dict) and metadata.get("generated_by") == "hermes"


def _canvas_preview(*, title: str, rel_path: str, capture_count: int, garden_count: int, canvas_mode: str = "theme_canvas") -> str:
    if canvas_mode == "garden_cluster":
        return "\n".join([
            f"已生成 Garden Card 聚类 Canvas：《{title}》",
            "",
            "里面包含：",
            f"- {garden_count} 张 Garden 卡",
            f"- {capture_count} 篇来源笔记",
            "",
            f"你可以在 Knowledge File storage 的 `{rel_path}` 打开它继续调整。",
        ])
    return "\n".join([
        f"已生成 Knowledge Canvas：《{title}》",
        "",
        "里面包含：",
        "- 1 个主题页",
        f"- {capture_count} 篇来源笔记",
        f"- {garden_count} 张 Garden 卡",
        "",
        f"你可以在 Knowledge File storage 的 `{rel_path}` 打开它继续调整。",
    ])


def _note_title(vault_path: Path, rel_path: str) -> str:
    note = read_note(vault_path, rel_path)
    if note:
        frontmatter, _body = note
        return str(frontmatter.get("title") or "")
    return ""


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else value
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item)]


def _json_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]") if isinstance(value, str) else value
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _safe_note_stem(title: str, card_id: str) -> str:
    cleaned = "".join(ch for ch in title if ch not in '/:*?"<>|\\').strip() or "未命名"
    return f"{cleaned}-{card_id}"


def _first_plain_paragraph(body: str) -> str:
    for block in re.split(r"\n\s*\n", body or ""):
        lines = [
            re.sub(r"^[#>*\-\d.\s]+", "", line).strip()
            for line in block.splitlines()
        ]
        text = " ".join(line for line in lines if line)
        if text:
            return text
    return ""


def _norm(value: str) -> str:
    return "".join(
        ch for ch in str(value or "").lower()
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result

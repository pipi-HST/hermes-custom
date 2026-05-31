"""Bridge for generating Obsidian Canvas relationship maps."""
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
    CanvasTheme,
    build_obsidian_canvas,
    safe_canvas_filename,
)
from agents.capture_agent import load_config
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
        },
        operation=lambda: _generate_canvas(
            repo,
            Path(resolved_vault_path).expanduser(),
            theme=theme,
            question_id=question_id,
            capture_ids=capture_ids or [],
            garden_card_ids=garden_card_ids or [],
        ),
    )


def _generate_canvas(
    repo: CaptureRepo,
    vault_path: Path,
    *,
    theme: str,
    question_id: str,
    capture_ids: list[str],
    garden_card_ids: list[str],
) -> dict[str, Any]:
    question_capture_ids = _capture_ids_for_question(repo, question_id)
    resolved_capture_ids = _dedupe(capture_ids + question_capture_ids)
    theme_node = _resolve_theme(vault_path, theme)
    if not theme_node:
        return error_envelope(
            agent="canvas",
            intent="generate_canvas",
            error="没有找到匹配的 Theme/MOC。可以先扫描主题，或直接说清楚要用哪几篇笔记生成画板。",
            chat_preview="没有找到匹配的 Theme/MOC。可以先扫描主题，或直接说清楚要用哪几篇笔记生成画板。",
            meta={"theme": theme, "question_id": question_id},
        )
    if not resolved_capture_ids:
        resolved_capture_ids = _capture_ids_from_theme_moc(repo, vault_path, theme_node.file_path)

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

    built = build_obsidian_canvas(
        CanvasBuildInput(
            title=theme_node.title,
            theme=theme_node,
            captures=captures,
            garden_cards=garden_items,
        )
    )
    warnings.extend(built.warnings)
    canvas_path = _write_canvas(vault_path, theme_node.title, built.json_text)
    rel_path = canvas_path.relative_to(vault_path).as_posix()
    chat_preview = _canvas_preview(
        title=canvas_path.stem,
        rel_path=rel_path,
        capture_count=len(captures),
        garden_count=len(garden_items),
    )
    return success_envelope(
        agent="canvas",
        intent="generate_canvas",
        chat_preview=chat_preview,
        durable_artifacts=[
            artifact_ref(
                type="obsidian_canvas",
                id=canvas_path.stem,
                title=canvas_path.stem,
                path=rel_path,
                status="created",
            )
        ],
        warnings=warnings,
        meta={
            "path": rel_path,
            "theme": theme_node.title,
            "capture_count": len(captures),
            "garden_count": len(garden_items),
            "node_count": built.node_count,
            "edge_count": built.edge_count,
        },
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
) -> list[dict[str, Any]]:
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
                summary=str(row.get("one_liner") or ""),
                source_ids=_json_list(row.get("capture_ids")),
            )
        )
    return items


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


def _canvas_preview(*, title: str, rel_path: str, capture_count: int, garden_count: int) -> str:
    return "\n".join([
        f"已生成 Obsidian Canvas：《{title}》",
        "",
        "里面包含：",
        "- 1 个主题页",
        f"- {capture_count} 篇来源笔记",
        f"- {garden_count} 张 Garden 卡",
        "",
        f"你可以在 Obsidian 的 `{rel_path}` 打开它继续调整。",
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

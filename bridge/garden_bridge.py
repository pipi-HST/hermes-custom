"""Knowledge Card bridge — 知识卡片合成入口."""
from __future__ import annotations
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import load_config
from agents.markdown_writer import append_garden_card_to_theme_page, read_note
from agents.garden_agent import (
    CARD_KIND_LABELS,
    GardenAgent,
    commit_garden_draft_to_card,
    create_garden_draft,
    get_garden_draft,
    get_latest_open_garden_draft,
    update_garden_draft,
)
from agents.garden_duplicates import find_garden_duplicate
from bridge.file_artifacts import write_markdown_export
from bridge.result_envelope import artifact_ref, error_envelope, next_action, success_envelope
from bridge.run_ledger import run_with_ledger
from db.schema import init_db
from db.repo import CaptureRepo

logger = logging.getLogger(__name__)


def _hydrate_capture(vault_path: Path, row: dict) -> dict:
    """从 .md 文件补充 title/summary/cached_text。"""
    file_path = row.get("file_path") or row.get("raw_path") or ""
    if not file_path:
        return row
    note = read_note(vault_path, file_path)
    if not note:
        return row
    frontmatter, body = note
    return {
        **row,
        "title": frontmatter.get("title", ""),
        "summary": frontmatter.get("story", ""),
        "tags": frontmatter.get("tags", []),
        "cached_text": body,
    }


def _json_list(text: str) -> list:
    try:
        value = json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _resolve_capture_row(conn, capture_id: str):
    capture_id = (capture_id or "").strip()
    if not capture_id:
        return None
    row = conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()
    if row:
        return row
    if len(capture_id) < 6:
        return None
    rows = conn.execute(
        "SELECT * FROM captures WHERE id LIKE ? ORDER BY updated_at DESC LIMIT 2",
        (f"{capture_id}%",),
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


def _draft_preview(
    title: str,
    one_liner: str,
    arguments: list[dict],
    draft_id: str,
    card_kind: str = "viewpoint",
    source_titles: list[str] | None = None,
) -> str:
    label = CARD_KIND_LABELS.get(card_kind, CARD_KIND_LABELS["viewpoint"])
    claims = []
    for item in arguments[:4]:
        claim = item.get("claim", "").strip()
        if claim:
            claims.append(f"- {claim}")
    claims_text = "\n".join(claims) if claims else "- 暂无明确观点，请补充修改意见"
    source_lines = []
    for index, source_title in enumerate(source_titles or [], 1):
        title_text = str(source_title or "").strip()
        if title_text:
            source_lines.append(f"[{index}] {title_text}")
    source_text = (
        "\n\n参考来源：\n" + "\n".join(source_lines)
        if source_lines else ""
    )
    return (
        f"已生成 Knowledge Card {label}草稿：《{title}》\n\n"
        f"一句话：{one_liner}\n\n"
        f"卡片预览：\n{claims_text}\n\n"
        f"草稿编号：{draft_id}\n"
        "满意回复“确认卡片”或“存入知识卡片”；不满意直接说怎么改。"
        f"{source_text}"
    )


def _safe_garden_filename(title: str, card_id: str) -> str:
    cleaned = "".join(ch for ch in title if ch not in '/:*?"<>|').strip()
    cleaned = cleaned[:48] or "未命名观点"
    return f"{cleaned}-{card_id}.md"


def _has_internal_prompt_markers(markdown: str) -> bool:
    markers = (
        "将以下结构化信息",
        "输出 Markdown",
        "结构化信息:",
        "结构化信息：",
    )
    return any(marker in (markdown or "") for marker in markers)


def _duplicate_garden_response(duplicate: dict) -> dict:
    match = duplicate.get("match") or {}
    item_type = match.get("type") or "garden_card"
    item_label = "已有知识卡片" if item_type == "garden_card" else "已有待确认知识卡片草稿"
    title = str(match.get("title") or "未命名").strip()
    item_id = str(match.get("id") or "").strip()
    reasons = duplicate.get("reasons") or []
    reason_text = "、".join(str(reason) for reason in reasons if reason) or "内容高度相似"
    artifact_type = "garden_card" if item_type == "garden_card" else "garden_draft"
    actions = []
    if artifact_type == "garden_draft":
        actions.append(
            next_action(
                label="确认已有卡片草稿入库",
                intent="commit_garden_draft",
                payload={"draft_id": item_id},
                requires_confirmation=True,
            )
        )
    return success_envelope(
        agent="garden",
        intent="synthesize_garden",
        chat_preview=(
            f"没有新建知识卡片草稿：这张卡和{item_label}《{title}》高度重复。\n\n"
            f"重复原因：{reason_text}\n\n"
            "建议直接沿用已有卡；如果你想保留新角度，可以告诉我“换一个角度生成”。"
        ),
        durable_artifacts=[
            artifact_ref(
                type=artifact_type,
                id=item_id,
                title=title,
                status="duplicate_match",
            )
        ],
        next_actions=actions,
        warnings=["Knowledge Card duplicate blocked"],
        meta={
            "duplicate_status": "duplicate",
            "duplicate_match": match,
            "duplicate_reasons": reasons,
        },
    )


def _possible_duplicate_warning(duplicate: dict) -> str:
    if duplicate.get("status") != "possible_duplicate":
        return ""
    match = duplicate.get("match") or {}
    title = str(match.get("title") or "未命名").strip()
    reasons = duplicate.get("reasons") or []
    reason_text = "、".join(str(reason) for reason in reasons if reason) or "内容相近"
    return f"提醒：可能和已有知识卡片相近：《{title}》（{reason_text}）。"


def _garden_card_markdown(
    draft: dict,
    card_id: str,
    *,
    source_links: list[str] | None = None,
    related_links: list[str] | None = None,
    source_theme: str = "",
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    frontmatter = {
        "type": "garden_card",
        "card_kind": draft.get("card_kind", "viewpoint"),
        "status": "seed",
        "title": draft.get("title", ""),
        "created": draft.get("created_at", ""),
        "updated": now,
    }
    if source_links:
        frontmatter["sources"] = source_links
    if related_links:
        frontmatter["related"] = related_links
    if source_theme:
        frontmatter["source_theme"] = source_theme
    body = (draft.get("markdown") or "").strip()
    return (
        "---\n"
        f"{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        "---\n\n"
        f"{body}\n"
    )


def _theme_path_for_question(conn, question_id: str) -> str:
    if not question_id:
        return ""
    row = conn.execute(
        "SELECT context_ref FROM pending_questions WHERE id=?",
        (question_id,),
    ).fetchone()
    if not row:
        return ""
    context_ref = row["context_ref"] or ""
    if context_ref.startswith("theme:"):
        return context_ref.removeprefix("theme:").strip()
    return ""


def _capture_property_links(conn, vault_path: Path, capture_ids: list[str]) -> list[str]:
    links: list[str] = []
    for capture_id in capture_ids:
        row = _resolve_capture_row(conn, capture_id)
        if not row:
            continue
        data = dict(row)
        file_path = data.get("file_path") or ""
        title = ""
        if file_path:
            note = read_note(vault_path, file_path)
            if note:
                title = str(note[0].get("title") or "").strip()
        title = title or Path(file_path).stem or str(data.get("id") or capture_id)
        if file_path:
            link = f"[[{Path(file_path).with_suffix('')}|{title}]]"
        else:
            link = title
        if link not in links:
            links.append(link)
    return links


def _garden_property_links(conn, card_ids: list[str]) -> list[str]:
    links: list[str] = []
    for card_id in card_ids:
        card_id = str(card_id or "").strip()
        if not card_id:
            continue
        row = conn.execute(
            "SELECT id, title FROM garden_cards WHERE id = ?",
            (card_id,),
        ).fetchone()
        if not row:
            continue
        title = str(row["title"] or "").strip() or card_id
        target = _safe_garden_filename(title, card_id).removesuffix(".md")
        link = f"[[garden/{target}|{title}]]"
        if link not in links:
            links.append(link)
    return links


def synthesize_garden(capture_ids: list[str] = None,
                      question_id: str = "",
                      title: str = "",
                      card_kind: str = "",
                      export_file: bool = False) -> dict:
    """合成知识卡片。

    Args:
        capture_ids: 要合成的 capture ID 列表（直接指定）
        question_id: pending_questions 表的 ID（从主题聚类问题触发）
        title: 可选标题，不填则由 LLM 生成
        card_kind: 可选卡片类型，不填则由 Knowledge Card Agent 判断

    Returns:
        Agent Result Envelope.
    """
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    return run_with_ledger(
        repo.conn,
        agent="garden",
        intent="synthesize_garden",
        trigger="wechat",
        input_ref={
            "capture_ids": capture_ids or [],
            "question_id": question_id,
            "title": title,
            "card_kind": card_kind,
            "export_file": export_file,
        },
        operation=lambda: _synthesize_garden(
            config,
            repo,
            capture_ids=capture_ids,
            question_id=question_id,
            title=title,
            card_kind=card_kind,
            export_file=export_file,
        ),
    )


def _synthesize_garden(
    config,
    repo: CaptureRepo,
    capture_ids: list[str] = None,
    question_id: str = "",
    title: str = "",
    card_kind: str = "",
    export_file: bool = False,
) -> dict:
    conn = repo.conn
    vault_path = Path(config.vault_path).expanduser()

    # 确定要合成的 captures
    if question_id:
        q_row = conn.execute(
            "SELECT * FROM pending_questions WHERE id = ?", (question_id,)
        ).fetchone()
        if not q_row:
            return error_envelope(
                agent="garden",
                intent="synthesize_garden",
                error=f"问题 {question_id} 不存在",
            )
        try:
            capture_ids = json.loads(q_row["related_capture_ids"])
        except (json.JSONDecodeError, TypeError):
            capture_ids = []

    if not capture_ids:
        return error_envelope(
            agent="garden",
            intent="synthesize_garden",
            error="未指定要合成的内容，请提供 capture_ids 或 question_id",
        )

    # 获取并 hydrate captures
    raw_rows = []
    for cid in capture_ids:
        row = _resolve_capture_row(conn, cid)
        if row:
            raw_rows.append(dict(row))

    if not raw_rows:
        return error_envelope(
            agent="garden",
            intent="synthesize_garden",
            error="未找到任何有效的 capture",
        )

    capture_ids = [r["id"] for r in raw_rows]
    captures = [_hydrate_capture(vault_path, r) for r in raw_rows]

    # 获取现有 garden cards（用于关联）
    existing_cards = [
        dict(r) for r in conn.execute("SELECT * FROM garden_cards").fetchall()
    ]

    # 调用 Knowledge Card Agent
    agent = GardenAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    result = agent.synthesize(
        captures,
        existing_cards,
        title=title,
        card_kind=card_kind,
    )

    if "error" in result:
        return error_envelope(
            agent="garden",
            intent="synthesize_garden",
            error=result["error"],
        )

    garden_data = {
        "title": result.get("title", ""),
        "one_liner": result.get("one_liner", ""),
        "card_kind": result.get("card_kind", "viewpoint"),
        "core_arguments": result.get("core_arguments", []),
        "key_materials": result.get("key_materials", []),
        "knowledge_links": result.get("knowledge_links", []),
        "markdown": result.get("markdown", ""),
        "capture_ids": capture_ids,
        "question_id": question_id,
        "instruction": title,
    }
    duplicate = find_garden_duplicate(conn, garden_data)
    if duplicate["status"] == "duplicate":
        return _duplicate_garden_response(duplicate)

    draft_id = create_garden_draft(repo, garden_data)

    title_text = result.get("title", "")
    one_liner = result.get("one_liner", "")
    duplicate_warning = _possible_duplicate_warning(duplicate)
    preview = _draft_preview(
        title_text,
        one_liner,
        result.get("core_arguments", []),
        draft_id,
        result.get("card_kind", "viewpoint"),
        [capture.get("title") or capture.get("id") or "" for capture in captures],
    )
    if duplicate_warning:
        preview = duplicate_warning + "\n\n" + preview
    artifacts = [
        artifact_ref(
            type="garden_draft",
            id=draft_id,
            title=title_text,
            status="draft_ready",
        )
    ]
    if export_file:
        artifacts.append(
            _garden_draft_export_artifact(
                draft_id=draft_id,
                title=title_text,
                markdown=result.get("markdown", ""),
            )
        )
    return success_envelope(
        agent="garden",
        intent="synthesize_garden",
        chat_preview=preview,
        durable_artifacts=artifacts,
        next_actions=[
            next_action(
                label="确认卡片入库",
                intent="commit_garden_draft",
                payload={"draft_id": draft_id},
                requires_confirmation=True,
            )
        ],
        meta={
            "title": title_text,
            "one_liner": one_liner,
            "card_kind": result.get("card_kind", "viewpoint"),
            "capture_ids": capture_ids,
            "draft_id": draft_id,
            "knowledge_link_count": len(result.get("knowledge_links", [])),
            "duplicate_status": duplicate["status"],
            "duplicate_match": duplicate.get("match") or {},
            "export_file": export_file,
        },
        warnings=[duplicate_warning] if duplicate_warning else [],
    )


def _garden_draft_export_artifact(
    *,
    draft_id: str,
    title: str,
    markdown: str,
    export_dir: Path | None = None,
) -> dict:
    title_text = (title or "Knowledge Card 草稿").strip()
    body = (markdown or "").strip()
    content = f"# {title_text}\n\n{body}" if body else f"# {title_text}\n"
    base_dir = export_dir or (Path(__file__).parent.parent / "data" / "exports" / "garden")
    path = write_markdown_export(
        base_dir=base_dir,
        stem=f"garden-draft-{draft_id or title_text}",
        content=content,
    )
    return artifact_ref(
        type="garden_draft_report",
        id=draft_id,
        title="Knowledge Card 草稿全文",
        path=path,
        status="created",
    )


def revise_garden_draft(draft_id: str = "", instruction: str = "") -> dict:
    """修改最近或指定 Knowledge Card Draft。"""
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    return run_with_ledger(
        repo.conn,
        agent="garden",
        intent="revise_garden_draft",
        trigger="wechat",
        input_ref={"draft_id": draft_id, "instruction": instruction},
        operation=lambda: _revise_garden_draft(
            config,
            repo,
            draft_id=draft_id,
            instruction=instruction,
        ),
    )


def _revise_garden_draft(
    config,
    repo: CaptureRepo,
    draft_id: str = "",
    instruction: str = "",
) -> dict:
    conn = repo.conn
    vault_path = Path(config.vault_path).expanduser()

    if not draft_id:
        latest = get_latest_open_garden_draft(repo)
        draft_id = latest["id"] if latest else ""
    draft = get_garden_draft(repo, draft_id) if draft_id else None
    if not draft:
        return error_envelope(
            agent="garden",
            intent="revise_garden_draft",
            error="没有找到可修改的知识卡片草稿。",
        )
    if not instruction.strip():
        return error_envelope(
            agent="garden",
            intent="revise_garden_draft",
            error="请说明你想怎么修改这个知识卡片草稿。",
        )

    capture_ids = _json_list(draft["capture_ids"])
    raw_rows = []
    for cid in capture_ids:
        row = conn.execute("SELECT * FROM captures WHERE id = ?", (cid,)).fetchone()
        if row:
            raw_rows.append(dict(row))
    captures = [_hydrate_capture(vault_path, r) for r in raw_rows]
    captures.append({
        "id": f"garden-draft-{draft_id}",
        "title": draft["title"],
        "cached_text": draft["markdown"],
    })
    captures.append({
        "id": f"garden-instruction-{draft_id}",
        "title": "用户修改意见",
        "cached_text": instruction.strip(),
    })

    existing_cards = [
        dict(r) for r in conn.execute("SELECT * FROM garden_cards").fetchall()
    ]
    agent = GardenAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    result = agent.synthesize(
        captures,
        existing_cards,
        title=draft["title"],
        card_kind=draft["card_kind"],
    )
    if "error" in result:
        return error_envelope(
            agent="garden",
            intent="revise_garden_draft",
            error=result["error"],
        )

    updated = update_garden_draft(repo, draft_id, {
        "title": result.get("title", draft["title"]),
        "one_liner": result.get("one_liner", draft["one_liner"]),
        "card_kind": result.get("card_kind", draft["card_kind"]),
        "core_arguments": result.get("core_arguments", []),
        "key_materials": result.get("key_materials", []),
        "knowledge_links": result.get("knowledge_links", []),
        "markdown": result.get("markdown", draft["markdown"]),
        "instruction": instruction,
    })

    return success_envelope(
        agent="garden",
        intent="revise_garden_draft",
        chat_preview=_draft_preview(
            updated["title"],
            updated["one_liner"],
            _json_list(updated["core_arguments"]),
            draft_id,
            updated["card_kind"],
        ),
        durable_artifacts=[
            artifact_ref(
                type="garden_draft",
                id=draft_id,
                title=updated["title"],
                status=updated["status"],
            )
        ],
        next_actions=[
            next_action(
                label="确认卡片入库",
                intent="commit_garden_draft",
                payload={"draft_id": draft_id},
                requires_confirmation=True,
            )
        ],
        meta={
            "draft_id": draft_id,
            "instruction": instruction,
            "card_kind": updated["card_kind"],
        },
    )


def commit_garden_draft(draft_id: str = "") -> dict:
    """确认 Knowledge Card Draft，写入 garden_cards 和 Obsidian garden 文件。"""
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    return run_with_ledger(
        repo.conn,
        agent="garden",
        intent="commit_garden_draft",
        trigger="wechat",
        input_ref={"draft_id": draft_id},
        operation=lambda: _commit_garden_draft(config, repo, draft_id=draft_id),
    )


def _commit_garden_draft(config, repo: CaptureRepo, draft_id: str = "") -> dict:
    vault_path = Path(config.vault_path).expanduser()

    if not draft_id:
        latest = get_latest_open_garden_draft(repo)
        draft_id = latest["id"] if latest else ""
    draft = get_garden_draft(repo, draft_id) if draft_id else None
    if not draft:
        return error_envelope(
            agent="garden",
            intent="commit_garden_draft",
            error="没有找到可确认入库的知识卡片草稿。",
        )
    if _has_internal_prompt_markers(draft["markdown"]):
        return error_envelope(
            agent="garden",
            intent="commit_garden_draft",
            error="这个知识卡片草稿包含内部模板痕迹，已阻止入库。请先重新生成或修改草稿。",
        )

    card_id = commit_garden_draft_to_card(repo, draft_id)
    if not card_id:
        return error_envelope(
            agent="garden",
            intent="commit_garden_draft",
            error="这个知识卡片草稿已经入库或无法入库。",
        )

    garden_dir = vault_path / "garden"
    garden_dir.mkdir(exist_ok=True)
    filename = _safe_garden_filename(draft["title"], card_id)
    md_path = garden_dir / filename
    source_links = _capture_property_links(
        repo.conn, vault_path, _json_list(draft["capture_ids"])
    )
    related_links = _garden_property_links(
        repo.conn, _json_list(draft["knowledge_links"])
    )
    source_theme = _theme_path_for_question(repo.conn, draft.get("question_id") or "")
    md_path.write_text(
        _garden_card_markdown(
            draft,
            card_id,
            source_links=source_links,
            related_links=related_links,
            source_theme=source_theme,
        ),
        encoding="utf-8",
    )
    rel_path = f"garden/{filename}"

    theme_sync_status = "not_applicable"
    theme_sync_error = ""
    if source_theme:
        try:
            synced = append_garden_card_to_theme_page(
                vault_path,
                source_theme,
                card_title=draft["title"],
                card_path=rel_path,
            )
            theme_sync_status = "synced" if synced else "missing_theme_page"
        except Exception as exc:
            logger.warning("Theme page sync failed for %s: %s", source_theme, exc)
            theme_sync_status = "failed"
            theme_sync_error = str(exc)

    if draft["question_id"]:
        repo.conn.execute(
            "UPDATE pending_questions SET status='answered', matched_response='synthesized' WHERE id=?",
            (draft["question_id"],),
        )
        repo.conn.commit()

    return success_envelope(
        agent="garden",
        intent="commit_garden_draft",
        chat_preview=f"已存入知识卡片：《{draft['title']}》",
        durable_artifacts=[
            artifact_ref(
                type="garden_card",
                id=card_id,
                title=draft["title"],
                path=rel_path,
                status="seed",
            )
        ],
        meta={
            "draft_id": draft_id,
            "card_id": card_id,
            "path": rel_path,
            "knowledge_link_count": len(_json_list(draft["knowledge_links"])),
            "source_theme": source_theme,
            "theme_sync_status": theme_sync_status,
            "theme_sync_error": theme_sync_error,
        },
    )

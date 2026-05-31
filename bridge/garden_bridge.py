"""Garden Agent bridge — 知识卡片合成入口."""
from __future__ import annotations
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import load_config
from agents.markdown_writer import read_note
from agents.garden_agent import (
    CARD_KIND_LABELS,
    GardenAgent,
    commit_garden_draft_to_card,
    create_garden_draft,
    get_garden_draft,
    get_latest_open_garden_draft,
    update_garden_draft,
)
from bridge.result_envelope import artifact_ref, error_envelope, next_action, success_envelope
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
) -> str:
    label = CARD_KIND_LABELS.get(card_kind, CARD_KIND_LABELS["viewpoint"])
    claims = []
    for item in arguments[:4]:
        claim = item.get("claim", "").strip()
        if claim:
            claims.append(f"- {claim}")
    claims_text = "\n".join(claims) if claims else "- 暂无明确观点，请补充修改意见"
    return (
        f"已生成 Garden {label}草稿：《{title}》\n\n"
        f"一句话：{one_liner}\n\n"
        f"卡片预览：\n{claims_text}\n\n"
        f"草稿编号：{draft_id}\n"
        "满意回复“确认卡片”或“存入花园”；不满意直接说怎么改。"
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


def _garden_card_markdown(draft: dict, card_id: str) -> str:
    now = datetime.now(timezone.utc).isoformat()
    frontmatter = {
        "id": card_id,
        "type": "garden_card",
        "card_kind": draft.get("card_kind", "viewpoint"),
        "status": "seed",
        "title": draft.get("title", ""),
        "source_captures": _json_list(draft.get("capture_ids", "[]")),
        "related_cards": _json_list(draft.get("knowledge_links", "[]")),
        "created": draft.get("created_at", ""),
        "updated": now,
    }
    body = (draft.get("markdown") or "").strip()
    return (
        "---\n"
        f"{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        "---\n\n"
        f"{body}\n"
    )


def synthesize_garden(capture_ids: list[str] = None,
                      question_id: str = "",
                      title: str = "",
                      card_kind: str = "") -> dict:
    """合成知识花园卡片。

    Args:
        capture_ids: 要合成的 capture ID 列表（直接指定）
        question_id: pending_questions 表的 ID（从主题聚类问题触发）
        title: 可选标题，不填则由 LLM 生成
        card_kind: 可选卡片类型，不填则由 GardenAgent 判断

    Returns:
        Agent Result Envelope.
    """
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
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

    # 调用 GardenAgent
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
    draft_id = create_garden_draft(repo, garden_data)

    title_text = result.get("title", "")
    one_liner = result.get("one_liner", "")
    return success_envelope(
        agent="garden",
        intent="synthesize_garden",
        chat_preview=_draft_preview(
            title_text,
            one_liner,
            result.get("core_arguments", []),
            draft_id,
            result.get("card_kind", "viewpoint"),
        ),
        durable_artifacts=[
            artifact_ref(
                type="garden_draft",
                id=draft_id,
                title=title_text,
                status="draft_ready",
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
            "title": title_text,
            "one_liner": one_liner,
            "card_kind": result.get("card_kind", "viewpoint"),
            "capture_ids": capture_ids,
            "draft_id": draft_id,
        },
    )


def revise_garden_draft(draft_id: str = "", instruction: str = "") -> dict:
    """修改最近或指定 Garden Draft。"""
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
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
            error="没有找到可修改的 Garden 草稿。",
        )
    if not instruction.strip():
        return error_envelope(
            agent="garden",
            intent="revise_garden_draft",
            error="请说明你想怎么修改这个 Garden 草稿。",
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
    """确认 Garden Draft，写入 garden_cards 和 Obsidian garden 文件。"""
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    vault_path = Path(config.vault_path).expanduser()

    if not draft_id:
        latest = get_latest_open_garden_draft(repo)
        draft_id = latest["id"] if latest else ""
    draft = get_garden_draft(repo, draft_id) if draft_id else None
    if not draft:
        return error_envelope(
            agent="garden",
            intent="commit_garden_draft",
            error="没有找到可确认入库的 Garden 草稿。",
        )
    if _has_internal_prompt_markers(draft["markdown"]):
        return error_envelope(
            agent="garden",
            intent="commit_garden_draft",
            error="这个 Garden 草稿包含内部模板痕迹，已阻止入库。请先重新生成或修改草稿。",
        )

    card_id = commit_garden_draft_to_card(repo, draft_id)
    if not card_id:
        return error_envelope(
            agent="garden",
            intent="commit_garden_draft",
            error="这个 Garden 草稿已经入库或无法入库。",
        )

    garden_dir = vault_path / "garden"
    garden_dir.mkdir(exist_ok=True)
    filename = _safe_garden_filename(draft["title"], card_id)
    md_path = garden_dir / filename
    md_path.write_text(_garden_card_markdown(draft, card_id), encoding="utf-8")
    rel_path = f"garden/{filename}"

    if draft["question_id"]:
        repo.conn.execute(
            "UPDATE pending_questions SET status='answered', matched_response='synthesized' WHERE id=?",
            (draft["question_id"],),
        )
        repo.conn.commit()

    return success_envelope(
        agent="garden",
        intent="commit_garden_draft",
        chat_preview=f"已存入知识花园：《{draft['title']}》",
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
        },
    )

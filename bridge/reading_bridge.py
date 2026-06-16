"""Reading Session bridge — temporary reading discussion state."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.capture_agent import load_config
from agents.embedder import Embedder
from agents.garden_agent import create_garden_card
from agents.library_agent import LibraryAgent
from agents.markdown_writer import read_note
from agents.outliner import Outliner
from bridge.result_envelope import (
    artifact_ref,
    error_envelope,
    next_action,
    source_ref,
    success_envelope,
)
from bridge.run_ledger import run_with_ledger
from db.reading_repo import ReadingRepo
from db.repo import CaptureRepo
from db.schema import init_db

_config = None
_repo: ReadingRepo | None = None
_capture_repo: CaptureRepo | None = None

CLARIFY_OUTPUT_TYPE = (
    "你想整理成哪种？\n"
    "1. 原文增强版主笔记：保留文章主线，并把刚才讨论补进去\n"
    "2. 局部学习笔记：只整理这次卡住的概念/段落\n"
    "3. 知识卡片：提炼成以后可复用的观点、方法、事实或风险\n"
    "回复 1/2/3 即可。"
)

OUTPUT_TYPES = {
    "enhance_capture": "原文增强版主笔记",
    "local_learning_note": "局部学习笔记",
    "knowledge_card": "知识卡片",
}


def _init():
    global _config, _repo, _capture_repo
    if _config is None:
        _config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
        conn = init_db(_config.db_path)
        _repo = ReadingRepo(conn)
        _capture_repo = CaptureRepo(conn)
    return _config, _repo, _capture_repo


def _run(intent: str, input_ref: dict, operation):
    _, repo, _ = _init()
    return run_with_ledger(
        repo.conn,
        agent="reading",
        intent=intent,
        trigger="wechat",
        input_ref=input_ref,
        operation=operation,
    )


def start_reading_session(
    message: str = "",
    source_url: str = "",
    capture_id: str = "",
    draft_id: str = "",
    excerpt: str = "",
    file_ref: str = "",
    image_ref: str = "",
) -> dict:
    """Start a temporary reading discussion without creating a capture draft."""
    return _run(
        "start_reading_session",
        {
            "message": message,
            "source_url": source_url,
            "capture_id": capture_id,
            "draft_id": draft_id,
            "has_excerpt": bool(excerpt),
            "file_ref": file_ref,
            "image_ref": image_ref,
        },
        lambda: _start_reading_session(
            message=message,
            source_url=source_url,
            capture_id=capture_id,
            draft_id=draft_id,
            excerpt=excerpt,
            file_ref=file_ref,
            image_ref=image_ref,
        ),
    )


def _start_reading_session(
    *,
    message: str = "",
    source_url: str = "",
    capture_id: str = "",
    draft_id: str = "",
    excerpt: str = "",
    file_ref: str = "",
    image_ref: str = "",
) -> dict:
    config, repo, capture_repo = _init()
    source_type = _infer_source_type(
        capture_id=capture_id,
        draft_id=draft_id,
        source_url=source_url,
        excerpt=excerpt,
        file_ref=file_ref,
        image_ref=image_ref,
    )
    source_title = ""
    source_id = capture_id or draft_id
    source_map = []
    if capture_id:
        capture = capture_repo.get_by_id(capture_id)
        if capture:
            source_title = _capture_title(Path(config.vault_path).expanduser(), capture)
            source_map.append(
                source_ref(
                    role="primary",
                    type="capture_note",
                    id=capture_id,
                    title=source_title,
                    path=capture.get("file_path", ""),
                )
            )
    if not source_title:
        source_title = _title_from_message(message) or _title_from_url(source_url)
    session = repo.create_session(
        source_type=source_type,
        source_id=source_id,
        source_url=source_url,
        source_title=source_title,
        focus=_derive_focus(message),
        meta={
            "initial_message": message,
            "excerpt_preview": _clip(excerpt, 500),
            "file_ref": file_ref,
            "image_ref": image_ref,
        },
    )
    preview_title = source_title or source_url or "这份材料"
    return success_envelope(
        agent="reading",
        intent="start_reading_session",
        chat_preview=(
            f"已进入阅读讨论：《{preview_title}》。\n"
            "你可以继续问这段什么意思、哪里重要，或让我和你的知识库/网页资料一起解释。"
        ),
        durable_artifacts=[
            artifact_ref(
                type="reading_session",
                id=session["id"],
                title=preview_title,
                status=session["status"],
            )
        ],
        source_map=source_map,
        meta={
            "reading_session_id": session["id"],
            "source_type": source_type,
            "source_id": source_id,
            "source_title": source_title,
            "expires_at": session["expires_at"],
        },
    )


def discuss_reading(
    session_id: str = "",
    message: str = "",
    focus: str = "",
    assistant_answer: str = "",
    evidence: list[dict[str, Any]] | None = None,
) -> dict:
    """Record and return a reading discussion turn."""
    return _run(
        "discuss_reading",
        {
            "session_id": session_id,
            "message": message,
            "focus": focus,
            "evidence_count": len(evidence or []),
        },
        lambda: _discuss_reading(
            session_id=session_id,
            message=message,
            focus=focus,
            assistant_answer=assistant_answer,
            evidence=evidence or [],
        ),
    )


def _discuss_reading(
    *,
    session_id: str = "",
    message: str = "",
    focus: str = "",
    assistant_answer: str = "",
    evidence: list[dict[str, Any]] | None = None,
) -> dict:
    _, repo, _ = _init()
    session = _resolve_session(repo, session_id)
    if not session:
        return error_envelope(
            agent="reading",
            intent="discuss_reading",
            error="没有找到当前阅读讨论。你可以先发原文、链接、截图或说明是哪篇文章。",
        )
    answer = assistant_answer.strip() or (
        "我已经把这个问题记到当前阅读讨论里。你可以继续补充你卡住的句子，"
        "我会围绕当前材料解释。"
    )
    turn = repo.add_turn(
        session["id"],
        user_message=message,
        assistant_answer=answer,
        focus=focus or _derive_focus(message) or session.get("focus", ""),
        confidence="medium",
    )
    source_map = []
    for item in evidence or []:
        ev = repo.add_evidence(
            session_id=session["id"],
            turn_id=turn["id"],
            source_kind=str(item.get("source_kind") or item.get("kind") or ""),
            source_id=str(item.get("source_id") or item.get("id") or ""),
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            excerpt=str(item.get("excerpt") or ""),
            used_for=str(item.get("used_for") or ""),
            meta=item.get("meta") if isinstance(item.get("meta"), dict) else None,
        )
        source_map.append(
            source_ref(
                role="evidence",
                type=ev.get("source_kind") or "reading_evidence",
                id=ev.get("source_id") or ev.get("id"),
                title=ev.get("title", ""),
                path=ev.get("url", ""),
                section=_clip(ev.get("excerpt", ""), 80),
            )
        )
    return success_envelope(
        agent="reading",
        intent="discuss_reading",
        chat_preview=_clip(answer, 1500),
        durable_artifacts=[
            artifact_ref(
                type="reading_session",
                id=session["id"],
                title=session.get("source_title") or "阅读讨论",
                status="active",
            )
        ],
        source_map=source_map,
        meta={
            "reading_session_id": session["id"],
            "reading_turn_id": turn["id"],
            "turn_index": turn["turn_index"],
            "evidence_count": len(source_map),
        },
    )


def show_reading_session(session_id: str = "") -> dict:
    """Show current Reading Session state."""
    return _run(
        "show_reading_session",
        {"session_id": session_id},
        lambda: _show_reading_session(session_id=session_id),
    )


def _show_reading_session(session_id: str = "") -> dict:
    _, repo, _ = _init()
    session = _resolve_session(repo, session_id)
    if not session:
        return error_envelope(
            agent="reading",
            intent="show_reading_session",
            error="没有找到当前阅读讨论。",
        )
    turns = repo.list_turns(session["id"])
    evidence = repo.list_evidence(session["id"])
    recent = "\n".join(
        f"- Q{turn['turn_index']}：{_clip(turn['user_message'], 60)}"
        for turn in turns[-5:]
    ) or "- 暂无问题"
    message = (
        f"当前阅读讨论：《{session.get('source_title') or '未命名材料'}》\n"
        f"焦点：{session.get('focus') or '未指定'}\n\n"
        f"最近问题：\n{recent}\n\n"
        f"已记录依据：{len(evidence)} 条"
    )
    return success_envelope(
        agent="reading",
        intent="show_reading_session",
        chat_preview=message,
        durable_artifacts=[
            artifact_ref(
                type="reading_session",
                id=session["id"],
                title=session.get("source_title") or "阅读讨论",
                status=session.get("status", ""),
            )
        ],
        meta={
            "reading_session_id": session["id"],
            "turn_count": len(turns),
            "evidence_count": len(evidence),
        },
    )


def summarize_reading_session(
    session_id: str = "",
    output_type: str = "",
    instruction: str = "",
) -> dict:
    """Create a reviewable Reading Draft, or clarify output type."""
    return _run(
        "summarize_reading_session",
        {
            "session_id": session_id,
            "output_type": output_type,
            "instruction": instruction,
        },
        lambda: _summarize_reading_session(
            session_id=session_id,
            output_type=output_type,
            instruction=instruction,
        ),
    )


def _summarize_reading_session(
    *,
    session_id: str = "",
    output_type: str = "",
    instruction: str = "",
) -> dict:
    config, repo, capture_repo = _init()
    session = _resolve_session(repo, session_id)
    if not session:
        return error_envelope(
            agent="reading",
            intent="summarize_reading_session",
            error="没有找到可整理的阅读讨论。",
        )
    normalized_type = _normalize_output_type(output_type)
    if not normalized_type:
        return success_envelope(
            agent="reading",
            intent="summarize_reading_session",
            chat_preview=CLARIFY_OUTPUT_TYPE,
            durable_artifacts=[
                artifact_ref(
                    type="reading_session",
                    id=session["id"],
                    title=session.get("source_title") or "阅读讨论",
                    status=session.get("status", ""),
                )
            ],
            meta={
                "reading_session_id": session["id"],
                "needs_clarification": True,
                "choices": list(OUTPUT_TYPES),
            },
        )
    turns = repo.list_turns(session["id"])
    evidence = repo.list_evidence(session["id"])
    target_capture_id = (
        session.get("source_id", "")
        if normalized_type == "enhance_capture" and session.get("source_type") == "capture"
        else ""
    )
    title = _draft_title(session, normalized_type)
    draft_text = _build_draft_text(
        vault_path=Path(config.vault_path).expanduser(),
        capture_repo=capture_repo,
        session=session,
        turns=turns,
        evidence=evidence,
        output_type=normalized_type,
        instruction=instruction,
    )
    draft = repo.create_draft(
        session_id=session["id"],
        output_type=normalized_type,
        target_capture_id=target_capture_id,
        title=title,
        summary=_draft_summary(turns),
        draft_text=draft_text,
        meta={"instruction": instruction},
    )
    return success_envelope(
        agent="reading",
        intent="summarize_reading_session",
        chat_preview=(
            f"已生成{OUTPUT_TYPES[normalized_type]}草稿：《{title}》\n\n"
            f"类型：{OUTPUT_TYPES[normalized_type]}\n"
            f"草稿编号：{draft['id']}\n\n"
            "回复“展开”查看全文；满意回复“确认”。"
        ),
        durable_artifacts=[
            artifact_ref(
                type="reading_draft",
                id=draft["id"],
                title=title,
                status=draft["status"],
            )
        ],
        next_actions=[
            next_action(
                label="确认",
                intent="commit_reading_artifact",
                payload={"draft_id": draft["id"]},
                requires_confirmation=True,
            )
        ],
        meta={
            "reading_session_id": session["id"],
            "reading_draft_id": draft["id"],
            "output_type": normalized_type,
            "target_capture_id": target_capture_id,
            "needs_clarification": False,
        },
    )


def commit_reading_artifact(draft_id: str = "") -> dict:
    """Commit a Reading Draft after user confirmation."""
    return _run(
        "commit_reading_artifact",
        {"draft_id": draft_id},
        lambda: _commit_reading_artifact(draft_id=draft_id),
    )


def _commit_reading_artifact(draft_id: str = "") -> dict:
    config, repo, capture_repo = _init()
    draft = repo.get_draft(draft_id) if draft_id else repo.get_latest_open_draft()
    if not draft:
        return error_envelope(
            agent="reading",
            intent="commit_reading_artifact",
            error="没有找到可确认的阅读整理草稿。",
        )
    if draft.get("status") != "open":
        return error_envelope(
            agent="reading",
            intent="commit_reading_artifact",
            error=f"阅读整理草稿已是 {draft.get('status')} 状态，不能重复确认。",
        )
    output_type = draft.get("output_type", "")
    vault_path = Path(config.vault_path).expanduser()
    if output_type == "enhance_capture":
        return _commit_enhanced_capture(vault_path, repo, capture_repo, draft)
    if output_type == "knowledge_card":
        return _commit_knowledge_card(vault_path, repo, draft)
    if output_type in {"local_learning_note", "discussion_note"}:
        return _commit_independent_note(config, vault_path, repo, capture_repo, draft)
    return error_envelope(
        agent="reading",
        intent="commit_reading_artifact",
        error=f"未知阅读整理类型：{output_type}",
    )


def _commit_independent_note(
    config,
    vault_path: Path,
    repo: ReadingRepo,
    capture_repo: CaptureRepo,
    draft: dict,
) -> dict:
    note_type = "learning_note" if draft["output_type"] == "local_learning_note" else "discussion_note"
    folder = "learning" if note_type == "learning_note" else "reading"
    capture = capture_repo.insert(file_path="", raw_path="")
    capture_id = capture["id"]
    draft_title = draft.get("title") or "阅读整理"
    draft_summary = draft.get("summary") or ""
    frontmatter = {
        "id": capture_id,
        "type": note_type,
        "status": "processed",
        "title": draft_title,
        "reading_session_id": draft["session_id"],
        "reading_draft_id": draft["id"],
        "created": _now(),
    }
    # Enrich frontmatter for better RAG identity embedding
    if draft_summary:
        frontmatter["story"] = draft_summary
    tags = _extract_tags(draft_title, draft.get("draft_text", ""))
    if tags:
        frontmatter["tags"] = tags
    rel_path = _write_note_file(
        vault_path=vault_path,
        folder=folder,
        title=draft_title,
        draft_id=capture_id,
        frontmatter=frontmatter,
        body=draft.get("draft_text", ""),
    )
    capture_repo.update(capture_id, file_path=rel_path, raw_path="")
    library_report = _postprocess_committed_learning_note(config, repo, capture_id)
    repo.update_draft(draft["id"], status="committed")
    repo.update_session(draft["session_id"], status="committed", committed_artifact_id=rel_path)
    return success_envelope(
        agent="reading",
        intent="commit_reading_artifact",
        chat_preview=f"已保存阅读整理：《{draft.get('title') or '阅读整理'}》。已加入知识库检索索引。",
        durable_artifacts=[
            artifact_ref(
                type=note_type,
                id=capture_id,
                title=draft.get("title") or "阅读整理",
                path=rel_path,
                status="created",
            )
        ],
        meta={
            "reading_draft_id": draft["id"],
            "capture_id": capture_id,
            "artifact_type": note_type,
            "file_path": rel_path,
            "library_postprocess": library_report,
        },
    )


def _postprocess_committed_learning_note(config, repo: ReadingRepo, capture_id: str) -> dict:
    try:
        outliner = Outliner(
            api_key=getattr(config, "llm_api_key", ""),
            model="deepseek-v4-flash",
            base_url=getattr(config, "llm_base_url", "") or "https://api.deepseek.com",
        )
        agent = LibraryAgent(
            config.vault_path,
            outliner=outliner,
            db_conn=repo.conn,
            embedder=(
                Embedder(api_key=getattr(config, "qwen_api_key", ""))
                if getattr(config, "qwen_api_key", "")
                else None
            ),
            min_outline_chars=2000,
        )
        return agent.maintain_capture(capture_id, apply=True, render_outline=True)
    except Exception as exc:
        return {"error": str(exc)}


def _commit_knowledge_card(vault_path: Path, repo: ReadingRepo, draft: dict) -> dict:
    garden_data = _garden_data_from_reading_draft(repo, draft)
    card_id = create_garden_card(repo, garden_data)
    rel_path = _write_note_file(
        vault_path=vault_path,
        folder="garden",
        title=draft.get("title") or "知识卡片",
        draft_id=card_id,
        frontmatter={
            "type": "garden_card",
            "card_kind": garden_data["card_kind"],
            "status": "seed",
            "title": draft.get("title") or "知识卡片",
            "garden_card_id": card_id,
            "reading_session_id": draft["session_id"],
            "created": _now(),
        },
        body=draft.get("draft_text", ""),
    )
    repo.update_draft(draft["id"], status="committed")
    repo.update_session(draft["session_id"], status="committed", committed_artifact_id=rel_path)
    return success_envelope(
        agent="reading",
        intent="commit_reading_artifact",
        chat_preview=f"已保存知识卡片：《{draft.get('title') or '知识卡片'}》",
        durable_artifacts=[
            artifact_ref(
                type="knowledge_card",
                id=card_id,
                title=draft.get("title") or "知识卡片",
                path=rel_path,
                status="created",
            )
        ],
        meta={
            "reading_draft_id": draft["id"],
            "garden_card_id": card_id,
            "artifact_type": "knowledge_card",
            "file_path": rel_path,
        },
    )


def _classify_card_kind(
    *, title: str, summary: str = "", body: str = ""
) -> str:
    """Heuristic card_kind classification from draft content.

    Priority: risk > method > fact > viewpoint (default).
    Uses weighted keyword scoring across title + summary + body.
    """
    text = f"{title}\n{summary}\n{body}".lower()

    scores: dict[str, float] = {
        "method": 0.0,
        "risk": 0.0,
        "fact": 0.0,
    }

    # Title matches are worth more (stronger signal)
    title_lower = title.lower()

    method_kw = [
        "步骤", "流程", "方法", "做法", "实践", "框架", "模式",
        "workflows", "pipeline", "how to", "实现", "搭建", "构建",
        "配置", "setup", "tutorial",
    ]
    risk_kw = [
        "风险", "注意", "陷阱", "坑", "问题", "失败", "缺陷",
        "pitfall", "gotcha", "caveat", "warning", "danger",
        "踩坑", "容易出错", "容易忽略",
    ]
    fact_kw = [
        "数据", "统计", "事实", "结论", "发现", "基准", "benchmark",
        "指标", "metric", "比例", "规模", "增长",
    ]

    for kw in method_kw:
        if kw in title_lower:
            scores["method"] += 3.0
        elif kw in text:
            scores["method"] += 1.0
    for kw in risk_kw:
        if kw in title_lower:
            scores["risk"] += 3.0
        elif kw in text:
            scores["risk"] += 1.0
    for kw in fact_kw:
        if kw in title_lower:
            scores["fact"] += 3.0
        elif kw in text:
            scores["fact"] += 1.0

    # Pick the highest-scoring non-viewpoint kind; need at least 2 points
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    if scores[best] >= 2.0:
        return best
    return "viewpoint"


def _garden_data_from_reading_draft(repo: ReadingRepo, draft: dict) -> dict:
    session = repo.get_session(draft["session_id"]) or {}
    evidence = repo.list_evidence(draft["session_id"])
    capture_ids = _reading_card_capture_ids(session, evidence)
    source_materials = _reading_card_key_materials(session, evidence)
    title = draft.get("title") or "知识卡片"
    one_liner = _clip(draft.get("summary") or draft.get("draft_text", ""), 180)
    if not one_liner:
        one_liner = "从阅读讨论中沉淀出来的可复用知识。"
    draft_text = draft.get("draft_text", "")
    card_kind = _classify_card_kind(
        title=title,
        summary=draft.get("summary") or "",
        body=draft_text,
    )
    return {
        "title": title,
        "one_liner": one_liner,
        "card_kind": card_kind,
        "core_arguments": [
            {
                "claim": _clip(draft.get("summary") or session.get("focus") or title, 180),
                "why_it_matters": "来自一次围绕材料的阅读讨论，适合后续作为理解线索复用。",
            }
        ],
        "knowledge_links": [],
        "key_materials": source_materials,
        "my_view": "",
        "status": "seed",
        "capture_ids": capture_ids,
    }


def _reading_card_capture_ids(session: dict, evidence: list[dict]) -> list[str]:
    ids: list[str] = []
    if session.get("source_type") == "capture" and session.get("source_id"):
        ids.append(str(session["source_id"]))
    for item in evidence:
        if item.get("source_kind") in {"capture", "capture_note", "knowledge_base"} and item.get("source_id"):
            ids.append(str(item["source_id"]))
    return _dedupe(ids)


def _reading_card_key_materials(session: dict, evidence: list[dict]) -> list[dict]:
    materials: list[dict] = []
    if session.get("source_title") or session.get("source_url") or session.get("source_id"):
        materials.append(
            {
                "type": _source_label(session.get("source_type", "")),
                "content": session.get("source_title") or session.get("source_url") or session.get("source_id"),
                "source_id": session.get("source_id", ""),
            }
        )
    for item in evidence[:6]:
        materials.append(
            {
                "type": _source_label(item.get("source_kind", "")),
                "content": _clip(item.get("excerpt") or item.get("title") or item.get("url", ""), 240),
                "source_id": item.get("source_id", ""),
            }
        )
    return materials


def _commit_enhanced_capture(
    vault_path: Path,
    repo: ReadingRepo,
    capture_repo: CaptureRepo,
    draft: dict,
) -> dict:
    capture_id = draft.get("target_capture_id", "")
    capture = capture_repo.get_by_id(capture_id)
    if not capture or not capture.get("file_path"):
        return error_envelope(
            agent="reading",
            intent="commit_reading_artifact",
            error="增强版需要绑定已有 Capture，但没有找到目标主笔记。",
        )
    full_path = vault_path / capture["file_path"]
    if not full_path.exists():
        return error_envelope(
            agent="reading",
            intent="commit_reading_artifact",
            error=f"目标 Capture 文件不存在：{capture['file_path']}",
        )
    old_text = full_path.read_text(encoding="utf-8")
    previous_hash = _sha256(old_text)
    backup_path = _backup_capture_file(full_path)
    new_text = _merge_frontmatter(old_text, draft.get("draft_text", ""))
    full_path.write_text(new_text, encoding="utf-8")
    new_hash = _sha256(new_text)
    backup = repo.record_capture_backup(
        capture_id=capture_id,
        reading_draft_id=draft["id"],
        backup_file_path=str(backup_path),
        previous_file_hash=previous_hash,
        new_file_hash=new_hash,
    )
    repo.update_draft(draft["id"], status="committed")
    repo.update_session(
        draft["session_id"],
        status="committed",
        committed_artifact_id=capture["file_path"],
    )
    return success_envelope(
        agent="reading",
        intent="commit_reading_artifact",
        chat_preview=f"已把阅读讨论补进主 Capture：《{draft.get('title') or capture_id}》。旧版已备份。",
        durable_artifacts=[
            artifact_ref(
                type="capture_note",
                id=capture_id,
                title=draft.get("title") or capture_id,
                path=capture["file_path"],
                status="updated",
            ),
            artifact_ref(
                type="capture_backup",
                id=backup["id"],
                title="旧版 Capture 备份",
                path=str(backup_path),
                status="created",
            ),
        ],
        meta={
            "reading_draft_id": draft["id"],
            "capture_id": capture_id,
            "backup_file_path": str(backup_path),
            "previous_file_hash": previous_hash,
            "new_file_hash": new_hash,
        },
    )


def _resolve_session(repo: ReadingRepo, session_id: str = "") -> dict | None:
    session_id = (session_id or "").strip()
    if session_id:
        return repo.get_session(session_id)
    return repo.get_latest_active()


def _infer_source_type(
    *,
    capture_id: str,
    draft_id: str,
    source_url: str,
    excerpt: str,
    file_ref: str,
    image_ref: str,
) -> str:
    if capture_id:
        return "capture"
    if draft_id:
        return "draft"
    if image_ref:
        return "image"
    if file_ref:
        return "file"
    if source_url:
        return "url"
    if excerpt:
        return "excerpt"
    return "excerpt"


def _normalize_output_type(output_type: str) -> str:
    text = (output_type or "").strip()
    mapping = {
        "1": "enhance_capture",
        "2": "local_learning_note",
        "3": "knowledge_card",
        "enhanced_capture": "enhance_capture",
        "enhance_capture": "enhance_capture",
        "原文增强版": "enhance_capture",
        "原文增强版主笔记": "enhance_capture",
        "local_learning_note": "local_learning_note",
        "learning_note": "local_learning_note",
        "局部学习笔记": "local_learning_note",
        "discussion_note": "local_learning_note",
        "讨论札记": "local_learning_note",
        "knowledge_card": "knowledge_card",
        "card": "knowledge_card",
        "知识卡片": "knowledge_card",
        "卡片": "knowledge_card",
        "做成卡片": "knowledge_card",
    }
    if text in mapping:
        return mapping[text]
    if "卡片" in text:
        return "knowledge_card"
    if "局部" in text or "学习笔记" in text or "札记" in text:
        return "local_learning_note"
    if "增强" in text or "主笔记" in text:
        return "enhance_capture"
    return ""


def _capture_title(vault_path: Path, capture: dict) -> str:
    note = read_note(vault_path, capture.get("file_path", ""))
    if not note:
        return Path(capture.get("file_path") or "").stem
    return str(note[0].get("title") or Path(capture.get("file_path") or "").stem)


def _title_from_url(source_url: str) -> str:
    if not source_url:
        return ""
    return re.sub(r"^https?://", "", source_url).strip("/")[:80]


def _title_from_message(message: str) -> str:
    text = (message or "").strip()
    match = re.search(r"《(.+?)》", text)
    if match:
        return match.group(1).strip()
    return ""


def _derive_focus(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return ""
    return _clip(re.sub(r"https?://\S+", "", text).strip(), 80)


def _draft_title(session: dict, output_type: str) -> str:
    base = session.get("source_title") or "阅读整理"
    if output_type == "enhance_capture":
        return str(base)
    suffix = {
        "local_learning_note": "学习笔记",
        "knowledge_card": "知识卡片",
    }.get(output_type, "学习笔记")
    return f"{base}-{suffix}"


def _draft_summary(turns: list[dict]) -> str:
    if not turns:
        return "整理当前阅读讨论。"
    return _clip(turns[-1].get("assistant_answer", ""), 140) or "整理当前阅读讨论。"


def _build_draft_text(
    *,
    vault_path: Path,
    capture_repo: CaptureRepo,
    session: dict,
    turns: list[dict],
    evidence: list[dict],
    output_type: str,
    instruction: str,
) -> str:
    discussion = _discussion_section(turns, evidence)
    if output_type == "enhance_capture":
        base_body = _source_capture_body(vault_path, capture_repo, session.get("source_id", ""))
        return (
            f"{base_body.rstrip()}\n\n"
            "## 阅读讨论补充\n\n"
            f"{discussion}\n"
        ).strip()
    title = _draft_title(session, output_type)
    source_title = (session.get("source_title") or "").strip()
    topic = _clip(source_title, 20) if source_title else (session.get("focus") or "阅读讨论")
    if output_type == "knowledge_card":
        lead = f"## {topic} 的可复用知识"
    else:
        lead = f"## {topic} 的要点"
    return (
        f"# {title}\n\n"
        f"{lead}\n\n"
        f"{session.get('focus') or instruction or '本次阅读讨论'}\n\n"
        f"{discussion}\n"
    ).strip()


def _discussion_section(turns: list[dict], evidence: list[dict]) -> str:
    lines = ["## 关键问题与解释", ""]
    if not turns:
        lines.extend(["暂无具体问答。", ""])
    for turn in turns:
        lines.extend(
            [
                f"### 问题 {turn['turn_index']}：{turn.get('user_message') or '未命名问题'}",
                "",
                turn.get("assistant_answer") or "",
                "",
            ]
        )
    if evidence:
        lines.extend(["## 来源", ""])
        for index, item in enumerate(evidence, 1):
            title = item.get("title") or item.get("url") or item.get("source_id") or "未命名来源"
            label = _source_label(item.get("source_kind", ""))
            excerpt = _clip(item.get("excerpt", ""), 160)
            line = f"- [{label}] {title}"
            if excerpt:
                line += f"：{excerpt}"
            lines.append(line)
    return "\n".join(lines).strip()


def _source_capture_body(vault_path: Path, capture_repo: CaptureRepo, capture_id: str) -> str:
    capture = capture_repo.get_by_id(capture_id)
    if not capture:
        return "# 阅读整理\n"
    note = read_note(vault_path, capture.get("file_path", ""))
    if not note:
        return "# 阅读整理\n"
    _, body = note
    return body.strip() or "# 阅读整理\n"


def _source_label(source_kind: str) -> str:
    return {
        "original": "原文",
        "capture": "主笔记",
        "capture_note": "主笔记",
        "knowledge_base": "知识库",
        "web": "网页",
        "url": "网页",
        "file": "文件",
        "excerpt": "摘录",
    }.get(source_kind or "", source_kind or "来源")


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _write_note_file(
    *,
    vault_path: Path,
    folder: str,
    title: str,
    draft_id: str,
    frontmatter: dict[str, Any],
    body: str,
) -> str:
    safe_title = _safe_filename(title)
    relative = Path(folder) / f"{safe_title}-{draft_id[:8]}.md"
    full_path = vault_path / relative
    full_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        f"{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        "---\n\n"
        f"{body.strip()}\n"
    )
    full_path.write_text(content, encoding="utf-8")
    return str(relative)


def _safe_filename(title: str) -> str:
    text = (title or "阅读整理").strip() or "阅读整理"
    text = re.sub(r'[/\\:*?"<>|？]', "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:48] or "阅读整理"


def _backup_capture_file(full_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path(__file__).parent.parent / "data" / "backups" / "reading"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{full_path.stem}-{timestamp}{full_path.suffix}"
    shutil.copy2(full_path, backup_path)
    return backup_path


def _merge_frontmatter(old_text: str, draft_text: str) -> str:
    if not old_text.startswith("---"):
        return draft_text.strip() + "\n"
    parts = old_text.split("---", 2)
    if len(parts) < 3:
        return draft_text.strip() + "\n"
    frontmatter = parts[1]
    body = _strip_frontmatter(draft_text)
    return f"---{frontmatter}---\n\n{body.strip()}\n"


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2] if len(parts) >= 3 else text


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_tags(title: str, body: str = "") -> list[str]:
    """Extract 1-3 short tags from title/body for identity embedding enrichment."""
    import re as _re

    tags: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        clean = _re.sub(r"[#*_`>\[\]()（）\-—]+", "", text).strip()
        clean = _re.sub(r"\s+", " ", clean)
        if 2 <= len(clean) <= 12 and clean not in seen:
            seen.add(clean)
            tags.append(clean)

    # From title: extract the core topic (skip generic suffixes)
    core = _re.sub(
        r"[-\s]*(学习笔记|知识卡片|阅读整理|讨论笔记|整理)$", "", title or ""
    ).strip()
    if core:
        _add(core)

    # From body: find the first meaningful ## heading
    for line in (body or "").splitlines():
        m = _re.match(r"^#{2,3}\s+(.+)", line.strip())
        if m:
            heading = m.group(1).strip()
            # Skip managed/generic headings
            if heading in ("参考来源", "来源", "目录纲要", "系统记录"):
                continue
            # Skip headings that contain the topic prefix markers
            prefix_stripped = _re.sub(r"^(问题|解释|讨论)[：:]\s*", "", heading)
            _add(prefix_stripped or heading)
            if len(tags) >= 3:
                break

    return tags[:3]


def _clip(text: str, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

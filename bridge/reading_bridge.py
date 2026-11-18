"""Reading Session bridge — temporary reading discussion state."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys

import httpx
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
from agents.summarizer import Summarizer
from bridge.context import KnowledgeContext
from bridge.knowledge_write_service import KnowledgeWriteService, MarkdownWriteRequest, KnowledgeWriteResult
from agents.outliner import Outliner
from bridge.ai_cancel import AIOperationCancelled, raise_if_cancelled
from bridge.atomic_io import resolve_inside
from bridge.knowledge_write_service import MarkdownVersionConflict
from bridge.result_envelope import (
    artifact_ref,
    error_envelope,
    next_action,
    source_ref,
    success_envelope,
)
from bridge.run_ledger import run_with_ledger
from db.concurrency import connection_lock
from db.reading_repo import ReadingRepo
from db.repo import CaptureRepo
from db.schema import init_db
from db.write_job_repo import KnowledgeWriteJobRepo
from bridge.config_paths import load_bridge_config

_config = None
_repo: ReadingRepo | None = None
_capture_repo: CaptureRepo | None = None
logger = logging.getLogger(__name__)

CLARIFY_OUTPUT_TYPE = (
    "你想整理成哪种？\n"
    "1. 原文增强版主笔记：保留文章主线，并把刚才讨论补进去\n"
    "2. 资料笔记：把这次卡住的概念/段落保存为知识文件\n"
    "3. 知识卡片：提炼成以后可复用的观点、方法、事实或风险\n"
    "回复 1/2/3 即可。"
)

OUTPUT_TYPES = {
    "enhance_capture": "原文增强版主笔记",
    "local_learning_note": "资料笔记",
    "knowledge_card": "知识卡片",
}

ENHANCEMENT_SOURCE_HASH = "source_file_hash"


def _init(context: KnowledgeContext | None = None):
    if context is not None:
        return context.config, context.reading_repo(), context.capture_repo()
    global _config, _repo, _capture_repo
    if _config is None:
        _config = load_bridge_config(loader=load_config)
        conn = init_db(_config.db_path)
        _repo = ReadingRepo(conn)
        _capture_repo = CaptureRepo(conn)
    return _config, _repo, _capture_repo


def _run(intent: str, input_ref: dict, operation, context: KnowledgeContext | None = None):
    _, repo, _ = _init(context)
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
    context: KnowledgeContext | None = None,
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
            context=context,
        ),
        context=context,
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
    context: KnowledgeContext | None = None,
) -> dict:
    config, repo, capture_repo = _init(context)
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
    context: KnowledgeContext | None = None,
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
            context=context,
        ),
        context=context,
    )


def _discuss_reading(
    *,
    session_id: str = "",
    message: str = "",
    focus: str = "",
    assistant_answer: str = "",
    evidence: list[dict[str, Any]] | None = None,
    context: KnowledgeContext | None = None,
) -> dict:
    _, repo, _ = _init(context)
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


def show_reading_session(session_id: str = "", context: KnowledgeContext | None = None) -> dict:
    """Show current Reading Session state."""
    return _run(
        "show_reading_session",
        {"session_id": session_id},
        lambda: _show_reading_session(session_id=session_id, context=context),
        context=context,
    )


def _show_reading_session(session_id: str = "", context: KnowledgeContext | None = None) -> dict:
    _, repo, _ = _init(context)
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
    context: KnowledgeContext | None = None,
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
            context=context,
        ),
        context=context,
    )


def _summarize_reading_session(
    *,
    session_id: str = "",
    output_type: str = "",
    instruction: str = "",
    context: KnowledgeContext | None = None,
) -> dict:
    config, repo, capture_repo = _init(context)
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
    source_body, source_meta = "", {}
    if normalized_type == "enhance_capture":
        try:
            source_body, source_meta = _enhancement_draft_base(
                Path(config.vault_path).expanduser(), capture_repo, target_capture_id,
            )
        except ValueError as exc:
            return error_envelope(agent="reading", intent="summarize_reading_session", error=str(exc))
    title = _draft_title(session, normalized_type)
    draft_text = _build_draft_text(
        session=session,
        turns=turns,
        evidence=evidence,
        output_type=normalized_type,
        instruction=instruction,
        source_body=source_body,
    )
    metadata = _generate_full_text_metadata(config, draft_text)
    draft = repo.create_draft(
        session_id=session["id"],
        output_type=normalized_type,
        target_capture_id=target_capture_id,
        title=title,
        summary=metadata.get("summary") or _draft_summary(turns),
        draft_text=draft_text,
        meta={"instruction": instruction, "metadata": metadata, **source_meta},
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


def commit_reading_artifact(
    draft_id: str = "",
    confirmation: str = "",
    confirmation_source: str = "agent",
    token_verified: bool = False,
    context: KnowledgeContext | None = None,
) -> dict:
    """Commit a Reading Draft after user confirmation."""
    from bridge.confirmation import confirmation_error

    gate_error = confirmation_error(
        "commit_reading_artifact",
        confirmation=confirmation,
        confirmation_source=confirmation_source,
        token_verified=token_verified,
    )
    if gate_error:
        return error_envelope(
            agent="reading",
            intent="commit_reading_artifact",
            error=gate_error,
        )
    _, _, capture_repo = _init(context)
    write_jobs = KnowledgeWriteJobRepo(capture_repo.conn)
    job_id = write_jobs.start(
        intent="commit_reading_artifact",
        object_type="reading_draft",
        object_id=draft_id,
        payload={"draft_id": draft_id},
    )

    def operation() -> dict:
        try:
            write_jobs.mark_step(job_id, status="writing_file", step="commit_reading_artifact")
            result = _commit_reading_artifact(draft_id=draft_id, context=context)
            if result.get("status") == "error":
                write_jobs.fail(job_id, step="commit_reading_artifact", error="; ".join(result.get("errors") or []))
            else:
                meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
                db_table = "garden_cards" if meta.get("garden_card_id") else "captures"
                capture_id = str(meta.get("capture_id") or "")
                embedding = None
                if capture_id:
                    embedding = capture_repo.conn.execute(
                        """SELECT 1 FROM capture_embeddings
                           WHERE capture_id = ? AND kind = 'identity'
                             AND embedding IS NOT NULL
                           LIMIT 1""",
                        (capture_id,),
                    ).fetchone()
                if capture_id and not embedding:
                    write_jobs.repair_needed(
                        job_id,
                        step="identity_embedding",
                        error="identity embedding pending or failed",
                        file_path=str(meta.get("file_path") or ""),
                        db_table="captures,capture_embeddings",
                    )
                else:
                    write_jobs.complete(
                        job_id,
                        file_path=str(meta.get("file_path") or ""),
                        db_table=db_table,
                    )
            return result
        except AIOperationCancelled as exc:
            write_jobs.fail(
                job_id,
                step=exc.stage or "commit_reading_artifact",
                error=str(exc),
                status="cancelled",
            )
            raise
        except Exception as exc:
            write_jobs.fail(job_id, step="commit_reading_artifact", error=str(exc))
            raise

    return _run(
        "commit_reading_artifact",
        {"draft_id": draft_id},
        operation,
        context=context,
    )


def discard_reading_artifact(draft_id: str = "", context: KnowledgeContext | None = None) -> dict:
    """Mark a reviewable Reading Draft as discarded."""
    return _run(
        "discard_reading_artifact",
        {"draft_id": draft_id},
        lambda: _discard_reading_artifact(draft_id=draft_id, context=context),
        context=context,
    )


def _discard_reading_artifact(draft_id: str = "", context: KnowledgeContext | None = None) -> dict:
    _, repo, _ = _init(context)
    draft = repo.get_draft(draft_id) if draft_id else repo.get_latest_open_draft()
    if not draft:
        return error_envelope(
            agent="reading",
            intent="discard_reading_artifact",
            error="没有找到可删除的阅读整理草稿。",
        )
    if draft.get("status") == "committed":
        return error_envelope(
            agent="reading",
            intent="discard_reading_artifact",
            error="阅读整理草稿已保存，不能删除。",
        )
    if draft.get("status") == "discarded":
        return error_envelope(
            agent="reading",
            intent="discard_reading_artifact",
            error="阅读整理草稿已删除，不能重复删除。",
        )
    updated = repo.update_draft(draft["id"], status="discarded") or draft
    title = updated.get("title") or draft.get("title") or "阅读整理草稿"
    return success_envelope(
        agent="reading",
        intent="discard_reading_artifact",
        chat_preview=f"已删除阅读整理草稿：《{title}》。",
        durable_artifacts=[
            artifact_ref(
                type="reading_draft",
                id=draft["id"],
                title=title,
                status=updated.get("status") or "discarded",
            )
        ],
        meta={
            "reading_draft_id": draft["id"],
            "draft_status": updated.get("status") or "discarded",
        },
    )


def _commit_reading_artifact(draft_id: str = "", context: KnowledgeContext | None = None) -> dict:
    config, repo, capture_repo = _init(context)
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
        return _commit_knowledge_card(config, repo, draft)
    if output_type in {"local_learning_note", "discussion_note"}:
        return _commit_independent_note(config, vault_path, repo, capture_repo, draft)
    return error_envelope(
        agent="reading",
        intent="commit_reading_artifact",
        error=f"未知阅读整理类型：{output_type}",
    )


def _resolve_note_folder(conn, draft: dict, explicit: str = "") -> str:
    """Resolve the logical topic folder for an answer-derived note.

    Explicit UI selection wins.  Otherwise use a strict majority of the cited
    local sources' ``capture_metadata.folder_path`` / card ``source_theme``;
    ties or no majority fall back to Inbox instead of guessing.
    """
    from bridge.path_safety import safe_vault_relative_path

    selected = safe_vault_relative_path(explicit, default="")
    if selected:
        return selected
    meta = draft.get("meta_json") if isinstance(draft.get("meta_json"), dict) else {}
    sources = meta.get("knowledge_sources") or meta.get("sources") or []
    candidates: list[str] = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or source.get("source_id") or "").strip()
        if not source_id:
            continue
        kind = str(source.get("type") or source.get("source_kind") or "").lower()
        row = None
        if kind in {"garden_card", "knowledge_card"}:
            row = conn.execute(
                "SELECT source_theme FROM garden_cards WHERE id = ?", (source_id,)
            ).fetchone()
            folder = str(row["source_theme"] or "").strip() if row else ""
        else:
            row = conn.execute(
                "SELECT folder_path FROM capture_metadata WHERE capture_id = ?",
                (source_id,),
            ).fetchone()
            folder = str(row["folder_path"] or "").strip() if row else ""
        folder = safe_vault_relative_path(folder, default="")
        if folder:
            candidates.append(folder)
    if not candidates:
        return "Inbox"
    counts: dict[str, int] = {}
    for folder in candidates:
        counts[folder] = counts.get(folder, 0) + 1
    best_folder, best_count = max(counts.items(), key=lambda item: (item[1], item[0]))
    if best_count >= (len(candidates) + 1) // 2:
        return best_folder
    return "Inbox"


def _commit_independent_note(
    config,
    vault_path: Path,
    repo: ReadingRepo,
    capture_repo: CaptureRepo,
    draft: dict,
) -> dict:
    note_type = "capture_note"
    folder = "captures"
    raise_if_cancelled("reading.commit_note.before_write")
    capture = capture_repo.insert(file_path="", raw_path="")
    capture_id = capture["id"]
    draft_title = draft.get("title") or "阅读整理"
    draft_summary = draft.get("summary") or ""
    frontmatter = {
        "id": capture_id,
        "type": "capture_note",
        "status": "processed",
        "title": draft_title,
        "reading_session_id": draft["session_id"],
        "reading_draft_id": draft["id"],
        "created": _now(),
    }
    # Enrich frontmatter for better RAG identity embedding
    if draft_summary:
        frontmatter["story"] = draft_summary
    draft_meta = draft.get("meta_json") if isinstance(draft.get("meta_json"), dict) else {}
    explicit_folder = str(draft_meta.get("folder_path") or "").strip()
    note_folder = _resolve_note_folder(capture_repo.conn, draft, explicit_folder)
    frontmatter["folder_path"] = note_folder
    if explicit_folder or note_folder != "Inbox":
        frontmatter["source_kind"] = "topic_synthesis"
    metadata = draft_meta.get("metadata") if isinstance(draft_meta.get("metadata"), dict) else {}
    metadata_tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
    tags = [str(tag).strip() for tag in metadata_tags if str(tag).strip()][:8]
    if not tags:
        tags = _extract_tags(draft_title, draft.get("draft_text", ""))
    if tags:
        frontmatter["tags"] = tags
    write_result = _write_note_file(
        vault_path=vault_path,
        folder=folder,
        title=draft_title,
        draft_id=capture_id,
        kind=note_type,
        db_object_id=capture_id,
        db_table="captures",
        frontmatter=frontmatter,
        body=draft.get("draft_text", ""),
    )
    rel_path = write_result.file_path
    capture_repo.update(capture_id, file_path=rel_path, raw_path="")
    library_report = _postprocess_committed_learning_note(config, repo, capture_id)
    repo.update_draft(draft["id"], status="committed", target_capture_id=capture_id)
    repo.update_session(draft["session_id"], status="committed", committed_artifact_id=rel_path)
    warnings = []
    if library_report.get("error"):
        warnings.append(f"Library postprocess failed: {library_report['error']}")
    chat_preview = f"已保存阅读整理：《{draft.get('title') or '阅读整理'}》。"
    if warnings:
        chat_preview += "但索引维护失败，稍后可用 Library 修复。"
    else:
        chat_preview += "已加入知识库检索索引。"
    return success_envelope(
        agent="reading",
        intent="commit_reading_artifact",
        chat_preview=chat_preview,
        durable_artifacts=[
            artifact_ref(
                type="capture_note",
                id=capture_id,
                title=draft.get("title") or "阅读整理",
                path=rel_path,
                status="created",
            )
        ],
        meta={
            "reading_draft_id": draft["id"],
            "capture_id": capture_id,
            "artifact_type": "capture_note",
            "source_output_type": draft["output_type"],
            "file_path": rel_path,
            "library_postprocess": library_report,
            "write_service": "knowledge_write_service",
            "write_backup_path": write_result.backup_path,
        },
        warnings=warnings,
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


def _commit_knowledge_card(config, repo: ReadingRepo, draft: dict) -> dict:
    garden_data, _card_body, synth_warnings = _garden_data_from_reading_draft(config, repo, draft)
    raise_if_cancelled("reading.commit_card.before_write")
    card_id = create_garden_card(repo, garden_data)
    try:
        from agents.knowledge_graph import refresh_graph_after_write

        refresh_graph_after_write(repo.conn, getattr(config, "vault_path", ""))
    except Exception:
        pass
    repo.update_draft(draft["id"], status="committed")
    repo.update_session(draft["session_id"], status="committed", committed_artifact_id=card_id)
    return success_envelope(
        agent="reading",
        intent="commit_reading_artifact",
        chat_preview=f"已保存知识卡片：《{draft.get('title') or '知识卡片'}》",
        durable_artifacts=[
            artifact_ref(
                type="knowledge_card",
                id=card_id,
                title=draft.get("title") or "知识卡片",
                status="created",
            )
        ],
        meta={
            "reading_draft_id": draft["id"],
            "garden_card_id": card_id,
            "artifact_type": "knowledge_card",
            "file_path": "",
            "storage": "sqlite",
            "synthesis_warnings": synth_warnings,
        },
        warnings=synth_warnings,
    )


_CARD_KIND_HINTS: dict[str, str] = {
    "viewpoint": "以后遇到类似话题时，可以用它作为一种解读角度或立场参考。",
    "fact": "以后做判断或引用时，可以直接使用这些经验和数据。",
    "method": "以后遇到类似问题时，可以按照这个思路或流程来处理。",
    "risk": "以后做判断或行动前，可以用它提醒自己注意边界和误区。",
}


def _synthesize_reading_card(
    config,
    *,
    title: str,
    draft_text: str,
    summary: str,
    session: dict,
    evidence: list[dict],
) -> tuple[dict, str, list[str]]:
    """Use LLM to synthesize a Q&A conversation into a structured knowledge card.

    Returns (garden_data_dict, synthesized_markdown_body).
    On any LLM failure, falls back to the manual assembly approach.
    """
    capture_ids = _reading_card_capture_ids(session, evidence)
    source_materials = _reading_card_key_materials(session, evidence)

    # --- LLM synthesis -------------------------------------------------
    api_key = getattr(config, "llm_api_key", "")
    base_url = getattr(config, "llm_base_url", "") or "https://api.deepseek.com"

    body_excerpt = draft_text.strip()
    if len(body_excerpt) > 8000:
        body_excerpt = body_excerpt[:8000] + "…"

    evidence_summary = "\n".join(
        f"- {item.get('title') or item.get('source_id') or '未知来源'}"
        for item in evidence[:6]
    ) or "无外部来源"

    prompt = (
        "你是一个知识整理助手。以下是一段用户与 AI 的问答对话，请将它蒸馏为一张"
        "可复用的 Knowledge Card。\n\n"
        f"对话标题：{title}\n\n"
        f"参考来源：\n{evidence_summary}\n\n"
        f"对话内容：\n{body_excerpt}\n\n"
        "输出 JSON（不要用 markdown 代码块包裹）：\n"
        '{\n'
        '  "card_kind": "viewpoint|fact|method|risk",\n'
        '  "one_liner": "一句话概括这张卡片的核心知识（完整判断句）",\n'
        '  "core_arguments": [\n'
        '    {"claim": "支撑这张卡的关键要点，不要逐段复制对话"}\n'
        '  ],\n'
        '  "key_materials": [\n'
        '    {"type": "quote|data|case", "content": "最能支撑判断的原文片段或案例"}\n'
        '  ]\n'
        '}\n\n'
        "要求：\n"
        "- card_kind 只能是 viewpoint、fact、method、risk 之一。\n"
        "- one_liner 是完整判断句，不是标题的复读。\n"
        "- core_arguments 保留 3-5 条，从对话中提炼核心知识点，不要逐条复制问答。\n"
        "- key_materials 保留 2-4 条，选最能支撑判断的原文片段。\n"
    )

    extracted: dict | None = None
    warnings: list[str] = []
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [
                        {"role": "system", "content": "你是专业的中文知识整理助手。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4000,
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            extracted = _parse_card_json(raw)
    except Exception as exc:
        message = f"Reading card LLM synthesis failed: {exc}"
        warnings.append(message)
        logger.warning(message)

    # --- Build result ---------------------------------------------------
    if extracted is not None:
        card_kind = str(extracted.get("card_kind", "viewpoint")).strip()
        if card_kind not in {"viewpoint", "fact", "method", "risk"}:
            card_kind = "viewpoint"
        one_liner = str(extracted.get("one_liner", "")).strip() or summary or title
        core_args = extracted.get("core_arguments") or []
        key_mats = extracted.get("key_materials") or []
    else:
        # Fallback: manual assembly
        card_kind = "viewpoint"
        one_liner = _clip(summary or draft_text, 180) or "从阅读讨论中沉淀出来的可复用知识。"
        core_args = [
            {"claim": _clip(summary or session.get("focus") or title, 180)}
        ]
        key_mats = []

    garden_data = {
        "title": title,
        "one_liner": one_liner,
        "card_kind": card_kind,
        "core_arguments": [
            {"claim": str(a.get("claim", "")).strip()}
            for a in core_args[:5]
            if str(a.get("claim", "")).strip()
        ] or [{"claim": one_liner}],
        "knowledge_links": _reading_card_linked_card_ids(evidence),
        "key_materials": source_materials if not key_mats else [
            {
                "type": str(m.get("type", "quote")).strip(),
                "content": _clip(str(m.get("content", "")).strip(), 240),
            }
            for m in key_mats[:4]
            if str(m.get("content", "")).strip()
        ],
        "my_view": "",
        "status": "seed",
        "capture_ids": capture_ids,
    }

    # Build synthesized markdown body
    md = _build_card_markdown(title, one_liner, card_kind, core_args, key_mats, evidence)
    return garden_data, md, warnings


def _parse_card_json(raw: str) -> dict:
    """Parse JSON from LLM output, tolerating markdown code fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _build_card_markdown(
    title: str,
    one_liner: str,
    card_kind: str,
    core_args: list[dict],
    key_mats: list[dict],
    evidence: list[dict],
) -> str:
    lines = [f"# {title}", "", one_liner, "", "## 内容"]

    for arg in core_args[:4]:
        claim = str(arg.get("claim", "")).strip()
        if claim:
            lines.append(f"- {claim}")

    for mat in key_mats[:4]:
        content = str(mat.get("content", "")).strip()
        mat_type = str(mat.get("type", "quote")).strip()
        if content:
            lines.append(f"- [{mat_type}] {_clip(content, 240)}")

    if not core_args and not key_mats:
        lines.append("- （待补充）")

    lines.extend(["", "## 适用或启发"])
    lines.append(_CARD_KIND_HINTS.get(card_kind, _CARD_KIND_HINTS["viewpoint"]))

    if evidence:
        lines.extend(["", "## 来源"])
        for item in evidence[:6]:
            label = str(item.get("title") or item.get("source_id") or "未命名来源").strip()
            kind = str(item.get("source_kind") or item.get("type") or "").strip()
            prefix = f"[{kind}] " if kind else ""
            lines.append(f"- {prefix}{label}")

    return "\n".join(lines).strip() + "\n"


def _garden_data_from_reading_draft(config, repo: ReadingRepo, draft: dict) -> tuple[dict, str, list[str]]:
    session = repo.get_session(draft["session_id"]) or {}
    evidence = repo.list_evidence(draft["session_id"])
    title = draft.get("title") or "知识卡片"
    draft_text = draft.get("draft_text", "")
    summary = draft.get("summary") or ""
    return _synthesize_reading_card(
        config,
        title=title,
        draft_text=draft_text,
        summary=summary,
        session=session,
        evidence=evidence,
    )


_CAPTURE_SOURCE_KINDS = {
    "capture",
    "capture_note",
    "capture_section",
    "knowledge_base",
}
_CARD_SOURCE_KINDS = {"knowledge_card", "garden_card"}


def _strip_section_suffix(source_id: str) -> str:
    """``cap_x#section-2`` -> ``cap_x`` (RAG sections reference the capture)."""
    return str(source_id or "").split("#", 1)[0].strip()


def _reading_card_capture_ids(session: dict, evidence: list[dict]) -> list[str]:
    ids: list[str] = []
    if session.get("source_type") in _CAPTURE_SOURCE_KINDS and session.get("source_id"):
        ids.append(_strip_section_suffix(session["source_id"]))
    for item in evidence:
        if item.get("source_kind") in _CAPTURE_SOURCE_KINDS and item.get("source_id"):
            ids.append(_strip_section_suffix(item["source_id"]))
    return _dedupe([item for item in ids if item])


def _reading_card_linked_card_ids(evidence: list[dict]) -> list[str]:
    """Cards already cited in the conversation become the new card's siblings."""
    ids = [
        str(item.get("source_id") or "")
        for item in evidence
        if item.get("source_kind") in _CARD_SOURCE_KINDS and item.get("source_id")
    ]
    return _dedupe([item for item in ids if item])


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
    meta = draft.get("meta_json")
    expected_hash = meta.get(ENHANCEMENT_SOURCE_HASH) if isinstance(meta, dict) else None
    if not expected_hash:
        return error_envelope(
            agent="reading",
            intent="commit_reading_artifact",
            error="这份草稿未绑定资料笔记的版本，请根据当前资料笔记重新生成草稿。",
        )
    try:
        file_path, old_text = _read_enhancement_source(vault_path, capture_repo, capture_id)
    except ValueError as exc:
        return error_envelope(agent="reading", intent="commit_reading_artifact", error=str(exc))

    conflict_error = "资料笔记在草稿生成后已修改，请根据当前资料笔记重新生成草稿。"
    previous_hash = _sha256(old_text)
    if previous_hash != expected_hash:
        return error_envelope(
            agent="reading", intent="commit_reading_artifact", error=conflict_error,
        )
    new_text = _merge_frontmatter(old_text, draft.get("draft_text", ""))
    new_hash = _sha256(new_text)
    backup = {}

    def register_backup(backup_path: str) -> None:
        nonlocal backup
        with connection_lock(repo.conn):
            # A nested SQLite commit could leave the backup row uncommitted.
            if repo.conn.in_transaction:
                raise RuntimeError("cannot replace a note before its backup registration is committed")
            backup = repo.record_capture_backup(
                capture_id=capture_id,
                reading_draft_id=draft["id"],
                backup_file_path=backup_path,
                previous_file_hash=previous_hash,
                new_file_hash=new_hash,
            )

    raise_if_cancelled("reading.enhance_capture.before_write")
    try:
        write_result = KnowledgeWriteService(vault_path).write_markdown_artifact(
            MarkdownWriteRequest(
                kind="capture_note",
                relative_path=file_path,
                content=new_text,
                db_object_id=capture_id,
                db_table="captures",
                requires_backup=True,
            ),
            expected_previous=old_text,
            register_backup=register_backup,
        )
    except MarkdownVersionConflict:
        return error_envelope(
            agent="reading", intent="commit_reading_artifact", error=conflict_error,
        )
    repo.update_draft(draft["id"], status="committed")
    repo.update_session(
        draft["session_id"],
        status="committed",
        committed_artifact_id=file_path,
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
                path=file_path,
                status="updated",
            ),
            artifact_ref(
                type="capture_backup",
                id=backup["id"],
                title="旧版 Capture 备份",
                path=write_result.backup_path,
                status="created",
            ),
        ],
        meta={
            "reading_draft_id": draft["id"],
            "capture_id": capture_id,
            "backup_file_path": write_result.backup_path,
            "previous_file_hash": previous_hash,
            "new_file_hash": new_hash,
            "write_service": "knowledge_write_service",
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
        "local_learning_note": "资料笔记",
        "knowledge_card": "知识卡片",
    }.get(output_type, "资料笔记")
    return f"{base}-{suffix}"


def _draft_summary(turns: list[dict]) -> str:
    if not turns:
        return "整理当前阅读讨论。"
    return _summary_from_markdown(turns[-1].get("assistant_answer", "")) or "整理当前阅读讨论。"


def _generate_full_text_metadata(config, markdown: str) -> dict:
    if not _metadata_llm_enabled(config):
        return {}
    try:
        summarizer = Summarizer(
            api_key=getattr(config, "llm_api_key", ""),
            model=getattr(config, "llm_model", ""),
            base_url=getattr(config, "llm_base_url", "") or "https://api.deepseek.com",
        )
        result = summarizer.summarize(markdown, [])
    except Exception:
        return {}
    return _normalize_metadata(result)


def _metadata_llm_enabled(config) -> bool:
    if os.environ.get("HERMES_FULL_TEXT_METADATA_LLM") != "1":
        return False
    api_key = str(getattr(config, "llm_api_key", "") or "").strip()
    base_url = str(getattr(config, "llm_base_url", "") or "")
    if not api_key or api_key.startswith("test-"):
        return False
    return "example.test" not in base_url


def _normalize_metadata(result: dict) -> dict:
    tags = result.get("tags") if isinstance(result.get("tags"), list) else []
    return {
        "title": str(result.get("title") or "").strip(),
        "summary": _clip_to_sentence(_plain_summary_text(str(result.get("summary") or "")), 140),
        "tags": [str(tag).strip() for tag in tags if str(tag).strip()][:8],
        "source": "ai_full_text",
    }


def _summary_from_markdown(markdown: str) -> str:
    paragraph: list[str] = []
    for raw_line in (markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            if paragraph:
                break
            continue
        if line.startswith("#"):
            if paragraph:
                break
            continue
        paragraph.append(line)
    text = _plain_summary_text(" ".join(paragraph))
    text = re.split(r"(?:以下是具体分析|具体分析如下|具体要点|参考来源|文内引用来源)", text, maxsplit=1)[0].strip()
    return _clip_to_sentence(text, 140)


def _plain_summary_text(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text or "")
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s*[-*]\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clip_to_sentence(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    window = text[:limit].rstrip()
    for index in range(len(window) - 1, max(0, limit // 2), -1):
        if window[index] in "。！？.!?":
            return window[: index + 1]
    return _clip(text, limit)


def _build_draft_text(
    *,
    session: dict,
    turns: list[dict],
    evidence: list[dict],
    output_type: str,
    instruction: str,
    source_body: str = "",
) -> str:
    discussion = _discussion_section(turns, evidence)
    if output_type == "enhance_capture":
        base_body = source_body or "# 阅读整理"
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


def _read_enhancement_source(
    vault_path: Path, capture_repo: CaptureRepo, capture_id: str,
) -> tuple[str, str]:
    capture = capture_repo.get_by_id(capture_id)
    if not capture or not capture.get("file_path"):
        raise ValueError("没有找到要增强的资料笔记。")
    file_path = capture["file_path"]
    try:
        # Preserve the exact saved UTF-8 bytes (including line endings).
        return file_path, resolve_inside(vault_path, file_path).read_bytes().decode("utf-8")
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取要增强的资料笔记：{file_path}") from exc


def _enhancement_draft_base(
    vault_path: Path, capture_repo: CaptureRepo, capture_id: str,
) -> tuple[str, dict]:
    _, text = _read_enhancement_source(vault_path, capture_repo, capture_id)
    return _strip_frontmatter(text).strip(), {ENHANCEMENT_SOURCE_HASH: _sha256(text)}


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
    kind: str,
    db_object_id: str,
    db_table: str,
    frontmatter: dict[str, Any],
    body: str,
) -> KnowledgeWriteResult:
    safe_title = _safe_filename(title)
    relative = Path(folder) / f"{safe_title}-{draft_id[:8]}.md"
    content = (
        "---\n"
        f"{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        "---\n\n"
        f"{body.strip()}\n"
    )
    return KnowledgeWriteService(vault_path).write_markdown_artifact(
        MarkdownWriteRequest(
            kind=kind,
            relative_path=relative.as_posix(),
            content=content,
            db_object_id=db_object_id,
            db_table=db_table,
        )
    )


def _safe_filename(title: str) -> str:
    text = (title or "阅读整理").strip() or "阅读整理"
    text = re.sub(r'[/\\:*?"<>|？]', "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:48] or "阅读整理"


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

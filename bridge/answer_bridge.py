"""Bridge for saving high-quality Hermes answers as reviewable knowledge drafts."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.capture_agent import load_config
from agents.summarizer import Summarizer
from bridge.result_envelope import artifact_ref, error_envelope, next_action, success_envelope
from bridge.run_ledger import run_with_ledger
from db.reading_repo import ReadingRepo
from db.schema import init_db

OUTPUT_TYPES = {"auto", "local_learning_note", "knowledge_card", "enhance_capture"}
ANSWER_SOURCES = {"auto", "provided", "latest_rag_full_answer"}

_config = None
_repo: ReadingRepo | None = None


def _init():
    global _config, _repo
    if _config is None:
        _config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
        conn = init_db(_config.db_path)
        _repo = ReadingRepo(conn)
    return _config, _repo


def _run(intent: str, input_ref: dict[str, Any], operation):
    _, repo = _init()
    return run_with_ledger(
        repo.conn,
        agent="reading",
        intent=intent,
        trigger="knowledge_workbench",
        input_ref=input_ref,
        operation=operation,
    )


def source_capture_candidates(sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    candidates: list[dict[str, str]] = []
    for item in sources or []:
        source_type = str(item.get("type") or item.get("source_kind") or "").strip()
        source_id = str(item.get("id") or item.get("source_id") or "").strip()
        if source_type not in {"capture", "capture_note", "knowledge_base"}:
            continue
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        candidates.append(
            {
                "id": source_id,
                "title": str(item.get("title") or item.get("label") or source_id).strip(),
            }
        )
    return candidates


def choose_answer_output_type(
    *,
    instruction: str,
    requested_output_type: str,
    sources: list[dict[str, Any]],
    target_capture_id: str,
) -> dict[str, Any]:
    requested = (requested_output_type or "auto").strip()
    if requested not in OUTPUT_TYPES:
        requested = "auto"

    candidates = source_capture_candidates(sources)
    wants_card = bool(re.search(r"卡片|知识卡|可复用", instruction or ""))
    wants_capture = bool(re.search(r"补进|合并|主笔记|原文增强", instruction or ""))

    if requested == "knowledge_card" or (requested == "auto" and wants_card):
        return {
            "output_type": "knowledge_card",
            "target_capture_id": "",
            "needs_target_choice": False,
            "candidate_captures": candidates,
        }

    if requested == "enhance_capture" or (requested == "auto" and wants_capture):
        if target_capture_id:
            return {
                "output_type": "enhance_capture",
                "target_capture_id": target_capture_id,
                "needs_target_choice": False,
                "candidate_captures": candidates,
            }
        if len(candidates) == 1:
            return {
                "output_type": "enhance_capture",
                "target_capture_id": candidates[0]["id"],
                "needs_target_choice": False,
                "candidate_captures": candidates,
            }
        return {
            "output_type": "",
            "target_capture_id": "",
            "needs_target_choice": True,
            "candidate_captures": candidates,
        }

    return {
        "output_type": "local_learning_note",
        "target_capture_id": "",
        "needs_target_choice": False,
        "candidate_captures": candidates,
    }


def save_answer_to_knowledge(
    *,
    hermes_session_id: str = "",
    user_message: str = "",
    assistant_answer: str = "",
    instruction: str = "",
    output_type: str = "auto",
    assistant_message_id: str = "",
    sources: list[dict[str, Any]] | None = None,
    target_capture_id: str = "",
    answer_source: str = "auto",
) -> dict:
    sources = sources or []
    if answer_source not in ANSWER_SOURCES:
        answer_source = "auto"
    return _run(
        "save_answer_to_knowledge",
        {
            "hermes_session_id": hermes_session_id,
            "assistant_message_id": assistant_message_id,
            "output_type": output_type,
            "source_count": len(sources),
            "answer_source": answer_source,
        },
        lambda: _save_answer_to_knowledge(
            hermes_session_id=hermes_session_id,
            user_message=user_message,
            assistant_answer=assistant_answer,
            instruction=instruction,
            output_type=output_type,
            assistant_message_id=assistant_message_id,
            sources=sources,
            target_capture_id=target_capture_id,
            answer_source=answer_source,
        ),
    )


def revise_answer_draft(draft_id: str = "", instruction: str = "") -> dict:
    """Revise a reviewable answer draft without committing it."""
    return _run(
        "revise_answer_draft",
        {"draft_id": draft_id, "instruction": instruction},
        lambda: _revise_answer_draft(draft_id=draft_id, instruction=instruction),
    )


def _revise_answer_draft(*, draft_id: str, instruction: str) -> dict:
    config, repo = _init()
    draft = repo.get_draft(draft_id)
    if not draft:
        return error_envelope(
            agent="reading",
            intent="revise_answer_draft",
            error="没有找到这份回答/文稿草稿。",
        )
    if draft.get("status") != "open":
        return error_envelope(
            agent="reading",
            intent="revise_answer_draft",
            error="这份草稿已保存，不能继续修改。",
            meta={"reading_draft_id": draft_id, "status": draft.get("status", "")},
        )
    if not instruction.strip():
        return error_envelope(
            agent="reading",
            intent="revise_answer_draft",
            error="请先写清楚你想怎么修改这份文稿草稿。",
            meta={"reading_draft_id": draft_id},
        )

    revised_text = _call_revision_llm(
        api_key=config.llm_api_key,
        model=config.llm_model,
        base_url=config.llm_base_url or "https://api.deepseek.com",
        draft_text=draft.get("draft_text", ""),
        instruction=instruction,
    )
    updated = repo.update_draft(
        draft_id,
        draft_text=revised_text.strip() + "\n",
        summary=_summary_from_markdown(revised_text),
    )
    return success_envelope(
        agent="reading",
        intent="revise_answer_draft",
        chat_preview=(
            f"已修改文稿草稿：《{updated.get('title') or draft.get('title') or '未命名草稿'}》\n\n"
            f"草稿编号：{draft_id}\n"
            "请继续审阅，满意后确认保存。"
        ),
        durable_artifacts=[
            artifact_ref(
                type="reading_draft",
                id=draft_id,
                title=updated.get("title") or draft.get("title") or "回答沉淀草稿",
                status=updated.get("status") or draft.get("status") or "",
            )
        ],
        next_actions=[
            next_action(
                label="继续修改",
                intent="revise_answer_draft",
                payload={"draft_id": draft_id},
                requires_confirmation=False,
            ),
            next_action(
                label="确认保存",
                intent="commit_reading_artifact",
                payload={"draft_id": draft_id},
                requires_confirmation=True,
            ),
        ],
        meta={"reading_draft_id": draft_id},
    )


def _save_answer_to_knowledge(
    *,
    hermes_session_id: str,
    user_message: str,
    assistant_answer: str,
    instruction: str,
    output_type: str,
    assistant_message_id: str,
    sources: list[dict[str, Any]],
    target_capture_id: str,
    answer_source: str,
) -> dict:
    config, repo = _init()
    resolved = _resolve_answer_source(
        repo=repo,
        user_message=user_message,
        assistant_answer=assistant_answer,
        instruction=instruction,
        sources=sources,
        answer_source=answer_source,
    )
    assistant_answer = resolved["assistant_answer"]
    sources = resolved["sources"]
    source_groups = _group_answer_sources(sources)
    decision = choose_answer_output_type(
        instruction=instruction,
        requested_output_type=output_type,
        sources=sources,
        target_capture_id=target_capture_id,
    )
    if decision["needs_target_choice"]:
        choices = "\n".join(
            f"- {item['title']}（{item['id']}）"
            for item in decision["candidate_captures"]
        ) or "- 没有识别到明确 Capture 来源"
        return success_envelope(
            agent="reading",
            intent="save_answer_to_knowledge",
            chat_preview=(
                "这段回答涉及多个材料，我不能自动判断要补进哪一篇主笔记。\n\n"
                f"候选材料：\n{choices}\n\n"
                "你可以改成保存为 Capture Note/知识卡片，或明确说要补进哪一篇。"
            ),
            meta={
                "needs_target_choice": True,
                "candidate_captures": decision["candidate_captures"],
            },
        )

    title = _answer_draft_title(user_message=user_message, assistant_answer=assistant_answer)
    session = repo.create_session(
        source_type="conversation",
        source_id=hermes_session_id,
        source_title=title,
        focus=_clip(user_message or instruction, 120),
        meta={
            "origin": "assistant_answer",
            "hermes_session_id": hermes_session_id,
            "assistant_message_id": assistant_message_id,
            "instruction": instruction,
            "answer_source": resolved["answer_source"],
            "source_type": source_groups["source_type"],
            "research_mode": source_groups["research_mode"],
        },
    )
    turn = repo.add_turn(
        session["id"],
        user_message=user_message,
        assistant_answer=assistant_answer,
        focus=_clip(user_message or instruction, 120),
        meta={"assistant_message_id": assistant_message_id},
    )
    for item in sources[:12]:
        repo.add_evidence(
            session_id=session["id"],
            turn_id=turn["id"],
            source_kind=str(item.get("type") or item.get("source_kind") or ""),
            source_id=str(item.get("id") or item.get("source_id") or ""),
            title=str(item.get("title") or item.get("label") or ""),
            url=str(item.get("url") or ""),
            excerpt=str(item.get("excerpt") or item.get("section") or ""),
            used_for="answer_draft_source",
        )

    draft_text = _build_answer_draft_text(
        title=title,
        user_message=user_message,
        assistant_answer=assistant_answer,
        output_type=decision["output_type"],
        sources=sources,
    )
    metadata = _generate_full_text_metadata(config, draft_text)
    title = _metadata_answer_draft_title(metadata, fallback=title)
    session = repo.update_session(session["id"], source_title=title) or session
    draft_text = _build_answer_draft_text(
        title=title,
        user_message=user_message,
        assistant_answer=assistant_answer,
        output_type=decision["output_type"],
        sources=sources,
    )
    draft = repo.create_draft(
        session_id=session["id"],
        output_type=decision["output_type"],
        target_capture_id=decision["target_capture_id"],
        title=title,
        summary=metadata.get("summary") or _summary_from_markdown(assistant_answer),
        draft_text=draft_text,
        meta={
            "origin": "assistant_answer",
            "hermes_session_id": hermes_session_id,
            "assistant_message_id": assistant_message_id,
            "instruction": instruction,
            "sources": sources,
            "knowledge_sources": source_groups["knowledge_sources"],
            "web_sources": source_groups["web_sources"],
            "source_type": source_groups["source_type"],
            "research_mode": source_groups["research_mode"],
            "answer_source": resolved["answer_source"],
            "metadata": metadata,
        },
    )
    label = {
        "local_learning_note": "Capture Note",
        "knowledge_card": "知识卡片",
        "enhance_capture": "主笔记增强",
    }[decision["output_type"]]
    return success_envelope(
        agent="reading",
        intent="save_answer_to_knowledge",
        chat_preview=(
            f"已生成{label}草稿：《{title}》\n\n"
            f"草稿编号：{draft['id']}\n"
            "你可以在右侧/草稿审阅区检查，满意后确认保存。"
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
                label="确认保存",
                intent="commit_reading_artifact",
                payload={"draft_id": draft["id"]},
                requires_confirmation=True,
            )
        ],
        meta={
            "reading_session_id": session["id"],
            "reading_draft_id": draft["id"],
            "output_type": decision["output_type"],
            "target_capture_id": decision["target_capture_id"],
            "needs_target_choice": False,
            "answer_source": resolved["answer_source"],
            "source_type": source_groups["source_type"],
            "research_mode": source_groups["research_mode"],
        },
    )


def _group_answer_sources(sources: list[dict[str, Any]]) -> dict[str, Any]:
    knowledge_sources: list[dict[str, Any]] = []
    web_sources: list[dict[str, Any]] = []
    for item in sources or []:
        source_type = str(item.get("type") or item.get("source_kind") or "").strip()
        if source_type in {"web", "web_page", "web_search"} or item.get("url"):
            web_sources.append(item)
        elif source_type in {"knowledge", "capture", "capture_note", "capture_section", "knowledge_base", "capture_draft"}:
            knowledge_sources.append(item)
    if web_sources and knowledge_sources:
        source_type = "knowledge_web_research"
        research_mode = "local_web"
    elif web_sources:
        source_type = "web_research"
        research_mode = "web_only"
    elif knowledge_sources:
        source_type = "knowledge_research"
        research_mode = "local_only"
    else:
        source_type = "conversation"
        research_mode = "none"
    return {
        "knowledge_sources": knowledge_sources,
        "web_sources": web_sources,
        "source_type": source_type,
        "research_mode": research_mode,
    }


def _resolve_answer_source(
    *,
    repo: ReadingRepo,
    user_message: str,
    assistant_answer: str,
    instruction: str,
    sources: list[dict[str, Any]],
    answer_source: str,
) -> dict[str, Any]:
    latest = _latest_rag_full_answer(repo)
    if latest and _should_use_latest_rag_answer(
        requested_source=answer_source,
        instruction=instruction,
        user_message=user_message,
        assistant_answer=assistant_answer,
        latest=latest,
    ):
        return {
            "assistant_answer": latest["answer"],
            "sources": sources or latest["sources"],
            "answer_source": "latest_rag_full_answer",
        }
    return {
        "assistant_answer": assistant_answer,
        "sources": sources,
        "answer_source": "provided",
    }


def _latest_rag_full_answer(repo: ReadingRepo) -> dict[str, Any] | None:
    row = repo.conn.execute(
        """SELECT input_json, output_json
           FROM agent_runs
           WHERE agent = 'rag'
             AND intent = 'search_knowledge_base'
             AND status = 'success'
             AND output_json IS NOT NULL
             AND output_json != ''
           ORDER BY datetime(replace(substr(completed_at, 1, 19), 'T', ' ')) DESC,
                    datetime(replace(substr(created_at, 1, 19), 'T', ' ')) DESC,
                    rowid DESC
           LIMIT 1"""
    ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["output_json"] or "{}")
        input_ref = json.loads(row["input_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    answer = str(payload.get("answer") or "").strip()
    if not answer:
        return None
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    return {
        "answer": answer,
        "sources": sources,
        "query": str(input_ref.get("query") or payload.get("query") or ""),
    }


def _should_use_latest_rag_answer(
    *,
    requested_source: str,
    instruction: str,
    user_message: str,
    assistant_answer: str,
    latest: dict[str, Any],
) -> bool:
    if requested_source == "provided":
        return False
    if requested_source == "latest_rag_full_answer":
        return True
    full_answer = str(latest.get("answer") or "")
    if len(full_answer) <= len(assistant_answer or ""):
        return False
    if _looks_truncated(assistant_answer):
        return True
    return False


def _looks_truncated(text: str) -> bool:
    return bool(re.search(r"已省略|继续展开|查看更多|后续内容|\\.\\.\\.|……", text or ""))


def _related_to_latest_query(user_message: str, query: str) -> bool:
    if not user_message or not query:
        return True
    user_terms = set(re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", user_message))
    query_terms = set(re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", query))
    if not user_terms or not query_terms:
        return True
    return bool(user_terms & query_terms)


def _answer_draft_title(*, user_message: str, assistant_answer: str) -> str:
    base = re.sub(r"\s+", " ", user_message or assistant_answer).strip()
    base = re.sub(r"[#*_`>\[\]()]+" , "", base)
    return (base[:36].strip(" ，。；：") or "对话沉淀笔记")


def _metadata_answer_draft_title(metadata: dict, *, fallback: str) -> str:
    base = str((metadata or {}).get("title") or "").strip()
    base = re.sub(r"\s+", " ", base)
    base = re.sub(r"[#*_`>\[\]()]+" , "", base)
    return (base[:36].strip(" ，。；：") or fallback)


def _topic_phrase(user_message: str, assistant_answer: str = "") -> str:
    """Extract a short topic phrase for section headings (improves RAG retrieval)."""
    text = re.sub(r"\s+", " ", user_message or assistant_answer or "").strip()
    text = re.sub(r"[#*_`>\[\]()]+", "", text)
    text = text.strip(" ，。；：？?")
    return text[:24].strip() or "对话笔记"


def _build_answer_draft_text(
    *,
    title: str,
    user_message: str,
    assistant_answer: str,
    output_type: str,
    sources: list[dict[str, Any]],
) -> str:
    source_lines = []
    for item in sources[:12]:
        label = str(
            item.get("title")
            or item.get("label")
            or item.get("url")
            or item.get("id")
            or item.get("source_id")
            or ""
        ).strip()
        kind = str(item.get("type") or item.get("source_kind") or "AI 解释").strip()
        if label:
            url = str(item.get("url") or "").strip()
            ref = str(item.get("ref") or item.get("label") or "").strip()
            prefix = f"[{ref}] " if ref and re.match(r"^[KW]\d+$", ref) else ""
            suffix = f" — {url}" if url and url != label else ""
            source_lines.append(f"- {kind}：{prefix}{label}{suffix}")
    if not source_lines:
        source_lines.append("- AI 解释：来自当前 Hermes 对话")
    if output_type == "knowledge_card":
        return (
            f"# {title}\n\n"
            "## 内容\n\n"
            f"{assistant_answer.strip()}\n\n"
            "## 来源\n\n"
            + "\n".join(source_lines)
            + "\n"
        )
    topic = _topic_phrase(user_message, assistant_answer)
    return (
        f"# {title}\n\n"
        f"## 问题：{topic}\n\n"
        f"{user_message.strip() or '这轮对话中有一段值得保存的解释。'}\n\n"
        f"## 解释：{topic}\n\n"
        f"{assistant_answer.strip()}\n\n"
        "## 参考来源\n\n"
        + "\n".join(source_lines)
        + "\n"
    )


def _call_revision_llm(
    *,
    api_key: str,
    model: str,
    base_url: str,
    draft_text: str,
    instruction: str,
) -> str:
    prompt = (
        "你是 Hermes Knowledge 的文稿修订助手。请根据用户修改意见，重写下面这份 Markdown 草稿。\n\n"
        "要求：\n"
        "- 返回完整 Markdown 正文，不要只返回修改说明。\n"
        "- 保留用户问题、Hermes 解释、参考来源等必要结构。\n"
        "- 不要加入“以下是修改后”等寒暄。\n"
        "- 不要用省略号表示未写完；除非原文确实包含省略号，否则不要截断内容。\n"
        "- 如果用户要求补充、展开或压缩，请直接改正文。\n\n"
        f"用户修改意见：\n{instruction.strip()}\n\n"
        f"当前草稿：\n{draft_text.strip()}\n"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是专业的中文知识整理和文稿修订助手。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 6000,
    }
    with httpx.Client(timeout=180) as client:
        resp = client.post(f"{base_url}/chat/completions", headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
    return str(data["choices"][0]["message"]["content"]).strip()


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


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"

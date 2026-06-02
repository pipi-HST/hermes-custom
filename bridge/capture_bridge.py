"""Capture Agent bridge."""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import CaptureAgent, CaptureConfig, load_config
from bridge.file_artifacts import write_markdown_export
from bridge.result_envelope import (
    artifact_ref,
    error_envelope,
    next_action,
    success_envelope,
)
from bridge.run_ledger import run_with_ledger

_config = None
_agent = None
PREVIEW_LIMIT = 1500
DRAFT_PAGE_SIZE = 1400

def _init():
    global _config, _agent
    if _config is None:
        _config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
        _agent = CaptureAgent(_config)
    return _config, _agent


def _clip_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _clip_text_with_flag(text: str, limit: int) -> tuple[str, bool]:
    clipped = _clip_text(text, limit)
    return clipped, clipped != text.strip()


def _draft_saved_preview(result: dict, action: str) -> tuple[str, bool]:
    if _is_full_text_draft(result):
        return _full_text_saved_preview(result, action)
    raw_title = result.get("title") or "无标题"
    raw_summary = result.get("summary") or "完整草稿已保存在草稿区，等待你确认。"
    title, title_truncated = _clip_text_with_flag(raw_title, 100)
    summary, summary_truncated = _clip_text_with_flag(raw_summary, 260)
    key_response, key_truncated = _clip_text_with_flag(_key_response_preview(result), 420)
    header = f"已{action}草稿。\n\n标题：{title}\n\n重点回应：\n{key_response}\n\n总结：{summary}"
    if result.get("status") == "draft_incomplete":
        footer = "\n\n草稿还没生成完整，暂不能存库。回复「展开草稿」查看现有内容；回复「继续整理」或新的修改意见继续生成。"
    else:
        footer = "\n\n完整草稿已保存。回复「展开草稿」查看全文；回复修改意见继续改；满意回复「存库」。"
    message, message_truncated = _clip_text_with_flag(f"{header}{footer}", PREVIEW_LIMIT)
    preview_truncated = any(
        [
            title_truncated,
            summary_truncated,
            key_truncated,
            message_truncated,
        ]
    )
    return message, preview_truncated


def _is_full_text_draft(result: dict) -> bool:
    capture_intent = result.get("capture_intent")
    if isinstance(capture_intent, str):
        try:
            capture_intent = json.loads(capture_intent)
        except json.JSONDecodeError:
            return False
    if not isinstance(capture_intent, dict):
        return False
    return capture_intent.get("output_shape") == ["full_text"]


def _full_text_saved_preview(result: dict, action: str) -> tuple[str, bool]:
    title, title_truncated = _clip_text_with_flag(result.get("title") or "无标题", 100)
    word_count = result.get("word_count", 0)
    summary, summary_truncated = _clip_text_with_flag(result.get("summary") or "全文已提取。", 260)
    message = (
        f"已{action}全文草稿。\n\n"
        f"标题：{title}\n\n"
        f"正文将按全文入库（{word_count} 字），不做压缩总结。\n\n"
        f"摘要：{summary}\n\n"
        "回复「展开草稿」抽查正文；满意回复「存库」。"
    )
    message, message_truncated = _clip_text_with_flag(message, PREVIEW_LIMIT)
    return message, any([title_truncated, summary_truncated, message_truncated])


def _capture_draft_file_artifact(draft: dict, export_dir: Path | None = None) -> dict:
    draft_id = str(draft.get("id") or "")
    title = str(draft.get("title") or "未命名")
    body = str(draft.get("draft_text") or draft.get("raw_text") or "").strip()
    base_dir = export_dir or (Path(__file__).parent.parent / "data" / "exports" / "capture")
    path = write_markdown_export(
        base_dir=base_dir,
        stem=f"capture-draft-{draft_id or title}",
        content=f"# {title}\n\n{body}",
    )
    return artifact_ref(
        type="capture_draft_fulltext",
        id=draft_id,
        title="Capture 草稿全文",
        path=path,
        status="created",
    )


def _key_response_preview(result: dict) -> str:
    draft_text = result.get("draft_text", "")
    emphasis = _preview_emphasis(result.get("capture_intent"))
    if emphasis:
        focused = _focused_emphasis_preview(draft_text, emphasis)
        if focused:
            return focused
    return _first_meaningful_chunk(draft_text)


def _preview_emphasis(capture_intent) -> list[str]:
    if isinstance(capture_intent, str):
        try:
            capture_intent = json.loads(capture_intent)
        except json.JSONDecodeError:
            return []
    if not isinstance(capture_intent, dict):
        return []
    emphasis = capture_intent.get("emphasis", [])
    if not isinstance(emphasis, list):
        return []
    return [str(item).strip() for item in emphasis if str(item).strip()]


def _focused_emphasis_preview(draft_text: str, emphasis: list[str]) -> str:
    blocks = _preview_blocks(draft_text)
    if not blocks:
        return ""
    lines = ["围绕你点名的重点，草稿主要回应："]
    used: set[int] = set()
    for item in emphasis[:3]:
        match = _find_emphasis_block(blocks, item, used)
        if not match:
            continue
        idx, snippet = match
        used.add(idx)
        lines.append(f"- {item}：{_clip_text(_first_sentence(snippet), 130)}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _preview_blocks(draft_text: str) -> list[str]:
    text = (draft_text or "").strip()
    if not text:
        return []
    blocks = []
    for block in re.split(r"\n\s*\n", text):
        cleaned = _clean_preview_block(block)
        if cleaned and not _is_preview_boilerplate(cleaned):
            blocks.append(cleaned)
    return blocks


def _clean_preview_block(block: str) -> str:
    block = re.sub(r"^#+\s*", "", block.strip())
    block = re.sub(r"^\*\*(.+?)\*\*$", r"\1", block)
    block = re.sub(r"^-{3,}$", "", block)
    return block.strip()


def _is_preview_boilerplate(text: str) -> bool:
    normalized = re.sub(r"[\W_]+", "", (text or "").strip().strip("。.!！"))
    if not normalized:
        return True
    exact = {
        "好的我们开始",
        "好的开始",
        "好我们开始",
        "开始",
    }
    if normalized in exact:
        return True
    return bool(re.match(r"^(下面|以下|接下来)(是|为)?(我)?(对|关于)?.{0,18}(整理|总结|提炼|草稿)$", normalized))


def _find_emphasis_block(blocks: list[str], emphasis: str, used: set[int]) -> tuple[int, str] | None:
    best: tuple[int, int, str] | None = None
    for idx, block in enumerate(blocks):
        if idx in used:
            continue
        score = _emphasis_match_score(block, emphasis)
        if score <= 0:
            continue
        snippet = block
        if _is_preview_heading(block):
            snippet = _body_after_heading(blocks, idx) or block
        if best is None or score > best[0]:
            best = (score, idx, snippet)
    if best is None:
        return None
    return best[1], best[2]


def _is_preview_heading(text: str) -> bool:
    text = (text or "").strip()
    if len(text) > 80:
        return False
    if re.search(r"[。！？!?]", text):
        return False
    return True


def _body_after_heading(blocks: list[str], heading_index: int) -> str:
    for block in blocks[heading_index + 1:]:
        if not _is_preview_heading(block):
            return block
    return ""


def _matches_emphasis(text: str, emphasis: str) -> bool:
    return _emphasis_match_score(text, emphasis) > 0


def _emphasis_match_score(text: str, emphasis: str) -> int:
    normalized_text = _normalize_match_text(text)
    fragments = _emphasis_fragments(emphasis)
    return sum(1 for fragment in fragments if fragment in normalized_text)


def _emphasis_fragments(emphasis: str) -> list[str]:
    fragments = [_normalize_match_text(emphasis)]
    for part in re.split(r"(?:的|是|那个|这个|这|部分|一层|期间|时期|阶段|和|以及|、)", emphasis):
        normalized = _normalize_match_text(part)
        if len(normalized) >= 2:
            fragments.append(normalized)
    return [fragment for fragment in fragments if len(fragment) >= 2]


def _normalize_match_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").lower())


def _first_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    match = re.search(r"^(.+?[。！？!?])", text)
    return match.group(1).strip() if match else text


def _first_meaningful_chunk(draft_text: str) -> str:
    blocks = _preview_blocks(draft_text)
    chunk = blocks[0] if blocks else ""
    return chunk or "暂无可预览正文。"


def _run_capture_with_ledger(agent_obj, intent: str, input_ref: dict, operation):
    conn = getattr(agent_obj, "conn", None)
    if conn is None:
        return operation()
    return run_with_ledger(
        conn,
        agent="capture",
        intent=intent,
        trigger="wechat",
        input_ref=input_ref,
        operation=operation,
    )


def capture_content(content: str, instruction: str = "") -> dict:
    """提取内容并生成可确认的草稿；不直接写入 Obsidian 正式库。"""
    _, agent = _init()
    return _run_capture_with_ledger(
        agent,
        "create_capture_draft",
        {
            "content_length": len(content or ""),
            "content_preview": _clip_text(content or "", 120),
            "instruction": instruction,
        },
        lambda: _capture_content(agent, content, instruction),
    )


def _capture_content(agent, content: str, instruction: str = "") -> dict:
    result = agent.create_draft(content, instruction=instruction)
    if "error" in result:
        hint = result.get("hint", "")
        return error_envelope(
            agent="capture",
            intent="create_capture_draft",
            error=f"{result['error']}。{hint}".strip("。"),
        )
    draft_text = result.get("draft_text", "")
    if draft_text:
        message, preview_truncated = _draft_saved_preview(result, "生成")
        actions = [
            next_action(
                label="展开草稿",
                intent="show_capture_draft",
                payload={"draft_id": result.get("id", ""), "page": 1},
                requires_confirmation=False,
            ),
            next_action(
                label="继续修改",
                intent="revise_capture_draft",
                payload={"draft_id": result.get("id", "")},
                requires_confirmation=False,
            ),
        ]
        if result.get("status") != "draft_incomplete":
            actions.insert(
                1,
                next_action(
                    label="存库",
                    intent="commit_capture_draft",
                    payload={"draft_id": result.get("id", "")},
                    requires_confirmation=True,
                ),
            )
    else:
        preview_truncated = False
        revision_error = result.get("revision_error", "")
        if revision_error:
            guidance = "整理草稿暂时超时，已保留原文草稿。稍后继续整理，或直接回复新的修改要求。"
        else:
            guidance = "想怎么整理？比如「提炼重点」「翻译成英文」「压缩到 500 字」"
        message = (
            f"已提取《{result.get('title') or '无标题'}》"
            f"（{result.get('word_count', 0)} 字）\n"
            f"草稿编号：{result.get('id', '')}\n"
            f"{guidance}"
        )
        actions = [
            next_action(
                label="继续整理",
                intent="revise_capture_draft",
                payload={"draft_id": result.get("id", "")},
                requires_confirmation=False,
            )
        ]
    return success_envelope(
        agent="capture",
        intent="create_capture_draft",
        chat_preview=message,
        durable_artifacts=[
            artifact_ref(
                type="capture_draft",
                id=result.get("id", ""),
                title=result.get("title", ""),
                status=result.get("status", ""),
            )
        ],
        next_actions=actions,
        meta={
            "title": result.get("title", ""),
            "word_count": result.get("word_count", 0),
            "draft_status": result.get("status", ""),
            "summary": result.get("summary", ""),
            "tags": result.get("tags", []),
            "capture_intent": result.get("capture_intent", {}),
            "preview_truncated": preview_truncated,
            "revision_error": result.get("revision_error", ""),
        },
    )


def show_capture_draft(draft_id: str = "", page: int = 1, as_file: bool = False) -> dict:
    """显示最近或指定的待确认草稿正文；只读草稿，不入库。"""
    _, agent = _init()
    return _run_capture_with_ledger(
        agent,
        "show_capture_draft",
        {"draft_id": draft_id, "page": page, "as_file": as_file},
        lambda: _show_capture_draft(agent, draft_id=draft_id, page=page, as_file=as_file),
    )


def _show_capture_draft(agent, draft_id: str = "", page: int = 1, as_file: bool = False) -> dict:
    if not draft_id:
        draft = agent.draft_repo.get_latest_open()
    else:
        draft = agent.draft_repo.get_by_id(draft_id)
    if not draft:
        return error_envelope(
            agent="capture",
            intent="show_capture_draft",
            error="没有找到可查看的草稿，请先发送链接或文本。",
        )

    try:
        page_num = int(page)
    except (TypeError, ValueError):
        page_num = 1
    page_num = max(1, page_num)

    body = (draft.get("draft_text") or draft.get("raw_text") or "").strip()
    if not body:
        return error_envelope(
            agent="capture",
            intent="show_capture_draft",
            error=f"草稿 {draft.get('id', '')} 没有可显示正文。",
        )

    title = _clip_text(draft.get("title") or "无标题", 80)
    artifacts = [
        artifact_ref(
            type="capture_draft",
            id=draft.get("id", ""),
            title=draft.get("title", ""),
            status=draft.get("status", ""),
        )
    ]
    if as_file:
        file_artifact = _capture_draft_file_artifact(draft)
        return success_envelope(
            agent="capture",
            intent="show_capture_draft",
            chat_preview=f"已生成草稿全文文件：《{title}》。我会把文件发给你。",
            durable_artifacts=[file_artifact],
            meta={
                "draft_id": draft.get("id", ""),
                "title": draft.get("title", ""),
                "draft_status": draft.get("status", ""),
                "as_file": True,
                "file_path": file_artifact.get("path", ""),
            },
        )

    total_pages = max(1, (len(body) + DRAFT_PAGE_SIZE - 1) // DRAFT_PAGE_SIZE)
    if page_num > total_pages:
        return error_envelope(
            agent="capture",
            intent="show_capture_draft",
            error=f"草稿《{draft.get('title') or '无标题'}》只有 {total_pages} 页，请输入 1 到 {total_pages}。",
        )

    start = (page_num - 1) * DRAFT_PAGE_SIZE
    has_next = page_num < total_pages
    next_hint = "回复「继续看」查看下一页；" if has_next else ""
    header = f"草稿《{title}》\n第 {page_num}/{total_pages} 页\n\n"
    footer = f"\n\n{next_hint}回复修改意见继续改；满意回复「存库」。"
    chunk_limit = max(200, PREVIEW_LIMIT - len(header) - len(footer))
    chunk, preview_truncated = _clip_text_with_flag(body[start:start + DRAFT_PAGE_SIZE], chunk_limit)
    message = f"{header}{chunk}{footer}"
    actions = []
    if has_next:
        actions.append(
            next_action(
                label="继续看",
                intent="show_capture_draft",
                payload={"draft_id": draft.get("id", ""), "page": page_num + 1},
                requires_confirmation=False,
            )
        )
    return success_envelope(
        agent="capture",
        intent="show_capture_draft",
        chat_preview=_clip_text(message, PREVIEW_LIMIT),
        durable_artifacts=artifacts,
        next_actions=actions,
        meta={
            "draft_id": draft.get("id", ""),
            "title": draft.get("title", ""),
            "page": page_num,
            "total_pages": total_pages,
            "has_next": has_next,
            "preview_truncated": preview_truncated,
        },
    )


def revise_capture_draft(draft_id: str = "", instruction: str = "") -> dict:
    """按用户反馈修改最近或指定草稿，不写入正式库。"""
    _, agent = _init()
    return _run_capture_with_ledger(
        agent,
        "revise_capture_draft",
        {"draft_id": draft_id, "instruction": instruction},
        lambda: _revise_capture_draft(agent, draft_id=draft_id, instruction=instruction),
    )


def _revise_capture_draft(agent, draft_id: str = "", instruction: str = "") -> dict:
    if not draft_id:
        latest = agent.draft_repo.get_latest_open()
        draft_id = latest["id"] if latest else ""
    if not draft_id:
        return error_envelope(
            agent="capture",
            intent="revise_capture_draft",
            error="没有可修改的草稿，请先发送链接或文本。",
        )
    result = agent.revise_draft(draft_id, instruction)
    if result is None:
        return error_envelope(
            agent="capture",
            intent="revise_capture_draft",
            error=f"draft {draft_id} 未找到",
        )
    if "error" in result:
        return error_envelope(
            agent="capture",
            intent="revise_capture_draft",
            error=result["error"],
        )
    message, preview_truncated = _draft_saved_preview(result, "更新")
    actions = [
        next_action(
            label="展开草稿",
            intent="show_capture_draft",
            payload={"draft_id": result.get("id", ""), "page": 1},
            requires_confirmation=False,
        ),
        next_action(
            label="继续修改",
            intent="revise_capture_draft",
            payload={"draft_id": result.get("id", "")},
            requires_confirmation=False,
        ),
    ]
    if result.get("status") != "draft_incomplete":
        actions.insert(
            1,
            next_action(
                label="存库",
                intent="commit_capture_draft",
                payload={"draft_id": result.get("id", "")},
                requires_confirmation=True,
            ),
        )
    return success_envelope(
        agent="capture",
        intent="revise_capture_draft",
        chat_preview=message,
        durable_artifacts=[
            artifact_ref(
                type="capture_draft",
                id=result.get("id", ""),
                title=result.get("title", ""),
                status=result.get("status", ""),
            )
        ],
        next_actions=actions,
        meta={
            "title": result.get("title", ""),
            "summary": result.get("summary", ""),
            "tags": result.get("tags", []),
            "capture_intent": result.get("capture_intent", {}),
            "draft_status": result.get("status", ""),
            "preview_truncated": preview_truncated,
        },
    )


def commit_capture_draft(draft_id: str = "") -> dict:
    """用户确认后将草稿写入 Obsidian raw/ 和 captures/。"""
    _, agent = _init()
    return _run_capture_with_ledger(
        agent,
        "commit_capture_draft",
        {"draft_id": draft_id},
        lambda: _commit_capture_draft(agent, draft_id=draft_id),
    )


def _commit_capture_draft(agent, draft_id: str = "") -> dict:
    if not draft_id:
        latest = agent.draft_repo.get_latest_open()
        draft_id = latest["id"] if latest else ""
    if not draft_id:
        return error_envelope(
            agent="capture",
            intent="commit_capture_draft",
            error="没有可入库的草稿。",
        )
    result = agent.commit_draft(draft_id)
    if result is None:
        return error_envelope(
            agent="capture",
            intent="commit_capture_draft",
            error=f"draft {draft_id} 未找到",
        )
    if "error" in result:
        return error_envelope(
            agent="capture",
            intent="commit_capture_draft",
            error=result["error"],
        )
    message = (
            f"已存入知识库《{result.get('title') or '无标题'}》\n"
            f"摘要：{result.get('summary', '')}\n"
            f"标签：{', '.join(result.get('tags', []))}"
    )
    return success_envelope(
        agent="capture",
        intent="commit_capture_draft",
        chat_preview=message,
        durable_artifacts=[
            artifact_ref(
                type="raw_evidence",
                id=result.get("capture_id", ""),
                title=result.get("title", ""),
                path=result.get("raw_path", ""),
                status="created",
            ),
            artifact_ref(
                type="capture_note",
                id=result.get("capture_id", ""),
                title=result.get("title", ""),
                path=result.get("file_path", ""),
                status="created",
            ),
        ],
        meta={
            "draft_id": draft_id,
            "capture_id": result.get("capture_id", ""),
            "title": result.get("title", ""),
            "summary": result.get("summary", ""),
            "tags": result.get("tags", []),
            "raw_path": result.get("raw_path", ""),
            "file_path": result.get("file_path", ""),
        },
    )


def refine_capture(draft_id: str = "", instruction: str = "") -> dict:
    """兼容旧工具名：现在只修改草稿，不直接入库。"""
    _, agent = _init()
    return _run_capture_with_ledger(
        agent,
        "refine_capture",
        {"draft_id": draft_id, "instruction": instruction},
        lambda: _revise_capture_draft(agent, draft_id=draft_id, instruction=instruction),
    )

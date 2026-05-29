"""Capture Agent bridge."""
from __future__ import annotations
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import CaptureAgent, CaptureConfig, load_config
from bridge.result_envelope import (
    artifact_ref,
    error_envelope,
    next_action,
    success_envelope,
)

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


def _format_tags(tags: list[str]) -> str:
    return ", ".join(tags) if tags else "未标注"


def _clip_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _clip_text_with_flag(text: str, limit: int) -> tuple[str, bool]:
    clipped = _clip_text(text, limit)
    return clipped, clipped != text.strip()


def _draft_outline_preview(draft_text: str, limit: int = 900) -> tuple[str, bool]:
    lines = [line.strip() for line in draft_text.splitlines() if line.strip()]
    structured: list[str] = []
    for line in lines:
        cleaned = line.lstrip("#").strip()
        if not cleaned:
            continue
        if (
            line.startswith("#")
            or re.match(r"^\d+[\.\)、]\s*", line)
            or re.match(r"^[-*+]\s+", line)
        ):
            cleaned = re.sub(r"^\d+[\.\)、]\s*", "", cleaned).strip()
            cleaned = re.sub(r"^[-*+]\s+", "", cleaned).strip()
            structured.append(cleaned)
        if len(structured) >= 8:
            break

    if structured:
        item_truncated = any(_clip_text(item, 90) != item for item in structured)
        bullets = [f"{idx}. {_clip_text(item, 90)}" for idx, item in enumerate(structured, 1)]
        preview, preview_truncated = _clip_text_with_flag("\n".join(bullets), limit)
        return preview, preview_truncated or item_truncated or len(lines) > len(structured)

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", draft_text) if p.strip()]
    if not paragraphs:
        paragraphs = lines
    source_snippets = [p.replace("\n", " ") for p in paragraphs[:4]]
    snippet_truncated = any(_clip_text(p, 180) != p for p in source_snippets)
    snippets = [_clip_text(p, 180) for p in source_snippets]
    preview, preview_truncated = _clip_text_with_flag(
        "\n".join(f"{idx}. {item}" for idx, item in enumerate(snippets, 1)),
        limit,
    )
    return preview, preview_truncated or snippet_truncated or len(paragraphs) > len(snippets)


def _draft_saved_preview(result: dict, action: str) -> tuple[str, bool]:
    raw_title = result.get("title") or "无标题"
    raw_summary = result.get("summary") or "完整草稿已保存在草稿区，等待你确认。"
    raw_tags = _format_tags(result.get("tags", []))
    title, title_truncated = _clip_text_with_flag(raw_title, 100)
    summary, summary_truncated = _clip_text_with_flag(raw_summary, 260)
    tags, tags_truncated = _clip_text_with_flag(raw_tags, 160)
    draft_text = result.get("draft_text", "")
    outline, source_outline_truncated = (
        _draft_outline_preview(draft_text, limit=PREVIEW_LIMIT) if draft_text else ("暂无可预览正文。", False)
    )
    header = (
        f"已{action}《{title}》草稿。\n"
        f"核心摘要：{summary}\n"
        f"标签：{tags}\n"
        f"草稿编号：{result.get('id', '')}\n"
    )
    section_label = "\n草稿预览：\n"
    footer = "\n\n完整草稿已保存。回复「展开草稿」查看全文；回复修改意见继续改；满意回复「存库」。"
    outline_limit = max(120, PREVIEW_LIMIT - len(header) - len(section_label) - len(footer))
    outline, outline_truncated = _clip_text_with_flag(outline, outline_limit)
    message = f"{header}{section_label}{outline}{footer}"
    preview_truncated = any(
        [
            title_truncated,
            summary_truncated,
            tags_truncated,
            source_outline_truncated,
            outline_truncated,
        ]
    )
    return message, preview_truncated


def capture_content(content: str, instruction: str = "") -> dict:
    """提取内容并生成可确认的草稿；不直接写入 Obsidian 正式库。"""
    _, agent = _init()
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
                label="存库",
                intent="commit_capture_draft",
                payload={"draft_id": result.get("id", "")},
                requires_confirmation=True,
            ),
            next_action(
                label="继续修改",
                intent="revise_capture_draft",
                payload={"draft_id": result.get("id", "")},
                requires_confirmation=False,
            ),
        ]
    else:
        preview_truncated = False
        message = (
            f"已提取《{result.get('title') or '无标题'}》"
            f"（{result.get('word_count', 0)} 字）\n"
            f"草稿编号：{result.get('id', '')}\n"
            f"想怎么整理？比如「提炼重点」「翻译成英文」「压缩到 500 字」"
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
            "preview_truncated": preview_truncated,
        },
    )


def show_capture_draft(draft_id: str = "", page: int = 1) -> dict:
    """显示最近或指定的待确认草稿正文；只读草稿，不入库。"""
    _, agent = _init()
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

    total_pages = max(1, (len(body) + DRAFT_PAGE_SIZE - 1) // DRAFT_PAGE_SIZE)
    if page_num > total_pages:
        return error_envelope(
            agent="capture",
            intent="show_capture_draft",
            error=f"草稿《{draft.get('title') or '无标题'}》只有 {total_pages} 页，请输入 1 到 {total_pages}。",
        )

    start = (page_num - 1) * DRAFT_PAGE_SIZE
    has_next = page_num < total_pages
    title = _clip_text(draft.get("title") or "无标题", 80)
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
        durable_artifacts=[
            artifact_ref(
                type="capture_draft",
                id=draft.get("id", ""),
                title=draft.get("title", ""),
                status=draft.get("status", ""),
            )
        ],
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
        next_actions=[
            next_action(
                label="展开草稿",
                intent="show_capture_draft",
                payload={"draft_id": result.get("id", ""), "page": 1},
                requires_confirmation=False,
            ),
            next_action(
                label="存库",
                intent="commit_capture_draft",
                payload={"draft_id": result.get("id", "")},
                requires_confirmation=True,
            ),
            next_action(
                label="继续修改",
                intent="revise_capture_draft",
                payload={"draft_id": result.get("id", "")},
                requires_confirmation=False,
            ),
        ],
        meta={
            "title": result.get("title", ""),
            "summary": result.get("summary", ""),
            "tags": result.get("tags", []),
            "draft_status": result.get("status", ""),
            "preview_truncated": preview_truncated,
        },
    )


def commit_capture_draft(draft_id: str = "") -> dict:
    """用户确认后将草稿写入 Obsidian raw/ 和 captures/。"""
    _, agent = _init()
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
    return revise_capture_draft(draft_id, instruction)

"""Capture Agent bridge."""
from __future__ import annotations
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

def _init():
    global _config, _agent
    if _config is None:
        _config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
        _agent = CaptureAgent(_config)
    return _config, _agent


def _format_tags(tags: list[str]) -> str:
    return ", ".join(tags) if tags else "未标注"


def _draft_saved_preview(result: dict, action: str) -> str:
    title = result.get("title") or "无标题"
    summary = result.get("summary") or "完整草稿已保存在草稿区，等待你确认。"
    tags = _format_tags(result.get("tags", []))
    return (
        f"已{action}《{title}》草稿。\n"
        f"摘要：{summary}\n"
        f"标签：{tags}\n"
        f"草稿编号：{result.get('id', '')}\n"
        f"完整草稿已保存，不在微信里展开。\n"
        f"满意的话回复「存库」，不满意可以继续说怎么改。"
    )


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
        preview_truncated = True
        message = _draft_saved_preview(result, "生成")
        actions = [
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
    message = _draft_saved_preview(result, "更新")
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
            "preview_truncated": bool(result.get("draft_text", "")),
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

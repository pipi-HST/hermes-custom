"""Capture Agent bridge."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import CaptureAgent, CaptureConfig, load_config

PREVIEW_CHAR_LIMIT = 1800

_config = None
_agent = None

def _init():
    global _config, _agent
    if _config is None:
        _config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
        _agent = CaptureAgent(_config)
    return _config, _agent


def _draft_preview(text: str) -> tuple[str, bool]:
    if len(text) <= PREVIEW_CHAR_LIMIT:
        return text, False
    return text[:PREVIEW_CHAR_LIMIT].rstrip() + "\n\n...（微信预览已截断，完整草稿已暂存）", True


def capture_content(content: str, instruction: str = "") -> dict:
    """提取内容并生成可确认的草稿；不直接写入 Obsidian 正式库。"""
    _, agent = _init()
    result = agent.create_draft(content, instruction=instruction)
    if "error" in result:
        hint = result.get("hint", "")
        return {"success": False, "error": f"{result['error']}。{hint}".strip("。")}
    draft_text = result.get("draft_text", "")
    if draft_text:
        preview, is_truncated = _draft_preview(draft_text)
        intro = (
            "以下是完整草稿，后续「存库」会写入同一份正文。"
            if not is_truncated
            else "以下是微信预览版；完整草稿已保存到草稿区，后续「存库」会写入完整草稿正文。"
        )
        message = (
            f"{intro}\n\n"
            f"已生成《{result.get('title') or '无标题'}》草稿：\n\n"
            f"{preview}\n\n"
            f"草稿编号：{result.get('id', '')}\n"
            f"满意的话回复「存库」，不满意可以继续说怎么改。"
        )
    else:
        message = (
            f"已提取《{result.get('title') or '无标题'}》"
            f"（{result.get('word_count', 0)} 字）\n"
            f"草稿编号：{result.get('id', '')}\n"
            f"想怎么整理？比如「提炼重点」「翻译成英文」「压缩到 500 字」"
        )
    return {
        "success": True,
        "draft_id": result.get("id", ""),
        "title": result.get("title", ""),
        "word_count": result.get("word_count", 0),
        "status": result.get("status", ""),
        "draft_text": draft_text,
        "summary": result.get("summary", ""),
        "tags": result.get("tags", []),
        "message": message,
    }


def revise_capture_draft(draft_id: str = "", instruction: str = "") -> dict:
    """按用户反馈修改最近或指定草稿，不写入正式库。"""
    _, agent = _init()
    if not draft_id:
        latest = agent.draft_repo.get_latest_open()
        draft_id = latest["id"] if latest else ""
    if not draft_id:
        return {"success": False, "error": "没有可修改的草稿，请先发送链接或文本。"}
    result = agent.revise_draft(draft_id, instruction)
    if result is None:
        return {"success": False, "error": f"draft {draft_id} 未找到"}
    if "error" in result:
        return {"success": False, "error": result["error"]}
    draft_text = result.get("draft_text", "")
    preview, is_truncated = _draft_preview(draft_text)
    intro = (
        "以下是完整草稿，后续「存库」会写入同一份正文。"
        if not is_truncated
        else "以下是微信预览版；完整草稿已保存到草稿区，后续「存库」会写入完整草稿正文。"
    )
    return {
        "success": True,
        "draft_id": result.get("id", ""),
        "title": result.get("title", ""),
        "summary": result.get("summary", ""),
        "tags": result.get("tags", []),
        "draft_text": draft_text,
        "status": result.get("status", ""),
        "message": (
            f"{intro}\n\n"
            f"已更新《{result.get('title') or '无标题'}》草稿：\n\n"
            f"{preview}\n\n"
            f"草稿编号：{result.get('id', '')}\n"
            f"摘要：{result.get('summary', '')}\n"
            f"标签：{', '.join(result.get('tags', []))}\n\n"
            f"满意的话回复「存库」，不满意可以继续说怎么改。"
        ),
    }


def commit_capture_draft(draft_id: str = "") -> dict:
    """用户确认后将草稿写入 Obsidian raw/ 和 captures/。"""
    _, agent = _init()
    if not draft_id:
        latest = agent.draft_repo.get_latest_open()
        draft_id = latest["id"] if latest else ""
    if not draft_id:
        return {"success": False, "error": "没有可入库的草稿。"}
    result = agent.commit_draft(draft_id)
    if result is None:
        return {"success": False, "error": f"draft {draft_id} 未找到"}
    if "error" in result:
        return {"success": False, "error": result["error"]}
    return {
        "success": True,
        **result,
        "message": (
            f"已存入知识库《{result.get('title') or '无标题'}》\n"
            f"摘要：{result.get('summary', '')}\n"
            f"标签：{', '.join(result.get('tags', []))}"
        ),
    }


def refine_capture(draft_id: str = "", instruction: str = "") -> dict:
    """兼容旧工具名：现在只修改草稿，不直接入库。"""
    return revise_capture_draft(draft_id, instruction)

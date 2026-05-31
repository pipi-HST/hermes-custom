"""Review Agent bridge — Cron 入口."""
from __future__ import annotations
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.markdown_writer import read_note
from agents.capture_agent import load_config
from agents.rag_agent import RagAgent, format_answer
from agents.review_agent import ReviewAgent
from bridge.result_envelope import source_ref, success_envelope
from bridge.run_ledger import run_with_ledger
from db.schema import init_db
from db.repo import CaptureRepo

def run_daily_review() -> dict:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    return run_with_ledger(
        repo.conn,
        agent="review",
        intent="run_daily_review",
        trigger="wechat",
        input_ref={},
        operation=lambda: _run_daily_review(config, repo),
    )


def _run_daily_review(config, repo: CaptureRepo) -> dict:
    agent = ReviewAgent(repo, vault_path=config.vault_path)
    result = agent.run_daily()
    chat_preview = _format_daily_review_preview(result)
    return success_envelope(
        agent="review",
        intent="run_daily_review",
        chat_preview=chat_preview,
        meta={
            "item_count": len(result),
            "review_item_ids": [
                m["item_id"] for m in result if m.get("type") == "review_explanation"
            ],
        },
    )


def record_review_feedback(item_id: str, outcome: str) -> dict:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    agent = ReviewAgent(repo, vault_path=config.vault_path)
    updated = agent.record_item_feedback(item_id, outcome)
    if not updated:
        chat_preview = "没有找到刚刚讲过的回顾项，或这个反馈类型不支持。"
    else:
        chat_preview = _format_feedback_preview(outcome)
    return success_envelope(
        agent="review",
        intent="record_review_feedback",
        chat_preview=chat_preview,
        meta={"item_id": item_id, "outcome": outcome, "updated": updated},
    )


def expand_latest_review_followup(question: str) -> dict:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    return run_with_ledger(
        repo.conn,
        agent="review",
        intent="expand_latest_review_followup",
        trigger="wechat",
        input_ref={"question": question},
        operation=lambda: _expand_latest_review_followup(config, repo, question),
    )


def _expand_latest_review_followup(config, repo: CaptureRepo, question: str) -> dict:
    item = _latest_viewed_review_item(repo)
    if not item:
        return success_envelope(
            agent="review",
            intent="expand_latest_review_followup",
            chat_preview="还没有刚刚讲过的回顾项。你可以先说“复习一下知识点”。",
            meta={"question": question, "found": False},
        )

    capture = repo.get_by_id(item["capture_id"]) or {}
    capture_context = _capture_context_for_review_item(config.vault_path, capture, item)
    if not capture_context:
        return success_envelope(
            agent="review",
            intent="expand_latest_review_followup",
            chat_preview="找不到刚刚回顾的材料正文，暂时没法展开。",
            meta={"question": question, "found": False, "review_item_id": item["id"]},
        )

    followup_question = (
        f"用户正在追问刚刚回顾的知识点「{item['question'] or item['title']}」。"
        f"请只基于这一篇/这一节材料展开，不要联想其他文章。用户追问：{question}"
    )
    rag = RagAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    resp = rag.query(followup_question, [capture_context])
    source_type = "capture_section" if capture_context.get("section_title") else "capture_note"
    return success_envelope(
        agent="review",
        intent="expand_latest_review_followup",
        chat_preview=format_answer(resp["answer"], resp["sources"]),
        source_map=[
            source_ref(
                role="evidence",
                type=source_type,
                id=item["capture_id"],
                title=capture_context.get("title", ""),
                path=capture_context.get("file_path", ""),
                section=capture_context.get("section_title", ""),
            )
        ],
        meta={
            "question": question,
            "found": True,
            "review_item_id": item["id"],
            "capture_id": item["capture_id"],
            "section_id": item["section_id"],
        },
    )


def _latest_viewed_review_item(repo: CaptureRepo):
    return repo.conn.execute(
        """SELECT * FROM review_items
           WHERE last_viewed IS NOT NULL
             AND review_state NOT IN ('muted', 'mastered')
           ORDER BY last_viewed DESC, updated_at DESC
           LIMIT 1"""
    ).fetchone()


def _capture_context_for_review_item(
    vault_path: str,
    capture: dict,
    item,
) -> dict | None:
    file_path = capture.get("file_path") or item["source_path"].split("#", 1)[0]
    if not file_path:
        return None
    note = read_note(Path(vault_path).expanduser(), file_path)
    if not note:
        return None
    frontmatter, body = note
    section_title = item["title"] if item["section_id"] else ""
    if section_title:
        body = _extract_section_body(body, section_title) or body
    return {
        **capture,
        "file_path": file_path,
        "title": frontmatter.get("title") or item["title"] or file_path,
        "story": frontmatter.get("story", ""),
        "body": body,
        "section_title": section_title,
    }


def _extract_section_body(body: str, section_title: str) -> str:
    headings = list(re.finditer(r"^(#{2,6})\s+(.+?)\s*$", body or "", re.MULTILINE))
    wanted = section_title.strip()
    for idx, match in enumerate(headings):
        level = len(match.group(1))
        title = match.group(2).strip()
        if title != wanted:
            continue
        start = match.start()
        end = len(body)
        for later in headings[idx + 1:]:
            if len(later.group(1)) <= level:
                end = later.start()
                break
        return body[start:end].strip()
    return ""


def _format_feedback_preview(outcome: str) -> str:
    normalized = (outcome or "").strip().lower()
    if normalized in {"useful", "remembered", "confirmed", "有用", "记得", "会"}:
        return "已记录：这类内容对你有用。"
    if normalized in {"revisit", "forgotten", "forgot", "以后再讲", "忘了", "不会"}:
        return "已记录：这个点以后再讲。"
    if normalized in {"mute", "mastered", "skip", "不用复习", "别推", "掌握", "跳过"}:
        return "已记录：这个点以后不再主动复习。"
    return "已更新这条回顾偏好。"


def _format_daily_review_preview(messages: list[dict]) -> str:
    review_items = [m for m in messages if m.get("type") == "review_explanation"]
    garden_items = [m for m in messages if m.get("type") == "garden_review"]
    summaries = [m["text"] for m in messages if m.get("type") == "daily_summary"]

    lines: list[str] = []
    if review_items:
        item = review_items[0]
        topic = item.get("topic") or item.get("title") or "未命名"
        lines.append(f"今日回顾：{topic}")
        lines.append("")
        lines.append(f"来源：{item.get('source_path') or '未知来源'}")
        lines.append(
            f"这个点已讲过 {item.get('view_count', 0)} 次；"
            f"这篇材料已被回顾 {item.get('capture_view_count', 0)} 次。"
        )
        lines.append("")
        lines.append(item.get("text", ""))

    for item in garden_items:
        if lines:
            lines.append("")
        lines.append(item.get("text", ""))

    if summaries:
        if lines:
            lines.append("")
        lines.extend(summaries)

    if not lines:
        return "今日无回顾项目。"
    lines.append("")
    lines.append("你可以继续问：展开一下 / 举例 / 和另一篇对比 / 这对我有什么启发。")
    lines.append("如果这类内容对你有用，可以直接回复：有用 / 以后再讲 / 不用复习。")
    return "\n".join(lines)

"""Review Agent bridge — Cron 入口."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import load_config
from agents.review_agent import ReviewAgent
from bridge.result_envelope import success_envelope
from db.schema import init_db
from db.repo import CaptureRepo

def run_daily_review() -> dict:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
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
                m["item_id"] for m in result if m.get("type") == "review_item"
            ],
        },
    )


def record_review_feedback(item_id: str, outcome: str) -> dict:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    agent = ReviewAgent(repo, vault_path=config.vault_path)
    updated = agent.record_item_feedback(item_id, outcome)
    if not updated:
        chat_preview = f"没有找到复习项，或反馈类型不支持：{item_id}"
    else:
        chat_preview = "已更新这条复习的进度。"
    return success_envelope(
        agent="review",
        intent="record_review_feedback",
        chat_preview=chat_preview,
        meta={"item_id": item_id, "outcome": outcome, "updated": updated},
    )


def _format_daily_review_preview(messages: list[dict]) -> str:
    review_items = [m for m in messages if m.get("type") == "review_item"]
    garden_items = [m for m in messages if m.get("type") == "garden_review"]
    summaries = [m["text"] for m in messages if m.get("type") == "daily_summary"]

    lines: list[str] = []
    if review_items:
        lines.append(f"今日复习 {len(review_items)} 条：")
        for idx, item in enumerate(review_items, 1):
            lines.append("")
            lines.append(f"{idx}. {item.get('title') or '未命名'}")
            lines.append(item.get("text", ""))
            lines.append(f"复习项：{item.get('item_id', '')}")

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
    lines.append("回复“记得/忘了/跳过 + 复习项编号”，我会更新复习进度。")
    return "\n".join(lines)

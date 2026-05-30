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
        chat_preview = f"没有找到复习项，或反馈类型不支持：{item_id}"
    else:
        chat_preview = "已更新这条回顾偏好。"
    return success_envelope(
        agent="review",
        intent="record_review_feedback",
        chat_preview=chat_preview,
        meta={"item_id": item_id, "outcome": outcome, "updated": updated},
    )


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
        lines.append("")
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
    lines.append("你可以继续问：展开一下 / 举例 / 和另一篇对比 / 这对我有什么启发。")
    lines.append("也可以回复：有用 / 以后再讲 / 不用复习 + 复习项编号。")
    return "\n".join(lines)

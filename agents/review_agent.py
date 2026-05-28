"""Review Agent — 艾宾浩斯抽查 + 随机抽查 + 每日摘要."""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from agents.markdown_writer import read_note

logger = logging.getLogger(__name__)

EBBINGHAUS_INTERVALS = ["1d", "3d", "1w", "2w", "1m", "mastered"]
INTERVAL_HOURS = {"1d": 24, "3d": 72, "1w": 168, "2w": 336, "1m": 720}


def next_interval(current: str) -> str:
    """返回下一个艾宾浩斯阶段。"""
    try:
        idx = EBBINGHAUS_INTERVALS.index(current)
        return EBBINGHAUS_INTERVALS[min(idx + 1, len(EBBINGHAUS_INTERVALS) - 1)]
    except ValueError:
        return "1d"


def is_due(last_reviewed_at: Optional[str], interval: str) -> bool:
    """判断是否到期需要抽查。"""
    if interval == "mastered":
        return False
    if not last_reviewed_at:
        return True
    try:
        last = datetime.fromisoformat(last_reviewed_at)
    except (ValueError, TypeError):
        return True
    hours = INTERVAL_HOURS.get(interval, 24)
    due_at = last + timedelta(hours=hours)
    return datetime.now(timezone.utc) >= due_at


class ReviewAgent:
    def __init__(self, repo, vault_path: str = ""):
        self.repo = repo
        self.vault_path = Path(vault_path).expanduser() if vault_path else None

    def run_daily(self) -> dict:
        """执行每日回顾——返回需推送的消息列表。"""
        messages: list[dict] = []
        now = datetime.now(timezone.utc).isoformat()

        # 1. 艾宾浩斯抽查 Garden Cards
        cards = self._get_due_garden_cards()
        if cards:
            selected = cards[:2]  # 每天最多 2 张
            for card in selected:
                messages.append({
                    "type": "garden_review",
                    "card_id": card["id"],
                    "title": card["title"],
                    "one_liner": card["one_liner"],
                    "interval": card["review_interval"],
                    "text": (
                        f"该回顾『{card['title']}』了——"
                        f"上次是{self._format_last(card.get('last_reviewed_at'))}。"
                        f"一句话核心: {card['one_liner']}\n还记得要点吗？"
                    ),
                })
                # 标记为已推送（不推进 interval，等用户回复）
                self._touch_card(card["id"], now)

        # 2. 随机抽查普通 Capture（排除已在 Garden Card 中的）
        captures = self._get_random_captures(3)
        if captures:
            items = "\n".join(
                f"  - {c.get('title') or c.get('story','')[:60]}"
                for c in captures
            )
            messages.append({
                "type": "capture_review",
                "capture_ids": [c["id"] for c in captures],
                "text": f"今天回顾一下这几条：\n{items}",
            })

        # 3. 每日摘要
        summary = self._daily_summary()
        if summary:
            messages.append({"type": "daily_summary", "text": summary})

        return messages

    def handle_reply(self, card_id: str, action: str) -> None:
        """处理用户对回顾的回复。action: confirmed|forgot|skip。"""
        card = self.repo.conn.execute(
            "SELECT * FROM garden_cards WHERE id = ?", (card_id,)
        ).fetchone()
        if not card:
            return

        now = datetime.now(timezone.utc).isoformat()
        current = card["review_interval"] or "1d"
        skip_count = int(card["skip_count"] or 0)

        if action == "confirmed":
            self.repo.conn.execute(
                """UPDATE garden_cards SET last_reviewed_at=?,
                   review_interval=?, skip_count=0, updated_at=?
                   WHERE id=?""",
                (now, next_interval(current), now, card_id),
            )
        elif action == "forgot":
            self.repo.conn.execute(
                """UPDATE garden_cards SET last_reviewed_at=?,
                   review_interval='1d', skip_count=0, updated_at=?
                   WHERE id=?""",
                (now, now, card_id),
            )
        elif action == "skip":
            new_skip = skip_count + 1
            status = card["status"]
            if new_skip >= 3:
                status = "archived"
            self.repo.conn.execute(
                """UPDATE garden_cards SET skip_count=?, status=?, updated_at=?
                   WHERE id=?""",
                (new_skip, status, now, card_id),
            )
        self.repo.conn.commit()

    def mark_capture_reviewed(self, capture_id: str) -> None:
        """标记一条 capture 已回顾。"""
        now = datetime.now(timezone.utc).isoformat()
        self.repo.conn.execute(
            """UPDATE captures SET last_reviewed=?, review_count=review_count+1,
               updated_at=? WHERE id=?""",
            (now, now, capture_id),
        )
        self.repo.conn.commit()

    def _get_due_garden_cards(self) -> list[dict]:
        rows = self.repo.conn.execute(
            "SELECT * FROM garden_cards WHERE status != 'archived'"
        ).fetchall()
        due = [
            dict(r) for r in rows
            if is_due(r["last_reviewed_at"], r["review_interval"])
        ]
        due.sort(key=lambda c: c.get("last_reviewed_at") or "2000-01-01")
        return due

    def _get_random_captures(self, n: int = 3) -> list[dict]:
        # 获取所有 Garden Card 包含的 capture IDs
        garden_rows = self.repo.conn.execute(
            "SELECT capture_ids FROM garden_cards"
        ).fetchall()
        garden_capture_ids: set[str] = set()
        for row in garden_rows:
            import json
            try:
                ids = json.loads(row["capture_ids"])
                garden_capture_ids.update(ids)
            except (json.JSONDecodeError, TypeError):
                pass

        all_caps = self.repo.list_all(limit=500)
        candidates = [
            self._hydrate(c) for c in all_caps if c["id"] not in garden_capture_ids
        ]
        if len(candidates) <= n:
            return candidates
        return random.sample(candidates, n)

    def _hydrate(self, row: dict) -> dict:
        """从 .md frontmatter 补充标题和摘要。"""
        if not self.vault_path:
            return row
        file_path = row.get("file_path", "")
        if not file_path:
            return row
        note = read_note(self.vault_path, file_path)
        if not note:
            return row
        frontmatter, _ = note
        return {
            **row,
            "title": frontmatter.get("title", ""),
            "story": frontmatter.get("story", ""),
        }

    def _daily_summary(self) -> Optional[str]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = self.repo.conn.execute(
            "SELECT count(*) as cnt FROM captures WHERE date(created_at) = ?",
            (today,),
        ).fetchone()
        count = rows["cnt"] if rows else 0

        gc_rows = self.repo.conn.execute(
            "SELECT count(*) as cnt FROM garden_cards"
        ).fetchone()
        gc_count = gc_rows["cnt"] if gc_rows else 0

        if count == 0:
            return None
        return f"今天捕获了 {count} 条内容。知识花园共 {gc_count} 张卡片。"

    def _touch_card(self, card_id: str, now: str) -> None:
        self.repo.conn.execute(
            "UPDATE garden_cards SET updated_at=? WHERE id=?",
            (now, card_id),
        )
        self.repo.conn.commit()

    def _format_last(self, last_reviewed_at: Optional[str]) -> str:
        if not last_reviewed_at:
            return "首次回顾"
        try:
            last = datetime.fromisoformat(last_reviewed_at)
            delta = datetime.now(timezone.utc) - last
            days = delta.days
            if days == 0:
                return "今天"
            if days == 1:
                return "昨天"
            if days < 7:
                return f"{days}天前"
            return f"{days // 7}周前"
        except (ValueError, TypeError):
            return "之前"

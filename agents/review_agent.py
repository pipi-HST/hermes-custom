"""Review Agent — 艾宾浩斯抽查 + 随机抽查 + 每日摘要."""
from __future__ import annotations

import logging
import random
import hashlib
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from agents.markdown_writer import read_note

logger = logging.getLogger(__name__)

EBBINGHAUS_INTERVALS = ["1d", "3d", "1w", "2w", "1m", "mastered"]
INTERVAL_HOURS = {"1d": 24, "3d": 72, "1w": 168, "2w": 336, "1m": 720}
REVIEW_INTERVAL_DAYS = [1, 3, 7, 14, 30, 90]
DEFAULT_LONG_NOTE_CHARS = 2000
DAILY_REVIEW_ITEM_LIMIT = 1


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
    def __init__(
        self,
        repo,
        vault_path: str = "",
        *,
        long_note_chars: int = DEFAULT_LONG_NOTE_CHARS,
    ):
        self.repo = repo
        self.vault_path = Path(vault_path).expanduser() if vault_path else None
        self.long_note_chars = long_note_chars

    def run_daily(self) -> dict:
        """执行每日回顾——返回需推送的消息列表。"""
        messages: list[dict] = []
        now = datetime.now(timezone.utc).isoformat()

        # 1. 普通 Capture 回顾点：短文按整篇，长文按章节。
        self.sync_review_items(now=now)
        for item in self._get_due_review_items(limit=DAILY_REVIEW_ITEM_LIMIT, now=now):
            messages.append(self._build_review_explanation(item, now))

        # 2. 如果暂时没有 Capture 回顾点，再回顾 Garden Card。
        if not any(m["type"] == "review_explanation" for m in messages):
            cards = self._get_due_garden_cards()
            if cards:
                card = cards[0]
                messages.append({
                    "type": "garden_review",
                    "card_id": card["id"],
                    "title": card["title"],
                    "one_liner": card["one_liner"],
                    "interval": card["review_interval"],
                    "text": (
                        f"今日回顾：{card['title']}\n"
                        f"核心观点：{card['one_liner']}"
                    ),
                })
                self._touch_card(card["id"], now)

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

    def sync_review_items(self, *, now: str | None = None) -> int:
        """Ensure captures have review items without rewriting prose."""
        timestamp = now or datetime.now(timezone.utc).isoformat()
        count = 0
        for row in self.repo.list_all(limit=1000):
            count += self._sync_capture_review_items(row, timestamp)
        self.repo.conn.commit()
        return count

    def record_item_feedback(self, item_id: str, outcome: str) -> bool:
        """Update one review item's schedule from user feedback."""
        row = self.repo.conn.execute(
            "SELECT * FROM review_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return False

        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        current = int(row["review_interval"] or 1)
        normalized = (outcome or "").strip().lower()

        if normalized in {"useful", "remembered", "confirmed", "有用", "记得", "会"}:
            interval = 14
            state = "seen"
            next_review = (now_dt + timedelta(days=interval)).isoformat()
            review_count_delta = 1
        elif normalized in {"revisit", "forgotten", "forgot", "以后再讲", "忘了", "不会"}:
            interval = 3
            state = "revisit"
            next_review = (now_dt + timedelta(days=interval)).isoformat()
            review_count_delta = 1
        elif normalized in {"mute", "mastered", "skip", "不用复习", "别推", "掌握", "跳过"}:
            interval = current
            state = "muted"
            next_review = None
            review_count_delta = 1
        elif normalized in {"deeper", "讲深一点", "展开"}:
            interval = current
            state = row["review_state"]
            next_review = row["next_review"]
            review_count_delta = 0
        else:
            return False

        self.repo.conn.execute(
            """UPDATE review_items
               SET review_state=?, review_interval=?,
                   review_count=review_count+?, last_reviewed=?,
                   next_review=?, updated_at=?
               WHERE id=?""",
            (state, interval, review_count_delta, now, next_review, now, item_id),
        )
        self.repo.conn.commit()
        return True

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

    def _get_due_review_items(self, *, limit: int, now: str) -> list[dict]:
        rows = self.repo.conn.execute(
            "SELECT * FROM review_items WHERE review_state NOT IN ('mastered', 'muted')"
        ).fetchall()
        due = []
        for row in rows:
            item = dict(row)
            next_review = item.get("next_review")
            if not next_review or next_review <= now:
                due.append(item)
        due.sort(
            key=lambda item: (
                -int(item.get("importance") or 1),
                item.get("next_review") or "2000-01-01",
                item.get("created_at") or "2000-01-01",
            )
        )
        return due[:limit]

    def _build_review_explanation(self, item: dict, now: str) -> dict:
        view_count = int(item.get("view_count") or 0) + 1
        interval = int(item.get("review_interval") or 7)
        next_review = (datetime.fromisoformat(now) + timedelta(days=interval)).isoformat()

        capture = self.repo.get_by_id(item["capture_id"]) or {}
        capture_view_count = int(capture.get("review_count") or 0) + 1

        self.repo.conn.execute(
            """UPDATE review_items
               SET view_count=?, last_viewed=?, last_reviewed=?,
                   next_review=?, review_state='seen', updated_at=?
               WHERE id=?""",
            (view_count, now, now, next_review, now, item["id"]),
        )
        self.repo.conn.execute(
            """UPDATE captures
               SET review_count=review_count+1, last_reviewed=?, updated_at=?
               WHERE id=?""",
            (now, now, item["capture_id"]),
        )
        self.repo.conn.commit()

        topic = item["question"] or item["title"]
        explanation = _explanation_text(item.get("answer_hint"), topic)
        source = self._format_source(item)
        text = (
            f"详细讲解：{explanation}\n\n"
            f"为什么值得回顾：这个点能帮你重新连接材料里的关键判断，"
            f"避免只记得标题、忘了它真正想说明的问题。\n"
            f"来源：{source}"
        )
        return {
            "type": "review_explanation",
            "item_id": item["id"],
            "capture_id": item["capture_id"],
            "section_id": item["section_id"],
            "title": item["title"],
            "topic": topic,
            "source_path": source,
            "view_count": view_count,
            "capture_view_count": capture_view_count,
            "text": text,
        }

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

    def _sync_capture_review_items(self, row: dict, now: str) -> int:
        parsed = self._read_capture_note(row)
        if parsed is None:
            return 0
        frontmatter, body = parsed
        if not _is_reviewable(frontmatter):
            return 0

        capture_id = row["id"]
        file_path = row.get("file_path", "")
        title = str(frontmatter.get("title") or "未命名").strip() or "未命名"
        importance = _importance_value(frontmatter.get("importance"))
        sections = _markdown_sections(body)

        if len(body) < self.long_note_chars or not sections:
            hint = _answer_hint(frontmatter.get("story") or body)
            self._upsert_review_item(
                object_type="capture",
                object_id=capture_id,
                capture_id=capture_id,
                section_id="",
                title=title,
                question=title,
                answer_hint=hint,
                source_path=file_path,
                importance=importance,
                now=now,
            )
            return 1

        count = 0
        for idx, (section_title, section_body) in enumerate(sections, 1):
            section_id = f"section-{idx}"
            clean_title = _single_line(section_title)
            if not clean_title:
                continue
            self._upsert_review_item(
                object_type="capture_section",
                object_id=capture_id,
                capture_id=capture_id,
                section_id=section_id,
                title=clean_title,
                question=clean_title,
                answer_hint=_answer_hint(section_body),
                source_path=file_path,
                importance=importance,
                now=now,
            )
            count += 1
        return count

    def _read_capture_note(self, row: dict) -> tuple[dict, str] | None:
        if not self.vault_path:
            return None
        file_path = row.get("file_path", "")
        if not file_path:
            return None
        return read_note(self.vault_path, file_path)

    def _upsert_review_item(
        self,
        *,
        object_type: str,
        object_id: str,
        capture_id: str,
        section_id: str,
        title: str,
        question: str,
        answer_hint: str,
        source_path: str,
        importance: int,
        now: str,
    ) -> None:
        item_id = _review_item_id(object_type, object_id, section_id)
        self.repo.conn.execute(
            """INSERT INTO review_items
               (id, object_type, object_id, capture_id, section_id, title,
                question, answer_hint, source_path, importance, review_interval, next_review,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(object_type, object_id, section_id) DO UPDATE SET
                   title=excluded.title,
                   question=excluded.question,
                   answer_hint=excluded.answer_hint,
                   source_path=excluded.source_path,
                   importance=excluded.importance,
                   updated_at=excluded.updated_at""",
            (
                item_id,
                object_type,
                object_id,
                capture_id,
                section_id,
                title,
                question,
                answer_hint,
                source_path,
                importance,
                7,
                now,
                now,
                now,
            ),
        )

    def _format_source(self, item: dict) -> str:
        source = item.get("source_path") or item.get("capture_id") or "未知来源"
        section = item.get("section_id") or ""
        return f"{source}#{section}" if section else source

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


def _next_review_days(current: int) -> int:
    for days in REVIEW_INTERVAL_DAYS:
        if days > current:
            return days
    return REVIEW_INTERVAL_DAYS[-1]


def _state_for_interval(interval: int) -> str:
    if interval >= 90:
        return "mastered"
    if interval >= 30:
        return "familiar"
    return "learning"


def _review_item_id(object_type: str, object_id: str, section_id: str) -> str:
    raw = f"{object_type}:{object_id}:{section_id}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _is_reviewable(frontmatter: dict) -> bool:
    value = frontmatter.get("reviewable")
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    text = str(value).strip().lower()
    return text not in {"false", "no", "0", "否", "不复习"}


def _importance_value(value) -> int:
    if isinstance(value, int):
        return max(1, min(value, 3))
    text = str(value or "").strip().lower()
    if text in {"high", "important", "重要", "高"}:
        return 3
    if text in {"medium", "normal", "中", "普通"}:
        return 2
    return 1


def _markdown_sections(body: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^(#{2,3})\s+(.+?)\s*$", body or "", re.MULTILINE))
    sections: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        title = match.group(2).strip()
        if title == "目录纲要":
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        section_body = body[start:end].strip()
        sections.append((title, section_body))
    return sections


def _answer_hint(text) -> str:
    body = _single_line(str(text or ""))
    return body[:500].rstrip()


def _explanation_text(text, topic: str) -> str:
    hint = _answer_hint(text)
    if hint:
        return hint
    return f"{topic} 是这篇材料里值得重新看的一点。"


def _single_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

"""Single-use confirmation tokens for durable Knowledge mutations.

An HTTP client must first ask for a short-lived token bound to one intent and
object (and optionally an exact version) and present it back when it commits.
Tokens are stored hashed, are single use and expire quickly.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from db.concurrency import transaction

DEFAULT_TTL_SECONDS = 300


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _hash_token(token: str, object_version: str | None) -> str:
    # Versioned tokens get their own hash domain without changing how
    # existing unversioned tokens or object IDs are stored.
    material = token if object_version is None else f"versioned\0{object_version}\0{token}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ConfirmationRepo:
    """Issue and consume confirmation tokens in the shared SQLite database."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def issue(
        self,
        *,
        intent: str,
        object_id: str,
        object_version: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        issued_to: str = "api",
    ) -> tuple[str, str]:
        """Create a token and return ``(token, expires_at)``."""
        if not intent or not object_id:
            raise ValueError("intent and object_id are required")
        token = secrets.token_urlsafe(32)
        now = _utcnow()
        expires_at = now + timedelta(seconds=max(30, int(ttl_seconds)))
        with transaction(self.conn):
            # Opportunistic cleanup keeps the table small without a background job.
            self.conn.execute(
                "DELETE FROM confirmation_tokens WHERE expires_at < ?",
                (_iso(now),),
            )
            self.conn.execute(
                """INSERT INTO confirmation_tokens
                   (token_hash, intent, object_id, issued_at, expires_at, used_at, issued_to)
                   VALUES (?, ?, ?, ?, ?, NULL, ?)""",
                (
                    _hash_token(token, object_version),
                    intent,
                    object_id,
                    _iso(now),
                    _iso(expires_at),
                    (issued_to or "api").strip()[:40],
                ),
            )
        return token, _iso(expires_at)

    def consume(
        self, *, intent: str, object_id: str, token: str, object_version: str | None = None,
    ) -> str:
        """Validate and burn a token.  Returns ``""`` on success, else a message."""
        raw = (token or "").strip()
        if not raw:
            return "该操作需要确认令牌；请先在界面上确认后再提交。"
        digest = _hash_token(raw, object_version)
        with transaction(self.conn):
            now = _iso(_utcnow())
            row = self.conn.execute(
                """SELECT intent, object_id, expires_at, used_at
                   FROM confirmation_tokens WHERE token_hash = ?""",
                (digest,),
            ).fetchone()
            if row is None:
                return "确认令牌无效，请重新确认。"
            if row["used_at"]:
                return "确认令牌已被使用，请重新确认。"
            if row["intent"] != intent or row["object_id"] != object_id:
                return "确认令牌与当前对象不匹配，请重新确认。"
            if str(row["expires_at"]) <= now:
                return "确认令牌已过期，请重新确认。"
            cursor = self.conn.execute(
                """UPDATE confirmation_tokens SET used_at = ?
                   WHERE token_hash = ? AND used_at IS NULL""",
                (now, digest),
            )
            if cursor.rowcount != 1:
                return "确认令牌已被使用，请重新确认。"
        return ""

"""Concurrency primitives for shared SQLite connections."""
from __future__ import annotations

import sqlite3
from threading import Lock, RLock


class SerializedConnection(sqlite3.Connection):
    """SQLite connection carrying the lock used by bridge operations."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.operation_lock = RLock()


_fallback_guard = Lock()
_fallback_locks: dict[sqlite3.Connection, RLock] = {}


def connection_lock(conn: sqlite3.Connection) -> RLock:
    lock = getattr(conn, "operation_lock", None)
    if lock is not None:
        return lock
    with _fallback_guard:
        return _fallback_locks.setdefault(conn, RLock())

"""Concurrency primitives for shared SQLite connections.

The app shares one SQLite connection across the FastAPI threadpool and several
bridge/agent threads.  Two rules keep that safe:

1. Every statement and every ``commit``/``rollback`` is serialised through a
   per-connection re-entrant lock (``SerializedConnection``).
2. Multi-statement writes use :func:`transaction`, which takes the same lock
   and wraps the work in an explicit ``BEGIN IMMEDIATE`` transaction (or a
   ``SAVEPOINT`` when a transaction is already open on this thread).

Connections are opened with ``isolation_level=None`` (autocommit) so Python
never opens implicit transactions behind our back — the previous combination
of implicit transactions + a shared connection is what produced
``cannot start a transaction within a transaction`` and silent row loss when
one thread rolled back another thread's committed work.

Inside a :func:`transaction` block, stray ``conn.commit()`` calls are ignored:
the block owns the transaction and commits/rolls back once at the end.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from threading import RLock
from typing import Iterator
from uuid import uuid4


class SerializedConnection(sqlite3.Connection):
    """SQLite connection that serialises statements and transaction control."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.operation_lock = RLock()
        self._transaction_state = threading.local()

    # -- transaction ownership ------------------------------------------------
    @property
    def _transaction_depth(self) -> int:
        return getattr(self._transaction_state, "depth", 0)

    def _enter_transaction(self) -> None:
        self._transaction_state.depth = self._transaction_depth + 1

    def _leave_transaction(self) -> None:
        depth = self._transaction_depth - 1
        self._transaction_state.depth = max(0, depth)

    # -- sqlite3.Connection overrides ----------------------------------------
    def execute(self, sql, parameters=(), /):  # type: ignore[override]
        with self.operation_lock:
            return super().execute(sql, parameters)

    def executemany(self, sql, parameters, /):  # type: ignore[override]
        with self.operation_lock:
            return super().executemany(sql, parameters)

    def executescript(self, sql_script, /):  # type: ignore[override]
        with self.operation_lock:
            return super().executescript(sql_script)

    def commit(self):  # type: ignore[override]
        if self._transaction_depth:
            # Owned by db.concurrency.transaction(); the block commits at the end.
            return
        with self.operation_lock:
            return super().commit()

    def rollback(self):  # type: ignore[override]
        if self._transaction_depth:
            return
        with self.operation_lock:
            return super().rollback()


_fallback_guard = RLock()
_fallback_locks: dict[sqlite3.Connection, RLock] = {}


def connection_lock(conn: sqlite3.Connection) -> RLock:
    lock = getattr(conn, "operation_lock", None)
    if lock is not None:
        return lock
    with _fallback_guard:
        return _fallback_locks.setdefault(conn, RLock())


def fetch_dict(conn: sqlite3.Connection, sql: str, parameters=()) -> dict | None:
    """Fetch one row and materialise it to a dict under the connection lock."""
    with connection_lock(conn):
        row = conn.execute(sql, parameters).fetchone()
        return dict(row) if row is not None else None


def fetch_dicts(conn: sqlite3.Connection, sql: str, parameters=()) -> list[dict]:
    """Fetch all rows and materialise them to dicts under the connection lock."""
    with connection_lock(conn):
        return [dict(row) for row in conn.execute(sql, parameters).fetchall()]


def _start_transaction(conn: sqlite3.Connection, *, immediate: bool, retries: int, backoff: float) -> None:
    statement = "BEGIN IMMEDIATE" if immediate else "BEGIN"
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(max(1, retries)):
        try:
            conn.execute(statement)
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    if last_error is not None:
        raise last_error


@contextmanager
def transaction(
    conn: sqlite3.Connection,
    *,
    immediate: bool = True,
    retries: int = 5,
    backoff: float = 0.05,
) -> Iterator[None]:
    """Run a block inside one explicit transaction (or a savepoint if nested)."""
    lock = connection_lock(conn)
    with lock:
        if conn.in_transaction:
            savepoint = f"sp_{uuid4().hex[:8]}"
            conn.execute(f"SAVEPOINT {savepoint}")
            _enter(conn)
            try:
                yield
            except BaseException:
                try:
                    conn.execute(f"ROLLBACK TO {savepoint}")
                    conn.execute(f"RELEASE {savepoint}")
                finally:
                    _leave(conn)
                raise
            else:
                conn.execute(f"RELEASE {savepoint}")
                _leave(conn)
            return

        _start_transaction(conn, immediate=immediate, retries=retries, backoff=backoff)
        _enter(conn)
        try:
            yield
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            finally:
                _leave(conn)
            raise
        else:
            try:
                conn.execute("COMMIT")
            except BaseException:
                # A failed COMMIT (e.g. deferred constraints) can leave the
                # transaction open. A later retry must use a fresh transaction.
                if conn.in_transaction:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise
            finally:
                _leave(conn)


def _enter(conn: sqlite3.Connection) -> None:
    hook = getattr(conn, "_enter_transaction", None)
    if hook is not None:
        hook()


def _leave(conn: sqlite3.Connection) -> None:
    hook = getattr(conn, "_leave_transaction", None)
    if hook is not None:
        hook()

"""Lightweight cooperative cancellation for user-visible AI operations."""
from __future__ import annotations

from contextlib import contextmanager
import contextvars
from dataclasses import dataclass
from threading import Lock


_current_operation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "hermes_ai_operation_id",
    default="",
)
_cancelled_operations: set[str] = set()
_lock = Lock()


@dataclass
class AIOperationCancelled(RuntimeError):
    """Raised when the current AI operation was cancelled by the user."""

    operation_id: str
    stage: str = ""

    def __str__(self) -> str:
        suffix = f" at {self.stage}" if self.stage else ""
        return f"AI operation cancelled: {self.operation_id}{suffix}"


def normalize_operation_id(operation_id: str | None) -> str:
    text = str(operation_id or "").strip()
    if not text:
        return ""
    return "".join(ch for ch in text if ch.isalnum() or ch in "-_:")[:120]


def cancel_operation(operation_id: str | None) -> bool:
    operation_id = normalize_operation_id(operation_id)
    if not operation_id:
        return False
    with _lock:
        _cancelled_operations.add(operation_id)
    return True


def clear_operation(operation_id: str | None) -> None:
    operation_id = normalize_operation_id(operation_id)
    if not operation_id:
        return
    with _lock:
        _cancelled_operations.discard(operation_id)


def current_operation_id() -> str:
    return _current_operation_id.get("")


def is_cancelled(operation_id: str | None = None) -> bool:
    operation_id = normalize_operation_id(operation_id) or current_operation_id()
    if not operation_id:
        return False
    with _lock:
        return operation_id in _cancelled_operations


def raise_if_cancelled(stage: str = "") -> None:
    operation_id = current_operation_id()
    if operation_id and is_cancelled(operation_id):
        raise AIOperationCancelled(operation_id=operation_id, stage=stage)


@contextmanager
def operation_context(operation_id: str | None):
    operation_id = normalize_operation_id(operation_id)
    token = _current_operation_id.set(operation_id)
    try:
        yield operation_id
    finally:
        _current_operation_id.reset(token)
        clear_operation(operation_id)

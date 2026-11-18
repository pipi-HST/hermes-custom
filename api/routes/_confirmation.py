"""Shared confirmation-token helpers for HTTP routes.

Durable Knowledge writes must be preceded by an explicit user confirmation.
The dashboard requests a short-lived single-use token and presents it when it
commits; the routes below burn the token before delegating to the bridge.
"""
from __future__ import annotations

from fastapi import HTTPException

from db.confirmation_repo import ConfirmationRepo


def issue_confirmation_token(
    repo: ConfirmationRepo,
    *,
    intent: str,
    object_id: str,
    object_version: str | None = None,
) -> dict:
    token, expires_at = repo.issue(intent=intent, object_id=object_id, object_version=object_version)
    return {"token": token, "expires_at": expires_at, "intent": intent}


def require_confirmation_token(
    repo: ConfirmationRepo,
    *,
    intent: str,
    object_id: str,
    token: str,
    object_version: str | None = None,
) -> None:
    error = repo.consume(intent=intent, object_id=object_id, token=token, object_version=object_version)
    if error:
        raise HTTPException(
            status_code=403,
            detail={"code": "confirmation_required", "message": error},
        )

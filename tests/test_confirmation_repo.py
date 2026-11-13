"""Unit tests for single-use confirmation tokens."""
from __future__ import annotations

import sqlite3

import pytest

from db.confirmation_repo import ConfirmationRepo
from db.schema import init_db


@pytest.fixture
def repo(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    return ConfirmationRepo(conn)


def test_issue_and_consume_roundtrip(repo):
    token, expires_at = repo.issue(intent="commit_capture_draft", object_id="d1")

    assert token
    assert expires_at
    assert repo.consume(intent="commit_capture_draft", object_id="d1", token=token) == ""


def test_token_is_single_use(repo):
    token, _ = repo.issue(intent="commit_capture_draft", object_id="d1")

    assert repo.consume(intent="commit_capture_draft", object_id="d1", token=token) == ""
    assert repo.consume(intent="commit_capture_draft", object_id="d1", token=token)


def test_token_is_bound_to_object_and_intent(repo):
    token, _ = repo.issue(intent="commit_capture_draft", object_id="d1")

    assert repo.consume(intent="commit_capture_draft", object_id="d2", token=token)
    assert repo.consume(intent="commit_reading_artifact", object_id="d1", token=token)


def test_expired_token_is_rejected(repo):
    token, _ = repo.issue(
        intent="commit_capture_draft",
        object_id="d1",
        ttl_seconds=30,
    )
    # Force expiry instead of sleeping.
    repo.conn.execute(
        "UPDATE confirmation_tokens SET expires_at = ? WHERE used_at IS NULL",
        ("2000-01-01T00:00:00+00:00",),
    )
    repo.conn.commit()

    assert repo.consume(intent="commit_capture_draft", object_id="d1", token=token)


def test_unknown_token_is_rejected(repo):
    assert repo.consume(intent="commit_capture_draft", object_id="d1", token="nope")
    assert repo.consume(intent="commit_capture_draft", object_id="d1", token="")


def test_token_is_stored_hashed(repo):
    token, _ = repo.issue(intent="commit_capture_draft", object_id="d1")

    rows = repo.conn.execute("SELECT token_hash FROM confirmation_tokens").fetchall()
    assert len(rows) == 1
    assert rows[0]["token_hash"] != token

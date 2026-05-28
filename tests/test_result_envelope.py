"""Tests for the shared Agent Result Envelope helpers."""

from __future__ import annotations

import pytest

from bridge.result_envelope import (
    ENVELOPE_FIELDS,
    artifact_ref,
    error_envelope,
    envelope_json,
    next_action,
    require_envelope,
    source_ref,
    success_envelope,
)


def test_success_envelope_has_stable_fields() -> None:
    envelope = success_envelope(
        agent="capture",
        intent="create_capture_draft",
        chat_preview="已生成草稿。",
    )

    assert list(envelope.keys()) == list(ENVELOPE_FIELDS)
    assert envelope["status"] == "success"
    assert envelope["agent"] == "capture"
    assert envelope["intent"] == "create_capture_draft"
    assert envelope["chat_preview"] == "已生成草稿。"
    assert envelope["durable_artifacts"] == []
    assert envelope["source_map"] == []
    assert envelope["next_actions"] == []
    assert envelope["warnings"] == []
    assert envelope["errors"] == []
    assert envelope["meta"] == {}


def test_error_envelope_has_stable_fields() -> None:
    envelope = error_envelope(
        agent="capture",
        intent="commit_capture_draft",
        error="没有可入库的草稿。",
    )

    assert list(envelope.keys()) == list(ENVELOPE_FIELDS)
    assert envelope["status"] == "error"
    assert envelope["chat_preview"] == "没有可入库的草稿。"
    assert envelope["errors"] == ["没有可入库的草稿。"]


def test_require_envelope_rejects_plain_string() -> None:
    with pytest.raises(TypeError, match="mapping"):
        require_envelope("已生成草稿。")  # type: ignore[arg-type]


def test_require_envelope_rejects_legacy_shape() -> None:
    legacy = {"success": True, "message": "已生成草稿。", "draft_id": "d1"}

    with pytest.raises(ValueError, match="missing fields"):
        require_envelope(legacy)


def test_envelope_json_preserves_full_contract() -> None:
    envelope = success_envelope(
        agent="capture",
        intent="create_capture_draft",
        chat_preview="已生成草稿。",
        next_actions=[
            next_action(
                label="存库",
                intent="commit_capture_draft",
                payload={"draft_id": "d1"},
                requires_confirmation=True,
            )
        ],
    )

    encoded = envelope_json(envelope)

    assert '"chat_preview": "已生成草稿。"' in encoded
    assert '"requires_confirmation": true' in encoded
    assert set(require_envelope(envelope)) == set(ENVELOPE_FIELDS)


def test_artifact_ref_has_stable_fields() -> None:
    ref = artifact_ref(
        type="capture_note",
        id="cap-1",
        title="测试笔记",
        path="captures/test.md",
        status="created",
    )

    assert ref == {
        "type": "capture_note",
        "id": "cap-1",
        "title": "测试笔记",
        "path": "captures/test.md",
        "status": "created",
    }


def test_source_ref_has_stable_fields() -> None:
    ref = source_ref(
        role="evidence",
        type="capture_section",
        id="cap-1",
        title="测试笔记",
        path="captures/test.md",
        section="核心机制",
        status="",
    )

    assert ref == {
        "role": "evidence",
        "type": "capture_section",
        "id": "cap-1",
        "title": "测试笔记",
        "path": "captures/test.md",
        "section": "核心机制",
        "status": "",
    }


def test_next_action_has_stable_fields() -> None:
    action = next_action(
        label="展开来源",
        intent="show_source_map",
        payload={"query": "AI"},
        requires_confirmation=False,
    )

    assert action == {
        "label": "展开来源",
        "intent": "show_source_map",
        "payload": {"query": "AI"},
        "requires_confirmation": False,
    }


@pytest.mark.parametrize(
    "intent",
    [
        "commit_capture_draft",
        "discard_capture_draft",
        "create_theme_page",
        "create_garden_card",
        "accept_garden_card",
        "archive_garden_card",
        "apply_librarian_repair",
        "mark_review_item_mastered",
    ],
)
def test_state_changing_next_actions_require_confirmation(intent: str) -> None:
    with pytest.raises(ValueError, match="requires_confirmation"):
        next_action(
            label="危险操作",
            intent=intent,
            payload={},
            requires_confirmation=False,
        )

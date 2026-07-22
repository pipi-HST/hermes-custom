import pytest


@pytest.mark.parametrize(
    ("intent", "confirmation"),
    [
        ("commit_capture_draft", "确认存库"),
        ("commit_capture_draft", "就这样"),
        ("commit_garden_draft", "确认卡片"),
        ("apply_librarian_repair", "执行修复"),
    ],
)
def test_explicit_confirmation_phrases_are_accepted(intent, confirmation):
    from bridge.confirmation import confirmation_error

    assert confirmation_error(intent, confirmation=confirmation) == ""


@pytest.mark.parametrize(
    ("intent", "confirmation"),
    [
        ("commit_capture_draft", "请把这篇文章整理后直接入库"),
        ("commit_capture_draft", "可以"),
        ("commit_capture_draft", "不要入库"),
        ("commit_garden_draft", "继续修改卡片"),
        ("apply_librarian_repair", "先不要修复"),
    ],
)
def test_ambiguous_or_negative_messages_are_rejected(intent, confirmation):
    from bridge.confirmation import confirmation_error

    assert confirmation_error(intent, confirmation=confirmation)


def test_trusted_dashboard_action_is_accepted_without_chat_phrase():
    from bridge.confirmation import confirmation_error

    assert confirmation_error(
        "commit_capture_draft",
        confirmation_source="dashboard",
    ) == ""

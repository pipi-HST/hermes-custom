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


def test_non_agent_source_requires_a_verified_token():
    """HTTP callers cannot bypass the gate with a 'trusted source' string."""
    from bridge.confirmation import confirmation_error

    assert confirmation_error(
        "commit_capture_draft",
        confirmation_source="dashboard",
    )
    assert confirmation_error(
        "commit_capture_draft",
        confirmation_source="api",
    )
    # Only a route that has already burnt a single-use token may pass this flag.
    assert (
        confirmation_error(
            "commit_capture_draft",
            confirmation_source="api",
            token_verified=True,
        )
        == ""
    )


def test_reading_commit_requires_explicit_confirmation():
    from bridge.confirmation import confirmation_error

    assert confirmation_error("commit_reading_artifact")
    assert confirmation_error("commit_reading_artifact", confirmation="继续修改")
    assert confirmation_error("commit_reading_artifact", confirmation="确认整理") == ""

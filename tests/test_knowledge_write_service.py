from datetime import datetime, timezone
from pathlib import Path

import pytest

import bridge.knowledge_write_service as knowledge_write_service
from bridge.knowledge_write_service import KnowledgeWriteService, MarkdownWriteRequest
from tests.conftest import requires_runtime, requires_symlink  # noqa: E402


def test_write_markdown_artifact_creates_parent_directory_and_file(tmp_path):
    service = KnowledgeWriteService(vault_path=tmp_path)

    result = service.write_markdown_artifact(
        MarkdownWriteRequest(
            kind="knowledge_card",
            relative_path="garden/card.md",
            content="# Card\n\nBody\n",
            db_object_id="card1",
            db_table="garden_cards",
        )
    )

    assert result.kind == "knowledge_card"
    assert result.file_path == "garden/card.md"
    assert result.db_object_id == "card1"
    assert result.db_table == "garden_cards"
    assert result.backup_path == ""
    assert result.warnings == []
    assert (tmp_path / "garden" / "card.md").read_text(encoding="utf-8") == "# Card\n\nBody\n"


def test_write_markdown_artifact_backs_up_existing_file(tmp_path):
    target = tmp_path / "garden" / "card.md"
    target.parent.mkdir(parents=True)
    target.write_text("old content", encoding="utf-8")
    service = KnowledgeWriteService(vault_path=tmp_path)

    result = service.write_markdown_artifact(
        MarkdownWriteRequest(
            kind="knowledge_card",
            relative_path="garden/card.md",
            content="new content",
            db_object_id="card1",
            db_table="garden_cards",
            requires_backup=True,
        )
    )

    assert result.file_path == "garden/card.md"
    assert result.backup_path.startswith("_archive/write_backups/garden/card-")
    assert (tmp_path / result.backup_path).read_text(encoding="utf-8") == "old content"
    assert target.read_text(encoding="utf-8") == "new content"


def test_write_markdown_artifact_does_not_overwrite_backup_created_in_same_second(
    tmp_path, monkeypatch
):
    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 10, 8, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(knowledge_write_service, "datetime", FrozenDateTime)
    target = tmp_path / "garden" / "card.md"
    target.parent.mkdir(parents=True)
    target.write_text("original content", encoding="utf-8")
    service = KnowledgeWriteService(vault_path=tmp_path)

    first = service.write_markdown_artifact(
        MarkdownWriteRequest(
            kind="knowledge_card",
            relative_path="garden/card.md",
            content="first update",
            requires_backup=True,
        )
    )
    second = service.write_markdown_artifact(
        MarkdownWriteRequest(
            kind="knowledge_card",
            relative_path="garden/card.md",
            content="second update",
            requires_backup=True,
        )
    )

    assert first.backup_path != second.backup_path
    assert (tmp_path / first.backup_path).read_text(encoding="utf-8") == "original content"
    assert (tmp_path / second.backup_path).read_text(encoding="utf-8") == "first update"


def test_snapshot_backup_does_not_reuse_the_full_note_title(tmp_path):
    relative = Path("captures") / ("长文章标题" * 12 + ".md")
    target = tmp_path / relative
    target.parent.mkdir()
    target.write_text("previous version", encoding="utf-8")
    result = KnowledgeWriteService(tmp_path).write_markdown_artifact(
        MarkdownWriteRequest("capture", relative.as_posix(), "updated", requires_backup=True),
        expected_previous="previous version",
    )
    backup = tmp_path / result.backup_path
    assert len(backup.name) <= 65
    assert backup.read_text(encoding="utf-8") == "previous version"
    assert target.read_text(encoding="utf-8") == "updated"


@requires_symlink
def test_write_markdown_artifact_rejects_symlink_escape_from_vault(tmp_path):
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    (vault / "linked").symlink_to(outside, target_is_directory=True)
    service = KnowledgeWriteService(vault_path=vault)

    with pytest.raises(ValueError, match="must stay inside"):
        service.write_markdown_artifact(
            MarkdownWriteRequest(
                kind="knowledge_card",
                relative_path="linked/escaped.md",
                content="escaped content",
            )
        )

    assert not (outside / "escaped.md").exists()


def test_write_markdown_artifact_preserves_existing_file_when_replace_fails(
    tmp_path, monkeypatch
):
    target = tmp_path / "garden" / "card.md"
    target.parent.mkdir(parents=True)
    target.write_text("existing content", encoding="utf-8")
    service = KnowledgeWriteService(vault_path=tmp_path)
    replace_paths = []

    def fail_replace(source, destination):
        replace_paths.append((source, destination))
        raise OSError("replace failed")

    monkeypatch.setattr(knowledge_write_service.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        service.write_markdown_artifact(
            MarkdownWriteRequest(
                kind="knowledge_card",
                relative_path="garden/card.md",
                content="new content",
            )
        )

    assert len(replace_paths) == 1
    source, destination = map(Path, replace_paths[0])
    assert source.parent == target.parent
    assert destination == target
    assert target.read_text(encoding="utf-8") == "existing content"

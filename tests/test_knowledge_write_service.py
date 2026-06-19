from bridge.knowledge_write_service import KnowledgeWriteService, MarkdownWriteRequest


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

from db.metadata_repo import CaptureMetadataRepo
from db.schema import init_db


def test_upsert_outline_stores_json_and_hash(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = CaptureMetadataRepo(conn)
    outline = [{"level": 1, "title": "主线", "summary": "核心内容"}]

    repo.upsert_outline("cap1", outline, source_hash="hash1")

    row = repo.get("cap1")
    assert row is not None
    assert row["outline"] == outline
    assert row["outline_source_hash"] == "hash1"


def test_upsert_outline_replaces_existing_outline(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    repo = CaptureMetadataRepo(conn)

    repo.upsert_outline("cap1", [{"level": 1, "title": "旧"}], source_hash="old")
    repo.upsert_outline("cap1", [{"level": 1, "title": "新"}], source_hash="new")

    row = repo.get("cap1")
    assert row is not None
    assert row["outline"] == [{"level": 1, "title": "新"}]
    assert row["outline_source_hash"] == "new"

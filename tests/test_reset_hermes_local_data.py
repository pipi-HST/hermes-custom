from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from pathlib import Path

import pytest

from db.schema import init_db
from scripts import reset_hermes_local_data as reset_script
from scripts.reset_hermes_local_data import (
    CONFIRMATION_PHRASE,
    reset_local_data,
    verify_empty_reset,
)


def test_dry_run_reports_tables_paths_new_storage_and_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reset_script,
        "_automation_preflight",
        lambda **_: {
            "blockers": ["watch_vault.py process is running"],
            "watcher_processes": [{"pid": 123, "command": "watch_vault.py"}],
        },
    )
    db_path = tmp_path / "captures.db"
    conn = init_db(str(db_path))
    conn.execute("INSERT INTO captures (id, file_path) VALUES ('cap1', 'captures/a.md')")
    conn.execute("CREATE TABLE life_events (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO life_events (id) VALUES ('life1')")
    conn.commit()
    conn.close()

    legacy = tmp_path / "Obsidian" / "hermes-vault"
    (legacy / "captures").mkdir(parents=True)
    (legacy / "captures" / "a.md").write_text("A", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / "backups" / "knowledge-cleanup").mkdir(parents=True)
    (repo / "backups" / "knowledge-cleanup" / "capture.json").write_text("{}", encoding="utf-8")
    (repo / "data" / "backups").mkdir(parents=True)
    (repo / "data" / "backups" / "capture.md").write_text("backup", encoding="utf-8")
    new_storage = tmp_path / "Hermes" / "knowledge"
    (new_storage / "captures").mkdir(parents=True)
    (new_storage / "captures" / "residual.md").write_text("residual", encoding="utf-8")

    result = reset_local_data(
        db_path=db_path,
        legacy_store=legacy,
        new_storage_root=new_storage,
        repo_root=repo,
    )

    assert result["dry_run"] is True
    tables = {item["name"]: item for item in result["tables"]}
    assert tables["captures"]["rows"] == 1
    assert tables["life_events"]["category"] == "legacy_life_residual"
    assert result["legacy_knowledge_store"]["markdown_files"] == 1
    backup_targets = {Path(item["path"]).name: item for item in result["content_backup_targets"]}
    assert backup_targets["backups"]["files"] == 1
    assert any(item["files"] == 1 for item in result["content_backup_targets"])
    assert result["automation_preflight"]["blockers"] == [
        "watch_vault.py process is running"
    ]
    assert {Path(item["path"]).name for item in result["new_storage_dirs"]} >= {
        "captures",
        "raw",
        "attachments",
    }
    assert result["new_storage_files"] == ["captures/residual.md"]


def test_apply_requires_confirmation(tmp_path: Path) -> None:
    db_path = tmp_path / "captures.db"
    init_db(str(db_path)).close()

    with pytest.raises(ValueError):
        reset_local_data(
            db_path=db_path,
            legacy_store=tmp_path / "vault",
            new_storage_root=tmp_path / "knowledge",
            repo_root=tmp_path / "repo",
            apply=True,
            confirmation="wrong",
        )


def test_matching_processes_ignores_test_and_inspection_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            stdout=(
                " 101 .venv/bin/python scripts/theme_scan_cron.py\n"
                " 102 .venv/bin/python -m pytest tests/tools/test_theme_scan_tool.py -q\n"
                " 103 /bin/zsh -lc ps ax -o pid= -o command= | rg theme_scan\n"
                " 104 rg theme_scan\n"
            )
        )

    monkeypatch.setattr(reset_script.subprocess, "run", fake_run)

    assert reset_script._matching_processes(["theme_scan", "theme_scan_cron.py"]) == [
        {"pid": 101, "command": ".venv/bin/python scripts/theme_scan_cron.py"}
    ]


def test_apply_refuses_when_preflight_has_blockers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reset_script,
        "_automation_preflight",
        lambda **_: {"blockers": ["enabled cron jobs: daily-review"]},
    )
    db_path = tmp_path / "captures.db"
    init_db(str(db_path)).close()

    with pytest.raises(ValueError, match="enabled cron jobs"):
        reset_local_data(
            db_path=db_path,
            legacy_store=tmp_path / "vault",
            new_storage_root=tmp_path / "knowledge",
            repo_root=tmp_path / "repo",
            apply=True,
            confirmation=CONFIRMATION_PHRASE,
        )


def test_apply_refuses_home_as_delete_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reset_script,
        "_automation_preflight",
        lambda **_: {"blockers": []},
    )
    db_path = tmp_path / "captures.db"
    init_db(str(db_path)).close()

    with pytest.raises(ValueError, match="unsafe legacy store"):
        reset_local_data(
            db_path=db_path,
            legacy_store=Path.home(),
            new_storage_root=tmp_path / "knowledge",
            repo_root=tmp_path / "repo",
            apply=True,
            confirmation=CONFIRMATION_PHRASE,
        )


def test_apply_clears_rows_preserves_schema_and_creates_empty_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reset_script,
        "_automation_preflight",
        lambda **_: {"blockers": []},
    )
    db_path = tmp_path / "captures.db"
    conn = init_db(str(db_path))
    conn.execute("INSERT INTO captures (id, file_path) VALUES ('cap1', 'captures/a.md')")
    conn.execute("INSERT INTO garden_cards (id, title) VALUES ('card1', 'Card')")
    conn.execute(
        """INSERT INTO capture_search_fts
           (capture_id, kind, section_id, section_title_tokens, index_text_tokens)
           VALUES ('cap1', 'identity', '', 'card', 'body')"""
    )
    conn.execute("CREATE TABLE life_events (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO life_events (id) VALUES ('life1')")
    conn.commit()
    conn.close()

    legacy = tmp_path / "legacy"
    (legacy / "captures").mkdir(parents=True)
    (legacy / "captures" / "a.md").write_text("A", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / "backups" / "knowledge-cleanup").mkdir(parents=True)
    (repo / "backups" / "knowledge-cleanup" / "capture.json").write_text("{}", encoding="utf-8")
    (repo / "data" / "backups").mkdir(parents=True)
    (repo / "data" / "backups" / "capture.md").write_text("backup", encoding="utf-8")
    new_storage = tmp_path / "knowledge"
    (new_storage / "captures").mkdir(parents=True)
    (new_storage / "captures" / "residual.md").write_text("residual", encoding="utf-8")
    (new_storage / "attachments").mkdir(parents=True)
    (new_storage / "attachments" / "asset.bin").write_bytes(b"asset")

    result = reset_local_data(
        db_path=db_path,
        legacy_store=legacy,
        new_storage_root=new_storage,
        repo_root=repo,
        apply=True,
        confirmation=CONFIRMATION_PHRASE,
    )

    assert result["applied"] is True
    assert not legacy.exists()
    assert not (repo / "backups").exists()
    assert not (repo / "data" / "backups").exists()
    assert (new_storage / "captures").is_dir()
    assert (new_storage / "_archive" / "raw").is_dir()
    assert (new_storage / "attachments").is_dir()
    assert not (new_storage / "captures" / "residual.md").exists()
    assert not (new_storage / "attachments" / "asset.bin").exists()
    assert result["new_storage_files"] == []

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM garden_cards").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM capture_search_fts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM life_events").fetchone()[0] == 0
        cols = [row[1] for row in conn.execute("PRAGMA table_info(captures)")]
        assert "file_path" in cols
    finally:
        conn.close()


def test_verify_empty_reset_reports_success_after_empty_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reset_script,
        "_automation_preflight",
        lambda **_: {"blockers": []},
    )
    db_path = tmp_path / "captures.db"
    conn = init_db(str(db_path))
    conn.execute("INSERT INTO captures (id, file_path) VALUES ('cap1', 'captures/a.md')")
    conn.commit()
    conn.close()

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "config.yaml").write_text("vault:\n  path: /tmp/knowledge\n", encoding="utf-8")
    (repo / "data" / "rag.log").write_text("", encoding="utf-8")
    life_db = tmp_path / "life.db"
    life_db.write_text("", encoding="utf-8")
    new_storage = tmp_path / "knowledge"

    reset_local_data(
        db_path=db_path,
        legacy_store=legacy,
        new_storage_root=new_storage,
        repo_root=repo,
        apply=True,
        confirmation=CONFIRMATION_PHRASE,
    )
    result = verify_empty_reset(
        db_path=db_path,
        legacy_store=legacy,
        new_storage_root=new_storage,
        repo_root=repo,
        life_db_path=life_db,
    )

    assert result["ok"] is True
    assert result["failures"] == []


def test_verify_empty_reset_reports_residual_data_and_files(tmp_path: Path) -> None:
    db_path = tmp_path / "captures.db"
    conn = init_db(str(db_path))
    conn.execute("INSERT INTO captures (id, file_path) VALUES ('cap1', 'captures/a.md')")
    conn.commit()
    conn.close()

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    repo = tmp_path / "repo"
    (repo / "config.yaml").parent.mkdir(parents=True)
    (repo / "config.yaml").write_text("vault:\n  path: /tmp/knowledge\n", encoding="utf-8")
    life_db = tmp_path / "life.db"
    life_db.write_text("", encoding="utf-8")
    new_storage = tmp_path / "knowledge"
    (new_storage / "captures").mkdir(parents=True)
    (new_storage / "_archive" / "raw").mkdir(parents=True)
    (new_storage / "attachments").mkdir(parents=True)
    (new_storage / "captures" / "a.md").write_text("residual", encoding="utf-8")

    result = verify_empty_reset(
        db_path=db_path,
        legacy_store=legacy,
        new_storage_root=new_storage,
        repo_root=repo,
        life_db_path=life_db,
    )

    assert result["ok"] is False
    assert any("non-empty reset tables" in failure for failure in result["failures"])
    assert any("legacy store still exists" in failure for failure in result["failures"])
    assert any("new storage is not empty" in failure for failure in result["failures"])

"""Tests for the vault watcher hook."""
from __future__ import annotations

from pathlib import Path

from scripts import watch_vault


def test_run_repair_invokes_apply_and_readable_rename(monkeypatch, tmp_path):
    calls = []

    def fake_repair(db_path, vault_path, *, apply, rename_readable):
        calls.append(
            {
                "db_path": Path(db_path),
                "vault_path": Path(vault_path),
                "apply": apply,
                "rename_readable": rename_readable,
            }
        )
        return {"planned_actions": []}

    monkeypatch.setattr(watch_vault, "repair_data", fake_repair)

    result = watch_vault.run_repair(tmp_path / "captures.db", tmp_path / "vault")

    assert result == {"planned_actions": []}
    assert calls == [
        {
            "db_path": tmp_path / "captures.db",
            "vault_path": tmp_path / "vault",
            "apply": True,
            "rename_readable": True,
        }
    ]


def test_snapshot_vault_includes_archived_raw(tmp_path):
    vault = tmp_path / "vault"
    (vault / "captures").mkdir(parents=True)
    (vault / "raw").mkdir(parents=True)
    (vault / "_archive" / "raw").mkdir(parents=True)
    (vault / "captures" / "note.md").write_text("capture", encoding="utf-8")
    (vault / "raw" / "legacy.md").write_text("legacy", encoding="utf-8")
    (vault / "_archive" / "raw" / "source.md").write_text("raw", encoding="utf-8")

    snapshot = watch_vault.snapshot_vault(vault)

    assert "captures/note.md" in snapshot
    assert "_archive/raw/source.md" in snapshot
    assert "raw/legacy.md" not in snapshot

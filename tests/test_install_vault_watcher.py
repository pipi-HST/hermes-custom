"""Tests for the LaunchAgent installer."""
from __future__ import annotations

from pathlib import Path

from scripts.install_vault_watcher import build_plist
from scripts.install_vault_watcher import DEFAULT_INTERVAL_SECONDS
from scripts.install_vault_watcher import install


def test_build_plist_points_to_watcher_and_local_paths(tmp_path):
    plist = build_plist(
        python_path=tmp_path / ".venv/bin/python",
        script_path=tmp_path / "scripts/watch_vault.py",
        db_path=tmp_path / "data/captures.db",
        vault_path=tmp_path / "vault",
        log_path=tmp_path / "data/vault_watcher.log",
        interval=7,
    )

    assert "<key>Label</key>" in plist
    assert "com.pipi.hermes-vault-watcher" in plist
    assert str(tmp_path / "scripts/watch_vault.py") in plist
    assert "<string>--interval</string>" in plist
    assert "<string>7</string>" in plist
    assert str(tmp_path / "data/vault_watcher.log") in plist


def test_default_interval_is_six_hours():
    assert DEFAULT_INTERVAL_SECONDS == 21600


def test_install_uses_six_hour_default_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    plist_path = install(project_root=tmp_path / "project", vault_path=tmp_path / "vault")

    plist = plist_path.read_text(encoding="utf-8")
    assert "<string>--interval</string>" in plist
    assert f"<string>{DEFAULT_INTERVAL_SECONDS}</string>" in plist

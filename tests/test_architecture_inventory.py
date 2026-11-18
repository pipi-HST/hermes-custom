"""Regression tests for the architecture inventory gate.

``--strict`` verifies Runtime routes, so a missing Runtime checkout must fail
instead of reporting a false green.
"""
from __future__ import annotations

import sys

from scripts import architecture_inventory as inventory


def test_strict_fails_when_runtime_checkout_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(inventory, "RUNTIME_ROOT", tmp_path / "missing-runtime")
    monkeypatch.setattr(sys, "argv", ["architecture_inventory.py", "--strict"])

    assert inventory.main() == 1

    captured = capsys.readouterr()
    assert "runtime checkout not found" in captured.err
    assert "--strict cannot verify Runtime routes" in captured.err


def test_non_strict_skips_when_runtime_checkout_is_missing(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(inventory, "RUNTIME_ROOT", tmp_path / "missing-runtime")
    monkeypatch.setattr(sys, "argv", ["architecture_inventory.py", "--check"])

    assert inventory.main() == 0

    captured = capsys.readouterr()
    assert "architecture inventory check skipped" in captured.out


def test_strict_passes_with_runtime_checkout(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    (runtime_root / "hermes_cli").mkdir(parents=True)
    (runtime_root / "hermes_cli" / "web_server.py").write_text(
        "", encoding="utf-8"
    )
    output = tmp_path / "INVENTORY.md"

    monkeypatch.setattr(inventory, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(inventory, "OUTPUT", output)
    monkeypatch.setattr(sys, "argv", ["architecture_inventory.py", "--strict"])

    assert inventory.main() == 0
    assert output.exists()

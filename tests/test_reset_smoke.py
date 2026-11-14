from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.reset_smoke import run_reset_smoke


def test_reset_smoke_runs_capture_commit_index_and_rag(tmp_path: Path) -> None:
    result = run_reset_smoke(work_dir=tmp_path / "smoke")

    assert result["ok"] is True
    assert result["capture"]["file_exists"] is True
    assert result["capture"]["raw_exists"] is True
    assert result["capture"]["file_path"].startswith("captures/")
    assert result["capture"]["raw_path"].startswith("_archive/raw/")
    assert result["embedding"]["identity_count"] == 1
    assert result["rag"]["status"] == "success"
    assert result["rag"]["result_count"] == 1
    assert result["rag"]["source_map"][0]["type"] == "capture_note"
    assert result["counts"]["captures"] == 1
    assert result["counts"]["capture_drafts"] == 1


def test_reset_smoke_can_include_structured_card(tmp_path: Path) -> None:
    result = run_reset_smoke(work_dir=tmp_path / "smoke", include_card=True)

    assert result["ok"] is True
    assert result["card"]["id"] == "reset_smoke_card"
    assert result["card"]["storage"] == "sqlite"
    assert result["card"]["file_exists"] is False
    assert not any(path.startswith("garden/") for path in result["files"])
    assert result["counts"]["garden_cards"] == 1


@pytest.mark.skipif(
    not Path(".venv/bin/python").exists(),
    reason="test drives the POSIX venv launcher (.venv/bin/python)",
)
def test_reset_smoke_cli_outputs_json(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            ".venv/bin/python",
            "scripts/reset_smoke.py",
            "--work-dir",
            str(tmp_path / "cli-smoke"),
        ],
        cwd="/Users/pipi/hermes-custom",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["capture"]["file_exists"] is True

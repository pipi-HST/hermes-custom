"""Knowledge folder taxonomy shared by the dashboard, WeChat skill and seed data."""
from __future__ import annotations

import json

from bridge.folder_store import DEFAULT_FOLDERS, STORE_RELATIVE_PATH, visible_folders


def test_default_folders_match_learner_taxonomy():
    assert DEFAULT_FOLDERS == (
        "Inbox",
        "AI 产品面试",
        "Agent 与大模型",
        "行业与产品拆解",
        "读书与随笔",
    )


def test_legacy_stored_folders_stay_visible(tmp_path):
    vault = tmp_path / "vault"
    store = vault / STORE_RELATIVE_PATH
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"folders": ["数据 Agent"], "hidden": []}), encoding="utf-8")

    folders = visible_folders(vault)

    assert folders[0] == "Inbox"
    assert "数据 Agent" in folders
    assert "AI 产品面试" in folders

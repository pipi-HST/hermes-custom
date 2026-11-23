"""Regression tests for Tier 1 debt fixes: citation_events indexes, load_config cache, folder sync."""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import yaml

from agents.capture_agent import clear_config_cache, load_config
from bridge.folder_store import DEFAULT_FOLDERS
from db.schema import SCHEMA_VERSION, init_db


def _citation_event_indexes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='citation_events'"
    ).fetchall()
    return {str(r[0]) for r in rows}


def test_citation_events_indexes_created_on_fresh_and_upgraded_db(tmp_path: Path):
    db_path = tmp_path / "fresh.db"
    conn = init_db(str(db_path))
    try:
        indexes = _citation_event_indexes(conn)
        assert "idx_citation_events_card" in indexes
        assert "idx_citation_events_run" in indexes
        assert "idx_citation_events_cited_at" in indexes

        # Simulate an older v4 database missing the new run_id / cited_at indexes.
        conn.execute("DROP INDEX idx_citation_events_run")
        conn.execute("DROP INDEX idx_citation_events_cited_at")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()

    reopened = init_db(str(db_path))
    try:
        indexes = _citation_event_indexes(reopened)
        assert "idx_citation_events_card" in indexes
        assert "idx_citation_events_run" in indexes
        assert "idx_citation_events_cited_at" in indexes
    finally:
        reopened.close()


def test_load_config_caches_by_stat_and_isolates_callers(tmp_path: Path, monkeypatch):
    clear_config_cache()
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"llm": {"model": "deepseek-chat", "api_key": "k1"}}),
        encoding="utf-8",
    )

    real_safe_load = yaml.safe_load
    load_count = 0

    def counting_safe_load(stream):
        nonlocal load_count
        load_count += 1
        return real_safe_load(stream)

    monkeypatch.setattr(yaml, "safe_load", counting_safe_load)

    first = load_config(str(cfg_path))
    second = load_config(str(cfg_path))
    assert load_count == 1
    assert first.llm_model == "deepseek-chat"
    assert second.llm_model == "deepseek-chat"

    # Mutating one caller's config must not corrupt the cached entry.
    first.llm_model = "mutated"
    third = load_config(str(cfg_path))
    assert third.llm_model == "deepseek-chat"
    assert load_count == 1

    # Updating the file on disk (different size) must invalidate the cache.
    cfg_path.write_text(
        yaml.safe_dump({"llm": {"model": "deepseek-reasoner-v2", "api_key": "k2"}}),
        encoding="utf-8",
    )
    reloaded = load_config(str(cfg_path))
    assert reloaded.llm_model == "deepseek-reasoner-v2"
    assert load_count == 2

    clear_config_cache()
    after_clear = load_config(str(cfg_path))
    assert after_clear.llm_model == "deepseek-reasoner-v2"
    assert load_count == 3


def test_runtime_default_folders_match_folder_store():
    runtime_root = Path(__file__).resolve().parents[2] / "hermes-agent-main"
    candidates = [
        runtime_root / "plugins" / "web_knowledge" / "routes.py",
        runtime_root / "hermes_cli" / "web_server.py",
    ]
    existing = [p for p in candidates if p.exists()]
    if not existing:
        return
    for path in existing:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_KNOWLEDGE_DEFAULT_FOLDERS":
                        runtime_folders = tuple(ast.literal_eval(node.value))
                        assert runtime_folders == DEFAULT_FOLDERS
                        return
    raise AssertionError("_KNOWLEDGE_DEFAULT_FOLDERS not found in runtime knowledge routes")

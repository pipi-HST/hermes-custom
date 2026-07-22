"""Tests for Librarian bridge envelope behavior."""
from __future__ import annotations

from pathlib import Path


def _db_sync_report(*, dry_run: bool, actions: list[dict] | None = None) -> dict:
    return {
        "dry_run": dry_run,
        "db_path": "data/captures.db",
        "vault_path": "/tmp/vault",
        "backup_path": "" if dry_run else "data/backups/captures-test.db",
        "report_path": "data/repair_reports/repair-test.json",
        "before": {
            "captures": 3,
            "capture_drafts": 5,
            "duplicate_capture_paths": 1,
            "open_draft_duplicate_groups": 2,
            "missing_capture_paths": 1,
        },
        "after": {
            "captures": 3,
            "capture_drafts": 5,
            "duplicate_capture_paths": 0 if not dry_run else 1,
            "open_draft_duplicate_groups": 0 if not dry_run else 2,
            "missing_capture_paths": 0 if not dry_run else 1,
        },
        "planned_actions": [
            {"type": "delete_missing_capture", "capture_id": "cap-1"},
            {"type": "discard_duplicate_open_draft", "discard_id": "draft-1"},
        ] if actions is None else actions,
        "missing_file_reports": [],
        "missing_file_deletions": [],
    }


def _outline_report(*, dry_run: bool, updated: int = 0) -> dict:
    return {
        "dry_run": dry_run,
        "scanned": 4,
        "needs_outline": 1 if dry_run else updated,
        "updated": updated,
        "skipped_short": 1,
        "skipped_existing": 2,
        "errors": 0,
        "items": [
            {
                "file_path": "captures/long.md",
                "action": "needs_outline" if dry_run else "updated_outline",
                "body_chars": 3000,
            }
        ],
    }


def _embedding_index_report(
    *,
    dry_run: bool,
    missing_or_stale: int = 2,
    updated: int = 0,
) -> dict:
    return {
        "dry_run": dry_run,
        "scanned": 3,
        "missing_or_stale": missing_or_stale,
        "updated": updated,
        "errors": 0,
        "items": (
            [{"file_path": "captures/a.md", "action": "needs_embedding_index"}]
            if missing_or_stale
            else []
        ),
    }


def _property_report(*, dry_run: bool, actions: list[dict] | None = None) -> dict:
    has_gaps = actions is None or bool(actions)
    planned_actions = (
        [
            {
                "type": "backfill_capture_properties",
                "file_path": "captures/a.md",
                "fields": {"type": "capture", "status": "processed"},
            },
            {
                "type": "backfill_raw_properties",
                "file_path": "_archive/raw/a.md",
                "fields": {"type": "raw", "capture_id": "cap-1"},
            },
        ]
        if actions is None
        else actions
    )
    return {
        "dry_run": dry_run,
        "planned_action_count": len(planned_actions),
        "applied_action_count": 0 if dry_run else len(planned_actions),
        "planned_actions": planned_actions,
        "before": {
            "capture_notes_scanned": 3,
            "raw_notes_scanned": 2,
            "capture_gap_count": 1 if has_gaps else 0,
            "raw_gap_count": 1 if has_gaps else 0,
            "capture_gaps": (
                [{"file_path": "captures/a.md", "missing_fields": ["type"]}]
                if has_gaps
                else []
            ),
            "raw_gaps": (
                [{"file_path": "_archive/raw/a.md", "missing_fields": ["capture_id"]}]
                if has_gaps
                else []
            ),
        },
        "after": {
            "capture_notes_scanned": 3,
            "raw_notes_scanned": 2,
            "capture_gap_count": (
                0 if (not dry_run and planned_actions) or not has_gaps else 1
            ),
            "raw_gap_count": 0 if (not dry_run and planned_actions) or not has_gaps else 1,
            "capture_gaps": (
                []
                if (not dry_run and planned_actions) or not has_gaps
                else [{"file_path": "captures/a.md", "missing_fields": ["type"]}]
            ),
            "raw_gaps": (
                []
                if (not dry_run and planned_actions) or not has_gaps
                else [{"file_path": "_archive/raw/a.md", "missing_fields": ["capture_id"]}]
            ),
        },
    }


class FakeLibraryAgent:
    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def maintain_database_sync(self, *, apply: bool = False) -> dict:
        self.calls.append({"method": "database_sync", "apply": apply})
        return _db_sync_report(dry_run=not apply)

    def maintain_outlines(self, *, apply: bool = False) -> dict:
        self.calls.append({"method": "outlines", "apply": apply})
        return _outline_report(dry_run=not apply, updated=1 if apply else 0)

    def maintain_property_gaps(self, *, apply: bool = False) -> dict:
        self.calls.append({"method": "property_gaps", "apply": apply})
        return _property_report(dry_run=not apply)

    def maintain_embedding_index(self, *, apply: bool = False) -> dict:
        self.calls.append({"method": "embedding_index", "apply": apply})
        return _embedding_index_report(dry_run=not apply, updated=2 if apply else 0)


def test_librarian_audit_is_dry_run_by_default(monkeypatch) -> None:
    from bridge import librarian_bridge

    FakeLibraryAgent.calls = []
    monkeypatch.setattr(librarian_bridge, "LibraryAgent", FakeLibraryAgent)
    monkeypatch.setattr(
        librarian_bridge,
        "load_config",
        lambda _path: type(
            "Config",
            (),
            {
                "vault_path": "/tmp/vault",
                "db_path": "data/captures.db",
                "llm_api_key": "sk-test",
                "llm_base_url": "https://example.com",
            },
        )(),
    )
    monkeypatch.setattr(librarian_bridge, "Outliner", lambda **_kwargs: object())
    written_reports: list[dict] = []
    monkeypatch.setattr(
        librarian_bridge,
        "_write_librarian_report",
        lambda *args, **kwargs: written_reports.append(kwargs["payload"])
        or "data/repair_reports/librarian-test.json",
    )

    envelope = librarian_bridge.run_librarian_audit()

    assert envelope["status"] == "success"
    assert envelope["agent"] == "librarian"
    assert envelope["intent"] == "audit_library"
    assert "审计完成" in envelope["chat_preview"]
    assert "待确认修复" in envelope["chat_preview"]
    assert "data/repair_reports/librarian-test.json" in envelope["chat_preview"]
    assert "Properties" in envelope["chat_preview"]
    assert envelope["next_actions"][0]["intent"] == "apply_librarian_repair"
    assert envelope["next_actions"][0]["requires_confirmation"] is True
    assert envelope["meta"]["dry_run"] is True
    assert envelope["meta"]["database_sync"]["planned_action_count"] == 2
    assert envelope["meta"]["outlines"]["needs_outline"] == 1
    assert envelope["meta"]["property_gaps"]["planned_action_count"] == 2
    assert envelope["meta"]["embedding_index"]["missing_or_stale"] == 2
    assert "索引" in envelope["chat_preview"]
    assert envelope["meta"]["property_gaps"]["after"]["capture_gap_count"] == 1
    assert written_reports[0]["property_gaps"]["planned_action_count"] == 2
    assert written_reports[0]["embedding_index"]["missing_or_stale"] == 2
    assert (
        written_reports[0]["database_sync"]["raw_report_path"]
        == "data/repair_reports/repair-test.json"
    )
    assert FakeLibraryAgent.calls == [
        {"method": "database_sync", "apply": False},
        {"method": "outlines", "apply": False},
        {"method": "property_gaps", "apply": False},
        {"method": "embedding_index", "apply": False},
    ]


def test_librarian_apply_runs_confirmed_repairs(monkeypatch) -> None:
    from bridge import librarian_bridge

    FakeLibraryAgent.calls = []
    monkeypatch.setattr(librarian_bridge, "LibraryAgent", FakeLibraryAgent)
    monkeypatch.setattr(
        librarian_bridge,
        "load_config",
        lambda _path: type(
            "Config",
            (),
            {
                "vault_path": "/tmp/vault",
                "db_path": "data/captures.db",
                "llm_api_key": "sk-test",
                "llm_base_url": "https://example.com",
            },
        )(),
    )
    monkeypatch.setattr(librarian_bridge, "Outliner", lambda **_kwargs: object())
    monkeypatch.setattr(
        librarian_bridge,
        "_write_librarian_report",
        lambda *args, **kwargs: "data/repair_reports/librarian-applied.json",
    )

    envelope = librarian_bridge.run_librarian_audit(
        apply=True,
        confirmation_source="manual",
    )

    assert envelope["status"] == "success"
    assert envelope["intent"] == "apply_librarian_repair"
    assert "已执行" in envelope["chat_preview"]
    assert "备份" in envelope["chat_preview"]
    assert envelope["next_actions"] == []
    assert envelope["meta"]["dry_run"] is False
    assert envelope["meta"]["outlines"]["updated"] == 1
    assert envelope["meta"]["property_gaps"]["applied_action_count"] == 2
    assert envelope["meta"]["embedding_index"]["updated"] == 2
    assert FakeLibraryAgent.calls == [
        {"method": "database_sync", "apply": True},
        {"method": "outlines", "apply": True},
        {"method": "property_gaps", "apply": True},
        {"method": "embedding_index", "apply": True},
    ]


def test_librarian_apply_requires_explicit_confirmation(monkeypatch) -> None:
    from bridge import librarian_bridge

    FakeLibraryAgent.calls = []
    monkeypatch.setattr(librarian_bridge, "LibraryAgent", FakeLibraryAgent)
    monkeypatch.setattr(
        librarian_bridge,
        "load_config",
        lambda _path: type(
            "Config",
            (),
            {
                "vault_path": "/tmp/vault",
                "db_path": "data/captures.db",
                "llm_api_key": "sk-test",
                "llm_base_url": "https://example.com",
            },
        )(),
    )

    envelope = librarian_bridge.run_librarian_audit(apply=True)

    assert envelope["status"] == "error"
    assert "确认修复" in envelope["chat_preview"]
    assert FakeLibraryAgent.calls == []


def test_librarian_bridge_reports_embedding_index_status(monkeypatch) -> None:
    from bridge import librarian_bridge

    class IndexLibraryAgent(FakeLibraryAgent):
        def maintain_database_sync(self, *, apply: bool = False) -> dict:
            report = _db_sync_report(dry_run=not apply, actions=[])
            report["after"]["duplicate_capture_paths"] = 0
            report["after"]["open_draft_duplicate_groups"] = 0
            report["after"]["missing_capture_paths"] = 0
            return report

        def maintain_outlines(self, *, apply: bool = False) -> dict:
            return {
                **_outline_report(dry_run=not apply, updated=0),
                "needs_outline": 0,
                "items": [],
            }

        def maintain_property_gaps(self, *, apply: bool = False) -> dict:
            return _property_report(dry_run=not apply, actions=[])

        def maintain_embedding_index(self, *, apply: bool = False) -> dict:
            return _embedding_index_report(dry_run=not apply, missing_or_stale=0)

        def maintain_embedding_index(self, *, apply: bool = False) -> dict:
            return _embedding_index_report(dry_run=not apply, missing_or_stale=2)

    monkeypatch.setattr(librarian_bridge, "LibraryAgent", IndexLibraryAgent)
    monkeypatch.setattr(
        librarian_bridge,
        "load_config",
        lambda _path: type(
            "Config",
            (),
            {
                "vault_path": "/tmp/vault",
                "db_path": "data/captures.db",
                "llm_api_key": "sk-test",
                "llm_base_url": "https://example.com",
                "qwen_api_key": "qwen-test",
            },
        )(),
    )
    monkeypatch.setattr(librarian_bridge, "Outliner", lambda **_kwargs: object())

    envelope = librarian_bridge.run_librarian_audit(apply=False)

    assert envelope["meta"]["embedding_index"]["missing_or_stale"] == 2
    assert "索引" in envelope["chat_preview"]
    assert envelope["next_actions"][0]["intent"] == "apply_librarian_repair"


def test_librarian_clean_report_has_no_apply_action(monkeypatch) -> None:
    from bridge import librarian_bridge

    class CleanLibraryAgent(FakeLibraryAgent):
        def maintain_database_sync(self, *, apply: bool = False) -> dict:
            report = _db_sync_report(dry_run=not apply, actions=[])
            report["before"]["duplicate_capture_paths"] = 0
            report["before"]["open_draft_duplicate_groups"] = 0
            report["before"]["missing_capture_paths"] = 0
            report["after"]["duplicate_capture_paths"] = 0
            report["after"]["open_draft_duplicate_groups"] = 0
            report["after"]["missing_capture_paths"] = 0
            return report

        def maintain_outlines(self, *, apply: bool = False) -> dict:
            return {
                **_outline_report(dry_run=not apply, updated=0),
                "needs_outline": 0,
                "items": [],
            }

        def maintain_property_gaps(self, *, apply: bool = False) -> dict:
            return _property_report(dry_run=not apply, actions=[])

        def maintain_embedding_index(self, *, apply: bool = False) -> dict:
            return _embedding_index_report(dry_run=not apply, missing_or_stale=0)

    monkeypatch.setattr(librarian_bridge, "LibraryAgent", CleanLibraryAgent)
    monkeypatch.setattr(
        librarian_bridge,
        "load_config",
        lambda _path: type(
            "Config",
            (),
            {
                "vault_path": "/tmp/vault",
                "db_path": "data/captures.db",
                "llm_api_key": "sk-test",
                "llm_base_url": "https://example.com",
            },
        )(),
    )
    monkeypatch.setattr(librarian_bridge, "Outliner", lambda **_kwargs: object())

    envelope = librarian_bridge.run_librarian_audit()

    assert "当前干净" in envelope["chat_preview"]
    assert envelope["next_actions"] == []
    assert envelope["meta"]["database_sync"]["planned_action_count"] == 0


def test_librarian_property_gaps_do_not_create_apply_action(monkeypatch) -> None:
    from bridge import librarian_bridge

    class PropertyOnlyLibraryAgent(FakeLibraryAgent):
        def maintain_database_sync(self, *, apply: bool = False) -> dict:
            report = _db_sync_report(dry_run=not apply, actions=[])
            report["after"]["duplicate_capture_paths"] = 0
            report["after"]["open_draft_duplicate_groups"] = 0
            report["after"]["missing_capture_paths"] = 0
            return report

        def maintain_outlines(self, *, apply: bool = False) -> dict:
            return {
                **_outline_report(dry_run=not apply, updated=0),
                "needs_outline": 0,
                "items": [],
            }

    monkeypatch.setattr(librarian_bridge, "LibraryAgent", PropertyOnlyLibraryAgent)
    monkeypatch.setattr(
        librarian_bridge,
        "load_config",
        lambda _path: type(
            "Config",
            (),
            {
                "vault_path": "/tmp/vault",
                "db_path": "data/captures.db",
                "llm_api_key": "sk-test",
                "llm_base_url": "https://example.com",
            },
        )(),
    )
    monkeypatch.setattr(librarian_bridge, "Outliner", lambda **_kwargs: object())

    class JudgmentOnlyLibraryAgent(PropertyOnlyLibraryAgent):
        def maintain_property_gaps(self, *, apply: bool = False) -> dict:
            return {
                **_property_report(dry_run=not apply, actions=[]),
                "before": {
                    "capture_notes_scanned": 3,
                    "raw_notes_scanned": 2,
                    "capture_gap_count": 1,
                    "raw_gap_count": 0,
                    "capture_gaps": [
                        {
                            "file_path": "captures/a.md",
                            "missing_fields": ["reviewable", "importance"],
                        }
                    ],
                    "raw_gaps": [],
                },
                "after": {
                    "capture_notes_scanned": 3,
                    "raw_notes_scanned": 2,
                    "capture_gap_count": 1,
                    "raw_gap_count": 0,
                    "capture_gaps": [
                        {
                            "file_path": "captures/a.md",
                            "missing_fields": ["reviewable", "importance"],
                        }
                    ],
                    "raw_gaps": [],
                },
            }

        def maintain_embedding_index(self, *, apply: bool = False) -> dict:
            return _embedding_index_report(dry_run=not apply, missing_or_stale=0)

    monkeypatch.setattr(librarian_bridge, "LibraryAgent", JudgmentOnlyLibraryAgent)

    envelope = librarian_bridge.run_librarian_audit()

    assert "当前干净" not in envelope["chat_preview"]
    assert "不需要手动处理" in envelope["chat_preview"]
    assert envelope["next_actions"] == []

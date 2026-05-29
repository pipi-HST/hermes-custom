"""Tests for Library Agent vault maintenance."""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.markdown_writer import read_note
from agents.library_agent import LibraryAgent


class FakeOutliner:
    def __init__(self, outline: list[dict] | None = None, fail: bool = False) -> None:
        self.outline = outline or [
            {
                "level": 1,
                "title": "战略转向",
                "range": "段1-4",
                "summary": "说明产品节奏背后的战略变化。",
            },
            {
                "level": 2,
                "title": "Benchmark 信号",
                "range": "段5-8",
                "summary": "解释测试缺口为什么值得关注。",
            },
        ]
        self.fail = fail
        self.calls: list[tuple[str, int]] = []

    def generate(self, cleaned_text: str, word_count: int) -> list[dict]:
        self.calls.append((cleaned_text, word_count))
        if self.fail:
            raise RuntimeError("outline failed")
        return self.outline


def _write_capture(
    vault: Path,
    name: str = "long.md",
    *,
    body: str,
    outline: list[dict] | None = None,
) -> str:
    frontmatter = {
        "id": name.removesuffix(".md"),
        "title": "测试长文",
        "date": "2026-05-29",
        "created": "2026-05-29T00:00:00+00:00",
        "source": "https://example.com/post",
        "source_type": "web",
        "tags": ["ai"],
        "story": "",
        "word_count": len(body),
    }
    if outline is not None:
        frontmatter["outline"] = outline

    import yaml

    captures = vault / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    rel_path = f"captures/{name}"
    (vault / rel_path).write_text(
        "---\n"
        + yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + body,
        encoding="utf-8",
    )
    return rel_path


def test_dry_run_reports_long_capture_without_writing(tmp_path: Path) -> None:
    body = "# 测试长文\n\n" + "正文段落。\n\n" * 80
    rel_path = _write_capture(tmp_path, body=body)
    before = (tmp_path / rel_path).read_text(encoding="utf-8")
    outliner = FakeOutliner()

    report = LibraryAgent(tmp_path, outliner=outliner, min_outline_chars=100).maintain_outlines()

    assert report["dry_run"] is True
    assert report["needs_outline"] == 1
    assert report["updated"] == 0
    assert report["items"][0]["action"] == "needs_outline"
    assert (tmp_path / rel_path).read_text(encoding="utf-8") == before
    assert outliner.calls == []


def test_apply_adds_outline_frontmatter_and_directory_without_rewriting_body(
    tmp_path: Path,
) -> None:
    body = "# 测试长文\n\n第一段。\n\n第二段。\n\n" + "补充内容。\n\n" * 40
    rel_path = _write_capture(tmp_path, body=body)
    outliner = FakeOutliner()

    report = LibraryAgent(tmp_path, outliner=outliner, min_outline_chars=100).maintain_outlines(
        apply=True
    )

    assert report["dry_run"] is False
    assert report["updated"] == 1
    note = read_note(tmp_path, rel_path)
    assert note is not None
    frontmatter, updated_body = note
    assert frontmatter["outline"] == outliner.outline
    assert "## 目录纲要" in updated_body
    assert "- 战略转向：说明产品节奏背后的战略变化。" in updated_body
    assert "第一段。\n\n第二段。" in updated_body
    assert updated_body.count("## 目录纲要") == 1
    assert outliner.calls and outliner.calls[0][0] == body


def test_existing_outline_and_directory_are_not_duplicated(tmp_path: Path) -> None:
    outline = [{"level": 1, "title": "已有目录", "summary": "已经整理过。"}]
    body = "# 测试长文\n\n## 目录纲要\n\n- 已有目录：已经整理过。\n\n正文。\n\n" + "更多。\n\n" * 40
    rel_path = _write_capture(tmp_path, body=body, outline=outline)
    before = (tmp_path / rel_path).read_text(encoding="utf-8")
    outliner = FakeOutliner()

    report = LibraryAgent(tmp_path, outliner=outliner, min_outline_chars=100).maintain_outlines(
        apply=True
    )

    assert report["skipped_existing"] == 1
    assert (tmp_path / rel_path).read_text(encoding="utf-8") == before
    assert outliner.calls == []


def test_short_capture_is_skipped_without_calling_outliner(tmp_path: Path) -> None:
    _write_capture(tmp_path, name="short.md", body="# 短文\n\n内容。")
    outliner = FakeOutliner()

    report = LibraryAgent(tmp_path, outliner=outliner, min_outline_chars=100).maintain_outlines(
        apply=True
    )

    assert report["skipped_short"] == 1
    assert report["updated"] == 0
    assert outliner.calls == []


def test_outliner_failure_is_reported_without_writing(tmp_path: Path) -> None:
    body = "# 测试长文\n\n" + "正文段落。\n\n" * 80
    rel_path = _write_capture(tmp_path, body=body)
    before = (tmp_path / rel_path).read_text(encoding="utf-8")

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(fail=True),
        min_outline_chars=100,
    ).maintain_outlines(apply=True)

    assert report["errors"] == 1
    assert report["items"][0]["action"] == "outline_error"
    assert "outline failed" in report["items"][0]["error"]
    assert (tmp_path / rel_path).read_text(encoding="utf-8") == before


def test_library_maintenance_runner_uses_configured_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = "# 测试长文\n\n" + "正文段落。\n\n" * 80
    _write_capture(tmp_path, body=body)

    import scripts.library_maintenance as library_maintenance

    class Config:
        vault_path = str(tmp_path)
        db_path = str(tmp_path / "captures.db")
        llm_api_key = "sk-test"
        llm_model = "test-model"
        llm_base_url = "https://example.com"

    monkeypatch.setattr(library_maintenance, "load_config", lambda _path: Config())
    created_models: list[str] = []

    def fake_outliner(**kwargs):
        created_models.append(kwargs["model"])
        return FakeOutliner()

    sync_calls: list[dict] = []

    def fake_repair_data(db_path, vault_path, *, apply, rename_readable):
        sync_calls.append(
            {
                "db_path": db_path,
                "vault_path": vault_path,
                "apply": apply,
                "rename_readable": rename_readable,
            }
        )
        return {"dry_run": not apply, "planned_actions": []}

    monkeypatch.setattr(library_maintenance, "Outliner", fake_outliner)
    monkeypatch.setattr("agents.library_agent.repair_data", fake_repair_data)

    report = library_maintenance.run_library_maintenance(
        config_path="test.yaml",
        min_outline_chars=100,
    )

    assert report["dry_run"] is True
    assert report["database_sync"]["dry_run"] is True
    assert report["outlines"]["needs_outline"] == 1
    assert sync_calls == [
        {
            "db_path": Path(Config.db_path),
            "vault_path": Path(Config.vault_path),
            "apply": False,
            "rename_readable": True,
        }
    ]
    assert created_models == ["deepseek-v4-flash"]


def test_library_agent_database_sync_delegates_to_repair_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    def fake_repair_data(db_path, vault_path, *, apply, rename_readable):
        calls.append(
            {
                "db_path": db_path,
                "vault_path": vault_path,
                "apply": apply,
                "rename_readable": rename_readable,
            }
        )
        return {"dry_run": not apply, "planned_actions": [{"type": "delete_missing_capture"}]}

    monkeypatch.setattr("agents.library_agent.repair_data", fake_repair_data)

    report = LibraryAgent(
        tmp_path,
        outliner=FakeOutliner(),
        db_path=tmp_path / "captures.db",
    ).maintain_database_sync(apply=True)

    assert report["planned_actions"] == [{"type": "delete_missing_capture"}]
    assert calls == [
        {
            "db_path": tmp_path / "captures.db",
            "vault_path": tmp_path,
            "apply": True,
            "rename_readable": True,
        }
    ]

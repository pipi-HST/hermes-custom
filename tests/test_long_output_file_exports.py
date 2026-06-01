from pathlib import Path


def test_librarian_markdown_report_artifact_is_human_readable(tmp_path):
    from bridge.librarian_bridge import _librarian_markdown_report_artifact

    summary = {
        "database_sync": {"capture_count": 3, "planned_action_count": 1},
        "outlines": {"scanned": 2, "needs_outline": 1, "updated": 0, "errors": 0},
        "property_gaps": {"planned_action_count": 4, "applied_action_count": 0},
        "embedding_index": {"scanned": 3, "missing_or_stale": 2, "updated": 0, "errors": 0},
        "applied": False,
    }

    artifact = _librarian_markdown_report_artifact(
        summary=summary,
        json_report_path="/tmp/report.json",
        export_dir=tmp_path,
    )

    path = Path(artifact["path"])
    assert artifact["type"] == "librarian_markdown_report"
    assert path.is_absolute()
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# Library 审计与修复报告" in text
    assert "Capture 数量：3" in text
    assert "/tmp/report.json" in text


def test_garden_draft_export_artifact_writes_full_draft(tmp_path):
    from bridge.garden_bridge import _garden_draft_export_artifact

    artifact = _garden_draft_export_artifact(
        draft_id="draft1",
        title="AI Agent 方法卡",
        markdown="## 核心内容\n\n这是完整卡片正文。",
        export_dir=tmp_path,
    )

    path = Path(artifact["path"])
    assert artifact["type"] == "garden_draft_report"
    assert path.is_absolute()
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# AI Agent 方法卡" in text
    assert "这是完整卡片正文。" in text


def test_theme_scan_report_artifact_writes_candidates(tmp_path):
    from bridge.theme_bridge import _theme_scan_report_artifact

    artifact = _theme_scan_report_artifact(
        title="Theme 扫描报告",
        chat_preview="扫描完了，发现 1 组关联。",
        meta={"cluster_count": 1, "relationship_edges": 4},
        next_actions=[
            {
                "label": "展开第 1 组为知识卡片",
                "payload": {"question_id": "q1", "capture_ids": ["c1", "c2"]},
            }
        ],
        export_dir=tmp_path,
    )

    path = Path(artifact["path"])
    assert artifact["type"] == "theme_scan_report"
    assert path.is_absolute()
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# Theme 扫描报告" in text
    assert "扫描完了，发现 1 组关联。" in text
    assert "question_id: q1" in text


def test_canvas_file_artifact_uses_absolute_canvas_path(tmp_path):
    from bridge.canvas_bridge import _canvas_file_artifact

    canvas_path = tmp_path / "AI Agent-关系图.canvas"
    canvas_path.write_text("{}", encoding="utf-8")

    artifact = _canvas_file_artifact(canvas_path)

    assert artifact["type"] == "obsidian_canvas_file"
    assert Path(artifact["path"]).is_absolute()
    assert artifact["path"] == str(canvas_path.resolve())

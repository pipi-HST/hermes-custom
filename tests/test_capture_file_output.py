from pathlib import Path


def test_capture_draft_file_artifact_contains_full_draft(tmp_path):
    from bridge.capture_bridge import _capture_draft_file_artifact

    draft = {
        "id": "draft123",
        "title": "测试草稿",
        "draft_text": "第一段\n\n第二段",
        "raw_text": "原文不应优先",
    }

    artifact = _capture_draft_file_artifact(draft, export_dir=tmp_path)

    path = Path(artifact["path"])
    assert artifact["type"] == "capture_draft_fulltext"
    assert artifact["id"] == "draft123"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# 测试草稿" in text
    assert "第一段" in text
    assert "第二段" in text


def test_show_capture_draft_default_remains_chat_only(monkeypatch):
    from bridge import capture_bridge

    class DraftRepo:
        def get_latest_open(self):
            return {
                "id": "d1",
                "title": "草稿",
                "draft_text": "正文",
                "raw_text": "",
                "status": "draft_ready",
            }

    class Agent:
        draft_repo = DraftRepo()

    monkeypatch.setattr(capture_bridge, "_init", lambda: (None, Agent()))

    envelope = capture_bridge.show_capture_draft()

    assert envelope["durable_artifacts"][0]["type"] == "capture_draft"
    assert all(
        item["type"] != "capture_draft_fulltext"
        for item in envelope["durable_artifacts"]
    )


def test_show_capture_draft_exports_file_only_when_requested(monkeypatch, tmp_path):
    from bridge import capture_bridge

    class DraftRepo:
        def get_by_id(self, draft_id):
            return {
                "id": draft_id,
                "title": "草稿",
                "draft_text": "完整正文" * 500,
                "raw_text": "",
                "status": "draft_ready",
            }

    class Agent:
        draft_repo = DraftRepo()

    monkeypatch.setattr(capture_bridge, "_init", lambda: (None, Agent()))
    monkeypatch.setattr(
        capture_bridge,
        "_capture_draft_file_artifact",
        lambda draft: {
            "type": "capture_draft_fulltext",
            "id": draft["id"],
            "title": "Capture 草稿全文",
            "path": str(tmp_path / "draft.md"),
            "status": "created",
        },
    )

    envelope = capture_bridge.show_capture_draft("d1", as_file=True)

    assert [item["type"] for item in envelope["durable_artifacts"]] == [
        "capture_draft_fulltext"
    ]
    assert envelope["meta"]["draft_id"] == "d1"
    assert envelope["meta"]["file_path"] == str(tmp_path / "draft.md")
    assert "已生成草稿全文文件" in envelope["chat_preview"]

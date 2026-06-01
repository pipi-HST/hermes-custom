from pathlib import Path

from bridge.file_artifacts import safe_export_stem, write_markdown_export


def test_safe_export_stem_removes_unsafe_filename_chars():
    assert safe_export_stem('A/B: C? "D"') == "A-B-C-D"


def test_write_markdown_export_creates_parent_and_returns_absolute_path(tmp_path):
    path = write_markdown_export(
        base_dir=tmp_path / "exports",
        stem="测试报告",
        content="# 标题\n\n正文",
    )

    assert Path(path).is_absolute()
    assert Path(path).exists()
    assert Path(path).read_text(encoding="utf-8") == "# 标题\n\n正文\n"

"""Tests for theme_bridge wiki-link injection (Layer 1), MOC creation (Layer 2),
theme evolution (Layer 3), and graph report (Layer 4)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bridge.theme_bridge import (
    _create_moc,
    _evolve_themes,
    _hydrate_capture,
    _identity_index_map,
    _inject_wiki_links,
    _tag_captures_with_theme,
)


def _write_note(path: Path, subdir: str, name: str, title: str, body: str) -> str:
    """Write a minimal .md with frontmatter; return relative path."""
    d = path / subdir
    d.mkdir(parents=True, exist_ok=True)
    rel = f"{subdir}/{name}"
    (d / name).write_text(
        f"---\nid: {name}\ntitle: {title}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return rel


def test_inject_writes_bidirectional_links(tmp_path: Path) -> None:
    """new_capture 应链接到 related，related 应有反向链接。"""
    new_file = _write_note(tmp_path, "captures", "new.md", "新笔记", "新内容。")
    rel_file = _write_note(tmp_path, "captures", "rel.md", "旧笔记", "旧内容。")

    cluster = {
        "new_capture": {
            "id": "new-id", "title": "新笔记", "file_path": new_file,
            "summary": "新内容摘要",
        },
        "related_captures": [
            {"id": "rel-id", "title": "旧笔记", "file_path": rel_file,
             "summary": "旧内容摘要"},
        ],
    }

    n = _inject_wiki_links(tmp_path, cluster)
    assert n == 2  # one forward + one back

    new_text = (tmp_path / new_file).read_text(encoding="utf-8")
    assert "[[旧笔记]]" in new_text

    rel_text = (tmp_path / rel_file).read_text(encoding="utf-8")
    assert "[[新笔记]]" in rel_text


def test_inject_handles_missing_file_path(tmp_path: Path) -> None:
    """file_path 为空时应跳过，不抛异常。"""
    cluster = {
        "new_capture": {"id": "x", "title": "X", "file_path": "", "summary": ""},
        "related_captures": [
            {"id": "y", "title": "Y", "file_path": "", "summary": ""},
        ],
    }
    assert _inject_wiki_links(tmp_path, cluster) == 0


def test_inject_falls_back_to_untitled(tmp_path: Path) -> None:
    """title 为空时应使用 '未命名' 作为链接标题。"""
    new_file = _write_note(tmp_path, "captures", "new.md", "", "新内容。")
    rel_file = _write_note(tmp_path, "captures", "rel.md", "有标题", "旧内容。")

    cluster = {
        "new_capture": {
            "id": "n", "title": "", "file_path": new_file, "summary": "",
        },
        "related_captures": [
            {"id": "r", "title": "有标题", "file_path": rel_file, "summary": ""},
        ],
    }

    _inject_wiki_links(tmp_path, cluster)

    rel_text = (tmp_path / rel_file).read_text(encoding="utf-8")
    assert "[[未命名]]" in rel_text


def test_inject_deduplicates_on_rerun(tmp_path: Path) -> None:
    """重复调用不应产生重复链接。"""
    new_file = _write_note(tmp_path, "captures", "new.md", "新笔记", "新内容。")
    rel_file = _write_note(tmp_path, "captures", "rel.md", "旧笔记", "旧内容。")

    cluster = {
        "new_capture": {
            "id": "n", "title": "新笔记", "file_path": new_file, "summary": "",
        },
        "related_captures": [
            {"id": "r", "title": "旧笔记", "file_path": rel_file, "summary": ""},
        ],
    }

    n1 = _inject_wiki_links(tmp_path, cluster)
    n2 = _inject_wiki_links(tmp_path, cluster)
    assert n1 == 2
    assert n2 == 0  # all deduplicated

    new_text = (tmp_path / new_file).read_text(encoding="utf-8")
    assert new_text.count("[[旧笔记]]") == 1


# ── _create_moc (Layer 2) ──


def test_create_moc_skips_small_clusters(tmp_path: Path) -> None:
    """聚类少于 3 篇笔记时不应创建 MOC。"""
    agent = MagicMock()
    cluster = {
        "new_capture": {"id": "n", "title": "新", "tags": [], "summary": ""},
        "related_captures": [
            {"id": "r", "title": "旧", "tags": [], "summary": ""},
        ],
    }
    result = _create_moc(tmp_path, agent, cluster)
    assert result is None
    agent.generate_theme_name.assert_not_called()


def test_create_moc_creates_file_for_large_cluster(tmp_path: Path) -> None:
    """聚类 ≥3 篇笔记时应创建 MOC 文件。"""
    agent = MagicMock()
    agent.generate_theme_name.return_value = "AI与教育"
    agent.build_moc.return_value = "# AI与教育\n\n描述...\n\n## 相关笔记\n1. [[A]]"

    cluster = {
        "new_capture": {"id": "n", "title": "新笔记", "tags": ["ai"], "summary": ""},
        "related_captures": [
            {"id": "r1", "title": "旧1", "tags": ["education"], "summary": ""},
            {"id": "r2", "title": "旧2", "tags": [], "summary": ""},
        ],
    }
    result = _create_moc(tmp_path, agent, cluster)

    assert result is not None
    assert result.startswith("themes/")
    assert (tmp_path / result).exists()

    content = (tmp_path / result).read_text(encoding="utf-8")
    assert "type: theme" in content
    assert "theme_name: AI与教育" in content
    assert "captures: 3" in content
    # Tags should be merged from all captures
    assert "ai" in content
    assert "education" in content


def test_create_moc_deduplicates_tags(tmp_path: Path) -> None:
    """重复标签应只保留一份。"""
    agent = MagicMock()
    agent.generate_theme_name.return_value = "主题"
    agent.build_moc.return_value = "# 主题"

    cluster = {
        "new_capture": {"id": "n", "title": "新", "tags": ["ai", "ml"], "summary": ""},
        "related_captures": [
            {"id": "r1", "title": "旧1", "tags": ["ai", "deep-learning"], "summary": ""},
            {"id": "r2", "title": "旧2", "tags": ["ml"], "summary": ""},
        ],
    }
    result = _create_moc(tmp_path, agent, cluster)

    content = (tmp_path / result).read_text(encoding="utf-8")
    # ai appears once, ml appears once
    assert content.count("- ai") == 1
    assert content.count("- ml") == 1
    assert "deep-learning" in content


def test_tag_captures_with_theme_updates_linked_themes_frontmatter(tmp_path: Path) -> None:
    """主题页创建后，相关 capture frontmatter 应记录 linked_themes。"""
    cap_file = _write_note(tmp_path, "captures", "cap.md", "新笔记", "正文。")

    updated = _tag_captures_with_theme(
        tmp_path,
        [{"id": "cap-id", "file_path": cap_file, "title": "新笔记"}],
        "AI与教育",
    )

    assert updated == 1
    content = (tmp_path / cap_file).read_text(encoding="utf-8")
    assert "linked_themes:" in content
    assert "AI与教育" in content
    assert "正文。" in content


def test_tag_captures_with_theme_deduplicates_existing_theme(tmp_path: Path) -> None:
    """重复扫描同一主题不应写入重复 linked_themes。"""
    cap_file = _write_note(tmp_path, "captures", "cap.md", "新笔记", "正文。")

    _tag_captures_with_theme(tmp_path, [{"file_path": cap_file}], "AI与教育")
    updated = _tag_captures_with_theme(tmp_path, [{"file_path": cap_file}], "AI与教育")

    assert updated == 0
    content = (tmp_path / cap_file).read_text(encoding="utf-8")
    assert content.count("AI与教育") == 1


def test_hydrate_capture_prefers_identity_embedding_index(tmp_path: Path) -> None:
    """Theme scan 应使用 identity embedding，而不是依赖 captures.embedding。"""
    from db.embedding_repo import CaptureEmbeddingRepo
    from db.schema import init_db

    cap_file = _write_note(tmp_path, "captures", "cap.md", "新笔记", "正文。")
    conn = init_db(str(tmp_path / "test.db"))
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("cap-id", cap_file))
    CaptureEmbeddingRepo(conn).upsert(
        capture_id="cap-id",
        kind="identity",
        section_id="",
        section_title="",
        index_text="标题：新笔记\n摘要：AI Agent 主题关联线索",
        embedding=b"identity-embedding",
        source_hash="hash",
        file_path=cap_file,
    )
    row = dict(conn.execute("SELECT * FROM captures WHERE id = ?", ("cap-id",)).fetchone())

    identity_map = _identity_index_map(conn)
    hydrated = _hydrate_capture(tmp_path, row, identity_map)

    assert hydrated["embedding"] == b"identity-embedding"
    assert hydrated["index_text"].startswith("标题：新笔记")
    assert hydrated["summary"] == "AI Agent 主题关联线索"


def test_run_theme_scan_rescan_all_includes_previously_scanned_captures(
    tmp_path: Path,
) -> None:
    """首次补建主题页时，应能显式重扫已经标记过的 captures。"""
    from db.embedding_repo import CaptureEmbeddingRepo
    from db.schema import init_db

    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    scanned_file = _write_note(
        tmp_path, "captures", "scanned.md", "旧笔记", "旧内容。"
    )
    fresh_file = _write_note(
        tmp_path, "captures", "fresh.md", "新笔记", "新内容。"
    )
    conn.execute(
        "INSERT INTO captures (id, file_path, theme_scanned_at, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        ("scanned-id", scanned_file, "2026-05-30T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("fresh-id", fresh_file),
    )
    embed_repo = CaptureEmbeddingRepo(conn)
    for capture_id, file_path in (
        ("scanned-id", scanned_file),
        ("fresh-id", fresh_file),
    ):
        embed_repo.upsert(
            capture_id=capture_id,
            kind="identity",
            section_id="",
            section_title="",
            index_text=f"标题：{capture_id}",
            embedding=b"identity",
            source_hash=f"hash-{capture_id}",
            file_path=file_path,
        )

    seen_new_ids: list[str] = []

    class FakeThemeAgent:
        def __init__(self, **_kwargs):
            pass

        def scan(self, new_caps, all_caps):
            seen_new_ids.extend(cap["id"] for cap in new_caps)
            return []

    with patch("bridge.theme_bridge.load_config") as mock_cfg, patch(
        "bridge.theme_bridge.ThemeAgent", FakeThemeAgent
    ):
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        from bridge.theme_bridge import run_theme_scan

        result = run_theme_scan(rescan_all=True)

    assert result["status"] == "success"
    assert set(seen_new_ids) == {"scanned-id", "fresh-id"}


def test_run_theme_scan_reports_unindexed_new_captures(tmp_path: Path) -> None:
    """未扫描但缺 identity embedding 的新文章，不应被误报为没有新内容。"""
    from db.schema import init_db

    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    cap_file = _write_note(tmp_path, "captures", "fresh.md", "新笔记", "新内容。")
    conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("fresh-id", cap_file),
    )
    conn.commit()

    with patch("bridge.theme_bridge.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        from bridge.theme_bridge import run_theme_scan

        result = run_theme_scan()

    assert result["status"] == "success"
    assert result["meta"]["scanned_count"] == 0
    assert result["meta"]["unindexed_count"] == 1
    assert "索引未完成" in result["chat_preview"]


# ── _evolve_themes (Layer 3) ──


def test_evolve_themes_updates_matching_moc(tmp_path: Path) -> None:
    """匹配到已有 MOC 的新 capture 应触发 update_moc。"""
    from agents.markdown_writer import write_moc

    # Pre-create an existing MOC.
    moc_path = write_moc(
        tmp_path, "AI与教育", "# AI与教育\n\n## 相关笔记\n1. [[旧]] — 旧摘要\n",
        tags=["ai", "education"], capture_count=1,
    )

    agent = MagicMock()
    agent.match_to_themes.return_value = moc_path

    unclustered = [
        {"id": "n1", "title": "新笔记", "tags": ["ai", "education"],
         "summary": "新内容"},
    ]
    updated = _evolve_themes(tmp_path, agent, unclustered)

    assert updated == 1
    content = (tmp_path / moc_path).read_text(encoding="utf-8")
    assert "[[新笔记]]" in content
    assert "captures: 2" in content


def test_evolve_themes_skips_no_match(tmp_path: Path) -> None:
    """没有匹配的 MOC 时不应修改任何文件。"""
    agent = MagicMock()
    agent.match_to_themes.return_value = None

    unclustered = [
        {"id": "n1", "title": "无关", "tags": ["cooking"], "summary": ""},
    ]
    updated = _evolve_themes(tmp_path, agent, unclustered)
    assert updated == 0


def test_evolve_themes_handles_multiple_captures(tmp_path: Path) -> None:
    """多个 unclustered captures 应逐一处理。"""
    from agents.markdown_writer import write_moc

    moc_path = write_moc(
        tmp_path, "AI与教育", "# AI与教育\n\n## 相关笔记\n",
        tags=["ai"], capture_count=0,
    )

    agent = MagicMock()
    # First capture matches, second doesn't.
    agent.match_to_themes.side_effect = [moc_path, None]

    unclustered = [
        {"id": "n1", "title": "匹配1", "tags": ["ai"], "summary": ""},
        {"id": "n2", "title": "不匹配", "tags": ["x"], "summary": ""},
    ]
    updated = _evolve_themes(tmp_path, agent, unclustered)
    assert updated == 1


# ── run_graph_report (Layer 4) ──


def test_run_graph_report_returns_report_string(tmp_path: Path) -> None:
    """run_graph_report 应返回完整的报告字符串。"""
    from agents.markdown_writer import write_moc
    from db.schema import init_db
    from db.repo import CaptureRepo

    # Create a capture file.
    caps_dir = tmp_path / "captures"
    caps_dir.mkdir()
    (caps_dir / "a.md").write_text(
        "---\ntitle: A笔记\ntags: [ai]\n---\n\n正文 [[B笔记]]\n", encoding="utf-8",
    )
    (caps_dir / "b.md").write_text(
        "---\ntitle: B笔记\ntags: [ai]\n---\n\n无链接\n", encoding="utf-8",
    )

    # Create a MOC.
    write_moc(tmp_path, "AI主题", "# AI\n\n## 相关笔记\n- [[A笔记]]\n- [[B笔记]]",
              tags=["ai"], capture_count=2)

    # Create DB with captures.
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    repo = CaptureRepo(conn)
    # Insert captures into DB.
    conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("id1", "captures/a.md"),
    )
    conn.execute(
        "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
        ("id2", "captures/b.md"),
    )
    conn.commit()

    with patch("bridge.theme_bridge.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        from bridge.theme_bridge import run_graph_report
        report = run_graph_report()

    assert report["status"] == "success"
    assert report["agent"] == "theme"
    assert "总笔记: 2" in report["chat_preview"]
    assert "主题: 1" in report["chat_preview"]
    artifact = report["durable_artifacts"][0]
    assert artifact["type"] == "librarian_report"
    assert artifact["status"] == "reported"
    assert (Path(__file__).parent.parent / artifact["path"]).exists()

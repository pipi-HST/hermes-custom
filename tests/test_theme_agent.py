"""Tests for Theme Agent — scan / filter / question generation."""
from __future__ import annotations

import json
import struct
import pytest
from unittest.mock import patch, MagicMock

from agents.theme_agent import ThemeAgent, create_pending_question
from agents.embedder import DIM


# ── helpers ──

def _make_emb(seed=0.0):
    """Deterministic 1024-dim packed bytes (same format as Embedder.embed)."""
    vec = [seed + (i % 100) * 0.01 for i in range(DIM)]
    return struct.pack(f"{DIM}f", *vec)


def _make_capture(cid, title="", summary="", tags=None,
                  embedding=None, cached_text=""):
    return {
        "id": cid,
        "title": title,
        "summary": summary,
        "tags": tags or [],
        "embedding": embedding,
        "cached_text": cached_text,
    }


# ── scan: Level 1 — vector pre-filter ──

def test_scan_skips_capture_without_embedding():
    """new_cap 没有 embedding 时应直接跳过。"""
    agent = ThemeAgent(llm_api_key="test")
    new_caps = [_make_capture("n1", embedding=None)]
    all_caps = [_make_capture("a1", embedding=_make_emb(seed=1.0))]
    assert agent.scan(new_caps, all_caps) == []


def test_scan_skips_when_no_candidates():
    """all_captures 中没有其他有 embedding 的条目时应跳过。"""
    agent = ThemeAgent(llm_api_key="test")
    new_caps = [_make_capture("n1", embedding=_make_emb())]
    all_caps = []  # no candidates
    assert agent.scan(new_caps, all_caps) == []


# ── scan: Level 2 — LLM filter ──

@patch.object(ThemeAgent, "_call_llm")
def test_scan_returns_empty_when_no_strong(mock_llm):
    """LLM 全部判为 weak 时，不应产生聚类。"""
    mock_llm.return_value = json.dumps([
        {"capture_id": "a1", "related": True,
         "connection_type": "weak", "reason": "不太相关"},
    ])

    agent = ThemeAgent(llm_api_key="test")
    new_caps = [_make_capture("n1", summary="新内容",
                              embedding=_make_emb(seed=0.0))]
    all_caps = [_make_capture("a1", summary="旧内容",
                              embedding=_make_emb(seed=5.0))]

    assert agent.scan(new_caps, all_caps) == []


@patch.object(ThemeAgent, "_call_llm")
def test_scan_produces_cluster_on_strong_confirm(mock_llm):
    """LLM filter 返回 strong + confirm 确认 → 应产出聚类。"""
    call_count = [0]

    def side_effect(prompt, max_tokens=1000):
        call_count[0] += 1
        if call_count[0] == 1:
            # Level 2: LLM filter
            return json.dumps([
                {"capture_id": "a1", "related": True,
                 "connection_type": "strong", "reason": "同一主题"},
            ])
        else:
            # Level 3: LLM confirm
            return '["a1"]'

    mock_llm.side_effect = side_effect

    agent = ThemeAgent(llm_api_key="test")
    new_caps = [_make_capture("n1", title="AI Agent", summary="Agent 设计",
                              embedding=_make_emb(seed=0.0),
                              cached_text="Agent 架构详解")]
    all_caps = [_make_capture("a1", title="Agent 记忆", summary="记忆系统",
                              embedding=_make_emb(seed=0.5),
                              cached_text="记忆系统概述")]

    clusters = agent.scan(new_caps, all_caps)
    assert len(clusters) == 1
    assert clusters[0]["new_capture"]["id"] == "n1"
    assert clusters[0]["related_captures"][0]["id"] == "a1"


@patch.object(ThemeAgent, "_call_llm")
def test_scan_confirm_rejects_all(mock_llm):
    """Level 3 confirm 返回空数组时，不应产出聚类。"""
    call_count = [0]

    def side_effect(prompt, max_tokens=1000):
        call_count[0] += 1
        if call_count[0] == 1:
            return json.dumps([
                {"capture_id": "a1", "related": True,
                 "connection_type": "strong", "reason": "相关"},
            ])
        else:
            return "[]"  # confirm rejects all

    mock_llm.side_effect = side_effect

    agent = ThemeAgent(llm_api_key="test")
    new_caps = [_make_capture("n1", summary="X", embedding=_make_emb())]
    all_caps = [_make_capture("a1", summary="Y", embedding=_make_emb(seed=1.0))]

    assert agent.scan(new_caps, all_caps) == []


@patch.object(ThemeAgent, "_call_llm")
def test_scan_llm_filter_exception_skips(mock_llm):
    """LLM filter 抛异常时应跳过该 new_cap。"""
    mock_llm.side_effect = RuntimeError("API timeout")

    agent = ThemeAgent(llm_api_key="test")
    new_caps = [_make_capture("n1", summary="X", embedding=_make_emb())]
    all_caps = [_make_capture("a1", summary="Y", embedding=_make_emb(seed=1.0))]

    assert agent.scan(new_caps, all_caps) == []


# ── generate_question ──

@patch.object(ThemeAgent, "_call_llm")
def test_generate_question_returns_text(mock_llm):
    mock_llm.return_value = "你最近存了几篇关于 Agent 的文章，要我帮你整理成知识卡片吗？"

    agent = ThemeAgent(llm_api_key="test")
    cluster = {
        "new_capture": {"title": "Agent 设计", "summary": "Agent 架构概述"},
        "related_captures": [
            {"title": "记忆系统", "summary": "记忆三层结构"},
        ],
    }
    q = agent.generate_question(cluster)
    assert "知识卡片" in q
    mock_llm.assert_called_once()


def test_generate_question_fallback_title():
    """title 为空时应用 summary 截断作为 theme。"""
    agent = ThemeAgent(llm_api_key="test")
    with patch.object(agent, "_call_llm", return_value="提问文本") as mock:
        cluster = {
            "new_capture": {"title": "", "summary": "这是一个很长的摘要内容用来测试截断"},
            "related_captures": [],
        }
        agent.generate_question(cluster)
        prompt = mock.call_args[0][0]
        assert "这是一个很长的摘要内容" in prompt or "这是一个很长的摘要" in prompt


# ── _llm_confirm edge case ──

@patch.object(ThemeAgent, "_call_llm")
def test_llm_confirm_handles_malformed_json(mock_llm):
    """LLM 返回非法 JSON 时应返回空列表。"""
    mock_llm.return_value = "这不是JSON"
    agent = ThemeAgent(llm_api_key="test")
    result = agent._llm_confirm(
        {"cached_text": "新内容"},
        [{"id": "abc12345", "cached_text": "候选内容"}],
    )
    assert result == []


# ── create_pending_question ──

def test_create_pending_question(repo):
    """写入 pending_questions 表并返回完整数据。"""
    q = create_pending_question(
        repo, "theme", "测试提问？",
        context_ref="theme-abc",
        related_capture_ids=["cap1", "cap2"],
    )
    assert q["id"]
    assert "测试提问" in q["question_text"]

    row = repo.conn.execute(
        "SELECT * FROM pending_questions WHERE id = ?", (q["id"],)
    ).fetchone()
    assert row is not None
    assert row["status"] == "pending"
    assert row["agent_type"] == "theme"
    ids = json.loads(row["related_capture_ids"])
    assert "cap1" in ids and "cap2" in ids


@pytest.fixture
def repo(tmp_path):
    from db.schema import init_db
    from db.repo import CaptureRepo
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    return CaptureRepo(conn)


# ── generate_theme_name ──

@patch.object(ThemeAgent, "_call_llm")
def test_generate_theme_name_returns_llm_text(mock_llm):
    """LLM 应返回自然语言主题名。"""
    mock_llm.return_value = "AI与教育变革"
    agent = ThemeAgent(llm_api_key="test")
    captures = [
        {"title": "芯片又杀疯了", "summary": "AI将削弱名校优势", "tags": ["ai-education"]},
        {"title": "多读一点难读的书", "summary": "AI时代碎片化阅读", "tags": ["reading"]},
    ]
    name = agent.generate_theme_name(captures)
    assert name == "AI与教育变革"
    mock_llm.assert_called_once()


@patch.object(ThemeAgent, "_call_llm")
def test_generate_theme_name_strips_quotes(mock_llm):
    """LLM 返回带引号的主题名时应去除引号。"""
    mock_llm.return_value = '"AI与教育变革"'
    agent = ThemeAgent(llm_api_key="test")
    name = agent.generate_theme_name([{"title": "X", "summary": "Y", "tags": []}])
    assert name == "AI与教育变革"


# ── build_moc ──

@patch.object(ThemeAgent, "_call_llm")
def test_build_moc_produces_valid_markdown(mock_llm):
    """build_moc 应返回包含标题、描述、笔记列表、开放问题的 markdown body。"""
    mock_llm.return_value = json.dumps({
        "description": "AI正在重塑教育的形态。",
        "open_questions": ["AI真的能替代深度阅读吗？"],
    })

    agent = ThemeAgent(llm_api_key="test")
    captures = [
        {"title": "芯片又杀疯了", "summary": "AI将削弱名校优势", "tags": ["ai"]},
        {"title": "多读一点难读的书", "summary": "深度思考", "tags": ["reading"]},
    ]
    md = agent.build_moc("AI与教育变革", captures)

    # Body content (frontmatter is added by write_moc, not build_moc)
    assert "# AI与教育变革" in md
    assert "AI正在重塑教育的形态" in md
    assert "[[芯片又杀疯了]]" in md
    assert "[[多读一点难读的书]]" in md
    assert "AI真的能替代深度阅读吗" in md
    assert "## 相关笔记" in md
    assert "## 开放问题" in md


@patch.object(ThemeAgent, "_call_llm")
def test_build_moc_handles_llm_failure(mock_llm):
    """LLM 返回非法 JSON 时应使用空描述和空问题列表，但仍生成笔记列表。"""
    mock_llm.return_value = "这不是JSON"
    agent = ThemeAgent(llm_api_key="test")
    captures = [{"title": "X", "summary": "Y", "tags": []}]
    md = agent.build_moc("测试主题", captures)

    assert "# 测试主题" in md
    assert "[[X]]" in md
    assert "## 相关笔记" in md
    # No open_questions section when LLM fails
    assert "## 开放问题" not in md


# ── match_to_themes ──

def test_match_to_themes_matches_on_tag_overlap(tmp_path):
    """tag 重叠度足够高时应返回匹配的 MOC 路径。"""
    from agents.markdown_writer import write_moc
    from pathlib import Path

    # Create two MOCs with different tag sets.
    write_moc(tmp_path, "AI与教育", "# AI与教育", tags=["ai", "education"],
              capture_count=3)
    write_moc(tmp_path, "芯片产业", "# 芯片产业", tags=["chip", "hardware"],
              capture_count=2)

    agent = ThemeAgent(llm_api_key="test")
    new_cap = {
        "id": "n", "title": "新笔记",
        "tags": ["ai", "education", "reading"],
        "embedding": None,
    }
    result = agent.match_to_themes(tmp_path, new_cap)

    assert result is not None
    assert "ai-education" in result or "ai" in result


def test_match_to_themes_returns_none_on_no_overlap(tmp_path):
    """没有 tag 重叠且无 embedding 时应返回 None。"""
    from agents.markdown_writer import write_moc
    from pathlib import Path

    write_moc(tmp_path, "芯片产业", "# 芯片产业", tags=["chip"],
              capture_count=2)

    agent = ThemeAgent(llm_api_key="test")
    new_cap = {
        "id": "n", "title": "无关笔记",
        "tags": ["cooking"],
        "embedding": None,
    }
    result = agent.match_to_themes(tmp_path, new_cap)
    assert result is None


def test_match_to_themes_empty_themes_dir(tmp_path):
    """themes 目录不存在或为空时应返回 None。"""
    agent = ThemeAgent(llm_api_key="test")
    new_cap = {"id": "n", "title": "X", "tags": ["ai"], "embedding": None}
    result = agent.match_to_themes(tmp_path, new_cap)
    assert result is None


# ── graph_report ──

def test_graph_report_counts_captures_and_themes(tmp_path):
    """应正确统计总笔记数和主题数。"""
    from pathlib import Path
    from agents.markdown_writer import write_moc

    # Write 3 captures.
    caps_dir = tmp_path / "captures"
    caps_dir.mkdir()
    for i, title in enumerate(["A", "B", "C"]):
        (caps_dir / f"cap{i}.md").write_text(
            f"---\ntitle: {title}\ntags: []\n---\n\n正文\n", encoding="utf-8",
        )

    write_moc(tmp_path, "主题1", "# 主题1", tags=[], capture_count=1)

    agent = ThemeAgent(llm_api_key="test")
    report = agent.graph_report(tmp_path)

    assert "总笔记: 3" in report
    assert "主题: 1" in report


def test_graph_report_identifies_orphans(tmp_path):
    """没有任何 [[wiki-link]] 的 capture 应被列为孤岛。"""
    from pathlib import Path

    caps_dir = tmp_path / "captures"
    caps_dir.mkdir()
    (caps_dir / "linked.md").write_text(
        "---\ntitle: 已连接\n---\n\n正文 [[其他]]\n", encoding="utf-8",
    )
    (caps_dir / "orphan.md").write_text(
        "---\ntitle: 孤岛笔记\n---\n\n没有任何链接的正文。\n", encoding="utf-8",
    )

    agent = ThemeAgent(llm_api_key="test")
    report = agent.graph_report(tmp_path)

    assert "孤岛" in report
    assert "孤岛笔记" in report


def test_graph_report_identifies_bridges(tmp_path):
    """同时出现在 ≥2 个 MOC 的 [[wiki-link]] 中的 capture 应被列为桥接。"""
    from pathlib import Path
    from agents.markdown_writer import write_moc

    caps_dir = tmp_path / "captures"
    caps_dir.mkdir()
    (caps_dir / "bridge.md").write_text(
        "---\ntitle: 桥接笔记\ntags: []\n---\n\n正文\n", encoding="utf-8",
    )

    # Two MOCs both link to [[桥接笔记]].
    write_moc(tmp_path, "主题A", "# A\n\n## 相关笔记\n- [[桥接笔记]]", tags=[],
              capture_count=1)
    write_moc(tmp_path, "主题B", "# B\n\n## 相关笔记\n- [[桥接笔记]]", tags=[],
              capture_count=1)

    agent = ThemeAgent(llm_api_key="test")
    report = agent.graph_report(tmp_path)

    assert "桥接" in report
    assert "桥接笔记" in report


def test_graph_report_connected_percentage(tmp_path):
    """应计算已连接笔记的百分比。"""
    from pathlib import Path

    caps_dir = tmp_path / "captures"
    caps_dir.mkdir()
    (caps_dir / "a.md").write_text(
        "---\ntitle: A\n---\n\n[[B]]\n", encoding="utf-8",
    )
    (caps_dir / "b.md").write_text(
        "---\ntitle: B\n---\n\n无链接\n", encoding="utf-8",
    )

    agent = ThemeAgent(llm_api_key="test")
    report = agent.graph_report(tmp_path)

    # 1 out of 2 has links → 50%
    assert "50%" in report


def test_graph_report_empty_vault(tmp_path):
    """空 vault 应返回零统计，不抛异常。"""
    agent = ThemeAgent(llm_api_key="test")
    report = agent.graph_report(tmp_path)
    assert "总笔记: 0" in report

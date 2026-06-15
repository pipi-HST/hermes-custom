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
def test_scan_uses_strong_filter_when_confirm_output_is_unparseable(mock_llm):
    """全文确认输出损坏时，不应丢弃前两层已经确认的 strong 关联。"""
    call_count = [0]

    def side_effect(prompt, max_tokens=1000):
        call_count[0] += 1
        if call_count[0] == 1:
            return json.dumps([
                {"capture_id": "a1", "related": True,
                 "connection_type": "strong", "reason": "同一主题"},
            ])
        return ""

    mock_llm.side_effect = side_effect

    agent = ThemeAgent(llm_api_key="test")
    new_caps = [_make_capture("n1", summary="Agent", embedding=_make_emb())]
    all_caps = [_make_capture("a1", summary="Agent 工具", embedding=_make_emb(seed=1.0))]

    clusters = agent.scan(new_caps, all_caps)

    assert len(clusters) == 1
    assert clusters[0]["related_captures"][0]["id"] == "a1"


@patch.object(ThemeAgent, "_call_llm")
def test_scan_llm_filter_exception_skips(mock_llm):
    """LLM filter 抛异常时应跳过该 new_cap。"""
    mock_llm.side_effect = RuntimeError("API timeout")

    agent = ThemeAgent(llm_api_key="test")
    new_caps = [_make_capture("n1", summary="X", embedding=_make_emb())]
    all_caps = [_make_capture("a1", summary="Y", embedding=_make_emb(seed=1.0))]

    assert agent.scan(new_caps, all_caps) == []


def test_scan_filters_test_garbage_from_new_items_and_candidates():
    """明显测试垃圾不应参与 Theme 聚类，避免污染真实关联发现。"""
    agent = ThemeAgent(llm_api_key="test")
    diagnostics: dict = {}

    garbage_new = _make_capture(
        "junk-new",
        title="这是一段足够长的原始内容，用来生成待确认草稿",
        summary="长草稿摘要",
        embedding=_make_emb(seed=0.0),
    )
    real_new = _make_capture(
        "real-new",
        title="AI 落地",
        summary="AI 需要嵌入工作流才能在企业落地",
        embedding=_make_emb(seed=1.0),
    )
    garbage_candidate = _make_capture(
        "junk-old",
        title="这是一段足够长的原始内容，用来生成待确认草稿",
        summary="长草稿摘要",
        embedding=_make_emb(seed=1.0),
    )
    real_candidate = _make_capture(
        "real-old",
        title="工程底座",
        summary="组织工程底座决定 AI 工具的放大效果",
        embedding=_make_emb(seed=1.0),
    )

    with patch.object(agent, "_llm_filter", return_value=[]) as mock_filter:
        clusters = agent.scan(
            [garbage_new, real_new],
            [garbage_new, real_new, garbage_candidate, real_candidate],
            diagnostics=diagnostics,
        )

    assert clusters == []
    mock_filter.assert_called_once()
    candidates = mock_filter.call_args.args[1]
    assert [c["id"] for c in candidates] == ["real-old"]
    assert diagnostics["filtered_test_captures"] == 2


def test_scan_records_llm_filter_failures_in_diagnostics():
    """LLM 精筛失败不能被吞成“真的没有关联”。"""
    agent = ThemeAgent(llm_api_key="test")
    diagnostics: dict = {}

    new_caps = [_make_capture("n1", summary="AI 落地", embedding=_make_emb())]
    all_caps = [_make_capture("a1", summary="企业 AI 工程", embedding=_make_emb(seed=1.0))]

    with patch.object(agent, "_llm_filter", side_effect=ValueError("No JSON payload found")):
        clusters = agent.scan(new_caps, all_caps, diagnostics=diagnostics)

    assert clusters == []
    assert diagnostics["llm_filter_failures"] == 1
    assert diagnostics["processed_count"] == 1
    assert "No JSON payload found" in diagnostics["warnings"][0]


def test_scan_stops_when_time_budget_is_exceeded():
    """达到时间预算时应提前返回已有结果，避免微信长时间无响应。"""
    agent = ThemeAgent(llm_api_key="test")
    diagnostics: dict = {}

    new_caps = [_make_capture("n1", summary="AI 落地", embedding=_make_emb())]
    all_caps = [_make_capture("a1", summary="企业 AI 工程", embedding=_make_emb(seed=1.0))]

    clusters = agent.scan(
        new_caps,
        all_caps,
        diagnostics=diagnostics,
        time_budget_seconds=0.0,
    )

    assert clusters == []
    assert diagnostics["processed_count"] == 0
    assert diagnostics["time_budget_exceeded"] is True
    assert "时间预算" in diagnostics["warnings"][0]


@patch.object(ThemeAgent, "_call_llm")
def test_llm_filter_extracts_json_array_from_fenced_text(mock_llm):
    """LLM 输出带说明或代码块时，也应提取 JSON 数组。"""
    mock_llm.return_value = (
        "下面是判断结果：\n"
        "```json\n"
        "[{\"capture_id\":\"a1\",\"related\":true,"
        "\"connection_type\":\"strong\",\"reason\":\"同一主题\"}]\n"
        "```"
    )

    agent = ThemeAgent(llm_api_key="test")
    result = agent._llm_filter(
        {"summary": "新内容", "tags": []},
        [{"id": "a1", "summary": "旧内容", "tags": []}],
    )

    assert result[0]["capture_id"] == "a1"
    assert result[0]["connection_type"] == "strong"


@patch.object(ThemeAgent, "_call_llm")
def test_llm_filter_retries_once_when_json_is_missing(mock_llm):
    """第一次没拿到 JSON 时，应短重试一次，减少误判为无关联。"""
    mock_llm.side_effect = [
        "这些内容大体相关，但我不输出 JSON。",
        json.dumps([
            {"capture_id": "a1", "related": True,
             "connection_type": "strong", "reason": "同一主题"},
        ]),
    ]

    agent = ThemeAgent(llm_api_key="test")
    result = agent._llm_filter(
        {"summary": "AI 落地", "tags": ["ai-product"]},
        [{"id": "a1", "summary": "企业 AI 工程", "tags": ["ai-product"]}],
    )

    assert result[0]["capture_id"] == "a1"
    assert mock_llm.call_count == 2
    assert "只输出 JSON 数组" in mock_llm.call_args.args[0]


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


def test_generate_question_prompt_offers_directions_not_type_homework():
    """Theme 提问应让 Agent 给出沉淀方向，而不是让用户先判断卡片类型。"""
    agent = ThemeAgent(llm_api_key="test")
    cluster = {
        "new_capture": {"title": "Agent Harness", "summary": "可靠性来自外部约束"},
        "related_captures": [
            {"title": "Effective Agents", "summary": "先用简单工作流"},
        ],
    }

    with patch.object(agent, "_call_llm", return_value="提问文本") as mock:
        agent.generate_question(cluster)

    prompt = mock.call_args[0][0]
    assert "可沉淀方向" in prompt
    assert "不要让用户先判断" in prompt
    assert "先展开哪个" in prompt


# ── _llm_confirm edge case ──

@patch.object(ThemeAgent, "_call_llm")
def test_llm_confirm_handles_malformed_json(mock_llm):
    """LLM 返回非法 JSON 时应返回 None，让调用方保留前级判断。"""
    mock_llm.return_value = "这不是JSON"
    agent = ThemeAgent(llm_api_key="test")
    result = agent._llm_confirm(
        {"cached_text": "新内容"},
        [{"id": "abc12345", "cached_text": "候选内容"}],
    )
    assert result is None


@patch.object(ThemeAgent, "_call_llm")
def test_llm_confirm_extracts_json_array_from_prefaced_text(mock_llm):
    """全文确认返回带解释文本时，应提取其中的 ID 数组。"""
    mock_llm.return_value = "确认相关：\n[\"abc12345\"]"

    agent = ThemeAgent(llm_api_key="test")
    result = agent._llm_confirm(
        {"cached_text": "新内容"},
        [{"id": "abc12345", "cached_text": "候选内容"}],
    )

    assert result == ["abc12345"]


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


@patch.object(ThemeAgent, "_call_llm")
def test_generate_theme_name_falls_back_when_llm_returns_empty(mock_llm):
    """主题命名失败时不应创建 untitled MOC。"""
    mock_llm.return_value = ""
    captures = [
        {"title": "Building Effective AI Agents", "summary": "", "tags": ["ai-agent"]},
        {
            "title": "AI Harness深度解析",
            "summary": "",
            "tags": ["ai-agent", "engineering-management"],
        },
    ]

    agent = ThemeAgent(llm_api_key="test")
    name = agent.generate_theme_name(captures)

    assert name == "AI Agent工程化"


# ── build_theme_page / build_moc compatibility ──

@patch.object(ThemeAgent, "_call_llm")
def test_build_theme_page_outputs_new_theme_contract(mock_llm):
    """build_theme_page 应输出新版 Theme 主题地图结构。"""
    mock_llm.return_value = json.dumps({
        "theme_judgment": "AI正在重塑教育的形态。",
        "related_materials": [
            {"title": "芯片又杀疯了", "role": "说明 AI 如何改变教育资源分配。"},
            {"title": "多读一点难读的书", "role": "补充深度阅读仍然重要。"},
        ],
        "relationships": [
            "《芯片又杀疯了》讨论外部技术冲击，《多读一点难读的书》讨论个体如何保持深度。",
        ],
        "card_directions": ["观点卡：AI 时代仍需要深度阅读。"],
        "settled_cards": [],
    })

    agent = ThemeAgent(llm_api_key="test")
    captures = [
        {"title": "芯片又杀疯了", "summary": "AI将削弱名校优势", "tags": ["ai"]},
        {"title": "多读一点难读的书", "summary": "深度思考", "tags": ["reading"]},
    ]
    md = agent.build_theme_page("AI与教育变革", captures)

    assert "# AI与教育变革" in md
    assert "AI正在重塑教育的形态" in md
    assert "## 主题判断" in md
    assert "## 相关材料" in md
    assert "说明 AI 如何改变教育资源分配" in md
    assert "## 材料之间的关系" in md
    assert "外部技术冲击" in md
    assert "## 可沉淀的知识卡片方向" in md
    assert "观点卡：AI 时代仍需要深度阅读" in md
    assert "## 已沉淀的知识卡片" in md
    assert "## 开放问题" not in md
    assert "[[芯片又杀疯了]]" in md
    assert "[[多读一点难读的书]]" in md


@patch.object(ThemeAgent, "_call_llm")
def test_build_moc_uses_theme_page_contract_for_compatibility(mock_llm):
    mock_llm.return_value = json.dumps({
        "theme_judgment": "AI 正在重塑教育。",
        "related_materials": [],
        "relationships": [],
        "card_directions": [],
        "settled_cards": [],
    })

    agent = ThemeAgent(llm_api_key="test")
    md = agent.build_moc("AI与教育变革", [
        {"title": "芯片又杀疯了", "summary": "AI将削弱名校优势", "tags": ["ai"]},
    ])

    assert "## 主题判断" in md
    assert "## 相关材料" in md
    assert "## 已沉淀的知识卡片" in md
    assert "## 主题脉络" not in md


@patch.object(ThemeAgent, "_call_llm")
def test_build_theme_page_handles_llm_failure(mock_llm):
    """LLM 返回非法 JSON 时仍生成可读的主题地图。"""
    mock_llm.return_value = "这不是JSON"
    agent = ThemeAgent(llm_api_key="test")
    captures = [{"title": "X", "summary": "Y", "tags": []}]
    md = agent.build_theme_page("测试主题", captures)

    assert "# 测试主题" in md
    assert "[[X]]" in md
    assert "## 主题判断" in md
    assert "## 相关材料" in md
    assert "## 材料之间的关系" in md
    assert "## 可沉淀的知识卡片方向" in md
    assert "## 已沉淀的知识卡片" in md
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

from __future__ import annotations

import json

from agents.canvas_agent import (
    CanvasBuildInput,
    CanvasItem,
    CanvasTheme,
    build_obsidian_canvas,
    safe_canvas_filename,
)


def test_build_obsidian_canvas_uses_compact_text_nodes_with_wiki_links():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(
                title="AI Agent",
                file_path="themes/ai-agent.md",
                summary="把模型能力变成可验证、可复用、可协作的工程系统。",
            ),
            captures=[
                CanvasItem(
                    id="cap1",
                    title="Building Effective AI Agents",
                    file_path="captures/effective.md",
                    summary="从简单增强调用开始，逐步升级到工作流和自主代理。",
                ),
            ],
            garden_cards=[
                CanvasItem(
                    id="card1",
                    title="AI Agent 工程化方法卡",
                    file_path="garden/agent-method.md",
                    kind="method",
                    summary="先优化单次调用，再考虑复杂工作流。",
                ),
            ],
        )
    )

    payload = json.loads(result.json_text)
    assert all(node["type"] == "text" for node in payload["nodes"])
    text = "\n".join(node["text"] for node in payload["nodes"])
    assert "[[themes/ai-agent|AI Agent]]" in text
    assert "[[captures/effective|Building Effective AI Agents]]" in text
    assert "[[garden/agent-method|AI Agent 工程化方法卡]]" in text
    assert "把模型能力变成可验证" in text
    assert "先优化单次调用" in text
    assert "cap1" not in result.json_text


def test_build_obsidian_canvas_positions_captures_left_and_cards_right():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[CanvasItem(id="cap1", title="来源", file_path="captures/source.md")],
            garden_cards=[
                CanvasItem(
                    id="card1",
                    title="方法卡",
                    file_path="garden/method.md",
                    kind="method",
                )
            ],
        )
    )

    payload = json.loads(result.json_text)
    by_id = {node["id"]: node for node in payload["nodes"]}
    assert by_id["capture-1"]["x"] < by_id["theme"]["x"]
    assert by_id["garden-1"]["x"] > by_id["theme"]["x"]


def test_build_obsidian_canvas_caps_large_graph_and_reports_trimmed_items():
    captures = [
        CanvasItem(id=f"cap{i}", title=f"来源{i}", file_path=f"captures/{i}.md")
        for i in range(7)
    ]
    cards = [
        CanvasItem(
            id=f"card{i}",
            title=f"卡片{i}",
            file_path=f"garden/{i}.md",
            kind="viewpoint",
        )
        for i in range(7)
    ]

    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="大主题",
            theme=CanvasTheme(title="大主题", file_path="themes/big.md"),
            captures=captures,
            garden_cards=cards,
        )
    )

    payload = json.loads(result.json_text)
    text_nodes = [node for node in payload["nodes"] if node["type"] == "text"]
    assert len([node for node in text_nodes if node["id"].startswith("capture-") and node["id"] != "capture-more"]) == 5
    assert len([node for node in text_nodes if node["id"].startswith("garden-") and node["id"] != "garden-more"]) == 5
    assert any("还有 2 篇来源未展开" in node["text"] for node in text_nodes)
    assert any("还有 2 张卡未展开" in node["text"] for node in text_nodes)
    assert "已省略 2 篇来源笔记" in result.warnings
    assert "已省略 2 张 Garden 卡片" in result.warnings


def test_build_obsidian_canvas_uses_fewer_unlabeled_edges():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[
                CanvasItem(id="cap1", title="来源一", file_path="captures/one.md"),
                CanvasItem(id="cap2", title="来源二", file_path="captures/two.md"),
            ],
            garden_cards=[
                CanvasItem(id="card1", title="方法卡", file_path="garden/method.md", kind="method"),
                CanvasItem(id="card2", title="风险卡", file_path="garden/risk.md", kind="caution"),
            ],
        )
    )

    payload = json.loads(result.json_text)
    assert len(payload["edges"]) == 4
    assert all("label" not in edge for edge in payload["edges"])


def test_safe_canvas_filename_removes_invalid_path_characters():
    assert safe_canvas_filename('AI/Agent:关系图?') == "AIAgent关系图-关系图.canvas"

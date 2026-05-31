from __future__ import annotations

import json

from agents.canvas_agent import (
    CanvasBuildInput,
    CanvasItem,
    CanvasTheme,
    build_obsidian_canvas,
    safe_canvas_filename,
)


def test_build_obsidian_canvas_uses_garden_cards_as_main_board_nodes():
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
                    source_ids=["cap1"],
                ),
            ],
        )
    )

    payload = json.loads(result.json_text)
    by_id = {node["id"]: node for node in payload["nodes"]}
    assert by_id["theme"]["type"] == "text"
    assert by_id["theme"]["y"] < by_id["garden-1"]["y"]
    assert by_id["garden-1"]["type"] == "text"
    assert by_id["source-1"]["type"] == "text"
    assert "[[captures/effective|Building Effective AI Agents]]" in by_id["source-1"]["text"]
    assert by_id["source-1"]["y"] > by_id["garden-1"]["y"]
    text = "\n".join(node.get("text", "") for node in payload["nodes"])
    assert "[[themes/ai-agent|AI Agent]]" in text
    assert "[[garden/agent-method|AI Agent 工程化方法卡]]" in text
    assert "把模型能力变成可验证" in text
    assert "先优化单次调用" in text
    assert "来源：S1" in text
    assert "cap1" not in result.json_text


def test_build_obsidian_canvas_positions_theme_garden_and_sources_as_board():
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
    assert by_id["theme"]["y"] < by_id["garden-1"]["y"]
    assert by_id["source-1"]["y"] > by_id["garden-1"]["y"]
    assert by_id["source-1"]["height"] <= 72


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
    source_nodes = [
        node for node in text_nodes
        if node["id"].startswith("source-") and node["id"] != "source-more"
    ]
    assert len(source_nodes) == 5
    assert all(node["height"] <= 72 for node in source_nodes)
    assert len([node for node in text_nodes if node["id"].startswith("garden-") and node["id"] != "garden-more"]) == 6
    assert any("还有 2 篇来源收起" in node["text"] for node in text_nodes)
    assert any("还有 1 张 Garden Card 收起" in node["text"] for node in text_nodes)
    assert "已省略 2 篇来源笔记" in result.warnings
    assert "已省略 1 张 Garden 卡片" in result.warnings


def test_build_obsidian_canvas_avoids_visual_spiderweb_edges():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[
                CanvasItem(id="cap1", title="来源一", file_path="captures/one.md"),
                CanvasItem(id="cap2", title="来源二", file_path="captures/two.md"),
            ],
            garden_cards=[
                CanvasItem(id="card1", title="方法卡", file_path="garden/method.md", kind="method", source_ids=["cap1"]),
                CanvasItem(id="card2", title="风险卡", file_path="garden/risk.md", kind="caution", source_ids=["cap2"]),
            ],
        )
    )

    payload = json.loads(result.json_text)
    text = "\n".join(node.get("text", "") for node in payload["nodes"])
    assert payload["edges"] == []
    assert "来源：S1" in text
    assert "来源：S2" in text


def test_build_obsidian_canvas_limits_visible_source_edges_per_card():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[
                CanvasItem(id="cap1", title="来源一", file_path="captures/one.md"),
                CanvasItem(id="cap2", title="来源二", file_path="captures/two.md"),
                CanvasItem(id="cap3", title="来源三", file_path="captures/three.md"),
            ],
            garden_cards=[
                CanvasItem(
                    id="card1",
                    title="方法卡",
                    file_path="garden/method.md",
                    kind="method",
                    source_ids=["cap1", "cap2", "cap3"],
                ),
            ],
        )
    )

    payload = json.loads(result.json_text)
    by_id = {node["id"]: node for node in payload["nodes"]}
    text = by_id["garden-1"]["text"]
    assert payload["edges"] == []
    assert "来源：S1, S2" in text
    assert "S3" not in text


def test_build_obsidian_canvas_places_overflow_nodes_outside_main_cards():
    captures = [
        CanvasItem(id=f"cap{i}", title=f"来源{i}", file_path=f"captures/{i}.md")
        for i in range(7)
    ]
    cards = [
        CanvasItem(id=f"card{i}", title=f"卡片{i}", file_path=f"garden/{i}.md")
        for i in range(8)
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
    by_id = {node["id"]: node for node in payload["nodes"]}
    assert by_id["garden-more"]["x"] >= 800
    assert by_id["source-more"]["y"] >= 610


def test_build_obsidian_canvas_uses_light_custom_colors():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[CanvasItem(id="cap1", title="来源", file_path="captures/source.md")],
            garden_cards=[CanvasItem(id="card1", title="方法卡", file_path="garden/method.md", kind="method")],
        )
    )

    payload = json.loads(result.json_text)
    colors = {node["color"] for node in payload["nodes"] if "color" in node}
    assert "#EAF4E3" in colors
    assert "#FFF4D8" in colors
    assert all(color.startswith("#") for color in colors)


def test_safe_canvas_filename_removes_invalid_path_characters():
    assert safe_canvas_filename('AI/Agent:关系图?') == "AIAgent关系图-关系图.canvas"

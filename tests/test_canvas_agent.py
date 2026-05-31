from __future__ import annotations

import json

from agents.canvas_agent import (
    CanvasBuildInput,
    CanvasItem,
    CanvasTheme,
    build_obsidian_canvas,
    safe_canvas_filename,
)


def test_build_obsidian_canvas_uses_file_nodes_and_readable_labels():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[
                CanvasItem(
                    id="cap1",
                    title="Building Effective AI Agents",
                    file_path="captures/effective.md",
                ),
            ],
            garden_cards=[
                CanvasItem(
                    id="card1",
                    title="AI Agent 工程化方法卡",
                    file_path="garden/agent-method.md",
                    kind="method",
                ),
            ],
        )
    )

    payload = json.loads(result.json_text)
    assert payload["nodes"][0]["type"] == "file"
    assert payload["nodes"][0]["file"] == "themes/ai-agent.md"
    assert any(node["file"] == "captures/effective.md" for node in payload["nodes"])
    assert any(node["file"] == "garden/agent-method.md" for node in payload["nodes"])
    assert "cap1" not in result.json_text
    assert "AI Agent 工程化方法卡" in result.json_text


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
    by_file = {node["file"]: node for node in payload["nodes"] if node["type"] == "file"}
    assert by_file["captures/source.md"]["x"] < by_file["themes/ai-agent.md"]["x"]
    assert by_file["garden/method.md"]["x"] > by_file["themes/ai-agent.md"]["x"]


def test_build_obsidian_canvas_caps_large_graph_and_reports_trimmed_items():
    captures = [
        CanvasItem(id=f"cap{i}", title=f"来源{i}", file_path=f"captures/{i}.md")
        for i in range(12)
    ]
    cards = [
        CanvasItem(
            id=f"card{i}",
            title=f"卡片{i}",
            file_path=f"garden/{i}.md",
            kind="viewpoint",
        )
        for i in range(10)
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
    file_nodes = [node for node in payload["nodes"] if node["type"] == "file"]
    assert len([node for node in file_nodes if node["file"].startswith("captures/")]) == 10
    assert len([node for node in file_nodes if node["file"].startswith("garden/")]) == 8
    assert "已省略 2 篇来源笔记" in result.warnings
    assert "已省略 2 张 Garden 卡片" in result.warnings


def test_safe_canvas_filename_removes_invalid_path_characters():
    assert safe_canvas_filename('AI/Agent:关系图?') == "AIAgent关系图-关系图.canvas"

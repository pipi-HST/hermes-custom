from __future__ import annotations

import json

from agents.canvas_agent import (
    CanvasBuildInput,
    CanvasItem,
    CanvasStructure,
    CanvasStructureGroup,
    CanvasTheme,
    build_obsidian_canvas,
    safe_canvas_filename,
)


def test_build_obsidian_canvas_renders_structured_garden_cluster():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[
                CanvasItem(id="cap1", title="Building Effective AI Agents", file_path="captures/effective.md"),
            ],
            garden_cards=[
                CanvasItem(
                    id="card1",
                    title="工程化方法卡",
                    file_path="garden/method.md",
                    kind="method",
                    summary="先优化单次调用，再考虑工作流。",
                    source_ids=["cap1"],
                ),
                CanvasItem(
                    id="card2",
                    title="复杂度风险卡",
                    file_path="garden/risk.md",
                    kind="caution",
                    summary="代理系统会带来成本和延迟。",
                    source_ids=["cap1"],
                ),
            ],
            structure=CanvasStructure(
                core_claim="AI Agent 的价值来自工程底座，而不是单纯模型升级。",
                groups=[
                    CanvasStructureGroup(
                        role="method",
                        title="落地方法",
                        card_ids=["card1"],
                        reason="这张卡给出工程化路径。",
                    ),
                    CanvasStructureGroup(
                        role="risk",
                        title="边界风险",
                        card_ids=["card2"],
                        reason="这张卡提醒复杂度成本。",
                    ),
                ],
                evidence_ids=["cap1"],
            ),
        )
    )

    payload = json.loads(result.json_text)
    by_id = {node["id"]: node for node in payload["nodes"]}
    text = "\n".join(node.get("text", "") for node in payload["nodes"])
    assert "AI Agent 的价值来自工程底座" in by_id["core"]["text"]
    assert by_id["group-1"]["type"] == "group"
    assert by_id["group-1"]["label"] == "落地方法"
    assert by_id["group-2"]["label"] == "边界风险"
    assert "工程化方法卡" in text
    assert "[[captures/effective|S1 Building Effective AI Agents]]" in text
    assert result.edge_count == 4


def test_structured_canvas_renders_evidence_as_compact_pills_not_source_cards():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[
                CanvasItem(id="cap1", title="Building Effective AI Agents", file_path="captures/effective.md"),
                CanvasItem(id="cap2", title="Opus 4.8 多 Agent 协作", file_path="captures/opus.md"),
            ],
            garden_cards=[
                CanvasItem(
                    id="card1",
                    title="工程化方法卡",
                    file_path="garden/method.md",
                    kind="method",
                    summary="先优化单次调用，再考虑工作流。",
                    source_ids=["cap1", "cap2"],
                ),
            ],
            structure=CanvasStructure(
                core_claim="AI Agent 的价值来自工程底座。",
                groups=[
                    CanvasStructureGroup(role="method", title="落地方法", card_ids=["card1"]),
                ],
                evidence_ids=["cap1", "cap2"],
            ),
        )
    )

    payload = json.loads(result.json_text)
    by_id = {node["id"]: node for node in payload["nodes"]}
    assert by_id["evidence-panel"]["type"] == "group"
    assert by_id["evidence-panel"]["label"] == "Evidence"
    assert by_id["evidence-panel"]["y"] < 520
    assert by_id["source-1"]["text"].startswith("[[captures/effective|S1 Building Effective")
    assert by_id["source-1"]["height"] >= 52
    assert by_id["source-1"]["width"] >= 320
    assert "·" not in by_id["source-1"]["text"]
    assert by_id["source-2"]["text"].startswith("[[captures/opus|S2 Opus 4.8")


def test_structured_canvas_cards_are_large_readable_and_use_source_badges():
    long_summary = (
        "这是一段很长的卡片解释，原来会被塞进 Canvas 节点里导致需要滚动阅读。"
        "现在结构图只应该保留足够判断关系的短句，其余内容通过标题链接打开 Garden Card 查看。"
        "这里继续补充很多文字来模拟真实卡片。"
    )
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[
                CanvasItem(id="cap1", title="Building Effective AI Agents", file_path="captures/effective.md"),
                CanvasItem(id="cap2", title="Opus 4.8 多 Agent 协作", file_path="captures/opus.md"),
            ],
            garden_cards=[
                CanvasItem(
                    id="card1",
                    title="工程化方法卡",
                    file_path="garden/method.md",
                    kind="method",
                    summary=long_summary,
                    source_ids=["cap1", "cap2"],
                ),
            ],
            structure=CanvasStructure(
                core_claim="AI Agent 的价值来自工程底座。",
                groups=[CanvasStructureGroup(role="method", title="落地方法", card_ids=["card1"])],
                evidence_ids=["cap1", "cap2"],
            ),
        )
    )

    payload = json.loads(result.json_text)
    by_id = {node["id"]: node for node in payload["nodes"]}
    card = next(node for node in payload["nodes"] if node["id"] == "group-1-card-1")
    assert card["height"] >= 250
    assert card["width"] >= 420
    assert card["text"].startswith("**方法卡**")
    assert "## 工程化方法卡" in card["text"]
    assert "这里继续补充很多文字来模拟真实卡片" in card["text"]
    assert "…" not in card["text"]
    assert "[[garden/method|工程化方法卡]]" not in card["text"]
    assert "打开：" not in card["text"]
    assert "来源：" not in card["text"]
    assert by_id["group-1-card-1-ref-1"]["text"] == "S1"
    assert by_id["group-1-card-1-ref-2"]["text"] == "S2"
    assert by_id["group-1-card-1-ref-1"]["width"] >= 64
    assert by_id["group-1-card-1-ref-1"]["y"] >= card["y"] + card["height"] + 8
    assert by_id["group-1"]["height"] > card["height"]


def test_structured_canvas_shows_all_evidence_without_collapsing_sources():
    captures = [
        CanvasItem(id=f"cap{i}", title=f"来源{i}", file_path=f"captures/{i}.md")
        for i in range(1, 9)
    ]
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=captures,
            garden_cards=[
                CanvasItem(
                    id="card1",
                    title="工程化方法卡",
                    file_path="garden/method.md",
                    kind="method",
                    summary="先优化单次调用，再考虑工作流。",
                    source_ids=[capture.id for capture in captures],
                ),
            ],
            structure=CanvasStructure(
                core_claim="AI Agent 的价值来自工程底座。",
                groups=[CanvasStructureGroup(role="method", title="落地方法", card_ids=["card1"])],
                evidence_ids=[capture.id for capture in captures],
            ),
        )
    )

    payload = json.loads(result.json_text)
    source_nodes = [
        node for node in payload["nodes"]
        if node["id"].startswith("source-") and node["id"] != "source-more"
    ]
    assert len(source_nodes) == 8
    assert "source-more" not in {node["id"] for node in payload["nodes"]}
    assert not any("来源收起" in node.get("text", "") for node in payload["nodes"])
    assert result.warnings == []


def test_build_obsidian_canvas_structure_ignores_missing_card_ids():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            garden_cards=[
                CanvasItem(id="card1", title="方法卡", file_path="garden/method.md", kind="method"),
            ],
            structure=CanvasStructure(
                core_claim="核心关系",
                groups=[
                    CanvasStructureGroup(
                        role="method",
                        title="方法",
                        card_ids=["missing", "card1"],
                    ),
                ],
            ),
        )
    )

    payload = json.loads(result.json_text)
    text = "\n".join(node.get("text", "") for node in payload["nodes"])
    assert "方法卡" in text
    assert "missing" not in result.json_text


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
    assert by_id["source-1"]["height"] <= 48
    assert by_id["source-1"]["text"].startswith("S1 · [[")
    assert by_id["source-1"]["y"] > by_id["garden-1"]["y"]
    text = "\n".join(node.get("text", "") for node in payload["nodes"])
    assert "[[themes/ai-agent|AI Agent]]" in text
    assert "[[garden/agent-method|AI Agent 工程化方法卡]]" in text
    assert "### AI Agent 工程化方法卡" in text
    assert "### [[garden/agent-method|AI Agent 工程化方法卡]]" not in text
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
    assert by_id["source-1"]["height"] <= 48


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
    assert all(node["height"] <= 48 for node in source_nodes)
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


def test_build_obsidian_canvas_groups_cards_by_kind_lanes():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            garden_cards=[
                CanvasItem(id="method1", title="方法卡", file_path="garden/method.md", kind="method"),
                CanvasItem(id="view1", title="观点卡", file_path="garden/view.md", kind="viewpoint"),
                CanvasItem(id="risk1", title="风险卡", file_path="garden/risk.md", kind="caution"),
            ],
        )
    )

    payload = json.loads(result.json_text)
    by_title = {
        node["text"].split("### ", 1)[1].splitlines()[0]: node
        for node in payload["nodes"]
        if node["id"].startswith("garden-") and node["id"] != "garden-more"
    }
    assert by_title["观点卡"]["y"] < by_title["方法卡"]["y"]
    assert by_title["方法卡"]["x"] < by_title["风险卡"]["x"]


def test_build_obsidian_canvas_does_not_reuse_garden_card_positions():
    cards = [
        CanvasItem(id=f"card{i}", title=f"观点{i}", file_path=f"garden/view-{i}.md", kind="viewpoint")
        for i in range(4)
    ] + [
        CanvasItem(id="method1", title="方法", file_path="garden/method.md", kind="method"),
        CanvasItem(id="risk1", title="风险", file_path="garden/risk.md", kind="caution"),
    ]

    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            garden_cards=cards,
        )
    )

    payload = json.loads(result.json_text)
    positions = [
        (node["x"], node["y"])
        for node in payload["nodes"]
        if node["id"].startswith("garden-") and node["id"] != "garden-more"
    ]
    assert len(positions) == len(set(positions))


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
    assert "#DDEED4" in colors
    assert "#FFE8AA" in colors
    assert all(color.startswith("#") for color in colors)


def test_structured_canvas_uses_warmer_deeper_but_still_soft_colors():
    result = build_obsidian_canvas(
        CanvasBuildInput(
            title="AI Agent工程化",
            theme=CanvasTheme(title="AI Agent", file_path="themes/ai-agent.md"),
            captures=[CanvasItem(id="cap1", title="来源", file_path="captures/source.md")],
            garden_cards=[
                CanvasItem(id="card1", title="观点卡", file_path="garden/view.md", kind="viewpoint", source_ids=["cap1"]),
                CanvasItem(id="card2", title="风险卡", file_path="garden/risk.md", kind="caution", source_ids=["cap1"]),
            ],
            structure=CanvasStructure(
                core_claim="核心判断",
                groups=[
                    CanvasStructureGroup(role="support", title="支撑", card_ids=["card1"]),
                    CanvasStructureGroup(role="risk", title="边界", card_ids=["card2"]),
                ],
                evidence_ids=["cap1"],
            ),
        )
    )

    payload = json.loads(result.json_text)
    colors = {node["color"] for node in payload["nodes"] if "color" in node}
    assert {"#D7EBFF", "#FBD2C7", "#E7F0DE", "#ECE5D8", "#E7DED2"} <= colors
    assert "#FAFAF8" not in colors


def test_safe_canvas_filename_removes_invalid_path_characters():
    assert safe_canvas_filename('AI/Agent:关系图?') == "AIAgent关系图-关系图.canvas"

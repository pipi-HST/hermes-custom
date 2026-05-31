"""Deterministic Obsidian Canvas builder for Garden knowledge maps."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

MAX_CAPTURES = 5
MAX_GARDEN_CARDS = 5


@dataclass(frozen=True)
class CanvasTheme:
    title: str
    file_path: str
    summary: str = ""


@dataclass(frozen=True)
class CanvasItem:
    id: str
    title: str
    file_path: str
    kind: str = ""
    summary: str = ""


@dataclass(frozen=True)
class CanvasBuildInput:
    title: str
    theme: CanvasTheme
    captures: list[CanvasItem] = field(default_factory=list)
    garden_cards: list[CanvasItem] = field(default_factory=list)


@dataclass(frozen=True)
class CanvasBuildResult:
    json_text: str
    warnings: list[str]
    node_count: int
    edge_count: int


def safe_canvas_filename(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "", title or "").strip() or "未命名"
    return f"{cleaned}-关系图.canvas"


def build_obsidian_canvas(data: CanvasBuildInput) -> CanvasBuildResult:
    captures = data.captures[:MAX_CAPTURES]
    cards = data.garden_cards[:MAX_GARDEN_CARDS]
    warnings: list[str] = []
    if len(data.captures) > len(captures):
        warnings.append(f"已省略 {len(data.captures) - len(captures)} 篇来源笔记")
    if len(data.garden_cards) > len(cards):
        warnings.append(f"已省略 {len(data.garden_cards) - len(cards)} 张 Garden 卡片")

    nodes = [
        _text_node(
            "theme",
            _theme_text(data.theme),
            0,
            -20,
            380,
            250,
            "4",
        )
    ]
    edges = []
    for index, capture in enumerate(captures):
        node_id = f"capture-{index + 1}"
        nodes.append(
            _text_node(
                node_id,
                _capture_text(capture),
                -520,
                -260 + index * 120,
                340,
                128,
                "1",
            )
        )
        edges.append(_edge(f"edge-capture-{index + 1}", node_id, "theme"))
    if len(data.captures) > len(captures):
        nodes.append(
            _text_node(
                "capture-more",
                f"还有 {len(data.captures) - len(captures)} 篇来源未展开",
                -492,
                -260 + len(captures) * 120,
                280,
                70,
                "6",
            )
        )
    for index, card in enumerate(cards):
        node_id = f"garden-{index + 1}"
        nodes.append(
            _text_node(
                node_id,
                _garden_text(card),
                520,
                -230 + index * 138,
                340,
                136,
                _color_for_kind(card.kind),
            )
        )
        edges.append(_edge(f"edge-garden-{index + 1}", "theme", node_id))
    if len(data.garden_cards) > len(cards):
        nodes.append(
            _text_node(
                "garden-more",
                f"还有 {len(data.garden_cards) - len(cards)} 张卡未展开",
                550,
                -230 + len(cards) * 138,
                280,
                70,
                "6",
            )
        )

    payload = {
        "nodes": nodes,
        "edges": edges,
        "metadata": {"generated_by": "hermes", "title": data.title},
    }
    return CanvasBuildResult(
        json_text=json.dumps(payload, ensure_ascii=False, indent=2),
        warnings=warnings,
        node_count=len(nodes),
        edge_count=len(edges),
    )


def _text_node(
    node_id: str,
    text: str,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str,
) -> dict:
    return {
        "id": node_id,
        "type": "text",
        "text": text,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "color": color,
    }


def _edge(edge_id: str, from_node: str, to_node: str) -> dict:
    return {
        "id": edge_id,
        "fromNode": from_node,
        "fromSide": "right",
        "toNode": to_node,
        "toSide": "left",
    }


def _theme_text(theme: CanvasTheme) -> str:
    lines = [
        f"# {_wiki_link(theme.file_path, theme.title)}",
        "",
        theme.summary.strip() or "这个主题用于汇集相关来源笔记和 Garden 知识卡片。",
    ]
    return "\n".join(lines).strip()


def _capture_text(item: CanvasItem) -> str:
    lines = [
        "**Capture**",
        f"### {_wiki_link(item.file_path, item.title)}",
    ]
    if item.summary.strip():
        lines.extend(["", _clip(item.summary)])
    return "\n".join(lines).strip()


def _garden_text(item: CanvasItem) -> str:
    kind_label = _kind_label(item.kind)
    lines = [
        f"**{kind_label}**",
        f"### {_wiki_link(item.file_path, item.title)}",
    ]
    if item.summary.strip():
        lines.extend(["", _clip(item.summary)])
    return "\n".join(lines).strip()


def _wiki_link(file_path: str, title: str) -> str:
    target = re.sub(r"\.md$", "", file_path or "")
    return f"[[{target}|{title or target or '未命名'}]]"


def _clip(text: str, limit: int = 90) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _kind_label(kind: str) -> str:
    return {
        "method": "Method Card",
        "caution": "Caution Card",
        "question": "Question Card",
        "fact": "Fact Card",
        "viewpoint": "Viewpoint Card",
    }.get(kind or "", "Garden Card")


def _color_for_kind(kind: str) -> str:
    return {
        "method": "2",
        "caution": "1",
        "question": "6",
        "fact": "3",
        "viewpoint": "5",
    }.get(kind or "", "5")

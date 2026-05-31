"""Deterministic Obsidian Canvas builder for Garden knowledge maps."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

MAX_CAPTURES = 10
MAX_GARDEN_CARDS = 8


@dataclass(frozen=True)
class CanvasTheme:
    title: str
    file_path: str


@dataclass(frozen=True)
class CanvasItem:
    id: str
    title: str
    file_path: str
    kind: str = ""


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

    nodes = [_file_node("theme", data.theme.title, data.theme.file_path, 0, 0, "4")]
    edges = []
    for index, capture in enumerate(captures):
        node_id = f"capture-{index + 1}"
        nodes.append(
            _file_node(
                node_id,
                capture.title,
                capture.file_path,
                -520,
                -260 + index * 120,
                "1",
            )
        )
        edges.append(_edge(f"edge-capture-{index + 1}", node_id, "theme", "evidence"))
    for index, card in enumerate(cards):
        node_id = f"garden-{index + 1}"
        nodes.append(
            _file_node(
                node_id,
                card.title,
                card.file_path,
                520,
                -220 + index * 130,
                _color_for_kind(card.kind),
            )
        )
        edges.append(
            _edge(f"edge-garden-{index + 1}", "theme", node_id, _label_for_kind(card.kind))
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


def _file_node(
    node_id: str,
    label: str,
    file_path: str,
    x: int,
    y: int,
    color: str,
) -> dict:
    return {
        "id": node_id,
        "type": "file",
        "file": file_path,
        "label": label or file_path,
        "x": x,
        "y": y,
        "width": 360,
        "height": 220,
        "color": color,
    }


def _edge(edge_id: str, from_node: str, to_node: str, label: str) -> dict:
    return {
        "id": edge_id,
        "fromNode": from_node,
        "fromSide": "right",
        "toNode": to_node,
        "toSide": "left",
        "label": label,
    }


def _label_for_kind(kind: str) -> str:
    return {
        "method": "method",
        "caution": "risk",
        "question": "question",
        "fact": "distills",
        "viewpoint": "distills",
    }.get(kind or "", "related")


def _color_for_kind(kind: str) -> str:
    return {
        "method": "2",
        "caution": "1",
        "question": "6",
        "fact": "3",
        "viewpoint": "5",
    }.get(kind or "", "5")

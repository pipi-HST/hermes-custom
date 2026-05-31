"""Deterministic Obsidian Canvas builder for Garden knowledge maps."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

MAX_CAPTURES = 5
MAX_GARDEN_CARDS = 6
MAX_SOURCE_REFS_PER_CARD = 2

THEME_COLOR = "#EAF4E3"
METHOD_COLOR = "#FFF4D8"
VIEWPOINT_COLOR = "#E8F3FF"
CAUTION_COLOR = "#FDECE7"
QUESTION_COLOR = "#EFE8FF"
FACT_COLOR = "#F7EBC8"
SOURCE_COLOR = "#F2F2F2"
MORE_COLOR = "#EFEAF7"


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
    source_ids: list[str] = field(default_factory=list)


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
    cards = _ordered_cards(data.garden_cards[:MAX_GARDEN_CARDS])
    warnings: list[str] = []
    if len(data.captures) > len(captures):
        warnings.append(f"已省略 {len(data.captures) - len(captures)} 篇来源笔记")
    if len(data.garden_cards) > len(cards):
        warnings.append(f"已省略 {len(data.garden_cards) - len(cards)} 张 Garden 卡片")

    nodes = [
        _text_node("theme", _theme_text(data.theme), -420, -360, 840, 120, THEME_COLOR)
    ]
    edges = []
    lane_counts: dict[str, int] = {}
    for index, card in enumerate(cards):
        node_id = f"garden-{index + 1}"
        lane = _position_lane(card.kind)
        lane_index = lane_counts.get(lane, 0)
        lane_counts[lane] = lane_index + 1
        nodes.append(
            _text_node(
                node_id,
                _garden_text(card, _source_refs(card, captures)),
                *_garden_position(lane, lane_index),
                310,
                198,
                _color_for_kind(card.kind),
            )
        )
    for index, capture in enumerate(captures):
        node_id = f"source-{index + 1}"
        nodes.append(
            _text_node(
                node_id,
                _source_text(capture, index + 1),
                -560 + index * 280,
                430,
                240,
                42,
                SOURCE_COLOR,
            )
        )
    if len(data.captures) > len(captures):
        nodes.append(
            _text_node(
                "source-more",
                f"还有 {len(data.captures) - len(captures)} 篇来源收起",
                500,
                620,
                280,
                62,
                MORE_COLOR,
            )
        )
    if len(data.garden_cards) > len(cards):
        nodes.append(
            _text_node(
                "garden-more",
                f"还有 {len(data.garden_cards) - len(cards)} 张 Garden Card 收起",
                820,
                110,
                280,
                62,
                MORE_COLOR,
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


def _theme_text(theme: CanvasTheme) -> str:
    lines = [
        f"# {_wiki_link(theme.file_path, theme.title)}",
        "",
        theme.summary.strip() or "这个主题用于汇集相关来源笔记和 Garden 知识卡片。",
    ]
    return "\n".join(lines).strip()


def _garden_text(item: CanvasItem, refs: list[str] | None = None) -> str:
    kind_label = _kind_label(item.kind)
    lines = [
        f"**{kind_label}**",
        f"### {item.title or '未命名'}",
    ]
    if item.summary.strip():
        lines.extend(["", _clip(item.summary)])
    if refs:
        lines.extend(["", f"来源：{', '.join(refs)}"])
    lines.extend(["", f"打开：{_wiki_link(item.file_path, item.title)}"])
    return "\n".join(lines).strip()


def _source_text(item: CanvasItem, index: int) -> str:
    return f"S{index} · {_wiki_link(item.file_path, _clip(item.title, 28))}"


def _source_refs(card: CanvasItem, captures: list[CanvasItem]) -> list[str]:
    index_by_id = {capture.id: index + 1 for index, capture in enumerate(captures)}
    return [
        f"S{index_by_id[source_id]}"
        for source_id in card.source_ids[:MAX_SOURCE_REFS_PER_CARD]
        if source_id in index_by_id
    ]


def _position_lane(kind: str) -> str:
    if kind == "viewpoint":
        return "viewpoint"
    if kind in {"method", "fact"}:
        return "practice"
    return "edge"


def _garden_position(lane: str, index: int) -> tuple[int, int]:
    positions = {
        "viewpoint": [(-520, -150), (-155, -150), (210, -150), (-520, 100)],
        "practice": [(-155, 100), (210, 100), (575, 100)],
        "edge": [(575, -150), (575, 100), (940, 100)],
    }
    lane_positions = positions.get(lane, positions["edge"])
    return lane_positions[min(index, len(lane_positions) - 1)]


def _ordered_cards(cards: list[CanvasItem]) -> list[CanvasItem]:
    order = {
        "viewpoint": 0,
        "method": 1,
        "fact": 2,
        "caution": 3,
        "question": 4,
    }
    return [
        card
        for _index, card in sorted(
            enumerate(cards),
            key=lambda item: (order.get(item[1].kind or "", 9), item[0]),
        )
    ]


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
        "method": METHOD_COLOR,
        "caution": CAUTION_COLOR,
        "question": QUESTION_COLOR,
        "fact": FACT_COLOR,
        "viewpoint": VIEWPOINT_COLOR,
    }.get(kind or "", VIEWPOINT_COLOR)

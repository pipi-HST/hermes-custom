"""Deterministic Obsidian Canvas builder for Garden knowledge maps."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

MAX_CAPTURES = 5
MAX_GARDEN_CARDS = 6
MAX_SOURCE_REFS_PER_CARD = 2

THEME_COLOR = "#DDEED4"
METHOD_COLOR = "#FFE8AA"
VIEWPOINT_COLOR = "#D7EBFF"
CAUTION_COLOR = "#FBD2C7"
QUESTION_COLOR = "#E5DAFF"
FACT_COLOR = "#F3DA98"
SOURCE_COLOR = "#E8E4DC"
MORE_COLOR = "#E4D9EE"
CORE_COLOR = "#E7F0DE"
GROUP_COLOR = "#ECE5D8"
EVIDENCE_PANEL_COLOR = "#E7DED2"
SOURCE_BADGE_COLOR = "#F0E9DE"


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
class CanvasStructureGroup:
    role: str
    title: str
    card_ids: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True)
class CanvasStructure:
    core_claim: str
    groups: list[CanvasStructureGroup] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CanvasBuildInput:
    title: str
    theme: CanvasTheme
    captures: list[CanvasItem] = field(default_factory=list)
    garden_cards: list[CanvasItem] = field(default_factory=list)
    structure: CanvasStructure | None = None


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
    if data.structure:
        return _build_structured_canvas(data)

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


def _build_structured_canvas(data: CanvasBuildInput) -> CanvasBuildResult:
    structure = data.structure
    assert structure is not None

    card_lookup = {card.id: card for card in data.garden_cards if card.id}
    captures = _ordered_captures_for_structure(data.captures, structure.evidence_ids)
    warnings: list[str] = []

    nodes = [
        _text_node("theme", _theme_text(data.theme), -360, -520, 720, 105, THEME_COLOR),
        _text_node("core", _core_text(structure.core_claim), -360, -360, 720, 116, CORE_COLOR),
    ]
    edges = []
    rendered_card_ids: set[str] = set()
    max_card_bottom = 0
    for group_index, group in enumerate(structure.groups[:5]):
        cards = [
            card_lookup[card_id]
            for card_id in group.card_ids
            if card_id in card_lookup and card_id not in rendered_card_ids
        ][:3]
        if not cards:
            continue
        rendered_card_ids.update(card.id for card in cards)
        group_id = f"group-{group_index + 1}"
        x = -760 + group_index * 500
        group_height = 480 + max(0, len(cards) - 1) * 350
        nodes.append(
            _group_node(
                group_id,
                group.title or _role_label(group.role),
                x - 18,
                -178,
                466,
                group_height,
                GROUP_COLOR,
            )
        )
        edges.append(_edge(f"edge-core-{group_index + 1}", "core", group_id))
        for card_index, card in enumerate(cards):
            node_id = f"{group_id}-card-{card_index + 1}"
            card_y = -28 + card_index * 350
            card_width = 430
            card_height = 260
            nodes.append(
                _text_node(
                    node_id,
                    _readable_garden_text(card),
                    x,
                    card_y,
                    card_width,
                    card_height,
                    _color_for_kind(card.kind),
                )
            )
            badge_y = card_y + card_height + 12
            for ref_index, ref in enumerate(_source_refs(card, captures)[:3]):
                nodes.append(
                    _text_node(
                        f"{node_id}-ref-{ref_index + 1}",
                        ref,
                        x + 24 + ref_index * 60,
                        badge_y,
                        64,
                        38,
                        SOURCE_BADGE_COLOR,
                    )
                )
            max_card_bottom = max(max_card_bottom, badge_y + 38)
            edges.append(_edge(f"edge-{group_id}-{card_index + 1}", group_id, node_id))

    evidence_y = max(380, max_card_bottom + 120)
    if captures:
        evidence_rows = (len(captures) + 3) // 4
        evidence_height = 88 + max(1, evidence_rows) * 68
        nodes.append(_group_node("evidence-panel", "Evidence", -760, evidence_y, 1860, evidence_height, EVIDENCE_PANEL_COLOR))
    for index, capture in enumerate(captures):
        row = index // 4
        col = index % 4
        nodes.append(
            _text_node(
                f"source-{index + 1}",
                _source_pill_text(capture, index + 1),
                -650 + col * 365,
                evidence_y + 50 + row * 68,
                330,
                52,
                SOURCE_COLOR,
            )
        )

    payload = {
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "generated_by": "hermes",
            "title": data.title,
            "canvas_kind": "garden_structure",
        },
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


def _group_node(
    node_id: str,
    label: str,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str,
) -> dict:
    return {
        "id": node_id,
        "type": "group",
        "label": label,
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
        "fromSide": "bottom",
        "toNode": to_node,
        "toSide": "top",
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


def _readable_garden_text(item: CanvasItem) -> str:
    lines = [
        f"**{_kind_label_zh(item.kind)}**",
        f"## {item.title or '未命名'}",
    ]
    if item.summary.strip():
        lines.extend(["", _clip(item.summary, 220)])
    return "\n".join(lines).strip()


def _source_text(item: CanvasItem, index: int) -> str:
    return f"S{index} · {_wiki_link(item.file_path, _clip(item.title, 28))}"


def _source_pill_text(item: CanvasItem, index: int) -> str:
    return _wiki_link(item.file_path, f"S{index} {_clip(item.title, 30)}")


def _source_refs(card: CanvasItem, captures: list[CanvasItem]) -> list[str]:
    index_by_id = {capture.id: index + 1 for index, capture in enumerate(captures)}
    return [
        f"S{index_by_id[source_id]}"
        for source_id in card.source_ids[:MAX_SOURCE_REFS_PER_CARD]
        if source_id in index_by_id
    ]


def _ordered_captures_for_structure(
    captures: list[CanvasItem],
    evidence_ids: list[str],
) -> list[CanvasItem]:
    by_id = {capture.id: capture for capture in captures}
    ordered = [
        by_id[capture_id]
        for capture_id in evidence_ids
        if capture_id in by_id
    ]
    seen = {capture.id for capture in ordered}
    ordered.extend(capture for capture in captures if capture.id not in seen)
    return ordered


def _core_text(core_claim: str) -> str:
    return "\n".join([
        "# 结构判断",
        "",
        _clip(core_claim, 180),
    ]).strip()


def _group_text(group: CanvasStructureGroup) -> str:
    lines = [
        f"**{_role_label(group.role)}**",
        f"### {group.title or '相关卡片'}",
    ]
    if group.reason.strip():
        lines.extend(["", _clip(group.reason, 80)])
    return "\n".join(lines).strip()


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


def _kind_label_zh(kind: str) -> str:
    return {
        "method": "方法卡",
        "caution": "风险边界",
        "question": "开放问题",
        "fact": "事实卡",
        "viewpoint": "观点卡",
    }.get(kind or "", "知识卡")


def _role_label(role: str) -> str:
    return {
        "support": "支撑",
        "method": "方法",
        "risk": "风险/边界",
        "question": "开放问题",
        "fact": "事实依据",
    }.get(role or "", "结构分组")


def _color_for_kind(kind: str) -> str:
    return {
        "method": METHOD_COLOR,
        "caution": CAUTION_COLOR,
        "question": QUESTION_COLOR,
        "fact": FACT_COLOR,
        "viewpoint": VIEWPOINT_COLOR,
    }.get(kind or "", VIEWPOINT_COLOR)

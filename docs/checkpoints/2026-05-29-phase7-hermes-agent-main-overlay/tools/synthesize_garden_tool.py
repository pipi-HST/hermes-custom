"""Hermes tool: synthesize_garden — 合成知识花园卡片."""
from __future__ import annotations

import sys

HERMES_CUSTOM = "/Users/pipi/hermes-custom"
if HERMES_CUSTOM not in sys.path:
    sys.path.insert(0, HERMES_CUSTOM)

from bridge.garden_bridge import synthesize_garden as do_synthesize
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

SYNTHESIZE_GARDEN_SCHEMA = {
    "name": "synthesize_garden",
    "description": (
        "将多条知识库内容合成为一张结构化知识卡片（Garden Card）。"
        "可指定 capture_ids 直接合成，或传 question_id 从主题聚类问题触发。"
        "合成后卡片存入知识花园，可在每日回顾中出现。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capture_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要合成的 capture ID 列表（与 question_id 二选一）",
            },
            "question_id": {
                "type": "string",
                "description": "主题聚类问题 ID（来自 theme_scan 生成的待回复问题）",
            },
            "title": {
                "type": "string",
                "description": "可选，卡片标题；不填则由 LLM 自动生成",
            },
        },
        "required": [],
    },
}


def _handler(params: dict, **kw) -> str:
    try:
        result = do_synthesize(
            capture_ids=params.get("capture_ids"),
            question_id=params.get("question_id", ""),
            title=params.get("title", ""),
        )
        return envelope_json(result)
    except Exception as exc:
        return envelope_error_json(agent="garden", intent="synthesize_garden", exc=exc)


registry.register(
    name=SYNTHESIZE_GARDEN_SCHEMA["name"],
    schema=SYNTHESIZE_GARDEN_SCHEMA,
    handler=_handler,
    toolset="personal-assistant",
)

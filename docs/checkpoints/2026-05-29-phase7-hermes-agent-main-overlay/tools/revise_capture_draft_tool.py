"""Hermes tool: revise_capture_draft — 修改待确认捕获草稿."""
from __future__ import annotations

import sys

HERMES_CUSTOM = "/Users/pipi/hermes-custom"
if HERMES_CUSTOM not in sys.path:
    sys.path.insert(0, HERMES_CUSTOM)

from bridge.capture_bridge import revise_capture_draft
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

REVISE_CAPTURE_DRAFT_SCHEMA = {
    "name": "revise_capture_draft",
    "description": (
        "按用户反馈修改最近或指定的捕获草稿，例如“再短一点”“聚焦某部分”"
        "“不要泛泛总结”。只更新草稿，不正式入库。微信只展示短预览，"
        "但完整草稿会保存在草稿区；用户确认后入库的是完整草稿。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "draft_id": {
                "type": "string",
                "description": "要修改的草稿编号（可选，不填则修改最近未入库草稿）",
            },
            "instruction": {
                "type": "string",
                "description": "用户要求的修改或提取方向",
            },
        },
        "required": ["instruction"],
    },
}


def _handler(args: dict, **kw) -> str:
    try:
        result = revise_capture_draft(
            args.get("draft_id", ""),
            args.get("instruction", ""),
        )
        return envelope_json(result)
    except Exception as exc:
        return envelope_error_json(agent="capture", intent="revise_capture_draft", exc=exc)


registry.register(
    name="revise_capture_draft",
    toolset="personal-assistant",
    schema=REVISE_CAPTURE_DRAFT_SCHEMA,
    handler=_handler,
    emoji="✏️",
)

"""Hermes tool: refine_capture — 兼容旧名，修改待确认草稿."""
from __future__ import annotations

import sys
sys.path.insert(0, "/Users/pipi/hermes-custom")

from bridge.capture_bridge import revise_capture_draft as do_revise
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

REFINE_SCHEMA = {
    "name": "refine_capture",
    "description": (
        "兼容旧工具名：修改最近或指定的待确认草稿。用户说「摘要不对」「重新总结」"
        "「压缩一下」「聚焦某部分」时调用。不会正式入库；用户确认后调用 commit_capture_draft。"
        "微信只展示短预览；完整草稿会保存在草稿区，用户确认后入库的是完整草稿。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capture_id": {
                "type": "string",
                "description": "兼容字段：要修改的 draft 编号（可选，不填则修改最近草稿）",
            },
            "instruction": {
                "type": "string",
                "description": "用户要求的操作（如「重新提取核心观点」「压缩到500字」）。不填则默认重新摘要。",
            },
        },
        "required": [],
    },
}


def _handler(args: dict, **kw) -> str:
    try:
        inst = args.get("instruction", "重新生成准确的摘要和标签")
        result = do_revise(args.get("capture_id", ""), inst)
        return envelope_json(result)
    except Exception as exc:
        return envelope_error_json(agent="capture", intent="revise_capture_draft", exc=exc)


registry.register(
    name="refine_capture",
    toolset="personal-assistant",
    schema=REFINE_SCHEMA,
    handler=_handler,
    emoji="✏️",
)

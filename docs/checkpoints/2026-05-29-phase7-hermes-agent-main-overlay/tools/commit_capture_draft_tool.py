"""Hermes tool: commit_capture_draft — 确认草稿入库."""
from __future__ import annotations

import sys

HERMES_CUSTOM = "/Users/pipi/hermes-custom"
if HERMES_CUSTOM not in sys.path:
    sys.path.insert(0, HERMES_CUSTOM)

from bridge.capture_bridge import commit_capture_draft
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

COMMIT_CAPTURE_DRAFT_SCHEMA = {
    "name": "commit_capture_draft",
    "description": (
        "用户明确满意并要求“存库”“归档”“就这样”“可以”时调用。"
        "把最近或指定草稿写入 Obsidian raw/ 和 captures/，并加入正式知识库。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "draft_id": {
                "type": "string",
                "description": "要入库的草稿编号（可选，不填则提交最近未入库草稿）",
            },
        },
        "required": [],
    },
}


def _handler(args: dict, **kw) -> str:
    try:
        result = commit_capture_draft(args.get("draft_id", ""))
        return envelope_json(result)
    except Exception as exc:
        return envelope_error_json(agent="capture", intent="commit_capture_draft", exc=exc)


registry.register(
    name="commit_capture_draft",
    toolset="personal-assistant",
    schema=COMMIT_CAPTURE_DRAFT_SCHEMA,
    handler=_handler,
    emoji="📚",
)

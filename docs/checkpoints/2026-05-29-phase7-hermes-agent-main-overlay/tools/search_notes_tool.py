"""Hermes tool: search_my_notes — 知识库搜索."""
from __future__ import annotations

import sys

HERMES_CUSTOM = "/Users/pipi/hermes-custom"
if HERMES_CUSTOM not in sys.path:
    sys.path.insert(0, HERMES_CUSTOM)

from bridge.rag_bridge import search_knowledge_base as do_search
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

SEARCH_SCHEMA = {
    "name": "search_my_notes",
    "description": "搜索个人知识库中之前保存的文章、笔记。返回结果包含 [编号] 引用格式，请在回答中保留这些引用标注。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "用户的自然语言问题或搜索关键词",
            },
        },
        "required": ["query"],
    },
}


def _handler(args: dict, **kw) -> str:
    try:
        result = do_search(args.get("query", ""))
        result["meta"] = {
            **result.get("meta", {}),
            "handler_instruction": "最终回答使用 chat_preview，并保留所有引用标注和 source_map。",
        }
        return envelope_json(result)
    except Exception as exc:
        return envelope_error_json(agent="rag", intent="search_knowledge_base", exc=exc)


registry.register(
    name="search_my_notes",
    toolset="personal-assistant",
    schema=SEARCH_SCHEMA,
    handler=_handler,
    emoji="🔍",
)

"""Hermes tool: graph_report — 知识图谱健康报告."""
from __future__ import annotations

import sys
sys.path.insert(0, "/Users/pipi/hermes-custom")

from bridge.theme_bridge import run_graph_report
from bridge.result_envelope import envelope_json
from tools.hermes_custom_envelope import envelope_error_json
from tools.registry import registry

GRAPH_REPORT_SCHEMA = {
    "name": "graph_report",
    "description": "生成知识图谱健康报告：总笔记数、主题数、孤岛笔记、桥接笔记、连接率。",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def _handler(args: dict, **kw) -> str:
    try:
        result = run_graph_report()
        return envelope_json(result)
    except Exception as exc:
        return envelope_error_json(agent="theme", intent="run_graph_report", exc=exc)


registry.register(
    name="graph_report",
    toolset="personal-assistant",
    schema=GRAPH_REPORT_SCHEMA,
    handler=_handler,
    emoji="📊",
)

"""Shared answer-contract fragments used by every synthesizer."""
from __future__ import annotations

MODEL_SUPPLEMENT_MARKER = "💡 延伸补充（模型通识）："

MODEL_SUPPLEMENT_CONTRACT = (
    "当已有文章、本地资料和网页材料只覆盖问题的一部分（且不是强时效、"
    "精确统计数值类问题）时，可以结合专业通识主动补充完整。"
    "凡是由你补充的通识或温和纠偏，必须独立成段，并以"
    f"「{MODEL_SUPPLEMENT_MARKER}」开头；该段不得添加 [K] 或 [W] 引用角标。"
)

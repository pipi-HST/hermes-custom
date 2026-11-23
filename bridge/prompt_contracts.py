"""Shared answer-contract fragments used by every synthesizer."""
from __future__ import annotations

MODEL_SUPPLEMENT_MARKER = "💡 延伸补充（模型通识）："

MODEL_SUPPLEMENT_CONTRACT = (
    "当已有文章、本地资料和网页材料只覆盖问题的一部分（且不是强时效、"
    "精确统计数值类问题）时，可以结合专业通识自然补充完整，直接融入正文即可；"
    "不要单独输出「💡 延伸补充（模型通识）」区块，未引用资料的内容不要添加 [K] 或 [W] 引用角标。"
)

"""Helpers for managed Markdown sections that are useful for navigation but poor RAG evidence."""
from __future__ import annotations

import re


_MANAGED_SECTION_TITLES = {
    "目录纲要",
    "相关笔记",
    "相关笔记引用",
    "知识关联",
    "参考来源",
    "系统记录",
    "Hermes 管理",
}


def is_managed_section_title(title: str) -> bool:
    clean = re.sub(r"\s+", " ", str(title or "")).strip()
    return clean in _MANAGED_SECTION_TITLES

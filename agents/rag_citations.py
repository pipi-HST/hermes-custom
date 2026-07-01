"""Citation validation helpers for sourced RAG answers."""

from __future__ import annotations

import re
from dataclasses import dataclass


_CITATION_RE = re.compile(r"\\?\[(\d{1,3})\\?\]")


@dataclass(frozen=True)
class CitationValidation:
    valid: bool
    missing_citations: bool
    unknown_citation_ids: list[str]
    cited_source_ids: list[str]
    warnings: list[str]

    def to_meta(self) -> dict:
        return {
            "valid": self.valid,
            "missing_citations": self.missing_citations,
            "unknown_citation_ids": self.unknown_citation_ids,
            "cited_source_ids": self.cited_source_ids,
        }


def validate_citations(answer_text: str, source_map: list[dict]) -> CitationValidation:
    """Validate numbered citation markers against a 1-indexed source map."""
    source_count = len(source_map)
    cited_ids = _unique_citation_ids(answer_text)
    valid_ids = {str(index) for index in range(1, source_count + 1)}
    unknown_ids = [citation_id for citation_id in cited_ids if citation_id not in valid_ids]
    missing = source_count > 0 and not cited_ids

    warnings: list[str] = []
    if missing:
        warnings.append("missing_citations")
    if unknown_ids:
        warnings.append(f"unknown_citation_ids:{','.join(unknown_ids)}")

    cited_known_ids = [citation_id for citation_id in cited_ids if citation_id in valid_ids]
    return CitationValidation(
        valid=not warnings,
        missing_citations=missing,
        unknown_citation_ids=unknown_ids,
        cited_source_ids=cited_known_ids,
        warnings=warnings,
    )


def build_citation_repair_prompt(answer_text: str, source_map: list[dict]) -> str:
    source_lines = []
    for index, source in enumerate(source_map, 1):
        title = str(source.get("title") or source.get("path") or f"来源 {index}").strip()
        section = str(source.get("section") or "").strip()
        path = str(source.get("path") or "").strip()
        label = f"[{index}] {title}"
        if section:
            label += f" / {section}"
        if path:
            label += f" ({path})"
        source_lines.append(label)
    return (
        "请修复下面回答中的来源编号引用。\n\n"
        "要求：\n"
        "- 保持原回答含义，不新增事实、不删除重要判断。\n"
        "- 只使用可用来源编号，例如 [1] 或 [1][2]。\n"
        "- 不要使用不存在的编号。\n"
        "- 直接输出修复后的回答，不要解释过程。\n\n"
        "可用来源：\n"
        f"{chr(10).join(source_lines)}\n\n"
        "原回答：\n"
        f"{answer_text.strip()}"
    )


def _unique_citation_ids(answer_text: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _CITATION_RE.finditer(answer_text or ""):
        citation_id = str(int(match.group(1)))
        if citation_id in seen:
            continue
        seen.add(citation_id)
        ordered.append(citation_id)
    return ordered

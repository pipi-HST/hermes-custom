"""Plain text extractor — catch-all fallback."""
from __future__ import annotations

from .base import BaseExtractor, ExtractionResult


class TextExtractor(BaseExtractor):
    def can_handle(self, source: str) -> bool:
        return True  # always handles as fallback

    def extract(self, source: str, **kwargs) -> ExtractionResult:
        return ExtractionResult(
            raw_text=source.strip(),
            source_type=kwargs.get("source_type", "note"),
            source_url=kwargs.get("source_url", ""),
        )

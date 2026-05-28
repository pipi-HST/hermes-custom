"""Base extractor interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ExtractionResult:
    raw_text: str
    source_type: str
    source_url: str = ""
    title: str = ""
    extraction_failed: bool = False


class BaseExtractor(ABC):
    @abstractmethod
    def can_handle(self, source: str) -> bool:
        ...

    @abstractmethod
    def extract(self, source: str, **kwargs) -> ExtractionResult:
        ...

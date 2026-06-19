"""Base extractor interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass


@dataclass
class ExtractionFailure:
    code: str
    message: str
    retryable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExtractionResult:
    raw_text: str
    source_type: str
    source_url: str = ""
    title: str = ""
    extraction_failed: bool = False
    failure: ExtractionFailure | None = None

    def __post_init__(self) -> None:
        if self.failure is not None:
            self.extraction_failed = True

    @classmethod
    def failed(
        cls,
        *,
        code: str,
        message: str,
        raw_text: str = "",
        source_type: str,
        source_url: str = "",
        title: str = "",
        retryable: bool = False,
    ) -> "ExtractionResult":
        return cls(
            raw_text=raw_text or f"[{message}]",
            source_type=source_type,
            source_url=source_url,
            title=title,
            extraction_failed=True,
            failure=ExtractionFailure(
                code=code,
                message=message,
                retryable=retryable,
            ),
        )


class BaseExtractor(ABC):
    @abstractmethod
    def can_handle(self, source: str) -> bool:
        ...

    @abstractmethod
    def extract(self, source: str, **kwargs) -> ExtractionResult:
        ...

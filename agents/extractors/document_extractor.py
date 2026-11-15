"""Document extractor for local text, Markdown, Word, and PDF files."""
from __future__ import annotations

import importlib
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .base import BaseExtractor, ExtractionFailure, ExtractionResult


TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".log",
    ".html",
    ".htm",
}
DOCUMENT_SUFFIXES = TEXT_SUFFIXES | {".docx", ".pdf"}


class DocumentExtractor(BaseExtractor):
    """Read supported local document paths into raw text for Capture."""

    # A real path is short; user content can be thousands of characters.  On
    # Linux a stat() on an over-long name raises OSError(ENAMETOOLONG) instead
    # of returning "not a file" (pathlib only ignores ENOENT/ENOTDIR/EBADF/
    # ELOOP), which used to crash capture for any long text input.
    _MAX_PATH_CHARS = 1024

    def can_handle(self, source: str) -> bool:
        candidate = (source or "").strip()
        if not candidate or len(candidate) > self._MAX_PATH_CHARS:
            return False
        if any(char in candidate for char in ("\n", "\r", "\x00")):
            return False
        path = Path(candidate)
        try:
            return path.is_file() and path.suffix.lower() in DOCUMENT_SUFFIXES
        except OSError:
            return False

    def extract(self, source: str, **kwargs) -> ExtractionResult:
        path = Path(source).expanduser()
        suffix = path.suffix.lower()
        try:
            if suffix in TEXT_SUFFIXES:
                text = path.read_text(encoding="utf-8", errors="replace")
            elif suffix == ".docx":
                text = self._extract_docx(path)
            elif suffix == ".pdf":
                text = self._extract_pdf(path)
            else:
                text = ""
        except Exception as exc:
            return ExtractionResult.failed(
                code="parse_error",
                message=f"文档内容提取失败: {exc}",
                raw_text=f"[文档内容提取失败: {exc}]",
                source_type="document",
                source_url=str(path),
                title=path.stem,
            )

        return ExtractionResult(
            raw_text=text,
            source_type="document",
            source_url=str(path),
            title=path.stem,
            extraction_failed=not bool(text.strip()),
            failure=(
                None
                if text.strip()
                else ExtractionFailure(
                    code="empty_content",
                    message="文档内容为空",
                    retryable=False,
                )
            ),
        )

    def _extract_docx(self, path: Path) -> str:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs: list[str] = []
        for paragraph in root.findall(".//w:p", namespace):
            texts = [
                node.text or ""
                for node in paragraph.findall(".//w:t", namespace)
                if node.text
            ]
            line = "".join(texts).strip()
            if line:
                paragraphs.append(line)
        return "\n\n".join(paragraphs)

    def _extract_pdf(self, path: Path) -> str:
        pypdf = importlib.import_module("pypdf")
        reader = pypdf.PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(page.strip() for page in pages if page.strip())

"""Xiaohongshu note extractor — text + ordered image OCR package."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from .base import BaseExtractor, ExtractionResult
from .image_extractor import ImageExtractor

logger = logging.getLogger(__name__)


class XiaohongshuExtractor(BaseExtractor):
    URL_RE = re.compile(r"https?://[^\s]+")
    XHS_RE = re.compile(r"(?:xhslink\.com|xiaohongshu\.com)", re.IGNORECASE)
    META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
    ATTR_RE = re.compile(r"([:\w-]+)\s*=\s*([\"'])(.*?)\2", re.DOTALL)
    TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
    IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
    XHS_IMAGE_RE = re.compile(
        r"https?://[^\"'\s<>]*(?:xhscdn|xhsimg)[^\"'\s<>]*",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        image_extractor: ImageExtractor | None = None,
        media_dir: str | Path | None = None,
    ) -> None:
        self.image_extractor = image_extractor or ImageExtractor()
        self.media_dir = Path(media_dir or Path(__file__).resolve().parents[2] / "data" / "media" / "xiaohongshu")

    def can_handle(self, source: str) -> bool:
        return bool(self.XHS_RE.search(source or ""))

    def extract(self, source: str, **kwargs) -> ExtractionResult:
        url = self._extract_url(source)
        if not url:
            return self._failure(source)

        try:
            final_url, page_html = self._fetch_url(url)
        except Exception as exc:
            logger.warning("Xiaohongshu fetch failed: %s", exc)
            return self._failure(url, source_url=url)

        note_id = self._note_id(final_url)
        title = self._title(page_html)
        description = self._description(page_html)
        image_urls = self._image_urls(page_html)
        image_sections = self._image_sections(image_urls, note_id)
        if self._is_generic_title(title):
            title = self._derive_meaningful_title(source, description, image_sections) or title
        body = self._format_note(
            title=title,
            source_url=final_url,
            description=description,
            image_sections=image_sections,
        )
        if not self._has_material(description, image_sections):
            return self._failure(final_url, source_url=final_url)

        return ExtractionResult(
            raw_text=body,
            source_type="xiaohongshu",
            source_url=final_url,
            title=title,
        )

    def _extract_url(self, source: str) -> str:
        match = self.URL_RE.search(source or "")
        return match.group(0).rstrip("，。；;、)") if match else ""

    def _fetch_url(self, url: str) -> tuple[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return str(resp.url), resp.text

    def _description(self, page_html: str) -> str:
        candidates = self._meta_values(page_html, {"og:description", "description"})
        candidates.extend(self._json_text_values(page_html, keys={"desc", "description", "content"}))
        return self._joined_text(candidates)

    def _image_urls(self, page_html: str) -> list[str]:
        normalized = self._normalize_page_text(page_html)
        urls = self._meta_values(normalized, {"og:image", "image"})
        urls.extend(self._img_attr_values(normalized))
        urls.extend(self.XHS_IMAGE_RE.findall(normalized))
        cleaned = []
        seen = set()
        for url in urls:
            item = self._normalize_page_text(url).strip()
            if item and item not in seen:
                seen.add(item)
                cleaned.append(item)
        return cleaned[:12]

    def _image_sections(self, image_urls: list[str], note_id: str) -> list[str]:
        sections = []
        for index, url in enumerate(image_urls, start=1):
            local_path = self._download_image(url, note_id, index)
            if not local_path:
                sections.append(f"## 图片 {index}\n图片地址：{url}\n图片下载失败，无法 OCR。")
                continue
            ocr = self.image_extractor.extract(str(local_path))
            text = (ocr.raw_text or "").strip()
            if not text:
                text = "图片 OCR 结果为空。"
            sections.append(
                f"## 图片 {index}\n"
                f"本地图片：{local_path}\n"
                f"{text}"
            )
        return sections

    def _download_image(self, url: str, note_id: str, index: int) -> Path | None:
        ext = self._image_ext(url)
        target_dir = self.media_dir / note_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{index:02d}{ext}"
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                resp = client.get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://www.xiaohongshu.com/",
                    },
                )
                resp.raise_for_status()
                target.write_bytes(resp.content)
                return target
        except Exception as exc:
            logger.warning("Xiaohongshu image download failed: %s", exc)
            return None

    def _note_id(self, url: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in ("note_id", "id"):
            value = query.get(key)
            if value and value[0]:
                return self._safe_id(value[0])
        parts = [part for part in parsed.path.split("/") if part]
        for marker in ("explore", "item"):
            if marker in parts:
                idx = parts.index(marker)
                if idx + 1 < len(parts):
                    return self._safe_id(parts[idx + 1])
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]

    def _safe_id(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]+", "", value)[:48] or "note"

    def _image_ext(self, url: str) -> str:
        path = urlparse(url).path.lower()
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            if path.endswith(ext):
                return ext
        return ".jpg"

    def _format_note(
        self,
        *,
        title: str,
        source_url: str,
        description: str,
        image_sections: list[str],
    ) -> str:
        lines = [
            f"# {title or '小红书笔记'}",
            "",
            f"来源：{source_url}",
        ]
        if description:
            lines.extend(["", "## 正文文案", description])
        if image_sections:
            lines.extend(["", "## 图片内容", *image_sections])
        return "\n\n".join(line for line in lines if line is not None).strip()

    def _failure(self, source: str, *, source_url: str = "") -> ExtractionResult:
        return ExtractionResult(
            raw_text=(
                f"[小红书内容未能提取] {source}\n"
                "可以把正文复制给我，或把关键图片/截图发给我，我会继续 OCR 整理。"
            ),
            source_type="xiaohongshu",
            source_url=source_url or source,
            extraction_failed=True,
        )

    def _title(self, page_html: str) -> str:
        values = self._meta_values(page_html, {"og:title", "title"})
        if values:
            return values[0]
        match = self.TITLE_TAG_RE.search(page_html or "")
        if match:
            return html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
        return ""

    def _is_generic_title(self, title: str) -> bool:
        normalized = re.sub(r"\s+", "", title or "").lower()
        return normalized in {"", "小红书", "小红书笔记", "xiaohongshu"}

    def _derive_meaningful_title(
        self,
        source: str,
        description: str,
        image_sections: list[str],
    ) -> str:
        for candidate in self._image_title_candidates(image_sections):
            if self._is_title_candidate(candidate):
                return candidate
        for candidate in self._source_title_candidates(source):
            if self._is_title_candidate(candidate):
                return candidate
        for candidate in self._plain_text_title_candidates(description):
            if self._is_title_candidate(candidate):
                return candidate
        return ""

    def _source_title_candidates(self, source: str) -> list[str]:
        text = re.sub(r"https?://\S+", "", source or "").strip()
        candidates = []
        match = re.search(r"([^\n。！？!?]{2,40})\s*[|｜]\s*([^\n。！？!?]{2,40})", text)
        if match:
            candidates.append(match.group(2))
            candidates.append(match.group(1))
        return candidates

    def _image_title_candidates(self, image_sections: list[str]) -> list[str]:
        if not image_sections:
            return []
        text = re.sub(r"^## 图片 \d+\s*", "", image_sections[0], flags=re.MULTILINE).strip()
        lines = [
            line.strip().strip("#> -")
            for line in text.splitlines()
            if line.strip()
        ]
        return lines[:6]

    def _plain_text_title_candidates(self, text: str) -> list[str]:
        return [
            line.strip().strip("#> -")
            for line in (text or "").splitlines()
            if line.strip()
        ][:3]

    def _is_title_candidate(self, text: str) -> bool:
        candidate = (text or "").strip().strip("。！？!?；;，,：:")
        if not candidate or self._is_generic_title(candidate):
            return False
        if candidate in {"观察", "笔记", "正文", "图片内容", "正文文案"}:
            return False
        if candidate.startswith(("本地图片：", "来源：", "http://", "https://")):
            return False
        if len(candidate) < 4 or len(candidate) > 40:
            return False
        return True

    def _meta_values(self, page_html: str, names: set[str]) -> list[str]:
        values = []
        for tag in self.META_TAG_RE.findall(page_html or ""):
            attrs = {
                key.lower(): html.unescape(value).strip()
                for key, _, value in self.ATTR_RE.findall(tag)
            }
            name = (attrs.get("property") or attrs.get("name") or "").lower()
            content = attrs.get("content", "")
            if name in names and content:
                values.append(content)
        return values

    def _img_attr_values(self, page_html: str) -> list[str]:
        values = []
        for tag in self.IMG_TAG_RE.findall(page_html or ""):
            attrs = {
                key.lower(): html.unescape(value).strip()
                for key, _, value in self.ATTR_RE.findall(tag)
            }
            for key in ("src", "data-src", "data-original", "data-lazy-src"):
                value = attrs.get(key, "")
                if value:
                    values.append(value)
        return values

    def _normalize_page_text(self, text: str) -> str:
        result = text or ""
        for _ in range(3):
            previous = result
            result = html.unescape(result)
            result = result.replace("\\u002F", "/").replace("\\/", "/")
            result = result.replace("\\u003D", "=").replace("\\u0026", "&")
            if result == previous:
                break
        return result

    def _json_text_values(self, page_html: str, *, keys: set[str]) -> list[str]:
        values = []
        decoder = json.JSONDecoder()
        text = self._normalize_page_text(page_html)
        for match in re.finditer(r"\{", text):
            try:
                obj, _ = decoder.raw_decode(text[match.start():])
            except json.JSONDecodeError:
                continue
            self._collect_text_values(obj, keys, values)
        return values

    def _collect_text_values(self, value, keys: set[str], values: list[str]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in keys and isinstance(item, str):
                    values.append(item)
                else:
                    self._collect_text_values(item, keys, values)
        elif isinstance(value, list):
            for item in value:
                self._collect_text_values(item, keys, values)

    def _joined_text(self, candidates: list[str]) -> str:
        cleaned = []
        seen = set()
        for item in candidates:
            text = self._normalize_page_text(item).strip() if item else ""
            if text.startswith(("http://", "https://")):
                continue
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        return "\n\n".join(cleaned)

    def _has_material(self, description: str, image_sections: list[str]) -> bool:
        if description.strip():
            return True
        return any(
            "图片下载失败" not in section and "图片 OCR 结果为空" not in section
            for section in image_sections
        )

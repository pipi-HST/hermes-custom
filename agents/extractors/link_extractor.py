"""链接提取器 — readability → trafilatura → 粘贴入口 三级降级."""
from __future__ import annotations

import logging, re
from .base import BaseExtractor, ExtractionResult

try:
    import httpx
    from readability import Document as ReadabilityDoc
    HAS_READABILITY = True
except ImportError:
    HAS_READABILITY = False

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

logger = logging.getLogger(__name__)


class LinkExtractor(BaseExtractor):
    URL_RE = re.compile(r'https?://[^\s]+')
    OG_TITLE_RE = re.compile(
        r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', re.IGNORECASE)
    WECHAT_RE = re.compile(r'mp\.weixin\.qq\.com')
    XHS_RE = re.compile(r'xhslink\.com|xiaohongshu\.com')
    VIDEO_RE = re.compile(r'video.*\.(mp4|mov|avi|mkv|webm)')
    BILIBILI_RE = re.compile(r'bilibili\.com|b23\.tv')

    @staticmethod
    def _extract_title(html: str) -> str:
        m = LinkExtractor.OG_TITLE_RE.search(html)
        if m: return m.group(1).strip()
        m = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    def can_handle(self, source: str) -> bool:
        return bool(self.URL_RE.search(source))

    def extract(self, source: str, **kwargs) -> ExtractionResult:
        url_match = self.URL_RE.search(source)
        url = url_match.group(0) if url_match else source
        ua = "Mozilla/5.0 (compatible; Hermes-Capture/1.0)"
        if self.WECHAT_RE.search(url):
            ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.38")
        if self.BILIBILI_RE.search(url) or self.VIDEO_RE.search(url):
            return ExtractionResult(raw_text=f"[视频内容] {url}", source_type="video", source_url=url)

        # Level 1: readability
        result = self._try_readability(url, ua)
        if result and result.raw_text.strip():
            # Set title from og:title if available
            if not result.title:
                result.title = ""
            return result

        # Level 2: trafilatura
        result = self._try_trafilatura(url)
        if result and result.raw_text.strip():
            return result

        # Level 3: give up
        return ExtractionResult.failed(
            code="empty_content",
            message="网页内容未能提取",
            raw_text=(f"[内容未能提取] {url}\n请复制粘贴正文给我。"),
            source_type="link",
            source_url=url,
            retryable=True,
        )

    def _try_readability(self, url: str, ua: str) -> ExtractionResult | None:
        if not HAS_READABILITY:
            return None
        try:
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": ua})
                resp.raise_for_status()
                html = resp.text
                title = self._extract_title(html)
                doc = ReadabilityDoc(html)
                content = doc.summary(html_partial=True)
                content = re.sub(r'<[^>]+>', '', content)
                return ExtractionResult(raw_text=content.strip(), source_type="link",
                                        source_url=url, title=title)
        except Exception:
            return None

    def _try_trafilatura(self, url: str) -> ExtractionResult | None:
        if not HAS_TRAFILATURA:
            return None
        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                content = trafilatura.extract(downloaded, include_links=False,
                                              include_images=False, output_format="markdown")
                if content and len(content.strip()) > 100:
                    return ExtractionResult(raw_text=content.strip(), source_type="link", source_url=url)
        except Exception:
            pass
        return None

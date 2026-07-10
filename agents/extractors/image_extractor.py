"""图片提取器 — 千问 VL OCR + Vision 描述."""
from __future__ import annotations

import base64
import logging
import re
from pathlib import Path

import httpx

from .base import BaseExtractor, ExtractionResult
from .http_safety import DownloadTooLarge, UnsafeURL, fetch_public_bytes

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 10 * 1024 * 1024

IMAGE_PROMPT = """请对这张图片做两件事：
1. 逐字提取图片中的所有可见文字（OCR），尽量保留原文顺序、段落和换行。不要总结、不要改写、不要只提标题。
2. 用一句话描述图片的视觉内容。如果图片主要是文字长图，描述只做辅助，不能替代 OCR。

按以下格式回复：
---
text: <完整 OCR 文字，如果没有文字写"无文字">
description: <一句话视觉描述>
---"""


class ImageExtractor(BaseExtractor):
    """用千问 VL 模型同时做 OCR + 图片描述。"""

    def __init__(self, api_key: str = "",
                 base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"):
        self.api_key = api_key
        self.base_url = base_url

    def can_handle(self, source: str) -> bool:
        # 检测图片路径或 base64 data URI
        if source.startswith("data:image/"):
            return True
        if source.startswith("http") and any(
            ext in source.lower() for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]
        ):
            return True
        # 本地文件路径
        if source.startswith("/") and Path(source).suffix.lower() in {
            ".png", ".jpg", ".jpeg", ".gif", ".webp",
        }:
            return True
        return False

    def extract(self, source: str, **kwargs) -> ExtractionResult:
        if not self.api_key:
            return ExtractionResult.failed(
                code="auth_error",
                message="图片提取未配置 API key",
                raw_text="[图片提取未配置 API key]",
                source_type="image",
                source_url=kwargs.get("source_url", ""),
            )

        try:
            image_data = self._resolve_image(source)
        except UnsafeURL as exc:
            return ExtractionResult.failed(
                code="unsafe_source",
                message=f"图片地址不安全: {exc}",
                raw_text="[图片地址被安全策略拒绝]",
                source_type="image",
                source_url=source,
            )
        except DownloadTooLarge as exc:
            return ExtractionResult.failed(
                code="source_too_large",
                message=f"图片内容过大: {exc}",
                raw_text="[图片内容超过下载上限]",
                source_type="image",
                source_url=source,
            )
        if not image_data:
            return ExtractionResult.failed(
                code="network_error",
                message="图片数据获取失败",
                raw_text="[图片数据获取失败]",
                source_type="image",
                source_url=source,
                retryable=True,
            )

        try:
            text, description = self._call_vl(image_data)
            result_text = text if text and text != "无文字" else ""
            if not result_text and description and description != "无文字":
                result_text = description
            if not result_text.strip():
                return ExtractionResult.failed(
                    code="empty_content",
                    message="图片中无文字内容",
                    raw_text="[图片中无文字内容]",
                    source_type="image",
                    source_url=kwargs.get("source_url", source),
                )
            return ExtractionResult(
                raw_text=result_text,
                source_type="image",
                source_url=kwargs.get("source_url", source),
            )
        except Exception as e:
            logger.warning(f"Image VL extraction failed: {e}")
            return ExtractionResult.failed(
                code="external_service_error",
                message=f"图片内容提取失败: {e}",
                raw_text=f"[图片内容提取失败: {e}]",
                source_type="image",
                source_url=source,
                retryable=True,
            )

    def _resolve_image(self, source: str) -> str | None:
        """返回 base64 data URI 或 None。"""
        if source.startswith("data:image/"):
            header, _, payload = source.partition(",")
            max_payload_bytes = (
                (MAX_IMAGE_BYTES * 4 // 3) + 4
                if ";base64" in header.lower()
                else MAX_IMAGE_BYTES
            )
            if len(payload.encode("utf-8")) > max_payload_bytes:
                raise DownloadTooLarge(f"image exceeds {MAX_IMAGE_BYTES} bytes")
            return source
        if source.startswith("http"):
            _final_url, headers, content = fetch_public_bytes(
                source,
                timeout=15,
                max_bytes=MAX_IMAGE_BYTES,
            )
            content_type = headers.get("content-type", "image/png").split(";", 1)[0]
            if content_type and not content_type.lower().startswith("image/"):
                return None
            b64 = base64.b64encode(content).decode()
            return f"data:{content_type or 'image/png'};base64,{b64}"
        if source.startswith("/"):
            try:
                path = Path(source)
                if path.exists():
                    if path.stat().st_size > MAX_IMAGE_BYTES:
                        raise DownloadTooLarge(f"image exceeds {MAX_IMAGE_BYTES} bytes")
                    ext = path.suffix.lower()
                    mime = {"jpg": "jpeg"}.get(ext.lstrip("."), ext.lstrip("."))
                    b64 = base64.b64encode(path.read_bytes()).decode()
                    return f"data:image/{mime};base64,{b64}"
            except DownloadTooLarge:
                raise
            except Exception as e:
                logger.warning(f"Failed to read local image: {e}")
        return None

    def _call_vl(self, image_data: str) -> tuple[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": "qwen-vl-max",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data}},
                    {"type": "text", "text": IMAGE_PROMPT},
                ],
            }],
            "temperature": 0.1,
            "max_tokens": 3000,
        }
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers, json=body,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]

        text, desc = self._parse_vl_response(raw)
        return text, desc

    def _parse_vl_response(self, raw: str) -> tuple[str, str]:
        raw = (raw or "").strip()
        raw = raw.removeprefix("---").removesuffix("---").strip()

        desc = ""
        desc_match = re.search(r"(?ims)^description\s*:\s*(.+?)\s*$", raw)
        if desc_match:
            desc = desc_match.group(1).strip()

        text_match = re.search(
            r"(?ims)^text\s*:\s*(.+?)(?=^\s*description\s*:|\Z)",
            raw,
        )
        text = text_match.group(1).strip() if text_match else raw.strip()
        text = text.removesuffix("---").strip()
        return text, desc

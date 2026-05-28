"""图片提取器 — 千问 VL OCR + Vision 描述."""
from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

from .base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

IMAGE_PROMPT = """请对这张图片做两件事：
1. 提取图片中的所有文字（OCR）
2. 用一句话描述图片的视觉内容

按以下格式回复：
---
text: <提取的文字，如果没有文字写"无文字">
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
            return ExtractionResult(
                raw_text="[图片提取未配置 API key]", source_type="image",
                source_url=kwargs.get("source_url", ""),
            )

        image_data = self._resolve_image(source)
        if not image_data:
            return ExtractionResult(
                raw_text="[图片数据获取失败]", source_type="image",
                source_url=source,
            )

        try:
            text, description = self._call_vl(image_data)
            result_text = text if text and text != "无文字" else ""
            if description and description != "无文字":
                result_text = f"{description}\n{result_text}" if result_text else description
            return ExtractionResult(
                raw_text=result_text or "[图片中无文字内容]",
                source_type="image",
                source_url=kwargs.get("source_url", source),
            )
        except Exception as e:
            logger.warning(f"Image VL extraction failed: {e}")
            return ExtractionResult(
                raw_text=f"[图片内容提取失败: {e}]", source_type="image",
                source_url=source,
            )

    def _resolve_image(self, source: str) -> str | None:
        """返回 base64 data URI 或 None。"""
        if source.startswith("data:image/"):
            return source
        if source.startswith("http"):
            try:
                with httpx.Client(timeout=15) as client:
                    resp = client.get(source)
                    resp.raise_for_status()
                    ct = resp.headers.get("content-type", "image/png")
                    b64 = base64.b64encode(resp.content).decode()
                    return f"data:{ct};base64,{b64}"
            except Exception as e:
                logger.warning(f"Failed to download image: {e}")
                return None
        if source.startswith("/"):
            try:
                path = Path(source)
                if path.exists():
                    ext = path.suffix.lower()
                    mime = {"jpg": "jpeg"}.get(ext.lstrip("."), ext.lstrip("."))
                    b64 = base64.b64encode(path.read_bytes()).decode()
                    return f"data:image/{mime};base64,{b64}"
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
            "max_tokens": 500,
        }
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers, json=body,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]

        import re
        text_match = re.search(r"text:\s*(.+?)(?:\n|$)", raw)
        desc_match = re.search(r"description:\s*(.+?)(?:\n|$)", raw)
        text = text_match.group(1).strip() if text_match else raw.strip()
        desc = desc_match.group(1).strip() if desc_match else ""
        return text, desc

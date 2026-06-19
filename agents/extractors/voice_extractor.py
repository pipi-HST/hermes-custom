"""语音提取器 — 千问 Paraformer ASR."""
from __future__ import annotations

import logging

import httpx

from .base import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)


class VoiceExtractor(BaseExtractor):
    """用千问 Paraformer 语音识别 API 做 ASR。"""

    def __init__(self, api_key: str = "",
                 base_url: str = "https://dashscope.aliyuncs.com/api/v1"):
        self.api_key = api_key
        self.base_url = base_url

    def can_handle(self, source: str) -> bool:
        # 检测语音文件 URL 或路径（含微信 .silk 格式）
        lower = source.lower()
        return any(
            lower.endswith(ext) for ext in
            [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".amr", ".silk"]
        ) or lower.startswith("http") and any(
            fmt in lower for fmt in ["voice", "audio", ".mp3?", ".wav?", ".m4a?"]
        )

    def extract(self, source: str, **kwargs) -> ExtractionResult:
        if not self.api_key:
            return ExtractionResult.failed(
                code="auth_error",
                message="语音转文字未配置 API key",
                raw_text="[语音转文字未配置 API key]",
                source_type="voice",
                source_url=kwargs.get("source_url", ""),
            )

        try:
            text = self._call_asr(source)
            if text:
                return ExtractionResult(
                    raw_text=text, source_type="voice",
                    source_url=kwargs.get("source_url", source),
                )
            return ExtractionResult.failed(
                code="empty_content",
                message="语音识别结果为空",
                raw_text="[语音识别结果为空]",
                source_type="voice",
                source_url=source,
            )
        except Exception as e:
            logger.warning(f"Voice ASR failed: {e}")
            return ExtractionResult.failed(
                code="external_service_error",
                message=f"语音识别失败: {e}",
                raw_text=f"[语音识别失败: {e}]",
                source_type="voice",
                source_url=source,
                retryable=True,
            )

    def _call_asr(self, audio_url: str) -> str:
        """Paraformer 语音识别 — 传入音频 URL。"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": "paraformer-realtime-v2",
            "input": {
                "file_urls": [audio_url],
            },
        }
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{self.base_url}/services/aigc/multimodal-generation/generation",
                headers=headers, json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            output = data.get("output", {})
            choices = output.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return ""

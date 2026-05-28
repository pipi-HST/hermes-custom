"""Tests for media capture — image OCR and voice ASR through capture workflow."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agents.capture_agent import CaptureAgent, CaptureConfig
from agents.extractors.image_extractor import ImageExtractor
from agents.extractors.voice_extractor import VoiceExtractor
from agents.extractors.text_extractor import TextExtractor


@pytest.fixture
def agent(tmp_path: Path) -> CaptureAgent:
    config = CaptureConfig(
        llm_api_key="sk-test",
        llm_provider="openai",
        llm_model="gpt-4o",
        llm_base_url="https://api.deepseek.com",
        qwen_api_key="sk-qwen-test",
        vault_path=str(tmp_path),
        db_path=str(tmp_path / "test.db"),
    )
    return CaptureAgent(config)


# ── ImageExtractor.can_handle ──


def test_image_extractor_handles_local_jpg(tmp_path: Path):
    """ImageExtractor should accept local .jpg paths."""
    img = tmp_path / "test.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG magic bytes
    ext = ImageExtractor(api_key="sk-test")
    assert ext.can_handle(str(img)) is True


def test_image_extractor_handles_local_png(tmp_path: Path):
    """ImageExtractor should accept local .png paths."""
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG")
    ext = ImageExtractor(api_key="sk-test")
    assert ext.can_handle(str(img)) is True


def test_image_extractor_rejects_non_image_path():
    """ImageExtractor should reject non-image file paths."""
    ext = ImageExtractor(api_key="sk-test")
    assert ext.can_handle("/tmp/test.txt") is False
    assert ext.can_handle("/tmp/test.mp3") is False
    assert ext.can_handle("https://example.com/article") is False


# ── VoiceExtractor.can_handle ──


def test_voice_extractor_handles_silk():
    """VoiceExtractor should accept .silk (WeChat voice format)."""
    ext = VoiceExtractor(api_key="sk-test")
    assert ext.can_handle("/tmp/voice.silk") is True


def test_voice_extractor_handles_mp3():
    """VoiceExtractor should accept .mp3 paths."""
    ext = VoiceExtractor(api_key="sk-test")
    assert ext.can_handle("/tmp/audio.mp3") is True


def test_voice_extractor_rejects_non_audio():
    """VoiceExtractor should reject non-audio paths."""
    ext = VoiceExtractor(api_key="sk-test")
    assert ext.can_handle("/tmp/image.jpg") is False
    assert ext.can_handle("https://example.com/article") is False


# ── Image capture end-to-end ──


_OCR_TEXT = (
    "小红书笔记标题：AI时代必读的5本书。"
    "正文：第一本是思考快与慢，作者是丹尼尔卡尼曼，讲的是人类决策的两种模式。"
    "第二本是原则，作者是瑞达利欧，讲的是如何用系统化的方法做决策。"
    "第三本是反脆弱，作者是塔勒布，讲的是如何从不确定性中获益。"
    "第四本是黑天鹅，也是塔勒布的作品，讲的是极端事件的影响。"
    "第五本是穷查理宝典，讲的是芒格的多元思维模型。"
)


def test_create_draft_from_image_path(agent: CaptureAgent, tmp_path: Path):
    """Image file path → ImageExtractor OCR → draft with raw_text."""
    img = tmp_path / "xhs_screenshot.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    with patch.object(ImageExtractor, "_call_vl", return_value=(
        _OCR_TEXT, "一张小红书笔记截图，展示了AI相关书单",
    )):
        result = agent.create_draft(str(img))

    assert result["status"] == "raw_extracted"
    assert "AI时代" in result["raw_text"]
    assert result["source_type"] == "image"


def test_create_draft_from_image_with_instruction(agent: CaptureAgent, tmp_path: Path, monkeypatch):
    """Image path + instruction → OCR then LLM processing → draft_ready."""
    img = tmp_path / "xhs.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

    with patch.object(ImageExtractor, "_call_vl", return_value=(
        _OCR_TEXT, "一张小红书书单截图",
    )):
        monkeypatch.setattr(agent, "_call_llm", lambda prompt: "提炼书单：思考快与慢、原则、反脆弱。")
        monkeypatch.setattr(
            agent.summarizer, "summarize",
            lambda text, existing_tags=None: {
                "title": "AI时代书单",
                "summary": "AI时代推荐的三本书",
                "tags": ["ai", "reading"],
            },
        )
        result = agent.create_draft(str(img), instruction="提炼书单")

    assert result["status"] == "draft_ready"
    assert "书单" in result["draft_text"]
    assert result["summary"] == "AI时代推荐的三本书"
    assert result["tags"] == ["ai", "reading"]


# ── Voice text capture (iLink already transcribed) ──


_VOICE_TEXT = (
    "我今天看了一篇关于AI芯片的文章觉得很有启发想存一下。"
    "文章说国产AI芯片在过去两年里性能提升了三倍，"
    "但是在生态建设上还有很长的路要走，特别是CUDA兼容性问题。"
    "我觉得这个观点很有价值，值得以后再仔细看看。"
)


def test_create_draft_from_voice_text(agent: CaptureAgent):
    """Transcribed voice text arrives as plain text → standard text capture."""
    result = agent.create_draft(_VOICE_TEXT)

    assert result["status"] == "raw_extracted"
    assert "AI芯片" in result["raw_text"]
    assert result["source_type"] == "note"  # treated as plain text


# ── Extractor routing ──


def test_extractor_routing_image(agent: CaptureAgent, tmp_path: Path):
    """CaptureAgent should route image paths to ImageExtractor."""
    img = tmp_path / "test.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")

    matched = next(e for e in agent.extractors if e.can_handle(str(img)))
    assert isinstance(matched, ImageExtractor)


def test_extractor_routing_voice(agent: CaptureAgent, tmp_path: Path):
    """CaptureAgent should route audio paths to VoiceExtractor."""
    audio = tmp_path / "voice.silk"
    audio.write_bytes(b"\x00" * 100)

    matched = next(e for e in agent.extractors if e.can_handle(str(audio)))
    assert isinstance(matched, VoiceExtractor)


def test_extractor_routing_text(agent: CaptureAgent):
    """CaptureAgent should route plain text to TextExtractor (fallback)."""
    matched = next(e for e in agent.extractors if e.can_handle("普通文本消息"))
    assert isinstance(matched, TextExtractor)

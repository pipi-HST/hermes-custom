from agents.extractors.image_extractor import ImageExtractor
from agents.extractors.link_extractor import LinkExtractor
from agents.extractors.document_extractor import DocumentExtractor
from agents.extractors.voice_extractor import VoiceExtractor


def test_image_extractor_reports_missing_api_key_as_typed_failure():
    result = ImageExtractor(api_key="").extract("/tmp/image.png")

    assert result.extraction_failed is True
    assert result.failure is not None
    assert result.failure.code == "auth_error"
    assert result.failure.retryable is False
    assert "API key" in result.failure.message


def test_voice_extractor_reports_missing_api_key_as_typed_failure():
    result = VoiceExtractor(api_key="").extract("https://example.com/audio.mp3")

    assert result.extraction_failed is True
    assert result.failure is not None
    assert result.failure.code == "auth_error"
    assert result.failure.retryable is False
    assert "API key" in result.failure.message


def test_link_extractor_reports_empty_content_as_typed_failure(monkeypatch):
    extractor = LinkExtractor()
    monkeypatch.setattr(extractor, "_try_readability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(extractor, "_try_trafilatura", lambda *_args, **_kwargs: None)

    result = extractor.extract("https://example.com/article")

    assert result.extraction_failed is True
    assert result.failure is not None
    assert result.failure.code == "empty_content"
    assert result.failure.retryable is True


def test_document_extractor_reports_parse_failure_as_typed_failure(tmp_path, monkeypatch):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a pdf")

    result = DocumentExtractor().extract(str(pdf))

    assert result.extraction_failed is True
    assert result.failure is not None
    assert result.failure.code == "parse_error"
    assert "文档内容提取失败" in result.failure.message

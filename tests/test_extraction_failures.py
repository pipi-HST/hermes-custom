from agents.extractors.image_extractor import ImageExtractor
from agents.extractors.link_extractor import LinkExtractor
from agents.extractors.document_extractor import DocumentExtractor
from agents.extractors.voice_extractor import VoiceExtractor
from agents.extractors.xiaohongshu_extractor import XiaohongshuExtractor


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


def test_link_extractor_extracts_wechat_js_content(monkeypatch):
    extractor = LinkExtractor()
    html = """
    <html>
      <head><meta property="og:title" content="公众号文章标题"></head>
      <body>
        <div id="js_content">
          <section>第一段正文。</section>
          <section><strong>第二段重点</strong>，还有&nbsp;空格。</section>
          <section>第三段用于模拟一篇有足够长度的公众号文章正文，避免把导航、空页面或验证码页误判为正文。</section>
          <section>第四段继续补充正文内容，确保微信专用提取分支能返回稳定的文章文本，而不是落到通用网页失败路径。</section>
        </div>
      </body>
    </html>
    """
    monkeypatch.setattr(extractor, "_fetch_html", lambda url, *_args, **_kwargs: (url, html))
    monkeypatch.setattr(extractor, "_try_readability", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(extractor, "_try_trafilatura", lambda *_args, **_kwargs: None)

    result = extractor.extract("https://mp.weixin.qq.com/s/example")

    assert result.extraction_failed is False
    assert result.title == "公众号文章标题"
    assert "第一段正文。" in result.raw_text
    assert "第二段重点，还有 空格。" in result.raw_text


def test_link_extractor_strips_chat_punctuation_from_urls(monkeypatch):
    extractor = LinkExtractor()
    seen: list[str] = []

    def fake_readability(url, _ua):
        seen.append(url)
        return None

    monkeypatch.setattr(extractor, "_try_readability", fake_readability)
    monkeypatch.setattr(extractor, "_try_trafilatura", lambda *_args, **_kwargs: None)

    extractor.extract("请收录：https://example.com/article）。")

    assert seen == ["https://example.com/article"]


def test_xiaohongshu_extractor_strips_chat_punctuation_from_urls():
    extractor = XiaohongshuExtractor()

    url = extractor._extract_url("复制来的小红书 https://xhslink.com/a/abc123）。")

    assert url == "https://xhslink.com/a/abc123"


def test_document_extractor_reports_parse_failure_as_typed_failure(tmp_path, monkeypatch):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a pdf")

    result = DocumentExtractor().extract(str(pdf))

    assert result.extraction_failed is True
    assert result.failure is not None
    assert result.failure.code == "parse_error"
    assert "文档内容提取失败" in result.failure.message

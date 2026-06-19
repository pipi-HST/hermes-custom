from __future__ import annotations

from pathlib import Path

from agents.extractors.base import ExtractionResult


class FakeImageExtractor:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def extract(self, source: str, **kwargs):
        self.paths.append(source)
        index = len(self.paths)
        return ExtractionResult(
            raw_text=f"图片 {index} OCR：图中文字 {index}。\n图片 {index} 描述：一张小红书配图。",
            source_type="image",
            source_url=source,
        )


def test_xiaohongshu_extractor_handles_share_and_note_urls():
    from agents.extractors.xiaohongshu_extractor import XiaohongshuExtractor

    extractor = XiaohongshuExtractor()

    assert extractor.can_handle("快来看 http://xhslink.com/aBcD")
    assert extractor.can_handle("https://www.xiaohongshu.com/explore/65f1abc123")
    assert not extractor.can_handle("https://mp.weixin.qq.com/s/test")


def test_xiaohongshu_extractor_builds_multimodal_note_package(tmp_path: Path):
    from agents.extractors.xiaohongshu_extractor import XiaohongshuExtractor

    html = """
    <html>
      <head>
        <meta property="og:title" content="AI 产品经理书单">
        <meta name="description" content="这篇笔记整理了 AI 产品经理需要看的几本书，并说明每本书适合解决什么问题。">
        <meta property="og:image" content="https://sns-webpic-qc.xhscdn.com/one.jpg">
        <meta property="og:image" content="https://sns-webpic-qc.xhscdn.com/two.jpg">
      </head>
      <body>
        <script>window.__INITIAL_STATE__ = {"note":{"desc":"正文补充：评测、数据飞轮和产品判断力是主线。"}};</script>
      </body>
    </html>
    """
    image_extractor = FakeImageExtractor()
    extractor = XiaohongshuExtractor(
        image_extractor=image_extractor,
        media_dir=tmp_path,
    )
    extractor._fetch_url = lambda url: ("https://www.xiaohongshu.com/explore/65f1abc123", html)
    extractor._download_image = lambda url, note_id, index: tmp_path / f"{index:02d}.jpg"

    result = extractor.extract("用户分享了 http://xhslink.com/test")

    assert result.source_type == "xiaohongshu"
    assert result.source_url == "https://www.xiaohongshu.com/explore/65f1abc123"
    assert result.title == "AI 产品经理书单"
    assert "# AI 产品经理书单" in result.raw_text
    assert "正文补充：评测、数据飞轮和产品判断力是主线。" in result.raw_text
    assert "## 图片 1" in result.raw_text
    assert "## 图片 2" in result.raw_text
    assert "图中文字 1" in result.raw_text
    assert "图中文字 2" in result.raw_text
    assert image_extractor.paths == [str(tmp_path / "01.jpg"), str(tmp_path / "02.jpg")]


def test_xiaohongshu_extractor_separates_comments_after_topic_tags(tmp_path: Path):
    from agents.extractors.xiaohongshu_extractor import XiaohongshuExtractor

    html = """
    <html>
      <head>
        <meta property="og:title" content="Subagent 的三岔路">
        <meta name="description" content="正文第一段。

正文第二段。

#AI[话题]# #Agent[话题]#

这个逆向角度挺锋利的。

所以 cc 的 harness 做得好">
      </head>
    </html>
    """
    extractor = XiaohongshuExtractor(media_dir=tmp_path)
    extractor._fetch_url = lambda url: ("https://www.xiaohongshu.com/explore/65f1abc123", html)

    result = extractor.extract("http://xhslink.com/test")

    assert "## 正文文案" in result.raw_text
    assert "正文第二段" in result.raw_text
    assert "## 评论区摘录" in result.raw_text
    assert result.raw_text.index("## 评论区摘录") > result.raw_text.index("#AI[话题]#")
    body = result.raw_text.split("## 正文文案", 1)[1].split("## 评论区摘录", 1)[0]
    assert "这个逆向角度挺锋利的" not in body


def test_xiaohongshu_extractor_derives_meaningful_title_from_image_ocr(tmp_path: Path):
    from agents.extractors.xiaohongshu_extractor import XiaohongshuExtractor

    class TitleImageExtractor:
        def extract(self, source: str, **kwargs):
            return ExtractionResult(
                raw_text=(
                    "观察\n"
                    "Subagent 的三岔路\n"
                    "Claude Code、Codex、Cursor 三家做了完全不同的 subagent 设计"
                ),
                source_type="image",
                source_url=source,
            )

    html = """
    <meta property="og:title" content="小红书">
    <meta property="og:image" content="https://sns-webpic-qc.xhscdn.com/one.jpg">
    """
    extractor = XiaohongshuExtractor(
        image_extractor=TitleImageExtractor(),
        media_dir=tmp_path,
    )
    extractor._fetch_url = lambda url: ("https://www.xiaohongshu.com/explore/65f1abc123", html)
    extractor._download_image = lambda url, note_id, index: tmp_path / f"{index:02d}.jpg"

    result = extractor.extract("http://xhslink.com/test")

    assert result.title == "Subagent 的三岔路"
    assert "# Subagent 的三岔路" in result.raw_text


def test_xiaohongshu_extractor_prefers_image_title_over_noisy_share_text(tmp_path: Path):
    from agents.extractors.xiaohongshu_extractor import XiaohongshuExtractor

    class TitleImageExtractor:
        def extract(self, source: str, **kwargs):
            return ExtractionResult(
                raw_text="观察\nSubagent 的三岔路\nClaude Code、Codex、Cursor 三家做了完全不同的设计",
                source_type="image",
                source_url=source,
            )

    html = """
    <meta property="og:title" content="小红书">
    <meta property="og:image" content="https://sns-webpic-qc.xhscdn.com/one.jpg">
    """
    extractor = XiaohongshuExtractor(
        image_extractor=TitleImageExtractor(),
        media_dir=tmp_path,
    )
    extractor._fetch_url = lambda url: ("https://www.xiaohongshu.com/explore/65f1abc123", html)
    extractor._download_image = lambda url, note_id, index: tmp_path / f"{index:02d}.jpg"

    result = extractor.extract(
        "这篇小红书的帖子不错提取总结核心内容观察 | subagent的三岔路 之前写过一篇 Cursor 的文章... http://xhslink.com/test"
    )

    assert result.title == "Subagent 的三岔路"


def test_xiaohongshu_extractor_handles_reordered_meta_and_escaped_images(tmp_path: Path):
    from agents.extractors.xiaohongshu_extractor import XiaohongshuExtractor

    html = r"""
    <html>
      <head>
        <meta content="职场方法论笔记" property="og:title">
        <meta content="这篇笔记讲了如何拆解复杂任务。" name="description">
      </head>
      <body>
        <script>
          window.__INITIAL_STATE__ = {
            "note": {
              "desc": "正文补充：先找主线，再拆证据。",
              "imageList": [
                {"urlDefault": "https:\u002F\u002Fsns-webpic-qc.xhscdn.com\u002Fescaped-one.jpg?imageView2/2/w/1080"},
                {"urlPre": "https:\/\/sns-webpic-qc.xhscdn.com\/escaped-two.webp"}
              ]
            }
          };
        </script>
      </body>
    </html>
    """
    image_extractor = FakeImageExtractor()
    extractor = XiaohongshuExtractor(
        image_extractor=image_extractor,
        media_dir=tmp_path,
    )
    extractor._fetch_url = lambda url: ("https://www.xiaohongshu.com/explore/65f1abc123", html)
    extractor._download_image = lambda url, note_id, index: tmp_path / f"{index:02d}.jpg"

    result = extractor.extract("http://xhslink.com/test")

    assert result.title == "职场方法论笔记"
    assert "这篇笔记讲了如何拆解复杂任务。" in result.raw_text
    assert "正文补充：先找主线，再拆证据。" in result.raw_text
    assert "## 图片 1" in result.raw_text
    assert "## 图片 2" in result.raw_text


def test_xiaohongshu_extractor_does_not_treat_failed_images_as_material():
    from agents.extractors.xiaohongshu_extractor import XiaohongshuExtractor

    html = """
    <meta property="og:image" content="https://sns-webpic-qc.xhscdn.com/one.jpg">
    """
    extractor = XiaohongshuExtractor(image_extractor=FakeImageExtractor())
    extractor._fetch_url = lambda url: ("https://www.xiaohongshu.com/explore/empty", html)
    extractor._download_image = lambda url, note_id, index: None

    result = extractor.extract("https://www.xiaohongshu.com/explore/empty")

    assert result.extraction_failed is True
    assert "小红书内容未能提取" in result.raw_text


def test_xiaohongshu_extractor_reports_actionable_failure():
    from agents.extractors.xiaohongshu_extractor import XiaohongshuExtractor

    extractor = XiaohongshuExtractor()
    extractor._fetch_url = lambda url: ("https://www.xiaohongshu.com/explore/empty", "<html></html>")

    result = extractor.extract("https://www.xiaohongshu.com/explore/empty")

    assert result.extraction_failed is True
    assert "小红书内容未能提取" in result.raw_text
    assert "截图" in result.raw_text

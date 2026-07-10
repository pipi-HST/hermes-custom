from __future__ import annotations

import socket

import httpx
import pytest

from agents.extractors.http_safety import (
    DownloadTooLarge,
    UnsafeURL,
    fetch_public_bytes,
    validate_public_http_url,
)
from agents.extractors.image_extractor import ImageExtractor
from agents.extractors.link_extractor import LinkExtractor
from agents.extractors.xiaohongshu_extractor import XiaohongshuExtractor


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
    ],
)
def test_validate_public_http_url_blocks_non_public_targets(url):
    with pytest.raises(UnsafeURL):
        validate_public_http_url(url)


def test_link_extractor_rejects_private_network_url():
    result = LinkExtractor().extract("http://127.0.0.1:8000/private")

    assert result.extraction_failed is True
    assert result.failure is not None
    assert result.failure.code == "unsafe_source"


def test_image_extractor_rejects_private_network_url():
    result = ImageExtractor(api_key="sk-test").extract("http://127.0.0.1/private.png")

    assert result.extraction_failed is True
    assert result.failure is not None
    assert result.failure.code == "unsafe_source"


def test_fetch_public_bytes_enforces_streamed_size_limit(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_getaddrinfo)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * 11, request=request)
    )

    with httpx.Client(transport=transport) as client:
        with pytest.raises(DownloadTooLarge):
            fetch_public_bytes(
                "https://example.com/file",
                timeout=1,
                max_bytes=10,
                client=client,
            )


def test_fetch_public_bytes_revalidates_redirect_targets(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_getaddrinfo)
    requests = []

    def redirect_to_private(request):
        requests.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/private"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(redirect_to_private)) as client:
        with pytest.raises(UnsafeURL):
            fetch_public_bytes(
                "https://example.com/start",
                timeout=1,
                max_bytes=1024,
                client=client,
            )

    assert requests == ["https://example.com/start"]


def test_fetch_public_bytes_rejects_private_connected_peer(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_getaddrinfo)

    class PrivateNetworkStream:
        def get_extra_info(self, name):
            if name == "server_addr":
                return ("127.0.0.1", 443)
            return None

    def connected_to_private(request):
        return httpx.Response(
            200,
            content=b"secret",
            request=request,
            extensions={"network_stream": PrivateNetworkStream()},
        )

    with httpx.Client(transport=httpx.MockTransport(connected_to_private)) as client:
        with pytest.raises(UnsafeURL):
            fetch_public_bytes(
                "https://example.com/start",
                timeout=1,
                max_bytes=1024,
                client=client,
            )


def test_xiaohongshu_images_enforce_aggregate_download_budget(tmp_path, monkeypatch):
    from agents.extractors import xiaohongshu_extractor as xhs_module

    monkeypatch.setattr(xhs_module, "MAX_TOTAL_IMAGE_BYTES", 10)
    ocr_paths = []

    class RecordingImageExtractor:
        def extract(self, path):
            ocr_paths.append(path)
            return type("Result", (), {"raw_text": "ocr"})()

    extractor = XiaohongshuExtractor(
        image_extractor=RecordingImageExtractor(),
        media_dir=tmp_path,
    )

    def fake_download(_url, note_id, index):
        path = tmp_path / note_id / f"{index}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"123456")
        return path

    monkeypatch.setattr(extractor, "_download_image", fake_download)
    sections = extractor._image_sections(
        ["https://example.com/1.jpg", "https://example.com/2.jpg", "https://example.com/3.jpg"],
        "note",
    )

    assert len(ocr_paths) == 1
    assert any("总下载上限" in section for section in sections)
    assert not (tmp_path / "note" / "2.jpg").exists()


def _public_getaddrinfo(host, port, *_args, **_kwargs):
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", port),
        )
    ]

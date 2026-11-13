"""SSRF guard coverage for the research fetch paths."""
from __future__ import annotations

import socket

import pytest

from agents.extractors.http_safety import UnsafeURL, fetch_public_text


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/health",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/internal",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ],
)
def test_private_and_non_http_targets_are_rejected(url):
    with pytest.raises(UnsafeURL):
        fetch_public_text(url, timeout=2, max_bytes=1024)


def test_dns_resolving_to_localhost_is_rejected(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(UnsafeURL):
        fetch_public_text("http://evil.example/", timeout=2, max_bytes=1024)


def test_research_adapter_uses_the_guard(monkeypatch):
    """The research fetch path must reject private targets, not fetch them."""
    from bridge.research import search

    monkeypatch.setattr(search, "_direct_probe_budget_available", lambda: True)
    assert search._fetch_url_summary("http://127.0.0.1:9/") is None

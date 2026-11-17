"""Batch 3 — DeepSeek fetch must run concurrently, not serially."""
from __future__ import annotations

import time

from agents.research import adapters


def test_deepseek_extract_fetches_urls_concurrently(monkeypatch):
    calls: list[str] = []

    def _slow_fetch(url, **kwargs):
        calls.append(url)
        time.sleep(0.3)
        return {"url": url, "title": "t", "content": f"正文：{url}"}

    monkeypatch.setattr(adapters, "deepseek_web_fetch", _slow_fetch)

    started = time.monotonic()
    result = adapters._deepseek_web_extract(
        ["https://a.example", "https://b.example", "https://c.example"],
        ("key", "https://api.deepseek.com"),
    )
    elapsed = time.monotonic() - started

    assert len(calls) == 3
    assert elapsed < 0.55, f"并发抓取应约等于单次耗时，实际 {elapsed:.2f}s"
    assert result and result["success"] is True
    assert {item["url"] for item in result["results"]} == {
        "https://a.example",
        "https://b.example",
        "https://c.example",
    }


def test_partial_fetch_failure_keeps_good_sources(monkeypatch):
    def _flaky_fetch(url, **kwargs):
        if "bad" in url:
            raise RuntimeError("boom")
        return {"url": url, "title": "t", "content": "可用正文"}

    monkeypatch.setattr(adapters, "deepseek_web_fetch", _flaky_fetch)
    result = adapters._deepseek_web_extract(
        ["https://good.example", "https://bad.example"],
        ("key", "https://api.deepseek.com"),
    )

    assert result is not None
    good = [item for item in result["results"] if item.get("content")]
    assert len(good) == 1

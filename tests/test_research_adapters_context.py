"""Request-local search backend state must not leak across tasks."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from bridge.research import adapters


def test_search_backend_context_is_isolated_per_thread():
    def worker(backend: str) -> tuple[str, str]:
        adapters.reset_search_backend()
        adapters._LAST_SEARCH_BACKEND.set(backend)
        time.sleep(0.02)
        return backend, adapters.current_search_backend()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, ["deepseek", "tavily"]))

    assert results == [("deepseek", "deepseek"), ("tavily", "tavily")]
    assert adapters.current_search_backend() == ""


def test_reset_clears_previous_backend():
    adapters._LAST_SEARCH_BACKEND.set("deepseek")
    assert adapters.current_search_backend() == "deepseek"

    adapters.reset_search_backend()

    assert adapters.current_search_backend() == ""

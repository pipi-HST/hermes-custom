"""Default search and extract adapters for the research pipeline."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Any, Awaitable, Callable

import httpx

from bridge.deepseek_web_tools import (
    deepseek_web_fetch,
    deepseek_web_search,
    resolve_deepseek_credentials,
)
from agents.research.types import (
    ResearchSearchAdapter,
    ResearchExtractAdapter,
    MAX_WEB_RESULTS,
    MAX_EXTRACT_URLS,
    LOCAL_EXTRACT_TIMEOUT_SECONDS,
    LOCAL_EXTRACT_MAX_WORKERS,
    EXTRACT_STAGE_TIMEOUT_SECONDS,
    SEARCH_ONLY_EXTRACT_BACKENDS,
    FALLBACK_SEARCH_BACKENDS,
    MAX_FALLBACK_SEARCH_BACKENDS,
)
from agents.research.utils import (
    _search_query_from_private_context,
    _parse_web_search,
    _parse_web_extract,
    _ensure_main_repo_on_path,
    _stage_timeout,
)
from agents.research.search import _html_text

logger = logging.getLogger(__name__)


def _web_tools_is_stubbed() -> bool:
    """True when a test replaced ``tools.web_tools`` with a fake module."""
    module = sys.modules.get("tools.web_tools")
    return module is not None and not getattr(module, "__file__", None)


def _deepseek_credentials() -> tuple[str, str] | None:
    """Return ``(api_key, root)`` when DeepSeek official tools are configured.

    ``DEEPSEEK_API_KEY`` always wins.  Otherwise the LLM config is used only
    when its base URL actually points at DeepSeek, so pointing the LLM at
    another provider never silently reroutes web search.
    """
    if _web_tools_is_stubbed():
        return None
    env_key = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if env_key:
        key, root = resolve_deepseek_credentials()
        return (key, root) if key else None
    try:
        from agents.capture_agent import load_config

        config = load_config()
    except Exception:
        return None
    api_key = str(getattr(config, "llm_api_key", "") or "").strip()
    base_url = str(getattr(config, "llm_base_url", "") or "").strip()
    if api_key and "deepseek" in base_url.lower():
        return resolve_deepseek_credentials(api_key, base_url)
    return None


def _legacy_search_web(
    query: str,
    warnings: list[str],
    *,
    search_adapter: ResearchSearchAdapter | None,
    extract_adapter: ResearchExtractAdapter | None,
) -> list[dict[str, Any]]:
    if search_adapter is None:
        search_adapter = _default_web_search
    if extract_adapter is None:
        extract_adapter = _default_web_extract
    try:
        raw = search_adapter(_search_query_from_private_context(query), MAX_WEB_RESULTS)
        results = _parse_web_search(raw)
    except Exception as exc:
        warnings.append(f"联网搜索失败：{exc}")
        return []

    urls = [item["url"] for item in results[:MAX_EXTRACT_URLS] if item.get("url")]
    extracted_by_url: dict[str, str] = {}
    if urls:
        try:
            extracted_by_url = _parse_web_extract(extract_adapter(urls))
        except Exception as exc:
            warnings.append(f"网页正文抽取失败，已仅使用搜索摘要：{exc}")

    sources: list[dict[str, Any]] = []
    for index, item in enumerate(results[:MAX_WEB_RESULTS], 1):
        url = item.get("url", "")
        sources.append(
            {
                "ref": f"W{index}",
                "type": "web",
                "id": url or f"web-{index}",
                "title": item.get("title") or url or f"网页来源 {index}",
                "url": url,
                "excerpt": (extracted_by_url.get(url) or item.get("description") or "").strip()[:1800],
                "description": item.get("description") or "",
            }
        )
    return sources


def _default_web_search(query: str, limit: int) -> str | dict[str, Any]:
    reset_search_backend()
    credentials = _deepseek_credentials()
    if credentials:
        api_key, base_url = credentials
        try:
            results = deepseek_web_search(
                query,
                limit=limit,
                api_key=api_key,
                base_url=base_url,
            )
        except Exception as exc:
            logger.warning("[research] DeepSeek web search failed: %s", exc)
            results = []
        if results:
            _LAST_SEARCH_BACKEND.set("deepseek")
            return {"success": True, "data": {"web": results}, "backend": "deepseek"}

    try:
        _ensure_main_repo_on_path()
        from tools.web_tools import web_search_tool
    except Exception as exc:  # runtime checkout not configured on this host
        logger.warning("[research] runtime web search unavailable: %s", exc)
        _LAST_SEARCH_BACKEND.set("unavailable")
        return {
            "success": False,
            "data": {"web": []},
            "backend": "unavailable",
            "error": "HERMES_AGENT_MAIN_ROOT is not set and no sibling hermes-agent-main checkout exists",
        }

    raw = web_search_tool(query, limit=limit)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        raw = parsed if isinstance(parsed, dict) else {"success": True, "raw": raw}
    if isinstance(raw, dict):
        # The caller must be able to tell that DeepSeek was *not* used.
        raw = {**raw, "backend": "fallback"}
    _LAST_SEARCH_BACKEND.set("fallback")
    return raw


_LAST_SEARCH_BACKEND: ContextVar[str] = ContextVar(
    "hermes_last_search_backend", default=""
)


def current_search_backend() -> str:
    """Backend that actually served the most recent search ('' if unknown)."""
    return _LAST_SEARCH_BACKEND.get()


def reset_search_backend() -> None:
    """Clear request-local backend state before starting a new search."""
    _LAST_SEARCH_BACKEND.set("")


def _default_search_backend() -> str:
    if _deepseek_credentials():
        return "deepseek"
    try:
        _ensure_main_repo_on_path()
        from tools import web_tools

        return str(web_tools._get_search_backend() or "").strip().lower()
    except Exception:
        return ""


def _available_fallback_search_backends(primary_backend: str) -> list[str]:
    try:
        _ensure_main_repo_on_path()
        from tools import web_tools
    except Exception:
        return []
    primary = (primary_backend or "").strip().lower()
    backends: list[str] = []
    for backend in FALLBACK_SEARCH_BACKENDS:
        if backend == primary:
            continue
        try:
            if web_tools._is_backend_available(backend):
                backends.append(backend)
        except Exception:
            continue
    return backends[:MAX_FALLBACK_SEARCH_BACKENDS]


def _fallback_search_adapter(backend: str) -> ResearchSearchAdapter:
    def _adapter(query: str, limit: int) -> str | dict[str, Any]:
        return _web_search_with_backend(query, limit, backend)

    return _adapter


def _web_search_with_backend(query: str, limit: int, backend: str) -> str | dict[str, Any]:
    reset_search_backend()
    _ensure_main_repo_on_path()
    from agent.web_search_registry import get_provider
    from tools import web_tools

    web_tools._ensure_builtin_web_provider_registered(backend)
    provider = get_provider(backend)
    if provider is None or not provider.supports_search():
        raise RuntimeError(f"搜索后端不可用：{backend}")
    result = provider.search(query, limit)
    _LAST_SEARCH_BACKEND.set(backend)
    if isinstance(result, dict):
        return {**result, "backend": backend}
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            return {**parsed, "backend": backend}
        if isinstance(parsed, list):
            return {"success": True, "results": parsed, "backend": backend}
    return {"success": True, "raw": result, "backend": backend}


def _deepseek_web_extract(
    urls: list[str],
    credentials: tuple[str, str],
) -> dict[str, Any] | None:
    """Extract pages with DeepSeek's official fetch API.

    Returns ``None`` when nothing could be fetched so the caller can fall back
    to the legacy/local extraction path.
    """
    selected_urls = [url for url in urls[:MAX_EXTRACT_URLS] if url]
    if not selected_urls:
        return None
    api_key, base_url = credentials
    stage_timeout = _stage_timeout(EXTRACT_STAGE_TIMEOUT_SECONDS)
    per_url_timeout = max(1.0, min(20.0, stage_timeout / max(1, len(selected_urls))))

    def _fetch_one(url: str) -> dict[str, Any]:
        try:
            fetched = deepseek_web_fetch(
                url,
                api_key=api_key,
                base_url=base_url,
                timeout=per_url_timeout,
            )
        except Exception as exc:
            return {"url": url, "content": "", "raw_content": "", "error": str(exc)}
        content = str(fetched.get("content") or "")
        if not content:
            return {
                "url": url,
                "content": "",
                "raw_content": "",
                "error": "empty deepseek fetch content",
            }
        return {"url": url, "content": content, "raw_content": content}

    # Serial fetching made the extract stage sum of N round trips; the API
    # allows 25 fetch QPS per account, so four in flight is safe.
    workers = max(1, min(4, len(selected_urls)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results: list[dict[str, Any]] = list(pool.map(_fetch_one, selected_urls))
    if not any(item.get("content") for item in results):
        return None
    return {
        "success": True,
        "results": results,
        "meta": {
            "extract_backend": "deepseek",
            "allow_summary_fallback": True,
        },
    }


def _default_web_extract(
    urls: list[str],
    *,
    local_extract: Callable[..., dict[str, Any]] | None = None,
) -> str | dict[str, Any]:
    credentials = _deepseek_credentials()
    if credentials:
        extracted = _deepseek_web_extract(urls, credentials)
        if extracted is not None:
            return extracted

    _ensure_main_repo_on_path()
    from tools import web_tools

    extract_backend = ""
    try:
        extract_backend = str(web_tools._get_extract_backend() or "").strip().lower()
    except Exception:
        extract_backend = ""
    if not extract_backend or extract_backend in SEARCH_ONLY_EXTRACT_BACKENDS:
        local_extract = local_extract or _local_web_extract
        return local_extract(urls, extract_backend=extract_backend)

    selected_urls = [url for url in urls[:MAX_EXTRACT_URLS] if url]
    if not selected_urls:
        return _web_extract_failure_result([], extract_backend=extract_backend, error="")
    timeout_seconds = _stage_timeout(EXTRACT_STAGE_TIMEOUT_SECONDS)
    if timeout_seconds < 1.0:
        return _web_extract_failure_result(
            selected_urls,
            extract_backend=extract_backend,
            error="research budget exhausted before extraction",
        )
    try:
        result = _run_callable_with_timeout(
            lambda: web_tools.web_extract_tool(selected_urls, format="markdown", use_llm_processing=False),
            timeout_seconds=timeout_seconds,
            label="web extract",
        )
        if asyncio.iscoroutine(result):
            result = _run_coroutine_with_timeout(result, timeout_seconds=timeout_seconds)
        return result
    except Exception as exc:
        return _web_extract_failure_result(
            selected_urls,
            extract_backend=extract_backend,
            error=str(exc),
        )


def _local_web_extract(urls: list[str], *, extract_backend: str = "") -> dict[str, Any]:
    selected_urls = [url for url in urls[:MAX_EXTRACT_URLS] if url]
    results: list[dict[str, Any]] = []
    headers = {"User-Agent": "HermesResearchBot/1.0"}
    timeout_seconds = _stage_timeout(LOCAL_EXTRACT_TIMEOUT_SECONDS)
    if timeout_seconds < 1.0:
        return {
            "success": True,
            "results": [
                {"url": url, "content": "", "error": "research budget exhausted before extraction"}
                for url in selected_urls
            ],
            "meta": {
                "extract_backend": extract_backend,
                "fallback_extract_backend": "local_http",
                "allow_summary_fallback": True,
            },
        }
    if not selected_urls:
        return {
            "success": True,
            "results": [],
            "meta": {
                "extract_backend": extract_backend,
                "fallback_extract_backend": "local_http",
                "allow_summary_fallback": True,
            },
        }

    def fetch(index: int, url: str) -> tuple[int, dict[str, Any]]:
        try:
            from agents.extractors.http_safety import fetch_public_text

            _, text = fetch_public_text(
                url,
                timeout=timeout_seconds,
                max_bytes=2_000_000,
                headers=headers,
            )
            content = _extract_readable_text(text, url=url)
            if not content:
                raise RuntimeError("empty extracted content")
            return index, {"url": url, "content": content}
        except Exception as exc:
            return index, {"url": url, "content": "", "error": str(exc)}

    ordered: list[dict[str, Any] | None] = [None] * len(selected_urls)
    max_workers = max(1, min(len(selected_urls), LOCAL_EXTRACT_MAX_WORKERS))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch, index, url) for index, url in enumerate(selected_urls)]
        try:
            for future in as_completed(futures, timeout=timeout_seconds + 1.0):
                index, item = future.result()
                ordered[index] = item
        except FuturesTimeoutError:
            for future in futures:
                future.cancel()
    for index, item in enumerate(ordered):
        results.append(item or {"url": selected_urls[index], "content": "", "error": "local extract timed out"})
    return {
        "success": True,
        "results": results,
        "meta": {
            "extract_backend": extract_backend,
            "fallback_extract_backend": "local_http",
            "allow_summary_fallback": True,
        },
    }


def _extract_readable_text(html: str, *, url: str) -> str:
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        if extracted and extracted.strip():
            return re.sub(r"\s+", " ", extracted).strip()[:12000]
    except Exception:
        pass
    try:
        from readability import Document

        summary_html = Document(html).summary()
        text = _html_text(summary_html)
        if text:
            return text[:12000]
    except Exception:
        pass
    text = _html_text(re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S))
    return text[:12000]


def _run_callable_with_timeout(
    func: Callable[[], str | dict[str, Any] | Awaitable[str | dict[str, Any]]],
    *,
    timeout_seconds: float,
    label: str,
) -> str | dict[str, Any] | Awaitable[str | dict[str, Any]]:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"{label} timed out after {timeout_seconds:.1f}s") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _run_coroutine_with_timeout(
    coro: Awaitable[str | dict[str, Any]],
    *,
    timeout_seconds: float,
) -> str | dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout_seconds))
    return _run_coroutine_in_thread(coro, timeout_seconds=timeout_seconds)


def _run_coroutine_in_thread(
    coro: Awaitable[str | dict[str, Any]],
    *,
    timeout_seconds: float,
) -> str | dict[str, Any]:
    return _run_callable_with_timeout(
        lambda: asyncio.run(asyncio.wait_for(coro, timeout=timeout_seconds)),
        timeout_seconds=timeout_seconds,
        label="web extract coroutine",
    )


def _web_extract_failure_result(
    urls: list[str],
    *,
    extract_backend: str,
    error: str,
) -> dict[str, Any]:
    return {
        "success": True,
        "results": [
            {
                "url": url,
                "content": "",
                "error": error or "web extraction unavailable",
            }
            for url in urls
        ],
        "meta": {
            "extract_backend": extract_backend,
            "allow_summary_fallback": True,
        },
    }

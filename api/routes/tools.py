"""Whitelisted bridge-tool dispatch for the runtime's envelope tools.

The runtime's envelope tools run ``call_custom_bridge(module, function, ...)``
locally.  In HTTP mode there is no local checkout, so they come through
``POST /api/tools/{module_name}/{function_name}`` instead.

This module owns the *only* modules/functions the service will ever execute:

* the module name must be a key of ``_BRIDGE_TOOL_WHITELIST``; the import path
  comes from that table (never from the request), so request data can never
  choose a module nor inject an import string;
* the function must be in that module's allowed set, so even a listed module
  cannot be used to reach arbitrary attributes such as ``os.system``.

Security: the route sits behind the app's auth middleware (Bearer token or
loopback client), exactly like every other route.  ``context`` and
``token_verified`` cannot be supplied by the caller: the dispatch layer always
drops the caller's ``context`` (it injects the service's own
``KnowledgeContext``) and drops ``token_verified`` so the tool surface cannot
bypass the confirmation gate.

Latency: bridge functions may run an LLM or a long pipeline (tens of seconds,
occasionally minutes).  The endpoint is a synchronous ``def`` handler, so
FastAPI runs it in the threadpool and the HTTP client must use a generous
timeout; there is deliberately no request-level timeout or cancellation here.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agents.extractors.input_policy import looks_like_local_path
from bridge.ai_cancel import AIOperationCancelled

logger = logging.getLogger(__name__)

# module name used by the runtime tool -> (fixed import path, allowed functions)
_BRIDGE_TOOL_WHITELIST: dict[str, tuple[str, frozenset[str]]] = {
    "agent_run_bridge": (
        "bridge.agent_run_bridge",
        frozenset({"get_agent_run_status"}),
    ),
    "answer_bridge": (
        "bridge.answer_bridge",
        frozenset({"revise_answer_draft", "save_answer_to_knowledge"}),
    ),
    "canvas_bridge": (
        "bridge.canvas_bridge",
        frozenset({"generate_canvas"}),
    ),
    "capture_bridge": (
        "bridge.capture_bridge",
        frozenset(
            {
                "capture_content",
                "commit_capture_draft",
                "create_editable_capture_draft",
                "revise_capture_draft",
                "show_capture_draft",
            }
        ),
    ),
    "garden_bridge": (
        "bridge.garden_bridge",
        frozenset(
            {
                "commit_garden_draft",
                "revise_garden_draft",
                "synthesize_garden",
            }
        ),
    ),
    "librarian_bridge": (
        "bridge.librarian_bridge",
        frozenset({"run_librarian_audit"}),
    ),
    "rag_bridge": (
        "bridge.rag_bridge",
        frozenset({"search_knowledge_base"}),
    ),
    "rag_continue_bridge": (
        "bridge.rag_continue_bridge",
        frozenset({"continue_latest_rag_answer"}),
    ),
    "review_bridge": (
        "bridge.review_bridge",
        frozenset(
            {
                "expand_latest_review_followup",
                "record_review_feedback",
                "run_daily_review",
            }
        ),
    ),
    "theme_bridge": (
        "bridge.theme_bridge",
        frozenset(
            {
                "list_theme_candidates",
                "run_graph_report",
                "run_theme_scan",
            }
        ),
    ),
    "reading_bridge": (
        "bridge.reading_bridge",
        frozenset(
            {
                "commit_reading_artifact",
                "discuss_reading",
                "show_reading_session",
                "start_reading_session",
                "summarize_reading_session",
            }
        ),
    ),
    "research_bridge": (
        "bridge.research_bridge",
        frozenset({"async_light_web_answer", "async_web_assist_answer"}),
    ),
}

_MODULE_CACHE: dict[str, Any] = {}
_DROPPED_KWARGS = ("context", "token_verified", "before_commit", "expected_version")
# These tool calls carry the existing chat confirmation phrase, not authority
# to enter a token-verified service path. Bridge signatures may change without
# exposing additional parameters over this API.
_CONFIRMING_TOOL_ARGUMENTS = {
    ("capture_bridge", "commit_capture_draft"): ("draft_id", "confirmation"),
    ("garden_bridge", "commit_garden_draft"): ("draft_id", "confirmation"),
    ("reading_bridge", "commit_reading_artifact"): ("draft_id", "confirmation"),
    ("librarian_bridge", "run_librarian_audit"): ("apply", "export_file", "confirmation"),
}


class ToolCallRequest(BaseModel):
    args: list = Field(default_factory=list)
    kwargs: dict = Field(default_factory=dict)


def create_tools_router(
    capture_repo,
    vault_path: str = "~/Hermes/knowledge",
    config: object | None = None,
) -> APIRouter:
    router = APIRouter()
    vault = Path(vault_path).expanduser()

    def _context():
        if config is None:
            return None
        from bridge.context import KnowledgeContext

        return KnowledgeContext(config=config, conn=capture_repo.conn, vault_path=vault)

    @router.post("/tools/{module_name}/{function_name}")
    def dispatch_tool(
        module_name: str,
        function_name: str,
        body: ToolCallRequest,
    ):
        """Run one whitelisted bridge function synchronously.

        Long-running bridge functions (LLM pipelines) block the worker thread;
        clients must use a generous timeout.  Only allowlisted
        ``(module, function)`` pairs are reachable and the caller cannot supply
        ``context`` or ``token_verified``.
        """
        module_key = (module_name or "").strip()
        function_key = (function_name or "").strip()
        entry = _BRIDGE_TOOL_WHITELIST.get(module_key)
        if entry is None or function_key not in entry[1]:
            raise HTTPException(status_code=404, detail="unknown tool")

        module = _load_whitelisted_module(module_key)
        function = getattr(module, function_key, None)
        if not callable(function):
            raise HTTPException(status_code=404, detail="unknown tool")

        kwargs = dict(body.kwargs)
        for dropped in _DROPPED_KWARGS:
            kwargs.pop(dropped, None)
        public = _CONFIRMING_TOOL_ARGUMENTS.get((module_key, function_key))
        if public is not None and (
            len(body.args) > (0 if module_key == "librarian_bridge" else len(public))
            or not kwargs.keys() <= set(public)
        ):
            raise HTTPException(status_code=422, detail="unexpected confirmation tool argument")
        bound = _bind_public_tool_arguments(function, body.args, kwargs)
        if (
            module_key == "capture_bridge"
            and function_key in {"capture_content", "create_editable_capture_draft"}
        ):
            content = bound.arguments.get("content")
            if isinstance(content, str) and looks_like_local_path(content):
                raise HTTPException(status_code=400, detail="local file capture is not allowed through the API")
        parameters = bound.signature.parameters
        if "context" in parameters or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
        ):
            kwargs["context"] = _context()

        try:
            result = function(*body.args, **kwargs)
            if inspect.isawaitable(result):
                result = asyncio.run(result)
        except (HTTPException, AIOperationCancelled):
            raise
        except Exception as exc:  # noqa: BLE001 - tool failures become structured 500s
            logger.exception(
                "bridge tool %s.%s failed", module_key, function_key
            )
            raise HTTPException(
                status_code=500,
                detail={"error_type": type(exc).__name__, "message": str(exc)},
            ) from None

        try:
            json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            logger.exception(
                "bridge tool %s.%s returned a non-JSON payload",
                module_key,
                function_key,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error_type": "SerializationError",
                    "message": f"tool returned non-JSON-serializable value: {exc}",
                },
            ) from None
        return result

    return router


def _load_whitelisted_module(module_key: str):
    """Import (once) the module registered for ``module_key``.

    ``module_key`` must already be a key of the whitelist; the import path is
    read from the table so user input never reaches ``importlib``.
    """
    module = _MODULE_CACHE.get(module_key)
    if module is None:
        module_path = _BRIDGE_TOOL_WHITELIST[module_key][0]
        module = importlib.import_module(module_path)
        _MODULE_CACHE[module_key] = module
    return module


def _bind_public_tool_arguments(function, args, kwargs) -> inspect.BoundArguments:
    """Validate the effective binding, not just the wire keyword names."""
    try:
        signature = inspect.signature(function)
        bound = signature.bind_partial(*args, **kwargs)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="invalid tool arguments") from None
    if any(name in bound.arguments for name in _DROPPED_KWARGS):
        raise HTTPException(status_code=422, detail="reserved tool argument")
    return bound

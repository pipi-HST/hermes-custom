"""Cross-process Hermes Knowledge pipeline E2E diagnostics.

Only external AI providers are replaced by a local OpenAI-compatible server.
Knowledge API, Hermes Runtime, bridges, agents, SQLite, Markdown storage and
the Runtime library APIs run as real processes against one isolated workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(
    os.environ.get(
        "HERMES_RUNTIME_ROOT",
        "/Users/pipi/Downloads/hermes-agent-main",
    )
).expanduser()
E2E_SIGNAL = "HERMES-E2E-PIPELINE-SIGNAL"
BROWSER_E2E_SIGNAL = "HERMES-E2E-BROWSER-SIGNAL"
BROWSER_E2E_TITLE = "Browser Real Pipeline Article"

ProductRunner = Callable[..., dict[str, Any]]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_pipeline_e2e(
    *,
    work_dir: str | Path | None = None,
    faults: set[str] | None = None,
    live_providers: bool = False,
    product_runner: ProductRunner | None = None,
) -> dict[str, Any]:
    temp_dir = None
    if work_dir is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="hermes-pipeline-e2e-")
        root = Path(temp_dir.name)
    else:
        root = Path(work_dir).expanduser()
        root.mkdir(parents=True, exist_ok=True)

    if live_providers and faults:
        raise ValueError("fault injection is only available with local providers")
    fake_server = None if live_providers else _FakeProviderServer(faults=faults or set())
    processes: list[subprocess.Popen] = []
    logs: list[Any] = []
    started = time.perf_counter()
    try:
        if fake_server is not None:
            fake_server.start()
            provider_settings = {
                "provider": "openai",
                "model": "hermes-e2e-model",
                "base_url": fake_server.base_url,
                "api_key": "e2e-key",
                "qwen_api_key": "e2e-embedding-key",
            }
            embedding_base_url = fake_server.base_url
        else:
            provider_settings = _live_provider_settings()
            embedding_base_url = ""
        paths = _prepare_isolated_root(root, provider_settings)
        api_port = _free_port()
        runtime_port = _free_port()
        env = _subprocess_env(paths, embedding_base_url)
        api_process, api_log = _start_process(
            [
                str(PROJECT_ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            cwd=paths["custom_root"],
            env=env,
            log_path=root / "knowledge-api.log",
        )
        runtime_process, runtime_log = _start_process(
            [
                str(RUNTIME_ROOT / ".venv/bin/python"),
                "-m",
                "hermes_cli.main",
                "dashboard",
                "--no-open",
                "--host",
                "127.0.0.1",
                "--port",
                str(runtime_port),
            ],
            cwd=RUNTIME_ROOT,
            env=env,
            log_path=root / "runtime-dashboard.log",
        )
        processes.extend([api_process, runtime_process])
        logs.extend([api_log, runtime_log])

        api_base = f"http://127.0.0.1:{api_port}"
        runtime_base = f"http://127.0.0.1:{runtime_port}"
        _wait_for_http(f"{api_base}/docs", api_process, root / "knowledge-api.log")
        _wait_for_http(
            f"{runtime_base}/knowledge-chat",
            runtime_process,
            root / "runtime-dashboard.log",
            timeout=90,
        )
        runtime_headers = _runtime_headers(runtime_base)

        with httpx.Client(timeout=90) as client:
            capture = _capture_pipeline(client, api_base, paths)
            runtime_visibility = _runtime_visibility_pipeline(
                client,
                runtime_base,
                runtime_headers,
                capture,
            )
            reading = _reading_pipeline(client, runtime_base, runtime_headers, paths)
            garden = _garden_pipeline(
                client,
                runtime_base,
                runtime_headers,
                paths,
                capture,
            )
            rag = _rag_pipeline(paths, env, capture)
            theme = _theme_pipeline(paths, env)
            review = _review_pipeline(paths, env)
            canvas = _canvas_pipeline(paths, env, capture, garden)
            graph = _graph_pipeline(client, api_base, capture, garden)

        pipelines = [capture, runtime_visibility, reading, garden, rag, theme, review, canvas, graph]
        product_acceptance: dict[str, Any] | None = None
        if product_runner is not None:
            runner_result = product_runner(
                runtime_base=runtime_base,
                runtime_headers=runtime_headers,
                paths=paths,
                env=env,
            )
            with httpx.Client(timeout=90) as client:
                browser_capture = _browser_created_capture_pipeline(
                    client,
                    runtime_base,
                    runtime_headers,
                    paths,
                    runner_result,
                )
            browser_rag = _rag_pipeline(
                paths,
                env,
                browser_capture,
                query_signal=BROWSER_E2E_SIGNAL,
            )
            browser_rag["name"] = "browser_created_capture_rag"
            pipelines.extend([browser_capture, browser_rag])
            product_acceptance = {
                "ok": browser_capture["ok"] and browser_rag["ok"],
                "runner": runner_result,
                "pipelines": [browser_capture, browser_rag],
            }
        return {
            "ok": all(item["ok"] for item in pipelines),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "work_dir": str(root),
            "pipelines": pipelines,
            "product_acceptance": product_acceptance,
            "external_boundaries": {
                "llm": "live_provider" if live_providers else "local_openai_compatible_http",
                "embeddings": "live_provider" if live_providers else "local_openai_compatible_http",
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "work_dir": str(root),
            "pipelines": [],
            "orchestrator_error": f"{type(exc).__name__}: {exc}",
            "logs": {
                "knowledge_api": _tail(root / "knowledge-api.log"),
                "runtime_dashboard": _tail(root / "runtime-dashboard.log"),
            },
        }
    finally:
        for process in reversed(processes):
            _stop_process(process)
        for handle in logs:
            handle.close()
        if fake_server is not None:
            fake_server.stop()
        if temp_dir is not None:
            temp_dir.cleanup()


def _capture_pipeline(client: httpx.Client, api_base: str, paths: dict[str, Path]) -> dict[str, Any]:
    source = (
        f"# E2E Pipeline Article\n\n{E2E_SIGNAL}。"
        "这篇材料验证 Capture 从 HTTP 入口生成草稿，确认后写入原始证据和正式正文。"
        "正式正文需要目录、章节、自然分段和重点加粗，并能够被下游检索。"
    )
    create_response = client.post(
        f"{api_base}/api/capture-drafts",
        json={"content": source, "instruction": "全文入库"},
    )
    created = _json_response(create_response)
    draft_id = str((created.get("meta") or {}).get("draft_id") or "")
    draft_response = client.get(f"{api_base}/api/capture-drafts/{draft_id}") if draft_id else None
    draft = _json_response(draft_response) if draft_response is not None else {}
    commit_response = client.post(f"{api_base}/api/capture-drafts/{draft_id}/commit") if draft_id else None
    committed = _json_response(commit_response) if commit_response is not None else {}
    meta = committed.get("meta") if isinstance(committed.get("meta"), dict) else {}
    capture_id = str(meta.get("capture_id") or "")
    capture_response = client.get(f"{api_base}/api/captures/{capture_id}") if capture_id else None
    capture_item = _json_response(capture_response) if capture_response is not None else {}
    raw_path = paths["vault"] / str(meta.get("raw_path") or "")
    capture_path = paths["vault"] / str(meta.get("file_path") or "")
    raw_text = raw_path.read_text(encoding="utf-8") if raw_path.is_file() else ""
    capture_text = capture_path.read_text(encoding="utf-8") if capture_path.is_file() else ""

    with sqlite3.connect(paths["db_path"]) as conn:
        draft_row = conn.execute(
            "SELECT status, committed_capture_id FROM capture_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone() if draft_id else None
        capture_row = conn.execute("SELECT id FROM captures WHERE id = ?", (capture_id,)).fetchone() if capture_id else None
        metadata_row = conn.execute(
            "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
            (capture_id,),
        ).fetchone() if capture_id else None
        embedding_kinds = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT kind FROM capture_embeddings WHERE capture_id = ? AND embedding IS NOT NULL",
                (capture_id,),
            ).fetchall()
        } if capture_id else set()
        write_job = conn.execute(
            "SELECT status, step FROM knowledge_write_jobs WHERE object_id = ? ORDER BY created_at DESC LIMIT 1",
            (draft_id,),
        ).fetchone() if draft_id else None

    checks = [
        _check("create_http_success", create_response.status_code == 200 and created.get("status") == "success", created.get("errors")),
        _check("draft_id_returned", bool(draft_id), draft_id),
        _check("draft_persisted", bool(draft_row), draft_row),
        _check("raw_source_preserved_before_commit", E2E_SIGNAL in str(draft.get("raw_text") or ""), draft.get("status")),
        _check("commit_http_success", commit_response is not None and commit_response.status_code == 200 and committed.get("status") == "success", committed.get("errors")),
        _check("draft_transitioned_to_committed", bool(draft_row and draft_row[0] == "committed" and draft_row[1] == capture_id), draft_row),
        _check("capture_row_created", bool(capture_row), capture_id),
        _check("raw_file_written", raw_path.is_file() and E2E_SIGNAL in raw_text, str(raw_path)),
        _check("capture_file_written", capture_path.is_file() and E2E_SIGNAL in capture_text, str(capture_path)),
        _check("presentation_contract", _presentation_contract_ready(capture_text), None),
        _check("capture_api_readback", E2E_SIGNAL in str(capture_item.get("body") or ""), capture_item.get("id")),
        _check("outline_metadata_written", bool(metadata_row and metadata_row[0]), None),
        _check("identity_and_section_embeddings_written", {"identity", "section"} <= embedding_kinds, sorted(embedding_kinds)),
        _check("write_job_completed", bool(write_job and write_job[0] == "completed" and write_job[1] == "completed"), write_job),
    ]
    return _pipeline(
        "capture_http_to_storage",
        checks,
        {"draft_id": draft_id, "capture_id": capture_id, "file_path": str(meta.get("file_path") or "")},
    )


def _runtime_visibility_pipeline(
    client: httpx.Client,
    runtime_base: str,
    headers: dict[str, str],
    capture: dict[str, Any],
) -> dict[str, Any]:
    draft_id = str(capture.get("details", {}).get("draft_id") or "")
    capture_id = str(capture.get("details", {}).get("capture_id") or "")
    drafts_response = client.get(f"{runtime_base}/api/knowledge/capture-drafts?limit=50", headers=headers)
    drafts_payload = _json_response(drafts_response)
    library_response = client.get(
        f"{runtime_base}/api/knowledge/library/items",
        headers=headers,
        params={"search": "E2E Pipeline Article", "limit": 50},
    )
    library_payload = _json_response(library_response)
    detail_response = client.get(
        f"{runtime_base}/api/knowledge/library/items/capture/{capture_id}",
        headers=headers,
    ) if capture_id else None
    detail_payload = _json_response(detail_response) if detail_response is not None else {}
    drafts = drafts_payload.get("drafts") or []
    items = library_payload.get("items") or []
    capture_item = next(
        (item for item in items if str(item.get("id") or "") == capture_id),
        {},
    )
    detail_text = json.dumps(detail_payload, ensure_ascii=False)
    checks = [
        _check("runtime_drafts_http_success", drafts_response.status_code == 200, drafts_payload),
        _check("committed_draft_visible", any(str(item.get("id") or "") == draft_id and item.get("status") == "committed" for item in drafts), draft_id),
        _check("runtime_library_http_success", library_response.status_code == 200, library_payload),
        _check("capture_visible_in_library", bool(capture_item), capture_id),
        _check("runtime_library_reports_indexed", capture_item.get("indexed") is True, capture_item.get("indexed")),
        _check("runtime_detail_contains_capture_body", detail_response is not None and detail_response.status_code == 200 and E2E_SIGNAL in detail_text, None),
    ]
    return _pipeline("runtime_library_visibility", checks, {"draft_id": draft_id, "capture_id": capture_id})


def _browser_created_capture_pipeline(
    client: httpx.Client,
    runtime_base: str,
    headers: dict[str, str],
    paths: dict[str, Path],
    runner_result: dict[str, Any],
) -> dict[str, Any]:
    capture_path = next(
        (
            path
            for path in sorted((paths["vault"] / "captures").glob("*.md"))
            if BROWSER_E2E_SIGNAL in path.read_text(encoding="utf-8", errors="replace")
        ),
        None,
    )
    capture_text = capture_path.read_text(encoding="utf-8") if capture_path is not None else ""
    relative_path = capture_path.relative_to(paths["vault"]).as_posix() if capture_path is not None else ""
    capture_id = ""
    draft_row = None
    metadata_row = None
    embedding_kinds: set[str] = set()
    with sqlite3.connect(paths["db_path"]) as conn:
        capture_row = conn.execute(
            "SELECT id FROM captures WHERE file_path = ?",
            (relative_path,),
        ).fetchone() if relative_path else None
        capture_id = str(capture_row[0]) if capture_row else ""
        draft_row = conn.execute(
            "SELECT status, committed_capture_id FROM capture_drafts WHERE committed_capture_id = ?",
            (capture_id,),
        ).fetchone() if capture_id else None
        metadata_row = conn.execute(
            "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
            (capture_id,),
        ).fetchone() if capture_id else None
        embedding_kinds = {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT kind FROM capture_embeddings WHERE capture_id = ? AND embedding IS NOT NULL",
                (capture_id,),
            ).fetchall()
        } if capture_id else set()

    library_response = client.get(
        f"{runtime_base}/api/knowledge/library/items",
        headers=headers,
        params={"search": BROWSER_E2E_TITLE, "limit": 50},
    )
    library_payload = _json_response(library_response)
    library_items = library_payload.get("items") or []
    detail_response = client.get(
        f"{runtime_base}/api/knowledge/library/items/capture/{capture_id}",
        headers=headers,
    ) if capture_id else None
    detail_payload = _json_response(detail_response) if detail_response is not None else {}
    checks = [
        _check("real_browser_process_succeeded", runner_result.get("ok") is True, runner_result.get("output_tail")),
        _check("browser_capture_file_written", capture_path is not None and capture_path.is_file(), relative_path),
        _check("browser_capture_signal_preserved", BROWSER_E2E_SIGNAL in capture_text, relative_path),
        _check("browser_capture_row_created", bool(capture_id), capture_id),
        _check("browser_draft_committed", bool(draft_row and draft_row[0] == "committed" and draft_row[1] == capture_id), draft_row),
        _check("browser_presentation_contract", _presentation_contract_ready(capture_text), None),
        _check("browser_outline_metadata_written", bool(metadata_row and metadata_row[0]), None),
        _check("browser_identity_and_section_embeddings_written", {"identity", "section"} <= embedding_kinds, sorted(embedding_kinds)),
        _check(
            "browser_capture_visible_in_runtime_library",
            library_response.status_code == 200
            and any(str(item.get("id") or "") == capture_id for item in library_items),
            capture_id,
        ),
        _check(
            "browser_runtime_detail_contains_capture",
            detail_response is not None
            and detail_response.status_code == 200
            and BROWSER_E2E_SIGNAL in json.dumps(detail_payload, ensure_ascii=False),
            None,
        ),
    ]
    return _pipeline(
        "browser_real_capture_to_library",
        checks,
        {"capture_id": capture_id, "file_path": relative_path},
    )


def _reading_pipeline(
    client: httpx.Client,
    runtime_base: str,
    headers: dict[str, str],
    paths: dict[str, Path],
) -> dict[str, Any]:
    before_count = _table_count(paths["db_path"], "captures")
    create_response = client.post(
        f"{runtime_base}/api/knowledge/answer-drafts",
        headers=headers,
        json={
            "hermes_session_id": "pipeline-e2e",
            "user_message": "E2E Pipeline Article 应该怎样验证？",
            "assistant_answer": (
                f"{E2E_SIGNAL}。这篇材料验证 Capture 从 HTTP 入口生成草稿，"
                "确认后写入原始证据和正式正文。正式正文需要目录、章节、自然分段和重点加粗，"
                "并能够被下游检索；验收还必须检查 SQLite、identity/section embedding、"
                "Runtime 已入库可见性和 RAG 索引检索，不能只检查 status=success。"
                f"测试副本标识：{E2E_SIGNAL}-READING"
            ),
            "instruction": "存成学习笔记",
            "output_type": "local_learning_note",
            "sources": [],
        },
    )
    created = _json_response(create_response)
    draft_id = str((created.get("meta") or {}).get("reading_draft_id") or "")
    commit_response = client.post(
        f"{runtime_base}/api/knowledge/reading-drafts/{draft_id}/commit",
        headers=headers,
    ) if draft_id else None
    committed = _json_response(commit_response) if commit_response is not None else {}
    meta = committed.get("meta") if isinstance(committed.get("meta"), dict) else {}
    file_path = paths["vault"] / str(meta.get("file_path") or "")
    body = file_path.read_text(encoding="utf-8") if file_path.is_file() else ""
    with sqlite3.connect(paths["db_path"]) as conn:
        row = conn.execute("SELECT status FROM reading_drafts WHERE id = ?", (draft_id,)).fetchone() if draft_id else None
    checks = [
        _check("reading_draft_http_success", create_response.status_code == 200 and created.get("status") == "success", created.get("errors")),
        _check("reading_draft_id_returned", bool(draft_id), draft_id),
        _check("reading_commit_http_success", commit_response is not None and commit_response.status_code == 200 and committed.get("status") == "success", committed.get("errors")),
        _check("reading_draft_committed", bool(row and row[0] == "committed"), row),
        _check("reading_created_capture", _table_count(paths["db_path"], "captures") == before_count + 1, meta.get("capture_id")),
        _check("reading_markdown_written", file_path.is_file() and f"{E2E_SIGNAL}-READING" in body, str(file_path)),
    ]
    return _pipeline("reading_http_to_capture", checks, {"draft_id": draft_id, "capture_id": meta.get("capture_id")})


def _garden_pipeline(
    client: httpx.Client,
    runtime_base: str,
    headers: dict[str, str],
    paths: dict[str, Path],
    capture: dict[str, Any],
) -> dict[str, Any]:
    capture_id = str(capture.get("details", {}).get("capture_id") or "")
    response = client.post(
        f"{runtime_base}/api/knowledge/garden-drafts",
        headers=headers,
        json={
            "capture_ids": [capture_id],
            "title": "Pipeline E2E Card",
            "card_kind": "method",
            "instruction": "提炼为可复用的端到端验收方法",
        },
    )
    payload = _json_response(response)
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    card_id = str(meta.get("card_id") or meta.get("garden_card_id") or "")
    cards_response = client.get(f"{runtime_base}/api/knowledge/cards", headers=headers)
    cards_payload = _json_response(cards_response)
    cards = cards_payload.get("cards") or cards_payload.get("items") or []
    with sqlite3.connect(paths["db_path"]) as conn:
        row = conn.execute("SELECT status, capture_ids FROM garden_cards WHERE id = ?", (card_id,)).fetchone() if card_id else None
    checks = [
        _check("garden_http_success", response.status_code == 200 and payload.get("status") == "success", payload.get("errors")),
        _check("garden_card_id_returned", bool(card_id), meta),
        _check("garden_card_persisted", bool(row and row[0] in {"seed", "accepted"} and capture_id in str(row[1] or "")), row),
        _check("garden_card_visible", cards_response.status_code == 200 and any(str(item.get("id") or "") == card_id for item in cards), card_id),
    ]
    return _pipeline("garden_http_to_card", checks, {"card_id": card_id, "capture_id": capture_id})


def _rag_pipeline(
    paths: dict[str, Path],
    env: dict[str, str],
    capture: dict[str, Any],
    *,
    query_signal: str = E2E_SIGNAL,
) -> dict[str, Any]:
    capture_id = str(capture.get("details", {}).get("capture_id") or "")
    code = (
        "import json; "
        "from bridge.rag_bridge import search_knowledge_base; "
        f"result=search_knowledge_base('{query_signal} 的端到端验收结论是什么？'); "
        "print('E2E_RESULT='+json.dumps(result, ensure_ascii=False))"
    )
    completed = subprocess.run(
        [str(PROJECT_ROOT / ".venv/bin/python"), "-c", code],
        cwd=paths["custom_root"],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    result = _result_from_subprocess(completed.stdout)
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    citation_validation = (
        meta.get("citation_validation")
        if isinstance(meta.get("citation_validation"), dict)
        else {}
    )
    source_ids = {str(item.get("id") or "") for item in result.get("source_map") or [] if isinstance(item, dict)}
    checks = [
        _check("rag_process_succeeded", completed.returncode == 0, completed.stderr[-1000:]),
        _check("rag_envelope_success", result.get("status") == "success", result.get("errors")),
        _check("rag_found_capture", int(meta.get("result_count") or 0) > 0 and capture_id in source_ids, sorted(source_ids)),
        _check("rag_used_index", meta.get("used_index") is True, meta.get("trace")),
        _check(
            "rag_answer_cited_source",
            citation_validation.get("valid") is True
            and citation_validation.get("missing_citations") is False
            and bool(citation_validation.get("cited_source_ids")),
            citation_validation,
        ),
    ]
    return _pipeline("rag_retrieval", checks, {"capture_id": capture_id, "retrieval": (meta.get("trace") or {}).get("retrieval")})


def _theme_pipeline(paths: dict[str, Path], env: dict[str, str]) -> dict[str, Any]:
    completed, result = _run_bridge_subprocess(
        paths,
        env,
        "from bridge.theme_bridge import run_theme_scan; result=run_theme_scan(rescan_all=True)",
    )
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    with sqlite3.connect(paths["db_path"]) as conn:
        scanned = int(conn.execute("SELECT count(*) FROM captures WHERE theme_scanned_at IS NOT NULL").fetchone()[0])
        questions = int(conn.execute("SELECT count(*) FROM pending_questions").fetchone()[0])
    checks = [
        _check("theme_process_succeeded", completed.returncode == 0, completed.stderr[-1000:]),
        _check("theme_envelope_success", result.get("status") == "success", result.get("errors")),
        _check("theme_scanned_captures", int(meta.get("scanned_count") or 0) >= 2 and scanned >= 2, {"meta": meta.get("scanned_count"), "db": scanned}),
        _check("theme_relationships_detected", int(meta.get("relationship_edges") or 0) > 0, meta.get("relationship_edges")),
        _check("theme_question_persisted", questions > 0, questions),
    ]
    return _pipeline("theme_scan", checks, {"scanned_count": meta.get("scanned_count"), "question_count": questions})


def _review_pipeline(paths: dict[str, Path], env: dict[str, str]) -> dict[str, Any]:
    completed, result = _run_bridge_subprocess(
        paths,
        env,
        "from bridge.review_bridge import run_daily_review; result=run_daily_review()",
    )
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    with sqlite3.connect(paths["db_path"]) as conn:
        review_items = int(conn.execute("SELECT count(*) FROM review_items").fetchone()[0])
    checks = [
        _check("review_process_succeeded", completed.returncode == 0, completed.stderr[-1000:]),
        _check("review_envelope_success", result.get("status") == "success", result.get("errors")),
        _check("review_items_materialized", review_items > 0, review_items),
        _check("daily_review_returned_items", int(meta.get("item_count") or 0) > 0, meta.get("item_count")),
    ]
    return _pipeline("daily_review", checks, {"review_item_count": review_items, "returned_count": meta.get("item_count")})


def _canvas_pipeline(
    paths: dict[str, Path],
    env: dict[str, str],
    capture: dict[str, Any],
    garden: dict[str, Any],
) -> dict[str, Any]:
    capture_id = str(capture.get("details", {}).get("capture_id") or "")
    card_id = str(garden.get("details", {}).get("card_id") or "")
    statement = (
        "from bridge.canvas_bridge import generate_canvas; "
        f"result=generate_canvas(capture_ids=['{capture_id}'], garden_card_ids=['{card_id}'])"
    )
    completed, result = _run_bridge_subprocess(paths, env, statement)
    artifacts = result.get("durable_artifacts") or []
    artifact_path = ""
    for item in artifacts:
        if isinstance(item, dict) and item.get("path"):
            artifact_path = str(item["path"])
            break
    canvas_path = Path(artifact_path).expanduser() if artifact_path else Path()
    if artifact_path and not canvas_path.is_absolute():
        canvas_path = paths["vault"] / artifact_path
    canvas_text = canvas_path.read_text(encoding="utf-8") if artifact_path and canvas_path.is_file() else ""
    checks = [
        _check("canvas_process_succeeded", completed.returncode == 0, completed.stderr[-1000:]),
        _check("canvas_envelope_success", result.get("status") == "success", result.get("errors")),
        _check("canvas_file_written", bool(artifact_path and canvas_path.is_file()), str(canvas_path) if artifact_path else ""),
        _check(
            "canvas_contains_capture_and_card",
            capture_id[:8] in canvas_text and card_id[:8] in canvas_text,
            None,
        ),
    ]
    return _pipeline("canvas_generation", checks, {"capture_id": capture_id, "card_id": card_id, "path": artifact_path})


def _graph_pipeline(
    client: httpx.Client,
    api_base: str,
    capture: dict[str, Any],
    garden: dict[str, Any],
) -> dict[str, Any]:
    capture_id = str(capture.get("details", {}).get("capture_id") or "")
    card_id = str(garden.get("details", {}).get("card_id") or "")
    deadline = time.monotonic() + 2.0
    attempts = 0
    refresh_response = None
    graph_response = None
    refresh: dict[str, Any] = {}
    graph: dict[str, Any] = {}
    graph_text = ""
    while time.monotonic() < deadline:
        attempts += 1
        refresh_response = client.post(f"{api_base}/api/knowledge/graph/refresh")
        refresh = _json_response(refresh_response)
        graph_response = client.get(
            f"{api_base}/api/knowledge/graph",
            params={"focus_kind": "capture", "focus_id": capture_id, "limit": 200},
        )
        graph = _json_response(graph_response)
        graph_text = json.dumps(graph, ensure_ascii=False)
        if capture_id in graph_text and card_id in graph_text:
            break
        time.sleep(0.05)
    checks = [
        _check("graph_refresh_http_success", refresh_response is not None and refresh_response.status_code == 200, refresh),
        _check("graph_read_http_success", graph_response is not None and graph_response.status_code == 200, graph),
        _check("graph_contains_capture", capture_id in graph_text, capture_id),
        _check("graph_contains_card", card_id in graph_text, card_id),
    ]
    return _pipeline(
        "knowledge_graph_refresh",
        checks,
        {"capture_id": capture_id, "card_id": card_id, "attempts": attempts},
    )


def _prepare_isolated_root(root: Path, provider_settings: dict[str, str]) -> dict[str, Path]:
    custom_root = root / "custom-root"
    custom_root.mkdir(parents=True, exist_ok=True)
    for name in ("agents", "api", "bridge", "db", "scripts"):
        target = PROJECT_ROOT / name
        link = custom_root / name
        if not link.exists():
            link.symlink_to(target, target_is_directory=True)
    (custom_root / "data").mkdir(exist_ok=True)
    vault = root / "knowledge"
    vault.mkdir(exist_ok=True)
    db_path = root / "captures.db"
    hermes_home = root / "hermes-home"
    hermes_home.mkdir(exist_ok=True)
    (custom_root / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "provider": provider_settings["provider"],
                    "model": provider_settings["model"],
                    "base_url": provider_settings["base_url"],
                    "api_key": provider_settings["api_key"],
                },
                "qwen": {"api_key": provider_settings["qwen_api_key"]},
                "vault": {"path": str(vault)},
                "database": {"path": str(db_path)},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {
                    "provider": "custom",
                    "default": provider_settings["model"],
                    "base_url": provider_settings["base_url"],
                    "api_key": provider_settings["api_key"],
                    "api_mode": "chat_completions",
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return {
        "root": root,
        "custom_root": custom_root,
        "vault": vault,
        "db_path": db_path,
        "hermes_home": hermes_home,
    }


def _subprocess_env(paths: dict[str, Path], embedding_base_url: str) -> dict[str, str]:
    env = dict(os.environ)
    hermes_home = paths.get("hermes_home") or paths["root"] / "hermes-home"
    hermes_home.mkdir(parents=True, exist_ok=True)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(paths["custom_root"]), str(RUNTIME_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    env["HERMES_CUSTOM_ROOT"] = str(paths["custom_root"])
    env["HERMES_KNOWLEDGE_DB"] = str(paths["db_path"])
    env["HERMES_HOME"] = str(hermes_home)
    if embedding_base_url:
        env["DASHSCOPE_BASE_URL"] = embedding_base_url
    else:
        env.pop("DASHSCOPE_BASE_URL", None)
    env.pop("HERMES_API_TOKEN", None)
    return env


def _live_provider_settings() -> dict[str, str]:
    from agents.capture_agent import load_config

    config = load_config(str(PROJECT_ROOT / "config.yaml"))
    settings = {
        "provider": str(config.llm_provider or "openai"),
        "model": str(config.llm_model or ""),
        "base_url": str(config.llm_base_url or "https://api.deepseek.com"),
        "api_key": str(config.llm_api_key or ""),
        "qwen_api_key": str(config.qwen_api_key or ""),
    }
    missing = [key for key in ("model", "api_key", "qwen_api_key") if not settings[key]]
    if missing:
        raise RuntimeError("live provider configuration missing: " + ", ".join(missing))
    return settings


def _start_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> tuple[subprocess.Popen, Any]:
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, handle


def _wait_for_http(url: str, process: subprocess.Popen, log_path: Path, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited before {url}: {_tail(log_path)}")
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {url}: {_tail(log_path)}")


def _runtime_headers(runtime_base: str) -> dict[str, str]:
    html = httpx.get(f"{runtime_base}/knowledge-chat", timeout=10).text
    match = re.search(r'__HERMES_SESSION_TOKEN__="([^"]+)"', html)
    if not match:
        raise RuntimeError("runtime session token was not injected into dashboard HTML")
    return {"X-Hermes-Session-Token": match.group(1)}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _json_response(response: httpx.Response | None) -> dict[str, Any]:
    if response is None:
        return {}
    try:
        value = response.json()
    except ValueError:
        return {"http_status": response.status_code, "raw": response.text[:1000]}
    return value if isinstance(value, dict) else {"value": value}


def _table_count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _presentation_contract_ready(text: str) -> bool:
    if "## 目录纲要" not in (text or ""):
        return False
    headings = [
        title.strip()
        for title in re.findall(r"^#{2,3}\s+(.+?)\s*$", text or "", flags=re.MULTILINE)
        if title.strip() != "目录纲要"
    ]
    return bool(
        headings
        and re.search(r"^[-*+]\s+\[[^\]]+\]\(#[^)]+\)\s*$", text or "", flags=re.MULTILINE)
        and re.search(r"\*\*[^*\n]+\*\*", text or "")
    )


def _result_from_subprocess(stdout: str) -> dict[str, Any]:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith("E2E_RESULT="):
            value = json.loads(line.removeprefix("E2E_RESULT="))
            return value if isinstance(value, dict) else {}
    return {}


def _run_bridge_subprocess(
    paths: dict[str, Path],
    env: dict[str, str],
    statement: str,
) -> tuple[subprocess.CompletedProcess, dict[str, Any]]:
    code = f"import json; {statement}; print('E2E_RESULT='+json.dumps(result, ensure_ascii=False))"
    completed = subprocess.run(
        [str(PROJECT_ROOT / ".venv/bin/python"), "-c", code],
        cwd=paths["custom_root"],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return completed, _result_from_subprocess(completed.stdout)


def _check(name: str, ok: bool, detail: Any = None) -> dict[str, Any]:
    result = {"name": name, "ok": bool(ok)}
    if detail not in (None, "", [], {}):
        result["detail"] = detail
    return result


def _pipeline(name: str, checks: list[dict[str, Any]], details: dict[str, Any]) -> dict[str, Any]:
    failed = [check["name"] for check in checks if not check["ok"]]
    return {"name": name, "ok": not failed, "failed_checks": failed, "checks": checks, "details": details}


def _tail(path: Path, limit: int = 5000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]


class _FakeProviderServer:
    def __init__(self, *, faults: set[str]) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeProviderHandler)
        self.server.faults = set(faults)  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=2)


class _FakeProviderHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.rstrip("/").endswith("/embeddings"):
            if "embeddings" in getattr(self.server, "faults", set()):
                self._send_json({"error": "injected embedding failure"}, status=503)
                return
            self._send_json({"data": [{"embedding": [1.0] + [0.0] * 1023}]})
            return
        messages = payload.get("messages") or []
        prompt = "\n".join(str(item.get("content") or "") for item in messages if isinstance(item, dict))
        if payload.get("stream"):
            has_tool_result = any(
                isinstance(item, dict) and item.get("role") == "tool"
                for item in messages
            )
            if BROWSER_E2E_SIGNAL in prompt and "mode=local_only" in prompt and not has_tool_result:
                self._send_sse_tool_call(
                    "research_with_web",
                    {
                        "query": f"{BROWSER_E2E_SIGNAL} 这篇文章强调了什么？",
                        "mode": "local_only",
                        "export_file": False,
                    },
                )
                return
            content = (
                f"《{BROWSER_E2E_TITLE}》强调端到端测试必须验证最终产物和下游可见性，"
                f"并保留 {BROWSER_E2E_SIGNAL}。[K1]"
                if BROWSER_E2E_SIGNAL in prompt and has_tool_result
                else _fake_completion(prompt)
            )
            self._send_sse_content(content)
            return
        content = _fake_completion(prompt)
        self._send_json(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send_json(self, value: Any, *, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_content(self, content: str) -> None:
        self._send_sse(
            [
                {
                    "id": "chatcmpl-e2e",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "hermes-e2e-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": content},
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "chatcmpl-e2e",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "hermes-e2e-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
        )

    def _send_sse_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        self._send_sse(
            [
                {
                    "id": "chatcmpl-e2e",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "hermes-e2e-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-e2e-rag",
                                        "type": "function",
                                        "function": {
                                            "name": name,
                                            "arguments": json.dumps(arguments, ensure_ascii=False),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "chatcmpl-e2e",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "hermes-e2e-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                },
            ]
        )

    def _send_sse(self, events: list[dict[str, Any]]) -> None:
        body = "".join(
            f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            for event in events
        ) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _fake_completion(prompt: str) -> str:
    active_signal = BROWSER_E2E_SIGNAL if BROWSER_E2E_SIGNAL in prompt else E2E_SIGNAL
    if "Capture Agent 的内部任务规划器" in prompt:
        return json.dumps(
            {
                "task_type": "organize",
                "coverage": "full_article",
                "focus": [],
                "detail_level": "source_like",
                "compression": "low",
                "preserve": ["original_wording", "data", "examples"],
                "title": "E2E Pipeline Article",
                "detail_policy": "保留全文并只增强可读性。",
            },
            ensure_ascii=False,
        )
    if "schema_version" in prompt and "Knowledge Card" in prompt:
        source_match = re.search(r"source_id=([A-Za-z0-9_-]+)", prompt)
        source_id = source_match.group(1) if source_match else ""
        return json.dumps(
            {
                "schema_version": "garden_card.v2",
                "card_kind": "method",
                "title": "端到端验收方法",
                "claim": "Pipeline 必须以最终产物和下游可见性作为通过标准。",
                "confidence": "high",
                "when_to_use": ["修改跨模块写入链路后"],
                "core_judgments": [
                    {
                        "id": "j1",
                        "statement": "测试必须穿透真实边界",
                        "reasoning": "只验证函数返回会掩盖模块断链。",
                        "support": [{"summary": "Capture E2E 证据", "source_ids": [source_id]}],
                    }
                ],
                "boundaries": ["外部模型供应商可使用兼容服务替代"],
                "action_items": ["核对 HTTP、数据库、文件和下游对象"],
                "source_ids": [source_id],
            },
            ensure_ascii=False,
        )
    if "知识库相关内容" in prompt:
        return "结论：端到端测试必须验证真实持久化与下游可见性。[1]"
    if "聚类信息" in prompt and "可沉淀方向" in prompt:
        return "这些材料都在讨论如何验证知识流水线。\n\n可沉淀方向：\n- 方法：跨进程验收\n- 注意：核对最终产物\n\n要我先展开哪个？"
    if "请严格按照以下格式回复" in prompt:
        return "---\ntitle: E2E Pipeline Article\nsummary: 端到端测试需要验证真实入口、持久化和下游可见性。\ntags: [\"ai-agent\", \"testing\", \"knowledge\"]\n---"
    if "body_markdown" in prompt and "文件夹建议" in prompt:
        return json.dumps(
            {
                "title": "E2E Pipeline Article",
                "summary": "端到端验收材料。",
                "tags": ["testing"],
                "body_markdown": f"## 正文\n\n**端到端测试** 必须保留 {active_signal}。",
                "folder_suggestion": {"mode": "existing", "folder_path": "Inbox", "reason": "验收材料"},
                "card_suggestion": {"should_generate": True, "reason": "可复用方法", "cards": []},
                "warnings": [],
            },
            ensure_ascii=False,
        )
    if "按段落编号" in prompt and "start_paragraph_index" in prompt:
        return json.dumps(
            [
                {
                    "level": 2,
                    "title": "正文",
                    "start_paragraph_index": 1,
                    "summary": "端到端验收正文。",
                }
            ],
            ensure_ascii=False,
        )
    return (
        "## 目录纲要\n\n"
        "- 正文\n\n"
        "## 正文\n\n"
        f"**端到端测试** 必须保留 {active_signal}。\n\n"
        "这篇材料验证 HTTP、Bridge、Agent、SQLite、Markdown、Library 和 RAG 是否真正连通。"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run isolated cross-process Hermes Knowledge E2E pipelines.")
    parser.add_argument("--work-dir", default="", help="Optional workspace to keep for diagnostics.")
    parser.add_argument("--live-providers", action="store_true", help="Use configured DeepSeek/Qwen providers.")
    args = parser.parse_args(argv)
    result = run_pipeline_e2e(
        work_dir=args.work_dir or None,
        live_providers=args.live_providers,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

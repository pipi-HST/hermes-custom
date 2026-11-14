"""Continue the latest RAG answer without running a new retrieval."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.capture_agent import load_config
from agents.rag_agent import ANSWER_PREVIEW_LIMIT, CONTINUATION_HINT, format_answer
from bridge.rag_bridge import _rag_report_artifact
from bridge.result_envelope import error_envelope, source_ref, success_envelope
from bridge.run_ledger import run_with_ledger
from db.repo import CaptureRepo
from db.schema import init_db
from bridge.config_paths import load_bridge_config

CONTINUATION_PAGE_LIMIT = 1000

_config = None
_repo = None


def _init():
    global _config, _repo
    if _config is None:
        _config = load_bridge_config(loader=load_config)
        _repo = CaptureRepo(init_db(_config.db_path))
    return _config, _repo


def continue_latest_rag_answer(export_file: bool = False) -> dict:
    _config, repo = _init()
    return run_with_ledger(
        repo.conn,
        agent="rag",
        intent="continue_rag_answer",
        trigger="wechat",
        input_ref={"export_file": export_file},
        operation=lambda: _continue_latest_rag_answer(repo, export_file=export_file),
    )


def _continue_latest_rag_answer(repo: CaptureRepo, *, export_file: bool = False) -> dict:
    source_run = _latest_rag_run_with_answer(repo)
    if not source_run:
        return error_envelope(
            agent="rag",
            intent="continue_rag_answer",
            error="没有找到可继续展开的 RAG 回答。你可以先问一个知识库问题。",
            chat_preview="没有找到可继续展开的 RAG 回答。你可以先问一个知识库问题。",
        )

    output = _json_obj(source_run["output_json"])
    answer = str(output.get("answer") or "")
    sources = _source_rows(output.get("sources"))
    if export_file:
        query = str(output.get("query") or "")
        artifact = _rag_report_artifact(
            query=query,
            answer=answer,
            sources=sources,
            results=[],
        )
        return success_envelope(
            agent="rag",
            intent="continue_rag_answer",
            chat_preview="已生成 RAG 完整回答与引用报告文件。我会把文件发给你。",
            durable_artifacts=[artifact],
            source_map=_source_map(sources),
            meta={
                "source_run_id": source_run["id"],
                "export_file": True,
            },
        )

    page = _next_page(repo, source_run["id"])
    chunk = _answer_page(answer, page)
    if not chunk:
        return success_envelope(
            agent="rag",
            intent="continue_rag_answer",
            chat_preview="已经没有更多内容了。",
            source_map=_source_map(sources),
            meta={"source_run_id": source_run["id"], "page": page, "has_more": False},
        )

    preview = format_answer(chunk, sources)
    has_more = bool(_answer_page(answer, page + 1))
    if not has_more:
        preview = preview.replace(CONTINUATION_HINT, "")
    return success_envelope(
        agent="rag",
        intent="continue_rag_answer",
        chat_preview=preview,
        source_map=_source_map(sources),
        meta={
            "source_run_id": source_run["id"],
            "page": page,
            "has_more": has_more,
        },
    )


def _latest_rag_run_with_answer(repo: CaptureRepo):
    rows = repo.conn.execute(
        """SELECT * FROM agent_runs
           WHERE agent = 'rag'
             AND intent = 'search_knowledge_base'
             AND status = 'success'
           ORDER BY datetime(created_at) DESC, id DESC
           LIMIT 20"""
    ).fetchall()
    for row in rows:
        if _json_obj(row["output_json"]).get("answer"):
            return row
    return None


def _next_page(repo: CaptureRepo, source_run_id: str) -> int:
    rows = repo.conn.execute(
        """SELECT output_json FROM agent_runs
           WHERE agent = 'rag'
             AND intent = 'continue_rag_answer'
             AND status = 'success'
           ORDER BY datetime(created_at) DESC, id DESC
           LIMIT 20"""
    ).fetchall()
    for row in rows:
        output = _json_obj(row["output_json"])
        if output.get("source_run_id") == source_run_id:
            return int(output.get("page") or 1) + 1
    return 2


def _answer_page(answer: str, page: int) -> str:
    if page <= 1:
        return _first_page_body(answer)
    first_len = len(_first_page_body(answer))
    offset = first_len + (page - 2) * CONTINUATION_PAGE_LIMIT
    if offset >= len(answer):
        return ""
    return answer[offset: offset + CONTINUATION_PAGE_LIMIT].strip()


def _first_page_body(answer: str) -> str:
    if len(answer) <= ANSWER_PREVIEW_LIMIT:
        return answer
    body = format_answer(answer, []).split(CONTINUATION_HINT, 1)[0]
    return body.strip()


def _source_rows(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else []
    result: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result.append(
            {
                "title": str(row.get("title") or ""),
                "summary": "",
                "file_path": str(row.get("path") or row.get("file_path") or ""),
                "section_title": str(row.get("section") or row.get("section_title") or ""),
                "excerpt": str(row.get("excerpt") or ""),
            }
        )
    return result


def _source_map(sources: list[dict[str, str]]) -> list[dict]:
    return [
        source_ref(
            role="evidence",
            type="capture_section" if source.get("section_title") else "capture_note",
            id="",
            title=source.get("title", ""),
            path=source.get("file_path", ""),
            section=source.get("section_title", ""),
            ref=str(index),
            file_path=source.get("file_path", ""),
            excerpt=source.get("excerpt", ""),
        )
        for index, source in enumerate(sources, 1)
    ]


def _json_obj(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}

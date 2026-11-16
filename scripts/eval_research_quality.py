#!/usr/bin/env python3
"""Deterministic eval harness for Hermes research quality contracts.

The default mode replays recorded planner/search/extract/critic fixtures so the
quality gates are stable and fast. Use --live for a small manual probe against
the configured search/LLM stack.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AGENT_MAIN_ROOT = Path(os.environ.get("HERMES_AGENT_MAIN_ROOT", "/Users/pipi/Downloads/hermes-agent-main"))
if AGENT_MAIN_ROOT.exists() and str(AGENT_MAIN_ROOT) not in sys.path:
    sys.path.append(str(AGENT_MAIN_ROOT))

from bridge import research_bridge  # noqa: E402


DEFAULT_CASES = ROOT / "tests" / "fixtures" / "research_eval_cases.jsonl"
DEFAULT_LIVE_CANARY_IDS = (
    "company_openai_official",
    "person_duan_profile_reliable",
    "concept_coding_agent",
    "finance_market_cap_requires_index",
    "concept_rag_local_web",
)
DEFAULT_MAX_LIVE_CASES = 5
DEFAULT_LIVE_CASE_TIMEOUT_SECONDS = int(
    max(120, float(getattr(research_bridge, "RESEARCH_ANSWER_TIMEOUT_SECONDS", 130)) + 20)
)


def load_runtime_env(
    *,
    hermes_home: str | os.PathLike | None = None,
    project_env: str | os.PathLike | None = None,
    loader=None,
) -> list[Path]:
    """Load the same runtime dotenv files as Hermes before live evaluation."""
    home_path = Path(hermes_home or os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    project_env_path = Path(project_env) if project_env is not None else (
        AGENT_MAIN_ROOT / ".env" if AGENT_MAIN_ROOT.exists() else None
    )
    if loader is None:
        try:
            from hermes_cli.env_loader import load_hermes_dotenv
        except Exception:
            return _load_runtime_env_simple(home_path=home_path, project_env_path=project_env_path)
        loader = load_hermes_dotenv
    kwargs: dict[str, Any] = {}
    kwargs["hermes_home"] = home_path
    if project_env_path is not None:
        kwargs["project_env"] = project_env_path
    try:
        return list(loader(**kwargs) or [])
    except Exception:
        return _load_runtime_env_simple(home_path=home_path, project_env_path=project_env_path)


def _load_runtime_env_simple(*, home_path: Path, project_env_path: Path | None) -> list[Path]:
    loaded: list[Path] = []
    user_env = home_path / ".env"
    if _load_dotenv_file_simple(user_env, override=True):
        loaded.append(user_env)
    if project_env_path is not None and _load_dotenv_file_simple(project_env_path, override=not loaded):
        loaded.append(project_env_path)
    return loaded


def _load_dotenv_file_simple(path: Path, *, override: bool) -> bool:
    if not path.exists():
        return False
    for raw_line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        parsed = _parse_dotenv_assignment(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = _sanitize_credential_value(key, value)
    return True


def _parse_dotenv_assignment(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    if line.startswith("export "):
        line = line[len("export ") :].lstrip()
    key, value = line.split("=", 1)
    key = key.strip()
    if not key or any(ch.isspace() for ch in key):
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def _sanitize_credential_value(key: str, value: str) -> str:
    credential_suffixes = ("_API_KEY", "_TOKEN", "_SECRET", "_KEY")
    if not any(key.endswith(suffix) for suffix in credential_suffixes):
        return value
    return value.encode("ascii", errors="ignore").decode("ascii")


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cases.append(json.loads(line))
    return cases


def select_cases(
    cases: list[dict[str, Any]],
    *,
    ids: list[str] | tuple[str, ...] | None = None,
    canary: bool = False,
    limit: int = 0,
) -> list[dict[str, Any]]:
    if ids and canary:
        raise ValueError("--ids and --canary are mutually exclusive")
    selected = list(cases)
    if canary:
        wanted = set(DEFAULT_LIVE_CANARY_IDS)
        selected = [case for case in selected if case.get("id") in wanted]
        found = {str(case.get("id") or "") for case in selected}
        missing = [case_id for case_id in DEFAULT_LIVE_CANARY_IDS if case_id not in found]
        if missing:
            raise ValueError(f"canary cases missing from fixture file: {', '.join(missing)}")
        selected.sort(key=lambda case: DEFAULT_LIVE_CANARY_IDS.index(str(case.get("id") or "")))
    elif ids:
        wanted = set(ids)
        selected = [case for case in selected if case.get("id") in wanted]
    if limit:
        selected = selected[:limit]
    return selected


def live_budget_error(
    cases: list[dict[str, Any]],
    *,
    live: bool,
    max_live_cases: int,
    allow_live_over_budget: bool = False,
) -> str:
    if not live or allow_live_over_budget or max_live_cases <= 0:
        return ""
    if len(cases) <= max_live_cases:
        return ""
    return (
        f"Refusing to run {len(cases)} live cases; max-live-cases is {max_live_cases}. "
        "Use --canary, --ids, --limit, or --allow-live-over-budget deliberately."
    )


def run_case(
    case: dict[str, Any],
    *,
    live: bool = False,
    capture_io: bool = False,
    io_records=None,
    path: str = "legacy",
) -> dict[str, Any]:
    original_planner = research_bridge._default_query_planner
    original_critic = research_bridge._default_evidence_critic
    original_local = research_bridge._search_local
    original_search = research_bridge._default_web_search
    original_extract = research_bridge._default_web_extract
    started = time.monotonic()
    io_capture = io_records if io_records is not None else []
    try:
        if not live:
            fixture_planner = lambda _payload: case.get("planner") or {}
            fixture_critic = lambda _payload: {"decisions": case.get("critic_decisions") or []}
            fixture_local = lambda _query, _warnings: (
                case.get("local_answer") or "",
                case.get("local_sources") or [],
            )
            fixture_search = lambda _query, _limit: {
                "success": True,
                "data": {"web": case.get("search_results") or []},
            }
            fixture_extract = lambda _urls: {"results": case.get("extract_results") or []}
            fixture_synth = lambda _payload: case.get("synth_answer") or _fixture_answer(case)
            research_bridge._default_query_planner = _capture_call("planner", fixture_planner, io_capture) if capture_io else fixture_planner
            research_bridge._default_evidence_critic = _capture_call("critic", fixture_critic, io_capture) if capture_io else fixture_critic
            research_bridge._search_local = _capture_call("local", fixture_local, io_capture) if capture_io else fixture_local
            search_adapter = _capture_call("search", fixture_search, io_capture) if capture_io else fixture_search
            extract_adapter = _capture_call("extract", fixture_extract, io_capture) if capture_io else fixture_extract
            synthesize_adapter = _capture_call("synthesis", fixture_synth, io_capture) if capture_io else fixture_synth
            if path == "assist":
                answer_func = research_bridge.web_assist_answer
            elif path == "light":
                answer_func = research_bridge.light_web_answer
            else:
                answer_func = research_bridge.research_answer
            result = answer_func(
                query=case["query"],
                mode=case.get("mode") or "local_web",
                selection_context=case.get("selection_context") or {},
                article_context=case.get("article_context") or {},
                search_adapter=search_adapter,
                extract_adapter=extract_adapter,
                synthesize_adapter=synthesize_adapter,
            )
        else:
            load_runtime_env()
            if capture_io:
                research_bridge._default_query_planner = _capture_call("planner", original_planner, io_capture)
                research_bridge._default_evidence_critic = _capture_call("critic", original_critic, io_capture)
                research_bridge._search_local = _capture_call("local", original_local, io_capture)
                research_bridge._default_web_search = _capture_call("search", original_search, io_capture)
                research_bridge._default_web_extract = _capture_call("extract", original_extract, io_capture)
            if path == "assist":
                answer_func = research_bridge.web_assist_answer
            elif path == "light":
                answer_func = research_bridge.light_web_answer
            else:
                answer_func = research_bridge.research_answer
            result = answer_func(
                query=case["query"],
                mode=case.get("mode") or "local_web",
                selection_context=case.get("selection_context") or {},
                article_context=case.get("article_context") or {},
            )
    finally:
        research_bridge._default_query_planner = original_planner
        research_bridge._default_evidence_critic = original_critic
        research_bridge._search_local = original_local
        research_bridge._default_web_search = original_search
        research_bridge._default_web_extract = original_extract

    failures = grade_case(case, result, live=live)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    case_result = {
        "id": case["id"],
        "category": case.get("category") or "",
        "path": path,
        "passed": not failures,
        "failures": failures,
        "summary": summarize_result(result, failures=failures, elapsed_ms=elapsed_ms),
    }
    if capture_io:
        case_result["io_capture"] = list(io_capture)
    return case_result


def _capture_call(stage: str, func, records):
    def wrapper(*args, **kwargs):
        record: dict[str, Any] = {
            "stage": stage,
            "input": _capture_input(stage, args, kwargs),
            "started_at": time.time(),
            "status": "started",
        }
        records.append(_json_safe(record))
        try:
            output = func(*args, **kwargs)
        except Exception as exc:
            records.append(_json_safe({**record, "status": "error", "error": f"{type(exc).__name__}: {exc}"}))
            raise
        records.append(_json_safe({**record, "status": "completed", "output": _json_safe(output)}))
        return output

    return wrapper


def _capture_input(stage: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    if stage in {"planner", "critic", "synthesis"}:
        return {"payload": args[0] if args else None}
    if stage == "search":
        return {"query": args[0] if args else None, "limit": args[1] if len(args) > 1 else kwargs.get("limit")}
    if stage == "extract":
        return {"urls": args[0] if args else kwargs.get("urls")}
    if stage == "local":
        return {"query": args[0] if args else None}
    return {"args": list(args), "kwargs": kwargs}


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def run_case_with_timeout(
    case: dict[str, Any],
    *,
    live: bool,
    timeout_seconds: int,
    capture_io: bool = False,
    path: str = "legacy",
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        return run_case(case, live=live, capture_io=capture_io, path=path)
    context = multiprocessing.get_context("spawn")
    queue: multiprocessing.Queue[dict[str, Any]] = context.Queue()
    manager = multiprocessing.Manager() if capture_io else None
    shared_io = manager.list() if manager is not None else None

    def with_timeout_capture(result: dict[str, Any]) -> dict[str, Any]:
        if capture_io and "io_capture" not in result:
            result["io_capture"] = list(shared_io or [])
        return result

    try:
        process = context.Process(target=_run_case_worker, args=(case, live, capture_io, path, queue, shared_io))
        process.start()
        deadline = time.monotonic() + timeout_seconds
        while process.is_alive() and time.monotonic() < deadline:
            if not queue.empty():
                result = queue.get()
                process.terminate()
                process.join(5)
                return with_timeout_capture(result)
            process.join(min(0.25, max(0.0, deadline - time.monotonic())))
        if not queue.empty():
            result = queue.get()
            if process.is_alive():
                process.terminate()
                process.join(5)
            return with_timeout_capture(result)
        if process.is_alive():
            process.terminate()
            process.join(5)
            return with_timeout_capture(
                {
                    "id": case["id"],
                    "category": case.get("category") or "",
                    "passed": False,
                    "failures": [f"case timed out after {timeout_seconds}s"],
                    "summary": {},
                }
            )
        return with_timeout_capture(
            {
                "id": case["id"],
                "category": case.get("category") or "",
                "passed": False,
                "failures": [f"case worker exited without result, code={process.exitcode}"],
                "summary": {},
            }
        )
    finally:
        if manager is not None:
            manager.shutdown()


def _run_case_worker(
    case: dict[str, Any],
    live: bool,
    capture_io: bool,
    path: str,
    queue: multiprocessing.Queue,
    shared_io=None,
) -> None:
    try:
        queue.put(run_case(case, live=live, capture_io=capture_io, io_records=shared_io, path=path))
    except BaseException as exc:
        result = {
            "id": case["id"],
            "category": case.get("category") or "",
            "passed": False,
            "failures": [f"case raised {type(exc).__name__}: {exc}"],
            "summary": {},
        }
        if capture_io and shared_io is not None:
            result["io_capture"] = list(shared_io)
        queue.put(result)


def _fixture_answer(case: dict[str, Any]) -> str:
    refs = []
    if case.get("local_sources"):
        refs.append("[K1]")
    adopted_count = int((case.get("expect") or {}).get("min_adopted_web") or 0)
    refs.extend(f"[W{i}]" for i in range(1, adopted_count + 1))
    return f"Fixture answer for {case['id']} {' '.join(refs)}".strip()


def summarize_result(
    result: dict[str, Any],
    *,
    failures: list[str] | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    meta = result.get("meta") or {}
    research_trace = meta.get("research_trace") if isinstance(meta.get("research_trace"), dict) else {}
    source_counts = research_trace.get("source_counts") if isinstance(research_trace.get("source_counts"), dict) else {}
    stage_diagnostics = _stage_diagnostics(
        result,
        failures=failures or [],
        elapsed_ms=elapsed_ms,
        source_counts=source_counts,
    )
    return {
        "web_search_attempted": meta.get("web_search_attempted"),
        "used_web": meta.get("used_web"),
        "used_local": meta.get("used_local"),
        "research_plan": meta.get("research_plan") or {},
        "adopted": [
            {
                "ref": item.get("ref"),
                "title": item.get("title"),
                "domain": item.get("domain"),
                "extract_status": item.get("extract_status"),
                "source_authority": item.get("source_authority"),
                "supports_claims": item.get("supports_claims") or [],
            }
            for item in meta.get("web_sources") or []
        ],
        "rejected": [
            {
                "title": item.get("title"),
                "domain": item.get("domain"),
                "quality_reason": item.get("quality_reason"),
                "source_authority": item.get("source_authority"),
                "supports_claims": item.get("supports_claims") or [],
                "reason": item.get("evidence_critic_reason") or "",
            }
            for item in meta.get("rejected_sources") or []
        ],
        "clue": [
            {
                "title": item.get("title"),
                "domain": item.get("domain"),
                "quality_reason": item.get("quality_reason"),
                "source_authority": item.get("source_authority"),
                "supports_claims": item.get("supports_claims") or [],
                "reason": item.get("evidence_critic_reason") or "",
            }
            for item in meta.get("clue_sources") or []
        ],
        "source_counts": source_counts,
        "stage_diagnostics": stage_diagnostics,
        "evidence_status": (meta.get("search_quality") or {}).get("evidence_status"),
        "answer": result.get("chat_preview") or "",
        "warnings": result.get("warnings") or [],
    }


def _stage_diagnostics(
    result: dict[str, Any],
    *,
    failures: list[str],
    elapsed_ms: int | None,
    source_counts: dict[str, Any],
) -> dict[str, Any]:
    meta = result.get("meta") or {}
    trace = meta.get("research_trace") if isinstance(meta.get("research_trace"), dict) else {}
    plan = meta.get("research_plan") if isinstance(meta.get("research_plan"), dict) else {}
    provider_status = meta.get("provider_status") if isinstance(meta.get("provider_status"), dict) else {}
    if not provider_status:
        provider_status = trace.get("provider_status") if isinstance(trace.get("provider_status"), dict) else {}
    adopted = meta.get("web_sources") or []
    rejected = meta.get("rejected_sources") or []
    clue = meta.get("clue_sources") or []
    retrieved = meta.get("retrieved_web_sources") or trace.get("retrieved_web_sources") or []
    warnings = [str(item) for item in (result.get("warnings") or [])]
    extract_status_counts = _extract_status_counts([*adopted, *rejected, *clue])
    failure_stage = _infer_failure_stage(
        failures,
        meta=meta,
        plan=plan,
        source_counts=source_counts,
        adopted=adopted,
        rejected=rejected,
        clue=clue,
        retrieved=retrieved,
        warnings=warnings,
        answer=result.get("chat_preview") or "",
    )
    return {
        "failure_stage": failure_stage,
        "elapsed_ms": elapsed_ms,
        "search_elapsed_ms": provider_status.get("search_elapsed_ms"),
        "plan_status": plan.get("plan_status") or meta.get("plan_status"),
        "intent_label": plan.get("intent_label") or "",
        "query_count": len(meta.get("web_queries") or trace.get("web_queries") or []),
        "retrieval_candidate_count": int(source_counts.get("retrieval_candidate") or 0),
        "candidate_count": int(source_counts.get("candidate") or len(retrieved) or 0),
        "adopted_count": len(adopted),
        "clue_count": len(clue),
        "rejected_count": len(rejected),
        "extract_status_counts": extract_status_counts,
        "search_backend": provider_status.get("search_backend") or trace.get("search_backend") or "",
        "extract_backend": provider_status.get("extract_backend") or trace.get("extract_backend") or "",
        "citation_warning": any("来源编号" in warning or "引用" in warning for warning in warnings),
        "warning_count": len(warnings),
    }


def _extract_status_counts(sources: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        status = str(source.get("extract_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _infer_failure_stage(
    failures: list[str],
    *,
    meta: dict[str, Any],
    plan: dict[str, Any],
    source_counts: dict[str, Any],
    adopted: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    clue: list[dict[str, Any]],
    retrieved: list[dict[str, Any]],
    warnings: list[str],
    answer: str,
) -> str:
    if not failures:
        return "none"
    if any("timed out" in failure or "worker exited" in failure for failure in failures):
        return "timeout"
    mode = str(plan.get("mode") or meta.get("mode") or "")
    needs_web = bool(plan.get("needs_web") or mode in {"web_only", "local_web"})
    if needs_web and not meta.get("web_search_attempted"):
        return "planner_or_mode_contract"
    retrieval_count = int(source_counts.get("retrieval_candidate") or 0)
    candidate_count = int(source_counts.get("candidate") or len(retrieved) or 0)
    if needs_web and retrieval_count == 0 and candidate_count == 0 and not adopted and not rejected and not clue:
        return "search"
    if any("网页正文抽取失败" in warning for warning in warnings):
        if not adopted:
            return "extract"
    if any(
        "claim " in failure
        or "missing adopted domain" in failure
        or "forbidden adopted domain" in failure
        or "forbidden adopted authority" in failure
        or "adopted web sources" in failure
        or "clue web sources" in failure
        or "missing clue title" in failure
        for failure in failures
    ):
        return "critic"
    if any("来源编号" in warning or "引用" in warning for warning in warnings):
        return "citation"
    if not adopted and (rejected or clue or candidate_count):
        return "critic"
    if adopted and not answer.strip():
        return "synthesis"
    return "contract"


def build_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for item in results if item["passed"])
    return {
        "cases": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }


def write_jsonl_report(path: Path, results: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for item in results:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def write_failure_capture(directory: Path, case: dict[str, Any], result: dict[str, Any]) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    slug = _case_slug(str(case.get("id") or result.get("id") or "case"))
    io_path = directory / f"{slug}.io.json"
    fixture_path = directory / f"{slug}.fixture.json"
    io_payload = {
        "case": {
            "id": case.get("id"),
            "category": case.get("category") or "",
            "mode": case.get("mode") or "local_web",
            "query": case.get("query") or "",
            "selection_context": case.get("selection_context") or {},
            "article_context": case.get("article_context") or {},
        },
        "failures": result.get("failures") or [],
        "summary": result.get("summary") or {},
        "io_capture": result.get("io_capture") or [],
    }
    fixture_payload = build_fixture_draft(case, result)
    io_path.write_text(json.dumps(io_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fixture_path.write_text(json.dumps(fixture_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "io": str(io_path),
        "fixture": str(fixture_path),
    }


def build_fixture_draft(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    records = result.get("io_capture") or []
    summary = result.get("summary") or {}
    fixture = {
        "id": case.get("id"),
        "category": case.get("category") or "",
        "mode": case.get("mode") or "local_web",
        "query": case.get("query") or "",
        "selection_context": case.get("selection_context") or {},
        "article_context": case.get("article_context") or {},
        "planner": _last_planner_output(records) or (summary.get("research_plan") or {}),
        "search_results": _captured_search_results(records),
        "extract_results": _captured_extract_results(records),
        "critic_decisions": _captured_critic_decisions(records),
        "synth_answer": summary.get("answer") or "",
        "expect": case.get("expect") or {},
        "expect_live": case.get("expect_live") or {},
    }
    return {key: value for key, value in fixture.items() if value not in ({}, [], "")}


def _case_slug(value: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip())
    slug = "-".join(part for part in slug.split("-") if part)
    return slug[:120] or "case"


def _last_planner_output(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in reversed(records):
        if record.get("stage") != "planner":
            continue
        output = record.get("output")
        if isinstance(output, dict):
            return output
    return {}


def _captured_search_results(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for record in records:
        if record.get("stage") != "search" or "output" not in record:
            continue
        try:
            parsed = research_bridge._parse_web_search(record.get("output"))
        except Exception:
            parsed = []
        for item in parsed:
            results.append(
                {
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "description": item.get("description") or "",
                }
            )
    return _dedupe_capture_items(results, key="url")


def _captured_extract_results(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for record in records:
        if record.get("stage") != "extract" or "output" not in record:
            continue
        try:
            parsed = research_bridge._parse_web_extract(record.get("output"))
        except Exception:
            parsed = {}
        for url, content in parsed.items():
            results.append({"url": url, "content": content})
    return _dedupe_capture_items(results, key="url")


def _captured_critic_decisions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for record in records:
        if record.get("stage") != "critic" or "output" not in record:
            continue
        try:
            parsed = research_bridge._parse_evidence_critic_decisions(record.get("output"))
        except Exception:
            parsed = []
        decisions.extend(parsed)
    return decisions


def _dedupe_capture_items(items: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        value = str(item.get(key) or "")
        if value in seen:
            continue
        seen.add(value)
        out.append(item)
    return out


def write_markdown_report(
    path: Path,
    report: dict[str, Any],
    *,
    live: bool,
    suite: str = "all",
    max_live_cases: int = DEFAULT_MAX_LIVE_CASES,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Hermes Research Quality Eval",
        "",
        f"- mode: {'live' if live else 'fixture'}",
        f"- suite: {suite}",
        f"- cases: {report['cases']}",
        f"- passed: {report['passed']}",
        f"- failed: {report['failed']}",
    ]
    if live:
        live_cap = str(max_live_cases) if max_live_cases > 0 else "unlimited"
        lines.append(f"- max_live_cases: {live_cap}")
    lines.extend(
        [
            "",
            "| status | id | category | stage | adopted | rejected | failures |",
            "|---|---|---|---|---:|---:|---|",
        ]
    )
    for item in report["results"]:
        summary = item.get("summary") or {}
        diagnostics = summary.get("stage_diagnostics") or {}
        adopted = len(summary.get("adopted") or [])
        rejected = len(summary.get("rejected") or [])
        failures = "<br>".join(item.get("failures") or [])
        status = "PASS" if item.get("passed") else "FAIL"
        lines.append(
            f"| {status} | `{item.get('id')}` | {item.get('category') or ''} | {diagnostics.get('failure_stage') or ''} | {adopted} | {rejected} | {failures} |"
        )
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def grade_case(case: dict[str, Any], result: dict[str, Any], *, live: bool = False) -> list[str]:
    failures: list[str] = []
    expect = dict(case.get("expect") or {})
    if live:
        expect.update(case.get("expect_live") or {})
    meta = result.get("meta") or {}
    answer = result.get("chat_preview") or ""
    adopted = meta.get("web_sources") or []
    rejected = meta.get("rejected_sources") or []
    clue = meta.get("clue_sources") or []
    adopted_titles = [str(item.get("title") or "") for item in adopted]
    adopted_domains = [str(item.get("domain") or "") for item in adopted]
    adopted_authorities = [str(item.get("source_authority") or "") for item in adopted]
    clue_titles = [str(item.get("title") or "") for item in clue]
    rejected_titles = [str(item.get("title") or "") for item in rejected]
    candidate_titles = adopted_titles + clue_titles + rejected_titles
    research_trace = meta.get("research_trace") if isinstance(meta.get("research_trace"), dict) else {}
    is_light_path = (
        meta.get("path") in {"light_web_qa", "web_assist", "web_context"}
        or research_trace.get("path") in {"light_web_qa", "web_assist", "web_context"}
    )
    allow_summary_only = bool(
        (research_trace.get("evidence_contract") or {}).get("allow_summary_only")
    )
    source_counts = research_trace.get("source_counts") if isinstance(research_trace.get("source_counts"), dict) else {}
    attempted_domains = _attempted_domains(meta)
    allowed_refusal = bool(expect.get("allow_refusal_with_rejected_sources") and not adopted and rejected)
    failures.extend(_grade_answer_presentation(answer, result, meta))

    if "web_search_attempted" in expect and expect["web_search_attempted"] is not None and meta.get("web_search_attempted") is not expect["web_search_attempted"]:
        failures.append(f"web_search_attempted expected {expect['web_search_attempted']} got {meta.get('web_search_attempted')}")
    if "used_web" in expect and expect["used_web"] is not None and not allowed_refusal and meta.get("used_web") is not expect["used_web"]:
        failures.append(f"used_web expected {expect['used_web']} got {meta.get('used_web')}")
    if "used_local" in expect and expect["used_local"] is not None and meta.get("used_local") is not expect["used_local"]:
        failures.append(f"used_local expected {expect['used_local']} got {meta.get('used_local')}")
    if not allowed_refusal and len(adopted) < int(expect.get("min_adopted_web") or 0):
        failures.append(f"adopted web sources below minimum: {len(adopted)} < {expect.get('min_adopted_web')}")
    if "max_adopted_web" in expect and (not live or int(expect["max_adopted_web"]) <= 0) and len(adopted) > int(expect["max_adopted_web"]):
        failures.append(f"adopted web sources above maximum: {len(adopted)} > {expect['max_adopted_web']}")
    if "min_rejected_web" in expect and not is_light_path and len(rejected) < int(expect["min_rejected_web"]):
        failures.append(f"rejected web sources below minimum: {len(rejected)} < {expect['min_rejected_web']}")
    enforce_clue_minimum = True
    min_clue_web = int(expect.get("min_clue_web") or 0)
    if live and expect.get("must_clue_title_contains"):
        seen_clue_targets = [
            str(needle)
            for needle in expect.get("must_clue_title_contains") or []
            if any(str(needle).lower() in title.lower() for title in candidate_titles)
        ]
        enforce_clue_minimum = bool(seen_clue_targets)
        if seen_clue_targets:
            min_clue_web = min(min_clue_web, len(seen_clue_targets))
    if enforce_clue_minimum and "min_clue_web" in expect and len(clue) < min_clue_web:
        failures.append(f"clue web sources below minimum: {len(clue)} < {min_clue_web}")
    if "max_clue_web" in expect and len(clue) > int(expect["max_clue_web"]):
        failures.append(f"clue web sources above maximum: {len(clue)} > {expect['max_clue_web']}")
    if "min_adopted_independent_domains" in expect:
        actual = int(source_counts.get("adopted_independent_domains") or 0)
        if actual < int(expect["min_adopted_independent_domains"]):
            failures.append(f"adopted independent domains below minimum: {actual} < {expect['min_adopted_independent_domains']}")
    if "max_adopted_independent_domains" in expect:
        actual = int(source_counts.get("adopted_independent_domains") or 0)
        if (not live or int(expect["max_adopted_independent_domains"]) <= 0) and actual > int(expect["max_adopted_independent_domains"]):
            failures.append(f"adopted independent domains above maximum: {actual} > {expect['max_adopted_independent_domains']}")
    if "evidence_status" in expect:
        actual = (meta.get("search_quality") or {}).get("evidence_status")
        if not (live and expect["evidence_status"] == "limited" and actual == "sufficient") and actual != expect["evidence_status"]:
            failures.append(f"evidence_status expected {expect['evidence_status']} got {actual}")

    for value in expect.get("must_adopt_domains") or []:
        if not _domain_present(value, adopted_domains):
            failures.append(f"missing adopted domain {value}")
    for value in expect.get("must_not_adopt_domains") or []:
        if _domain_present(value, adopted_domains):
            failures.append(f"forbidden adopted domain {value}")
    for value in expect.get("must_attempt_domains") or []:
        if not _domain_present(value, attempted_domains):
            failures.append(f"missing attempted domain {value}")
    for value in expect.get("must_not_adopt_authorities") or []:
        if value in adopted_authorities:
            failures.append(f"forbidden adopted authority {value}")
    if "min_reliable_adopted_web" in expect and not allowed_refusal:
        reliable = sum(
            1
            for authority in adopted_authorities
            if authority in {"primary", "official", "index_database", "reputable_secondary"}
        )
        if reliable < int(expect["min_reliable_adopted_web"]):
            failures.append(f"reliable adopted web sources below minimum: {reliable} < {expect['min_reliable_adopted_web']}")
    if "min_reliable_adopted_web_if_answered" in expect and adopted:
        reliable = sum(
            1
            for authority in adopted_authorities
            if authority in {"primary", "official", "index_database", "reputable_secondary"}
        )
        if is_light_path and allow_summary_only:
            # The assist path deliberately adopts search summaries for entity
            # intros; count them as usable evidence instead of forcing the
            # legacy full-fetch contract onto it.
            reliable += sum(
                1
                for item in adopted
                if item.get("extract_status") == "summary"
                and not item.get("source_authority")
            )
        if reliable < int(expect["min_reliable_adopted_web_if_answered"]):
            failures.append(f"reliable adopted web sources below minimum when answered: {reliable} < {expect['min_reliable_adopted_web_if_answered']}")
    for needle in expect.get("must_not_adopt_title_contains") or []:
        if any(needle.lower() in title.lower() for title in adopted_titles):
            failures.append(f"forbidden adopted title containing {needle!r}")
    for needle in expect.get("must_clue_title_contains") or []:
        if live and not any(needle.lower() in title.lower() for title in candidate_titles):
            continue
        if not any(needle.lower() in title.lower() for title in clue_titles):
            failures.append(f"missing clue title containing {needle!r}")
    for needle in expect.get("must_reject_title_contains") or []:
        if not any(needle.lower() in title.lower() for title in rejected_titles):
            failures.append(f"missing rejected title containing {needle!r}")

    claim_requirements = expect.get("claim_requirements") or {}
    if is_light_path:
        # The assist/light path deliberately skips the legacy evidence critic;
        # it is graded on adoption + citations, not claim-support decisions.
        claim_requirements = {}
    for claim_id, requirement in claim_requirements.items():
        claim_ids = _claim_ids_for_grading(
            claim_id,
            meta,
            live=live,
            allow_generic=bool(requirement == "must_support"),
        )
        support = [
            item
            for item in adopted
            if any(item_claim in (item.get("supports_claims") or []) for item_claim in claim_ids)
        ]
        if requirement == "must_support" and not support:
            failures.append(f"claim {claim_id} was not supported")
        if requirement == "must_not_support" and support:
            failures.append(f"claim {claim_id} should not be supported")
        if requirement == "primary_or_index":
            bad = [
                item
                for item in support
                if item.get("source_authority") not in {"primary", "official", "index_database"}
            ]
            if bad:
                failures.append(f"claim {claim_id} supported by insufficient authority")

    for text in expect.get("answer_must_contain") or []:
        if text not in answer:
            failures.append(f"answer missing {text!r}")
    for text in expect.get("answer_must_not_contain") or []:
        if text in answer:
            failures.append(f"answer contains forbidden text {text!r}")
    if allowed_refusal:
        for text in expect.get("refusal_answer_must_contain") or []:
            if text not in answer:
                failures.append(f"refusal answer missing {text!r}")
    all_warnings = "\n".join(str(item) for item in (result.get("warnings") or []))
    for text in expect.get("warning_must_contain") or []:
        if live and (meta.get("search_quality") or {}).get("evidence_status") == "sufficient":
            continue
        if text not in all_warnings:
            failures.append(f"warning missing {text!r}")

    return failures


PROCESS_META_PATTERNS = (
    r"从(?:目前|当前)?搜索结果看",
    r"本次搜索(?:显示|没有|未能|结果)",
    r"目前检索到",
    r"搜索摘要显示",
    r"以上信息(?:完全)?来自",
    r"已尝试的搜索词",
    r"搜索结果主要问题",
)

STANDALONE_DISCLAIMER_RE = re.compile(
    r"(?m)^\s*(?:注意|免责声明|建议进一步查阅|若需精确信息请进一步(?:查阅|查询))[:：]",
)

REFERENCE_SECTION_RE = re.compile(
    r"(?im)^\s*#{0,6}\s*(?:参考来源|来源|联网资料)\s*[:：]?\s*$",
)


def _grade_answer_presentation(answer: str, result: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for pattern in PROCESS_META_PATTERNS:
        if re.search(pattern, answer):
            failures.append(f"answer exposes search process text: {pattern}")
    if STANDALONE_DISCLAIMER_RE.search(answer):
        failures.append("answer contains standalone disclaimer block")
    if REFERENCE_SECTION_RE.search(answer):
        failures.append("answer contains default long reference/source section")
    invalid_refs = _invalid_answer_refs(answer, result, meta)
    if invalid_refs:
        failures.append(f"answer cites unavailable refs: {', '.join(invalid_refs)}")
    return failures


def _invalid_answer_refs(answer: str, result: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    refs = {match.upper() for match in re.findall(r"\[([KW]\d+)\]", answer or "", flags=re.I)}
    if not refs:
        return []
    available: set[str] = set()
    for source in result.get("source_map") or []:
        if isinstance(source, dict) and source.get("ref"):
            available.add(str(source["ref"]).upper())
    for key in ("web_sources", "knowledge_sources"):
        for source in meta.get(key) or []:
            if isinstance(source, dict) and source.get("ref"):
                available.add(str(source["ref"]).upper())
    if not available:
        return sorted(refs)
    return sorted(refs - available)


def _domain_present(expected: str, domains: list[str]) -> bool:
    expected = str(expected or "").lower().removeprefix("www.")
    for domain in domains:
        value = str(domain or "").lower().removeprefix("www.")
        if value == expected or value.endswith(f".{expected}"):
            return True
    return False


def _claim_ids_for_grading(
    claim_id: str,
    meta: dict[str, Any],
    *,
    live: bool,
    allow_generic: bool = False,
) -> list[str]:
    if not live:
        return [claim_id]
    plan = meta.get("research_plan") if isinstance(meta.get("research_plan"), dict) else {}
    plan_claims = [
        str(item or "")
        for item in (plan.get("answer_claims_needed") or [])
        if str(item or "")
    ]
    if not plan_claims or claim_id in plan_claims:
        claim_ids = [claim_id]
    else:
        related_claims = [item for item in plan_claims if _claim_ids_are_related(claim_id, item)]
        if related_claims:
            claim_ids = related_claims
        elif allow_generic:
            claim_ids = plan_claims
        else:
            claim_ids = [claim_id]
    if allow_generic:
        claim_ids.append("answer_user_question")
    return list(dict.fromkeys(claim_ids))


def _claim_ids_are_related(expected: str, actual: str) -> bool:
    expected_tokens = _claim_id_tokens(expected)
    actual_tokens = _claim_id_tokens(actual)
    if not expected_tokens or not actual_tokens:
        return False
    return bool(expected_tokens & actual_tokens)


def _claim_id_tokens(value: str) -> set[str]:
    tokens = {
        token
        for token in str(value or "").lower().replace("-", "_").split("_")
        if len(token) >= 3 and token not in {"current", "latest", "value", "claim"}
    }
    aliases = {
        "factor": "impact",
        "jif": "impact",
        "cap": "market",
        "capitalization": "market",
    }
    return {aliases.get(token, token) for token in tokens}


def _attempted_domains(meta: dict[str, Any]) -> list[str]:
    domains: list[str] = []
    plan = meta.get("research_plan") if isinstance(meta.get("research_plan"), dict) else {}
    preferences = plan.get("source_preferences") if isinstance(plan.get("source_preferences"), dict) else {}
    domains.extend(str(item or "") for item in preferences.get("preferred_domains") or [])
    for key in ("web_sources", "rejected_sources", "retrieved_web_sources", "clue_sources"):
        for source in meta.get(key) or []:
            if isinstance(source, dict):
                domains.append(str(source.get("domain") or ""))
    return [domain for domain in domains if domain]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--live", action="store_true", help="Run against configured live search/LLM instead of fixtures")
    parser.add_argument(
        "--path",
        choices=("auto", "legacy", "light", "assist"),
        default="auto",
        help="Research path to evaluate; auto uses assist for live canaries and legacy for offline fixtures.",
    )
    parser.add_argument("--canary", action="store_true", help="Run the fixed low-cost canary suite")
    parser.add_argument("--ids", nargs="*", default=[], help="Run only the listed case ids")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--max-live-cases",
        type=int,
        default=DEFAULT_MAX_LIVE_CASES,
        help="Cost guard for live evals; set 0 to disable",
    )
    parser.add_argument(
        "--allow-live-over-budget",
        action="store_true",
        help="Explicitly allow live evals above --max-live-cases",
    )
    parser.add_argument(
        "--case-timeout",
        type=int,
        default=DEFAULT_LIVE_CASE_TIMEOUT_SECONDS,
        help="Per-case timeout in seconds for live evals; 0 disables",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output-jsonl", type=Path, help="Write one result object per line")
    parser.add_argument("--output-md", type=Path, help="Write a markdown summary report")
    parser.add_argument(
        "--capture-failures-dir",
        type=Path,
        help="Write planner/search/extract/critic IO and a fixture draft for failed cases",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    try:
        cases = select_cases(cases, ids=args.ids, canary=args.canary, limit=args.limit)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    budget_error = live_budget_error(
        cases,
        live=args.live,
        max_live_cases=args.max_live_cases,
        allow_live_over_budget=args.allow_live_over_budget,
    )
    if budget_error:
        print(f"error: {budget_error}", file=sys.stderr)
        return 2
    eval_path = args.path
    if eval_path == "auto":
        eval_path = "assist" if args.live else "legacy"
    suite = "canary" if args.canary else ("ids" if args.ids else "all")
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        if args.live:
            print(f"[live-eval] {index}/{len(cases)} start {case.get('id')} path={eval_path}", file=sys.stderr, flush=True)
        result = run_case_with_timeout(
            case,
            live=args.live,
            timeout_seconds=args.case_timeout if args.live else 0,
            capture_io=bool(args.capture_failures_dir),
            path=eval_path,
        )
        if args.capture_failures_dir and not result.get("passed"):
            result["capture_files"] = write_failure_capture(args.capture_failures_dir, case, result)
        result.pop("io_capture", None)
        results.append(result)
        if args.live:
            status = "PASS" if result.get("passed") else "FAIL"
            failures = "; ".join(result.get("failures") or [])
            diagnostics = ((result.get("summary") or {}).get("stage_diagnostics") or {})
            stage = diagnostics.get("failure_stage") or "unknown"
            elapsed = diagnostics.get("elapsed_ms")
            suffix = f" - {failures}" if failures else ""
            print(
                f"[live-eval] {index}/{len(cases)} {status} {case.get('id')} stage={stage} elapsed_ms={elapsed}{suffix}",
                file=sys.stderr,
                flush=True,
            )
    report = build_report(results)
    if args.output_jsonl:
        write_jsonl_report(args.output_jsonl, results)
    if args.output_md:
        write_markdown_report(
            args.output_md,
            report,
            live=args.live,
            suite=suite,
            max_live_cases=args.max_live_cases,
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Research quality eval: {report['passed']}/{len(results)} passed")
        for item in results:
            status = "PASS" if item["passed"] else "FAIL"
            print(f"{status} {item['id']} {item['category']}")
            for failure in item["failures"]:
                print(f"  - {failure}")
    return 0 if report["passed"] == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

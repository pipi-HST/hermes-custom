"""Live LLM smoke checks for Hermes Knowledge pipelines.

The script is safe by default: it uses a temporary SQLite DB/vault and returns
``skipped`` unless HERMES_LIVE_LLM_SMOKE=1 and a real API key is present.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.capture_agent import CaptureAgent, CaptureConfig, load_config
from agents.extractors.text_extractor import TextExtractor
from agents.markdown_writer import read_note
from bridge import answer_bridge, capture_bridge, garden_bridge, reading_bridge
from bridge.context import KnowledgeContext
from db.reading_repo import ReadingRepo
from db.repo import CaptureRepo
from db.schema import init_db
from scripts.knowledge_pipeline_acceptance_matrix import FakeOutliner, FakeUrlExtractor


PIPELINES = {
    "editable-long",
    "capture-link",
    "full-text-commit",
    "reading-answer",
    "garden-card",
}

SAMPLE_MARKERS = [f"HERMES-LIVE-SIGNAL-{index:02d}" for index in range(1, 13)]
SAMPLE_LONG = "\n\n".join(
    (
        f"第 {index} 节（{marker}）：这一节记录一个独立验收事实。"
        f"事实 {index} 的重点是：Hermes Knowledge 的整理只能改善分段、标题和错别字，"
        f"不能删除这一节里的编号、例子、判断或操作步骤。示例场景 {index} 是用户从不同入口"
        "提交材料，系统先生成可审草稿，再由用户确认入库。"
    )
    for index, marker in enumerate(SAMPLE_MARKERS, start=1)
)


def run_live_smoke(
    *,
    pipeline: str,
    work_dir: str | Path | None = None,
    keep: bool = False,
) -> dict[str, Any]:
    pipeline = (pipeline or "").strip()
    started = time.perf_counter()
    if pipeline not in PIPELINES:
        return _report(
            pipeline=pipeline,
            status="failed",
            elapsed=time.perf_counter() - started,
            reason=f"unknown pipeline: {pipeline}",
        )

    llm_settings = _live_llm_settings()
    if os.environ.get("HERMES_LIVE_LLM_SMOKE") != "1" or not llm_settings["api_key"]:
        return _report(
            pipeline=pipeline,
            status="skipped",
            elapsed=time.perf_counter() - started,
            reason="set HERMES_LIVE_LLM_SMOKE=1 and OPENAI_API_KEY/DEEPSEEK_API_KEY/HERMES_LLM_API_KEY to run live LLM smoke",
        )

    temp_dir = None
    if work_dir is None:
        if keep:
            root = Path(tempfile.mkdtemp(prefix="hermes-knowledge-live-smoke-"))
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="hermes-knowledge-live-smoke-")
            root = Path(temp_dir.name)
    else:
        root = Path(work_dir).expanduser()
        root.mkdir(parents=True, exist_ok=True)

    try:
        config, conn, ctx, agent = _setup_runtime(root, llm_settings)
        return _run_pipeline(
            pipeline=pipeline,
            started=started,
            config=config,
            conn=conn,
            ctx=ctx,
            agent=agent,
        )
    except Exception as exc:
        return _report(
            pipeline=pipeline,
            status="failed",
            elapsed=time.perf_counter() - started,
            reason=f"{type(exc).__name__}: {exc}",
        )
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass
        if temp_dir is not None:
            temp_dir.cleanup()


def _setup_runtime(root: Path, llm_settings: dict[str, str]):
    conn = init_db(str(root / "captures.db"))
    config = CaptureConfig(
        llm_api_key=llm_settings["api_key"],
        llm_provider=llm_settings["provider"],
        llm_model=llm_settings["model"],
        llm_base_url=llm_settings["base_url"],
        qwen_api_key=llm_settings["qwen_api_key"],
        vault_path=str(root / "knowledge"),
        db_path=str(root / "captures.db"),
    )
    ctx = KnowledgeContext(config=config, conn=conn, vault_path=root / "knowledge")
    agent = CaptureAgent(config, conn=conn)
    agent.extractors = [FakeUrlExtractor(), TextExtractor()]

    capture_bridge._config = config
    capture_bridge._agent = agent
    answer_bridge._config = config
    answer_bridge._repo = ReadingRepo(conn)
    reading_bridge._config = config
    reading_bridge._repo = ReadingRepo(conn)
    reading_bridge._capture_repo = CaptureRepo(conn)

    import agents.capture_agent as capture_agent_module

    capture_agent_module.Outliner = FakeOutliner
    return config, conn, ctx, agent


def _run_pipeline(
    *,
    pipeline: str,
    started: float,
    config: CaptureConfig,
    conn,
    ctx: KnowledgeContext,
    agent: CaptureAgent,
) -> dict[str, Any]:
    if pipeline == "editable-long":
        draft = agent.create_editable_draft(SAMPLE_LONG, source="live-smoke.md", existing_folders=["Inbox"])
        return _report_from_capture_result(
            pipeline,
            started,
            conn,
            draft,
            max_tokens=agent.summarizer._editable_draft_max_tokens(SAMPLE_LONG),
            input_len=len(SAMPLE_LONG),
            expected_markers=SAMPLE_MARKERS,
        )

    if pipeline == "capture-link":
        result = capture_bridge.capture_content("https://acceptance.test/live-link", instruction="整理入库")
        return _report_from_envelope(pipeline, started, conn, result, vault_path=ctx.vault_path)

    if pipeline == "full-text-commit":
        result = capture_bridge.capture_content("https://acceptance.test/live-full", instruction="全文保存整理入库")
        draft_id = result.get("meta", {}).get("draft_id", "")
        committed = capture_bridge.commit_capture_draft(draft_id, confirmation_source="dashboard")
        return _report_from_envelope(pipeline, started, conn, committed, vault_path=ctx.vault_path)

    if pipeline == "reading-answer":
        draft = answer_bridge.save_answer_to_knowledge(
            hermes_session_id="live-smoke",
            user_message="这个回答怎么沉淀？",
            assistant_answer="先保留问题，再沉淀判断依据和后续动作。",
            instruction="存成学习笔记",
            output_type="local_learning_note",
            sources=[],
        )
        committed = reading_bridge.commit_reading_artifact(draft["meta"]["reading_draft_id"], context=ctx)
        return _report_from_envelope(pipeline, started, conn, committed, vault_path=ctx.vault_path)

    if pipeline == "garden-card":
        base = agent.create_editable_draft("Garden live smoke 来源正文，用于生成一张知识卡片。")
        committed = capture_bridge.commit_capture_draft(base["id"], confirmation_source="dashboard")
        capture_id = committed.get("meta", {}).get("capture_id", "")
        result = garden_bridge.synthesize_garden(capture_ids=[capture_id], title="Live Smoke Knowledge Card", context=ctx)
        draft_id = result.get("meta", {}).get("draft_id", "")
        committed = garden_bridge.commit_garden_draft(draft_id, context=ctx)
        return _report_from_envelope(pipeline, started, conn, committed, vault_path=ctx.vault_path)

    return _report(pipeline=pipeline, status="failed", elapsed=time.perf_counter() - started, reason="unhandled pipeline")


def _report_from_capture_result(
    pipeline: str,
    started: float,
    conn,
    result: dict[str, Any],
    *,
    max_tokens: int = 0,
    input_len: int = 0,
    expected_markers: list[str] | None = None,
) -> dict[str, Any]:
    markers = expected_markers or []
    preserved_markers = _count_preserved_markers(str(result.get("draft_text") or ""), markers)
    marker_ok = not markers or preserved_markers >= max(1, int(len(markers) * 0.8))
    return _report(
        pipeline=pipeline,
        status="passed" if result.get("id") and marker_ok else "failed",
        elapsed=time.perf_counter() - started,
        input_len=input_len,
        max_tokens=max_tokens,
        summary_len=len(str(result.get("summary") or "")),
        tag_count=len(result.get("tags") or []),
        body_len=len(str(result.get("draft_text") or "")),
        warnings=(result.get("capture_intent") or {}).get("warnings") or [],
        stage_timings=result.get("stage_timings_ms") or {},
        file_written=False,
        db_rows=_db_rows(conn),
        extra={
            "expected_marker_count": len(markers),
            "preserved_marker_count": preserved_markers,
        } if markers else None,
    )


def _report_from_envelope(
    pipeline: str,
    started: float,
    conn,
    result: dict[str, Any],
    *,
    vault_path: Path,
    input_len: int = 0,
) -> dict[str, Any]:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    artifact_paths = [
        item.get("path", "")
        for item in result.get("durable_artifacts", [])
        if isinstance(item, dict) and item.get("path")
    ]
    return _report(
        pipeline=pipeline,
        status="passed" if result.get("status") == "success" else "failed",
        elapsed=time.perf_counter() - started,
        input_len=input_len,
        summary_len=len(str(meta.get("summary") or "")),
        tag_count=len(meta.get("tags") or []),
        body_len=int(meta.get("word_count") or 0) or _body_len_from_envelope(vault_path, result),
        warnings=result.get("warnings") or [],
        stage_timings=meta.get("stage_timings_ms") or {},
        file_written=bool(meta.get("file_path") or artifact_paths),
        db_rows=_db_rows(conn),
        reason="; ".join(result.get("errors") or []),
    )


def _report(
    *,
    pipeline: str,
    status: str,
    elapsed: float,
    max_tokens: int = 0,
    input_len: int = 0,
    summary_len: int = 0,
    tag_count: int = 0,
    body_len: int = 0,
    warnings: list[str] | None = None,
    stage_timings: dict[str, Any] | None = None,
    file_written: bool = False,
    db_rows: dict[str, int] | None = None,
    reason: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = {
        "pipeline": pipeline,
        "status": status,
        "elapsed": round(elapsed, 3),
        "input_len": input_len,
        "max_tokens": max_tokens,
        "summary_len": summary_len,
        "tag_count": tag_count,
        "body_len": body_len,
        "warnings": warnings or [],
        "stage_timings": stage_timings or {},
        "file_written": file_written,
        "db_rows": db_rows or {},
    }
    if reason:
        report["reason"] = reason
    if extra:
        report.update(extra)
    return report


def _db_rows(conn) -> dict[str, int]:
    rows: dict[str, int] = {}
    for table in ("capture_drafts", "captures", "reading_drafts", "garden_cards", "agent_runs"):
        try:
            rows[table] = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        except Exception:
            rows[table] = 0
    return rows


def _body_len_from_envelope(vault_path: Path, result: dict[str, Any]) -> int:
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    paths: list[str] = []
    if meta.get("file_path"):
        paths.append(str(meta.get("file_path") or ""))
    for item in result.get("durable_artifacts") or []:
        if isinstance(item, dict) and item.get("path"):
            paths.append(str(item.get("path") or ""))
    for rel_path in paths:
        note = read_note(vault_path, rel_path)
        if note:
            return len(note[1])
    return 0


def _count_preserved_markers(text: str, markers: list[str]) -> int:
    return sum(1 for marker in markers if marker and marker in (text or ""))


def _live_llm_settings() -> dict[str, str]:
    config = None
    config_path = os.environ.get("HERMES_CONFIG_PATH") or str(PROJECT_ROOT / "config.yaml")
    try:
        if Path(config_path).exists():
            config = load_config(config_path)
    except Exception:
        config = None

    api_key = (
        os.environ.get("HERMES_LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or (getattr(config, "llm_api_key", "") if config else "")
    ).strip()
    return {
        "api_key": api_key,
        "provider": (
            os.environ.get("HERMES_LLM_PROVIDER")
            or (getattr(config, "llm_provider", "") if config else "")
            or "openai"
        ),
        "model": (
            os.environ.get("HERMES_LLM_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or (getattr(config, "llm_model", "") if config else "")
            or "gpt-4o-mini"
        ),
        "base_url": (
            os.environ.get("HERMES_LLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or (getattr(config, "llm_base_url", "") if config else "")
            or "https://api.openai.com/v1"
        ),
        "qwen_api_key": (
            os.environ.get("QWEN_API_KEY")
            or (getattr(config, "qwen_api_key", "") if config else "")
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Hermes Knowledge live LLM smoke checks.")
    parser.add_argument("--temp", action="store_true", help="Use temp DB/vault. This is the default.")
    parser.add_argument("--pipeline", required=True, choices=sorted(PIPELINES))
    parser.add_argument("--work-dir", default="", help="Optional temp workspace directory to reuse.")
    parser.add_argument("--keep", action="store_true", help="Keep generated temp workspace.")
    args = parser.parse_args(argv)
    report = run_live_smoke(pipeline=args.pipeline, work_dir=args.work_dir or None, keep=args.keep)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

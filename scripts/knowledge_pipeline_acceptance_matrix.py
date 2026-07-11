"""Acceptance matrix for Hermes Knowledge pipelines.

This script is intentionally non-destructive by default. It creates a temporary
SQLite database and vault, then exercises the real bridge/repository/write paths
with deterministic local substitutes for LLMs and external extraction.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.capture_agent import CaptureAgent, CaptureConfig
from agents.extractors.base import BaseExtractor, ExtractionResult
from agents.extractors.text_extractor import TextExtractor
from agents.garden_agent import create_garden_draft
from agents.markdown_writer import read_note
from bridge import answer_bridge, capture_bridge, garden_bridge, reading_bridge
from bridge.context import KnowledgeContext
from db.agent_run_repo import AgentRunRepo
from db.reading_repo import ReadingRepo
from db.repo import CaptureRepo
from db.schema import init_db


LONG_BODY = (
    "# Hermes Pipeline 验收长文\n\n"
    "## 目录纲要\n\n"
    "- 经验一：先保留原文主线\n"
    "- 经验二：写入前确认元信息\n\n"
    "### 经验一：先保留原文主线\n\n"
    "**经验一：先保留原文主线**\n\n"
    "正文说明整理只能改善可读性，不能改变原文观点。\n\n"
    "### 经验二：写入前确认元信息\n\n"
    "**经验二：写入前确认元信息**\n\n"
    "正文说明标题、摘要、标签和分类建议都要在入库前可见。\n\n"
) + ("补充段落用于触发长文 postprocess 与大纲维护。\n\n" * 90)


class FakeUrlExtractor(BaseExtractor):
    def can_handle(self, source: str) -> bool:
        return source.startswith("https://acceptance.test/")

    def extract(self, source: str, **_kwargs: Any) -> ExtractionResult:
        if "fail" in source:
            return ExtractionResult.failed(
                code="parse_error",
                message="测试提取失败",
                source_type="link",
                source_url=source,
                retryable=False,
            )
        return ExtractionResult(
            raw_text=(
                "这是一篇用于验收链接入库的文章。第一部分说明链接提取后应该先生成"
                "可审草稿，而不是直接污染正式知识库。第二部分说明整理入库和全文保存"
                "整理入库都需要保留正文、生成摘要标签并记录阶段耗时。第三部分强调慢"
                "链路必须先有 extract、planner、revision、metadata、write 的耗时证据。"
            ),
            source_type="link",
            source_url=source,
            title="链接 Pipeline 验收文章",
        )


class FakeOutliner:
    def __init__(self, **_kwargs: Any) -> None:
        pass

    def generate(self, _body: str, _length: int) -> list[dict[str, Any]]:
        return [
            {"level": 2, "title": "经验一：先保留原文主线", "summary": "保真整理。"},
            {"level": 2, "title": "经验二：写入前确认元信息", "summary": "确认元信息。"},
        ]


class FakeGardenAgent:
    def __init__(self, **_kwargs: Any) -> None:
        pass

    def synthesize(
        self,
        captures: list[dict[str, Any]],
        _existing_cards: list[dict[str, Any]],
        *,
        title: str = "",
        card_kind: str = "",
        instruction: str = "",
    ) -> dict[str, Any]:
        capture_ids = [str(item.get("id") or "") for item in captures if item.get("id")]
        card_title = title or "Pipeline 验收知识卡"
        one_liner = f"{card_title} 要覆盖多入口写入路径。"
        return {
            "title": card_title,
            "card_kind": card_kind or "method",
            "one_liner": one_liner,
            "core_arguments": [{"claim": "验收应走临时库和真实写文件路径。", "source_ids": capture_ids[:1]}],
            "key_materials": [{"type": "case", "content": "多 pipeline 覆盖。", "source_ids": capture_ids[:1]}],
            "knowledge_links": [],
            "payload": {"instruction": instruction},
            "markdown": f"# {card_title}\n\n{one_liner}\n",
        }


def run_pipeline_acceptance_matrix(
    *,
    work_dir: str | Path | None = None,
    keep: bool = False,
) -> dict[str, Any]:
    temp_dir = None
    if work_dir is None:
        if keep:
            root = Path(tempfile.mkdtemp(prefix="hermes-knowledge-pipeline-"))
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="hermes-knowledge-pipeline-")
            root = Path(temp_dir.name)
    else:
        root = Path(work_dir).expanduser()
        root.mkdir(parents=True, exist_ok=True)

    try:
        result = _run_matrix_in(root)
        result["work_dir"] = str(root)
        result["kept"] = keep or work_dir is not None
        result["report_level"] = "temp-db acceptance"
        return result
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def _run_matrix_in(root: Path) -> dict[str, Any]:
    conn = init_db(str(root / "captures.db"))
    config = CaptureConfig(
        llm_api_key="acceptance-test",
        llm_provider="openai",
        llm_model="acceptance-model",
        llm_base_url="https://example.invalid",
        qwen_api_key="",
        vault_path=str(root / "knowledge"),
        db_path=str(root / "captures.db"),
    )
    ctx = KnowledgeContext(config=config, conn=conn, vault_path=root / "knowledge")
    agent = CaptureAgent(config, conn=conn)
    agent.extractors = [FakeUrlExtractor(), TextExtractor()]
    _install_capture_fakes(agent)

    scenarios: list[dict[str, Any]] = []
    original_capture_config = capture_bridge._config
    original_capture_agent = capture_bridge._agent
    original_answer_config = answer_bridge._config
    original_answer_repo = answer_bridge._repo
    original_reading_config = reading_bridge._config
    original_reading_repo = reading_bridge._repo
    original_reading_capture_repo = reading_bridge._capture_repo
    original_reading_postprocess = reading_bridge._postprocess_committed_learning_note
    original_answer_revision = answer_bridge._call_revision_llm
    original_capture_outliner = None
    original_garden_agent = garden_bridge.GardenAgent
    import agents.capture_agent as capture_agent_module

    original_capture_outliner = capture_agent_module.Outliner
    try:
        capture_bridge._config = config
        capture_bridge._agent = agent
        answer_bridge._config = config
        answer_bridge._repo = ReadingRepo(conn)
        reading_bridge._config = config
        reading_bridge._repo = ReadingRepo(conn)
        reading_bridge._capture_repo = CaptureRepo(conn)
        reading_bridge._postprocess_committed_learning_note = lambda *_a, **_k: {"embedding_index": {"updated": 1}}
        answer_bridge._call_revision_llm = lambda **_k: "# 修改后文稿\n\n保留原始意思，并补充分段。"
        capture_agent_module.Outliner = FakeOutliner
        garden_bridge.GardenAgent = FakeGardenAgent

        matrix: list[tuple[str, Callable[[], dict[str, Any]]]] = [
            ("editable_long_paste", lambda: _scenario_editable_long_paste(agent)),
            ("editable_import_markdown", lambda: _scenario_editable_import_markdown(ctx)),
            ("editable_parse_failure_diagnostics", lambda: _scenario_editable_parse_failure_diagnostics(agent)),
            ("editable_update_and_commit", lambda: _scenario_editable_update_and_commit(ctx)),
            ("capture_plain_text", lambda: _scenario_capture_plain_text(ctx)),
            ("capture_link_default", lambda: _scenario_capture_link(ctx, "https://acceptance.test/default", "")),
            ("capture_link_organize", lambda: _scenario_capture_link(ctx, "https://acceptance.test/organize", "整理入库")),
            ("capture_link_full_text_commit", lambda: _scenario_capture_link_full_text_commit(ctx)),
            ("capture_link_extract_failure", lambda: _scenario_capture_link_extract_failure(ctx)),
            ("capture_revise_and_discard", lambda: _scenario_capture_revise_and_discard(ctx)),
            ("committed_capture_revise_preview_and_apply", lambda: _scenario_committed_capture_revise_preview_and_apply(ctx)),
            ("reading_answer_draft_commit", lambda: _scenario_reading_answer_draft_commit(ctx)),
            ("reading_answer_enhance_capture", lambda: _scenario_reading_answer_enhance_capture(ctx)),
            ("reading_draft_revision_guards", lambda: _scenario_reading_draft_revision_guards(ctx)),
            ("garden_draft_commit", lambda: _scenario_garden_draft_commit(ctx)),
            ("garden_duplicate_blocks_creation", lambda: _scenario_garden_duplicate_blocks_creation(ctx)),
            ("frontend_contracts_present", _scenario_frontend_contracts_present),
        ]
        for name, scenario in matrix:
            scenarios.append(_run_scenario(name, scenario))
    finally:
        capture_bridge._config = original_capture_config
        capture_bridge._agent = original_capture_agent
        answer_bridge._config = original_answer_config
        answer_bridge._repo = original_answer_repo
        reading_bridge._config = original_reading_config
        reading_bridge._repo = original_reading_repo
        reading_bridge._capture_repo = original_reading_capture_repo
        reading_bridge._postprocess_committed_learning_note = original_reading_postprocess
        answer_bridge._call_revision_llm = original_answer_revision
        capture_agent_module.Outliner = original_capture_outliner
        garden_bridge.GardenAgent = original_garden_agent
        conn.close()

    return {
        "ok": all(item["ok"] for item in scenarios),
        "scenarios": scenarios,
    }


def _install_capture_fakes(agent: CaptureAgent) -> None:
    agent._call_llm = lambda *_a, **_k: (
        "## 目录纲要\n\n"
        "- 正文\n\n"
        "## 正文\n\n"
        "**保留原文信息**，只改善可读性。\n\n"
        "这是一篇用于验收链接入库的文章。"
    )
    agent.summarizer.summarize = lambda *_a, **_k: {
        "title": "Pipeline 验收",
        "summary": "验收矩阵应覆盖真实写入路径。",
        "tags": ["pipeline-acceptance", "knowledge"],
    }
    agent.summarizer.organize_editable_draft = lambda content, **_k: {
        "title": "整理草稿验收",
        "summary": "整理草稿应返回标题、摘要、标签、文件夹建议和卡片建议。",
        "tags": ["editable", "acceptance"],
        "body_markdown": content.strip() if content.strip().startswith("#") else f"## 正文\n\n{content.strip()}",
        "folder_suggestion": {
            "mode": "existing",
            "folder_path": "Inbox",
            "reason": "临时验收默认归入 Inbox。",
        },
        "card_suggestion": {
            "should_generate": True,
            "reason": "样本包含可复用方法。",
            "cards": [{"title": "验收方法", "card_kind": "method", "reason": "可复用。"}],
        },
        "warnings": [],
    }


def _run_scenario(name: str, scenario: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = scenario()
    except Exception as exc:  # pragma: no cover - report detail for CLI use.
        return {"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    result.setdefault("name", name)
    return result


def _scenario_result(name: str, checks: list[bool], details: dict[str, Any] | None = None) -> dict[str, Any]:
    failed = [index for index, ok in enumerate(checks, start=1) if not ok]
    return {
        "name": name,
        "ok": not failed,
        "failed_checks": failed,
        "details": details or {},
    }


def _scenario_editable_long_paste(agent: CaptureAgent) -> dict[str, Any]:
    draft = agent.create_editable_draft(LONG_BODY, source="paste", existing_folders=["Inbox", "AI 产品"])
    intent = draft.get("capture_intent") or {}
    return _scenario_result(
        "editable_long_paste",
        [
            draft.get("status") == "draft_ready",
            bool(draft.get("title")),
            bool(draft.get("summary")),
            bool(draft.get("tags")),
            "目录纲要" in draft.get("draft_text", ""),
            bool(intent.get("folder_suggestion", {}).get("reason")),
            bool(intent.get("card_suggestion", {}).get("reason")),
        ],
        {"draft_id": draft.get("id", "")},
    )


def _scenario_editable_import_markdown(ctx: KnowledgeContext) -> dict[str, Any]:
    result = capture_bridge.create_editable_capture_draft(
        "# 导入文档\n\n导入正文。",
        source="imported.md",
        source_type="document",
        existing_folders=["Inbox", "资料库"],
    )
    meta = result.get("meta") or {}
    intent = meta.get("capture_intent") or {}
    return _scenario_result(
        "editable_import_markdown",
        [
            result.get("status") == "success",
            bool(meta.get("title")),
            bool(meta.get("summary")),
            bool(meta.get("tags")),
            bool(intent.get("folder_suggestion", {}).get("reason")),
            bool(intent.get("card_suggestion", {}).get("reason")),
        ],
        {"draft_id": meta.get("draft_id", "")},
    )


def _scenario_editable_parse_failure_diagnostics(agent: CaptureAgent) -> dict[str, Any]:
    original = agent.summarizer.organize_editable_draft
    agent.summarizer._call_llm = lambda *_a, **_k: '{"title": "半截"'
    from agents.summarizer import Summarizer

    raw = Summarizer(api_key="acceptance-test")
    raw._call_llm = lambda *_a, **_k: '{"title": "半截"'
    try:
        agent.summarizer.organize_editable_draft = raw.organize_editable_draft
        result = capture_bridge.create_editable_capture_draft(
            "这是一段会触发坏 JSON 的正文。",
            source="bad-json.md",
            existing_folders=["Inbox"],
        )
    finally:
        agent.summarizer.organize_editable_draft = original
    meta = result.get("meta") or {}
    diagnostics = ((meta.get("capture_intent") or {}).get("diagnostics") or {})
    evidence_candidates = [
        json.loads(row["evidence_json"] or "{}")
        for row in AgentRunRepo(agent.conn).latest(limit=20)
    ]
    evidence = next((item for item in evidence_candidates if item.get("llm_parse_failed")), {})
    return _scenario_result(
        "editable_parse_failure_diagnostics",
        [
            result.get("status") == "success",
            diagnostics.get("parse_error_kind") == "json_parse_failed",
            evidence.get("llm_parse_failed") is True,
            evidence.get("llm_response_len", 0) > 0,
            evidence.get("llm_max_tokens", 0) >= 4000,
        ],
        {"diagnostics": diagnostics, "evidence": evidence},
    )


def _scenario_editable_update_and_commit(ctx: KnowledgeContext) -> dict[str, Any]:
    created = capture_bridge.create_editable_capture_draft("初始正文。")
    draft_id = created["meta"]["draft_id"]
    updated = capture_bridge.update_editable_capture_draft(
        draft_id,
        content="更新后的正文。\n\n保留原意。",
        title="更新后草稿",
    )
    committed = capture_bridge.commit_capture_draft(draft_id, confirmation_source="dashboard")
    meta = committed.get("meta") or {}
    note = read_note(ctx.vault_path, meta.get("file_path", ""))
    return _scenario_result(
        "editable_update_and_commit",
        [
            updated.get("status") == "success",
            committed.get("status") == "success",
            bool(meta.get("capture_id")),
            note is not None and "更新后的正文" in note[1],
            ctx.conn.execute("SELECT committed_capture_id FROM capture_drafts WHERE id=?", (draft_id,)).fetchone()[0]
            == meta.get("capture_id"),
        ],
        meta,
    )


def _scenario_capture_plain_text(ctx: KnowledgeContext) -> dict[str, Any]:
    result = capture_bridge.capture_content(
        "普通文本入库样本，先生成草稿。这段内容用于验证普通文本入口也会生成可审草稿，"
        "而不是直接写入正式知识库。测试需要覆盖标题、摘要、标签、阶段耗时和运行记录，"
        "这样以后修改 Capture pipeline 时不会只验证网页端粘贴整理这一条路径。"
    )
    meta = result.get("meta") or {}
    return _scenario_result(
        "capture_plain_text",
        [result.get("status") == "success", bool(meta.get("draft_id")), meta.get("draft_status") in {"raw_extracted", "draft_ready"}],
        meta,
    )


def _scenario_capture_link(ctx: KnowledgeContext, url: str, instruction: str) -> dict[str, Any]:
    result = capture_bridge.capture_content(url, instruction=instruction)
    meta = result.get("meta") or {}
    timings = meta.get("stage_timings_ms") or {}
    return _scenario_result(
        "capture_link_organize" if instruction else "capture_link_default",
        [
            result.get("status") == "success",
            bool(meta.get("draft_id")),
            {"extract_clean", "intent_planning", "draft_create", "total"} <= set(timings),
        ],
        {"draft_id": meta.get("draft_id", ""), "stage_timings_ms": timings},
    )


def _scenario_capture_link_full_text_commit(ctx: KnowledgeContext) -> dict[str, Any]:
    result = capture_bridge.capture_content(
        "https://acceptance.test/full-text",
        instruction="全文保存整理入库",
    )
    draft_id = result["meta"]["draft_id"]
    committed = capture_bridge.commit_capture_draft(draft_id, confirmation_source="dashboard")
    meta = committed.get("meta") or {}
    note = read_note(ctx.vault_path, meta.get("file_path", ""))
    frontmatter = note[0] if note else {}
    return _scenario_result(
        "capture_link_full_text_commit",
        [
            result.get("status") == "success",
            committed.get("status") == "success",
            bool(frontmatter.get("story")),
            bool(frontmatter.get("tags")),
            note is not None and "用于验收链接入库的文章" in note[1],
        ],
        meta,
    )


def _scenario_capture_link_extract_failure(ctx: KnowledgeContext) -> dict[str, Any]:
    before = ctx.conn.execute("SELECT count(*) FROM capture_drafts").fetchone()[0]
    result = capture_bridge.capture_content("https://acceptance.test/fail")
    after = ctx.conn.execute("SELECT count(*) FROM capture_drafts").fetchone()[0]
    error_evidence = [
        json.loads(row["evidence_json"] or "{}")
        for row in AgentRunRepo(ctx.conn).latest(limit=20)
        if row["status"] == "error"
    ]
    evidence = next((item for item in error_evidence if item.get("extraction_failed")), {})
    return _scenario_result(
        "capture_link_extract_failure",
        [
            result.get("status") == "error",
            before == after,
            evidence.get("extraction_failed") is True,
            evidence.get("failure", {}).get("code") == "parse_error",
        ],
        {"result": result, "evidence": evidence},
    )


def _scenario_capture_revise_and_discard(ctx: KnowledgeContext) -> dict[str, Any]:
    created = capture_bridge.capture_content(
        "需要修改后废弃的草稿正文。这段内容用于验证草稿流转的 revise 和 discard 路径，"
        "必须先创建一个可审草稿，再按用户指令修订，最后标记为 abandoned。"
        "验收不能只看入库成功，还要覆盖用户放弃草稿时数据库状态是否正确更新。"
    )
    draft_id = created["meta"]["draft_id"]
    revised = capture_bridge.revise_capture_draft(draft_id=draft_id, instruction="只改善排版")
    discarded = capture_bridge.discard_capture_draft(draft_id=draft_id)
    row = ctx.conn.execute("SELECT status FROM capture_drafts WHERE id=?", (draft_id,)).fetchone()
    return _scenario_result(
        "capture_revise_and_discard",
        [revised.get("status") == "success", discarded.get("status") == "success", row["status"] == "abandoned"],
        {"draft_id": draft_id},
    )


def _create_committed_capture(ctx: KnowledgeContext, title: str = "已入库样本") -> dict[str, Any]:
    created = capture_bridge.create_editable_capture_draft(
        f"# {title}\n\n原始正文。",
        title=title,
    )
    committed = capture_bridge.commit_capture_draft(
        created["meta"]["draft_id"],
        confirmation_source="dashboard",
    )
    return committed.get("meta") or {}


def _scenario_committed_capture_revise_preview_and_apply(ctx: KnowledgeContext) -> dict[str, Any]:
    meta = _create_committed_capture(ctx, "已入库修订样本")
    capture_id = meta["capture_id"]
    note_before = read_note(ctx.vault_path, meta["file_path"])
    preview = capture_bridge.revise_capture_draft(
        draft_id=capture_id,
        instruction="只改善排版，不改意思",
    )
    note_after_preview = read_note(ctx.vault_path, meta["file_path"])
    revision_draft_id = preview.get("durable_artifacts", [{}])[0].get("id", "")
    applied = capture_bridge.commit_capture_draft(revision_draft_id, confirmation_source="dashboard")
    return _scenario_result(
        "committed_capture_revise_preview_and_apply",
        [
            preview.get("status") == "success",
            note_before == note_after_preview,
            applied.get("status") == "success",
            applied.get("meta", {}).get("capture_id") != capture_id,
        ],
        {"capture_id": capture_id, "revision_draft_id": revision_draft_id},
    )


def _scenario_reading_answer_draft_commit(ctx: KnowledgeContext) -> dict[str, Any]:
    draft = answer_bridge.save_answer_to_knowledge(
        hermes_session_id="acceptance-session",
        user_message="这个方法怎么沉淀？",
        assistant_answer="先保留问题，再沉淀判断依据。",
        instruction="存成学习笔记",
        output_type="local_learning_note",
        assistant_message_id="assistant-1",
        sources=[{"type": "capture_note", "id": "cap-a", "title": "材料 A"}],
    )
    committed = reading_bridge.commit_reading_artifact(draft["meta"]["reading_draft_id"], context=ctx)
    artifact = committed.get("durable_artifacts", [{}])[0]
    return _scenario_result(
        "reading_answer_draft_commit",
        [
            draft.get("status") == "success",
            committed.get("status") == "success",
            artifact.get("type") == "capture_note",
            (ctx.vault_path / artifact.get("path", "")).exists(),
        ],
        committed.get("meta") or {},
    )


def _scenario_reading_answer_enhance_capture(ctx: KnowledgeContext) -> dict[str, Any]:
    meta = _create_committed_capture(ctx, "阅读增强目标")
    before = (ctx.vault_path / meta["file_path"]).read_text(encoding="utf-8")
    draft = answer_bridge.save_answer_to_knowledge(
        hermes_session_id="acceptance-session-enhance",
        user_message="补进主笔记",
        assistant_answer="新增一段阅读讨论结论。",
        instruction="补进主笔记",
        output_type="enhance_capture",
        target_capture_id=meta["capture_id"],
        sources=[{"type": "capture_note", "id": meta["capture_id"], "title": "阅读增强目标"}],
    )
    committed = reading_bridge.commit_reading_artifact(draft["meta"]["reading_draft_id"], context=ctx)
    after = (ctx.vault_path / meta["file_path"]).read_text(encoding="utf-8")
    return _scenario_result(
        "reading_answer_enhance_capture",
        [
            draft.get("status") == "success",
            committed.get("status") == "success",
            before != after,
            "新增一段阅读讨论结论" in after,
            bool(committed.get("meta", {}).get("backup_file_path")),
        ],
        committed.get("meta") or {},
    )


def _scenario_reading_draft_revision_guards(ctx: KnowledgeContext) -> dict[str, Any]:
    draft = answer_bridge.save_answer_to_knowledge(
        hermes_session_id="acceptance-revision-guards",
        user_message="怎么改？",
        assistant_answer="先做一个开放草稿。",
        instruction="存成学习笔记",
        output_type="local_learning_note",
    )
    draft_id = draft["meta"]["reading_draft_id"]
    empty = answer_bridge.revise_answer_draft(draft_id=draft_id, instruction="")
    revised = answer_bridge.revise_answer_draft(draft_id=draft_id, instruction="展开一点")
    committed = reading_bridge.commit_reading_artifact(draft_id, context=ctx)
    after_commit = answer_bridge.revise_answer_draft(draft_id=draft_id, instruction="再改")
    return _scenario_result(
        "reading_draft_revision_guards",
        [
            empty.get("status") == "error",
            revised.get("status") == "success",
            committed.get("status") == "success",
            after_commit.get("status") == "error",
        ],
        {"draft_id": draft_id},
    )


def _scenario_garden_draft_commit(ctx: KnowledgeContext) -> dict[str, Any]:
    meta = _create_committed_capture(ctx, "Garden 来源")
    draft = garden_bridge.synthesize_garden(capture_ids=[meta["capture_id"]], context=ctx)
    draft_id = draft.get("meta", {}).get("draft_id", "")
    committed = garden_bridge.commit_garden_draft(draft_id, context=ctx)
    card_id = committed.get("meta", {}).get("card_id", "")
    row = ctx.conn.execute("SELECT * FROM garden_cards WHERE id=?", (card_id,)).fetchone()
    return _scenario_result(
        "garden_draft_commit",
        [
            draft.get("status") == "success",
            committed.get("status") == "success",
            row is not None,
            bool(row["title"]) if row else False,
        ],
        {"draft_id": draft_id, "card_id": card_id},
    )


def _scenario_garden_duplicate_blocks_creation(ctx: KnowledgeContext) -> dict[str, Any]:
    meta = _create_committed_capture(ctx, "Garden 重复来源")
    draft = garden_bridge.synthesize_garden(capture_ids=[meta["capture_id"]], title="重复卡片", context=ctx)
    committed = garden_bridge.commit_garden_draft(draft["meta"]["draft_id"], context=ctx)
    before = ctx.conn.execute("SELECT count(*) FROM garden_cards").fetchone()[0]
    duplicate = garden_bridge.synthesize_garden(capture_ids=[meta["capture_id"]], title="重复卡片", context=ctx)
    after = ctx.conn.execute("SELECT count(*) FROM garden_cards").fetchone()[0]
    status = duplicate.get("meta", {}).get("duplicate_status") or ("duplicate" if duplicate.get("status") == "error" else "")
    return _scenario_result(
        "garden_duplicate_blocks_creation",
        [
            committed.get("status") == "success",
            after == before,
            duplicate.get("status") in {"success", "error"},
            status in {"duplicate", "possible_duplicate"},
        ],
        {"duplicate_status": status, "duplicate_result": duplicate.get("status")},
    )


def _scenario_frontend_contracts_present() -> dict[str, Any]:
    web_root = PROJECT_ROOT.parent / "Downloads" / "hermes-agent-main" / "web"
    package_json = web_root / "package.json"
    library_page = web_root / "src" / "pages" / "KnowledgeLibraryPage.tsx"
    chat_page = web_root / "src" / "pages" / "KnowledgeChatPage.tsx"
    checks = [
        package_json.exists(),
        library_page.exists(),
        chat_page.exists(),
    ]
    text = library_page.read_text(encoding="utf-8") if library_page.exists() else ""
    package = json.loads(package_json.read_text(encoding="utf-8")) if package_json.exists() else {}
    scripts = package.get("scripts") or {}
    checks.extend(
        [
            "mode=edit" in text or "searchParams.get(\"mode\")" in text or "searchParams.get('mode')" in text,
            "check:knowledge-outline" in scripts,
            "check:knowledge-ai-revision" in scripts,
            "check:permanent-delete" in scripts,
        ]
    )
    return _scenario_result(
        "frontend_contracts_present",
        checks,
        {"web_root": str(web_root), "scripts": sorted(scripts)},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Hermes Knowledge pipeline acceptance matrix.")
    parser.add_argument("--temp", action="store_true", help="Use a temporary DB/vault. This is the default.")
    parser.add_argument("--work-dir", default="", help="Optional temp workspace directory to reuse.")
    parser.add_argument("--keep", action="store_true", help="Keep generated temp workspace.")
    args = parser.parse_args(argv)
    result = run_pipeline_acceptance_matrix(work_dir=args.work_dir or None, keep=args.keep)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Acceptance matrix for Hermes Knowledge capture/import workflows.

The matrix is intentionally non-destructive: it writes to a temporary vault and
SQLite database, while exercising the real CaptureAgent, bridge ledger, commit,
Markdown write, and LibraryAgent postprocess paths.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.capture_agent import CaptureAgent, CaptureConfig
from agents.extractors.base import BaseExtractor, ExtractionResult
from agents.extractors.text_extractor import TextExtractor
from agents.markdown_writer import read_note
from agents.summarizer import Summarizer
from bridge import capture_bridge
from db.agent_run_repo import AgentRunRepo
from db.schema import init_db


LONG_BODY = (
    "# 基本无害的 vibecoding 实践技巧\n\n"
    "## 目录纲要\n\n"
    "- 2. 学会用 git：版本控制。\n"
    "- 3. 先让 AI 分析问题：避免盲改。\n\n"
    "### 2. 学会用 git\n\n"
    "**2. 学会用 git**\n\n"
    "正文说明 git 为什么重要，尤其是在 AI 改动频繁时保留可回退点。\n\n"
    "### 3. 先让 AI 分析问题\n\n"
    "**3. 先让 AI 分析问题**\n\n"
    "正文说明先分析再行动，避免模型直接改代码造成新问题。\n\n"
) + ("补充段落用于超过长文阈值，确保 LibraryAgent postprocess 被触发。\n\n" * 90)


class FakeUrlExtractor(BaseExtractor):
    def can_handle(self, source: str) -> bool:
        return source.startswith("https://example.test/")

    def extract(self, source: str, **_kwargs) -> ExtractionResult:
        return ExtractionResult(
            raw_text=(
                "这是一篇用于验收链接入库的文章。文章开头先说明测试背景：用户把一篇"
                "长文链接交给 Hermes，希望系统提取正文后生成可审草稿，而不是直接写入"
                "知识库。这个流程必须保留原文主线，也要把标题、摘要、标签和分类建议"
                "补齐，方便用户在入库前判断是否可靠。\n\n"
                "正文第一部分讨论 AI 产品评测。作者认为评测不能只看一次漂亮演示，"
                "还要记录失败样本、边界条件和人工判断。每一次提取、整理、确认入库"
                "都应该留下可追踪记录，否则后续很难解释为什么某篇文章被归到某个文件夹，"
                "也很难判断摘要和标签是否来自全文。\n\n"
                "正文第二部分讨论样本沉淀。团队应该把真实文章、复制文本和移动端分享链接"
                "都纳入同一套验收样本。公众号、小红书和普通网页的链接格式不同，"
                "但只要提取成功，后续草稿创建、元信息建议、写入前确认和 ledger 记录"
                "就应该遵守同一份合同。\n\n"
                "结尾强调持续迭代。慢链路不要凭感觉优化，应该先记录 extract、planner、"
                "revision、metadata 和 write 等阶段耗时，再根据证据决定是否需要 fast path。"
                "这篇文章的目的不是测试外部网络，而是验证本地 capture pipeline 在链接输入"
                "场景下会产出可审草稿，并把阶段耗时写入运行记录。"
            ),
            source_type="link",
            source_url=source,
            title="链接入库验收文章",
        )


class FakeOutliner:
    def __init__(self, **_kwargs: Any) -> None:
        pass

    def generate(self, _body: str, _length: int) -> list[dict[str, Any]]:
        return [
            {"level": 2, "title": "2. 学会用 git", "range": "段1-3", "summary": "版本控制。"},
            {"level": 2, "title": "3. 先让 AI 分析问题", "range": "段4-6", "summary": "先分析。"},
        ]


def run_acceptance_matrix(*, work_dir: str | Path | None = None, keep: bool = False) -> dict[str, Any]:
    temp_dir = None
    if work_dir is None:
        if keep:
            root = Path(tempfile.mkdtemp(prefix="hermes-knowledge-acceptance-"))
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="hermes-knowledge-acceptance-")
            root = Path(temp_dir.name)
    else:
        root = Path(work_dir).expanduser()
        root.mkdir(parents=True, exist_ok=True)

    try:
        result = _run_matrix_in(root)
        result["work_dir"] = str(root)
        result["kept"] = keep or work_dir is not None
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
    agent = CaptureAgent(config, conn=conn)
    agent.extractors = [FakeUrlExtractor(), TextExtractor()]
    scenarios: list[dict[str, Any]] = []

    original_bridge_config = capture_bridge._config
    original_bridge_agent = capture_bridge._agent
    import agents.capture_agent as capture_agent_module

    original_outliner = capture_agent_module.Outliner
    try:
        capture_bridge._config = config
        capture_bridge._agent = agent
        capture_agent_module.Outliner = FakeOutliner
        scenarios.append(_scenario_editable_contract(agent))
        scenarios.append(_scenario_parse_failure_defaults())
        scenarios.append(_scenario_commit_metadata(agent, root / "knowledge"))
        scenarios.append(_scenario_long_outline(agent, root / "knowledge"))
        scenarios.append(_scenario_link_stage_timings(agent))
        scenarios.append(_scenario_frontend_fallbacks())
    finally:
        capture_bridge._config = original_bridge_config
        capture_bridge._agent = original_bridge_agent
        capture_agent_module.Outliner = original_outliner
        conn.close()

    return {
        "ok": all(item["ok"] for item in scenarios),
        "scenarios": scenarios,
    }


def _scenario_editable_contract(agent: CaptureAgent) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def organize(_text: str, *, existing_tags=None, existing_folders=None):
        seen["existing_folders"] = existing_folders
        return {
            "title": "Agent Harness 验收",
            "summary": "整理草稿应生成元信息和建议。",
            "tags": ["ai-agent", "knowledge-workflow"],
            "body_markdown": "## 核心观点\n\n正文只做排版，不改原意。",
            "folder_suggestion": {
                "mode": "existing",
                "folder_path": "AI 产品",
                "reason": "现有分类能承接 Agent 产品方法。",
            },
            "card_suggestion": {
                "should_generate": True,
                "reason": "材料包含可复用方法。",
                "cards": [
                    {"title": "Agent Harness 方法", "card_kind": "method", "reason": "可复用。"}
                ],
            },
            "warnings": [],
        }

    agent.summarizer.organize_editable_draft = organize
    draft = agent.create_editable_draft(
        "Agent Harness 原文内容。",
        source="Agent Harness.md",
        existing_folders=["AI 产品", "Vibecoding"],
    )
    intent = draft["capture_intent"]
    checks = [
        "AI 产品" in (seen.get("existing_folders") or []),
        draft["title"] == "Agent Harness 验收",
        draft["summary"] == "整理草稿应生成元信息和建议。",
        draft["tags"] == ["ai-agent", "knowledge-workflow"],
        draft["draft_text"].startswith("## 核心观点"),
        intent["folder_suggestion"]["folder_path"] == "AI 产品",
        intent["card_suggestion"]["should_generate"] is True,
        bool(intent["card_suggestion"]["reason"]),
    ]
    return _scenario_result("editable_draft_metadata_contract", checks, {"draft_id": draft["id"]})


def _scenario_parse_failure_defaults() -> dict[str, Any]:
    summarizer = Summarizer(api_key="acceptance-test")
    summarizer._call_llm = lambda *_args, **_kwargs: "{ invalid json"
    result = summarizer.organize_editable_draft("正文内容", existing_folders=["AI 产品"])
    checks = [
        result["body_markdown"] == "",
        result["folder_suggestion"]["mode"] == "existing",
        result["folder_suggestion"]["folder_path"] == "Inbox",
        bool(result["folder_suggestion"]["reason"]),
        result["card_suggestion"]["should_generate"] is False,
        bool(result["card_suggestion"]["reason"]),
        any("解析失败" in warning for warning in result["warnings"]),
    ]
    return _scenario_result("editable_draft_parse_failure_defaults", checks, result)


def _scenario_commit_metadata(agent: CaptureAgent, vault_path: Path) -> dict[str, Any]:
    agent.summarizer.organize_editable_draft = lambda *_args, **_kwargs: {
        "title": "入库元信息验收",
        "summary": "入库应复用整理草稿阶段的摘要。",
        "tags": ["metadata-contract", "knowledge-workflow"],
        "body_markdown": "正文内容。",
        "folder_suggestion": {
            "mode": "existing",
            "folder_path": "Inbox",
            "reason": "默认分类。",
        },
        "card_suggestion": {
            "should_generate": False,
            "reason": "本样本不生成卡片。",
            "cards": [],
        },
        "warnings": [],
    }
    agent.summarizer.summarize = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("commit should reuse editable draft metadata")
    )
    draft = agent.create_editable_draft("原始正文。")
    committed = agent.commit_draft(draft["id"])
    note = read_note(vault_path, committed["file_path"])
    frontmatter, body = note if note else ({}, "")
    checks = [
        committed["summary"] == "入库应复用整理草稿阶段的摘要。",
        committed["tags"] == ["metadata-contract", "knowledge-workflow"],
        frontmatter.get("story") == "入库应复用整理草稿阶段的摘要。",
        frontmatter.get("tags") == ["metadata-contract", "knowledge-workflow"],
        frontmatter.get("word_count") == len("正文内容。"),
        body == "正文内容。",
    ]
    return _scenario_result("commit_reuses_metadata_and_writes_frontmatter", checks, committed)


def _scenario_long_outline(agent: CaptureAgent, vault_path: Path) -> dict[str, Any]:
    agent.summarizer.organize_editable_draft = lambda *_args, **_kwargs: {
        "title": "vibecoding 长文验收",
        "summary": "长文入库后应生成提纲并修复重复标题。",
        "tags": ["vibecoding"],
        "body_markdown": LONG_BODY,
        "folder_suggestion": {
            "mode": "existing",
            "folder_path": "Inbox",
            "reason": "默认分类。",
        },
        "card_suggestion": {
            "should_generate": False,
            "reason": "本样本不生成卡片。",
            "cards": [],
        },
        "warnings": [],
    }
    agent.summarizer.summarize = lambda *_args, **_kwargs: {
        "summary": "不应使用该摘要。",
        "tags": ["unexpected"],
    }
    draft = agent.create_editable_draft("长文原始内容。")
    committed = agent.commit_draft(draft["id"])
    note = read_note(vault_path, committed["file_path"])
    _frontmatter, body = note if note else ({}, "")
    metadata = agent.conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        (committed["capture_id"],),
    ).fetchone()
    checks = [
        "## 目录纲要" in body,
        "### 2. 学会用 git" in body,
        "**2. 学会用 git**" not in body,
        "### 3. 先让 AI 分析问题" in body,
        "**3. 先让 AI 分析问题**" not in body,
        metadata is not None and "学会用 git" in metadata["outline_json"],
    ]
    return _scenario_result("long_commit_outline_and_duplicate_heading_repair", checks, committed)


def _scenario_link_stage_timings(agent: CaptureAgent) -> dict[str, Any]:
    agent._call_llm = lambda *_args, **_kwargs: "链接文章整理稿，保留原文主线。"
    agent.summarizer.summarize = lambda *_args, **_kwargs: {
        "title": "链接入库验收文章",
        "summary": "链接入库应记录阶段耗时。",
        "tags": ["link-capture"],
    }
    result = capture_bridge.capture_content("https://example.test/article", instruction="整理入库")
    latest = AgentRunRepo(agent.conn).latest(limit=1)[0]
    evidence = json.loads(latest["evidence_json"])
    timings = evidence.get("stage_timings_ms") or {}
    required = {
        "extract_clean",
        "intent_planning",
        "draft_create",
        "revision_llm",
        "metadata_summary",
        "repo_update",
        "total",
    }
    checks = [
        result["status"] == "success",
        evidence.get("draft_status") == "draft_ready",
        required <= set(timings),
        all(isinstance(timings.get(key), int) and timings.get(key) >= 0 for key in required),
    ]
    return _scenario_result("link_capture_stage_timings", checks, {"timings": timings})


def _scenario_frontend_fallbacks() -> dict[str, Any]:
    page = (
        PROJECT_ROOT.parent
        / "Downloads"
        / "hermes-agent-main"
        / "web"
        / "src"
        / "pages"
        / "KnowledgeLibraryPage.tsx"
    )
    text = page.read_text(encoding="utf-8")
    checks = [
        "AI 未返回明确文件夹建议，先放入 Inbox 等待人工确认。" in text,
        "AI 未返回有效卡片建议，默认暂不生成 Garden Card。" in text,
        '|| "Inbox"' in text,
        "suggestion.should_generate === true && Boolean(reason)" in text,
    ]
    return _scenario_result("frontend_legacy_empty_suggestion_fallbacks", checks, {"path": str(page)})


def _scenario_result(name: str, checks: list[bool], details: dict[str, Any]) -> dict[str, Any]:
    failed_indexes = [index for index, ok in enumerate(checks, start=1) if not ok]
    return {
        "name": name,
        "ok": not failed_indexes,
        "failed_checks": failed_indexes,
        "details": details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Hermes Knowledge capture acceptance matrix.")
    parser.add_argument("--work-dir", default="", help="Optional workspace directory. Defaults to a temp dir.")
    parser.add_argument("--keep", action="store_true", help="Keep generated temp workspace.")
    args = parser.parse_args(argv)
    result = run_acceptance_matrix(work_dir=args.work_dir or None, keep=args.keep)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

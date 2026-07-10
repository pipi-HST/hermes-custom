"""Capture Agent — two-step capture flow: extract→raw, then finalize→captures."""
from __future__ import annotations

import logging
import json
import re
import sqlite3
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
import yaml

from agents.cleaner import clean_text, is_meaningful_content
from agents.markdown_writer import make_note_filename, read_note, NoteMeta
from agents.summarizer import Summarizer
from agents.capture_indexer import build_identity_index
from agents.embedder import Embedder
from agents.extractors.base import BaseExtractor
from agents.extractors.link_extractor import LinkExtractor
from agents.extractors.text_extractor import TextExtractor
from agents.extractors.voice_extractor import VoiceExtractor
from agents.extractors.image_extractor import ImageExtractor
from agents.extractors.xiaohongshu_extractor import XiaohongshuExtractor
from agents.extractors.document_extractor import DocumentExtractor
from agents.library_agent import (
    LibraryAgent,
    _directory_absolute_url_free,
    _directory_links_match_content_headings,
    _has_capture_processing_note,
    _has_directory_content_heading_coverage_gap,
)
from agents.outliner import Outliner
from bridge.ai_cancel import raise_if_cancelled
from bridge.knowledge_write_service import KnowledgeWriteService, MarkdownWriteRequest
from db.repo import CaptureDraftRepo, CaptureRepo
from db.embedding_repo import CaptureEmbeddingRepo
from db.concurrency import connection_lock
from db.schema import init_db

logger = logging.getLogger(__name__)
CAPTURE_LLM_TIMEOUT_SECONDS = 300
CAPTURE_LLM_MAX_TOKENS = 8000
CAPTURE_LLM_MAX_CONTINUATIONS = 2
CAPTURE_SOURCE_CHAR_LIMIT = 24000
CAPTURE_LONG_READING_CHAR_THRESHOLD = 8000
CAPTURE_LONG_CHUNK_CHAR_LIMIT = 8000
CAPTURE_SECTION_NOTE_CHAR_LIMIT = 1500
CAPTURE_INTERNAL_PLAN_BUDGET_RATIO = 0.45
CAPTURE_DIRECTORY_RENDER_CHAR_THRESHOLD = 2000
CAPTURE_OVERCOMPRESSED_RATIO = 0.65
CAPTURE_STAGE_TIMING_KEYS = (
    "extract_clean",
    "intent_planning",
    "draft_create",
    "finalize_for_commit",
    "revision_llm",
    "metadata_summary",
    "repo_update",
    "total",
)


@dataclass
class CaptureConfig:
    llm_api_key: str = ""
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_base_url: str = ""
    qwen_api_key: str = ""
    vault_path: str = "~/Hermes/knowledge"
    db_path: str = "./data/captures.db"


@dataclass
class CaptureIntent:
    user_request: str
    material_intent: str
    task_type: str
    coverage: str
    emphasis: list[str]
    must_preserve: list[str]
    output_shape: list[str]
    detail_level: str = "medium"
    draft_guidance: str = "let_model_decide"
    compression: str = "medium"
    preserve: list[str] | None = None
    plan_source: str = "fallback"
    detail_policy: str = ""
    semantic_title: str = ""

    def to_dict(self) -> dict:
        return {
            "user_request": self.user_request,
            "material_intent": self.material_intent,
            "task_type": self.task_type,
            "coverage": self.coverage,
            "emphasis": self.emphasis,
            "must_preserve": self.must_preserve,
            "output_shape": self.output_shape,
            "detail_level": self.detail_level,
            "draft_guidance": self.draft_guidance,
            "compression": self.compression,
            "preserve": self.preserve or [],
            "plan_source": self.plan_source,
            "detail_policy": self.detail_policy,
            "semantic_title": self.semantic_title,
        }


@dataclass
class LLMResult:
    text: str
    incomplete: bool = False


def load_config(config_path: str = "config.yaml") -> CaptureConfig:
    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}
    llm = cfg.get("llm", {})
    vault = cfg.get("vault", {})
    database = cfg.get("database", {})
    return CaptureConfig(
        llm_api_key=llm.get("api_key", ""),
        llm_provider=llm.get("provider", "openai"),
        llm_model=llm.get("model", "gpt-4o"),
        llm_base_url=llm.get("base_url", ""),
        qwen_api_key=cfg.get("qwen", {}).get("api_key", llm.get("api_key", "")),
        vault_path=vault.get("path", "~/Hermes/knowledge"),
        db_path=database.get("path", "./data/captures.db"),
    )


class CaptureAgent:
    def __init__(self, config: CaptureConfig, conn: sqlite3.Connection | None = None):
        self.config = config
        self.conn = conn if conn is not None else init_db(config.db_path)
        self.repo = CaptureRepo(self.conn)
        self.draft_repo = CaptureDraftRepo(self.conn)
        self.summarizer = Summarizer(
            api_key=config.llm_api_key,
            provider=config.llm_provider,
            model=config.llm_model,
            base_url=config.llm_base_url or None,
        )
        self.extractors: list[BaseExtractor] = [
            XiaohongshuExtractor(image_extractor=ImageExtractor(api_key=config.qwen_api_key)),
            LinkExtractor(),
            VoiceExtractor(api_key=config.qwen_api_key),
            ImageExtractor(api_key=config.qwen_api_key),
            DocumentExtractor(),
            TextExtractor(),  # fallback — always handles
        ]

    def process(self, raw_input: str, source_url: str = "",
                source_type: str = "") -> dict:
        """Backward-compatible alias for creating a capture draft."""
        return self.create_draft(raw_input, source_url=source_url, source_type=source_type)

    def create_draft(self, raw_input: str, instruction: str = "",
                     source_url: str = "", source_type: str = "") -> dict:
        """提取内容并保存为 SQLite 草稿；只有 commit 后才写 Knowledge File。"""
        total_started = time.perf_counter()
        timings = _empty_capture_stage_timings()
        previous_timings = getattr(self, "_active_capture_stage_timings", None)
        self._active_capture_stage_timings = timings
        try:
            stage_started = time.perf_counter()
            extracted, cleaned = self._extract_and_clean(raw_input, source_url, source_type)
            _add_capture_stage_timing(timings, "extract_clean", stage_started)
            if isinstance(extracted, dict):
                return _attach_capture_stage_timings(extracted, timings, total_started)

            title = _derive_capture_title(extracted.title, cleaned, extracted.source_type)
            source_body = extracted.raw_text
            extracted_source = extracted.source_url or source_url
            existing = (
                self.draft_repo.find_duplicate(source=extracted_source)
                if extracted_source
                else None
            )
            if existing and existing.get("status") in (
                "raw_extracted",
                "draft_ready",
                "revision_requested",
                "draft_incomplete",
            ):
                existing["duplicate_of"] = existing.get("id", "")
                existing["duplicate_state"] = "open"
                return _attach_capture_stage_timings(existing, timings, total_started)
            if existing and existing.get("status") == "committed":
                existing["duplicate_of"] = existing.get("id", "")
                existing["duplicate_state"] = "committed"
                return _attach_capture_stage_timings(existing, timings, total_started)
            stage_started = time.perf_counter()
            capture_intent_obj = self._build_capture_intent_with_planning(
                instruction,
                source_text=cleaned,
                title_hint=title,
            )
            _add_capture_stage_timing(timings, "intent_planning", stage_started)
            title = capture_intent_obj.semantic_title or title
            capture_intent = capture_intent_obj.to_dict()
            stored_raw_text = source_body
            draft_body = self._presentation_text(
                extracted.source_type,
                stored_raw_text,
                instruction=instruction,
            )
            stage_started = time.perf_counter()
            draft = self.draft_repo.create(
                raw_text=stored_raw_text,
                source=extracted_source,
                source_type=extracted.source_type or source_type or "note",
                title=title,
                word_count=len(draft_body),
                instruction=instruction,
                capture_intent=capture_intent,
            )
            _add_capture_stage_timing(timings, "draft_create", stage_started)
            return _attach_capture_stage_timings(draft, timings, total_started)
        finally:
            if previous_timings is None:
                try:
                    delattr(self, "_active_capture_stage_timings")
                except AttributeError:
                    pass
            else:
                self._active_capture_stage_timings = previous_timings

    def create_editable_draft(
        self,
        content: str,
        *,
        source: str = "",
        source_type: str = "manual",
        instruction: str = "",
        title: str = "",
        title_source: str | None = None,
        folder_path: str = "",
        existing_folders: list[str] | None = None,
        organize: bool = True,
    ) -> dict:
        """Create a user-editable draft without rewriting the pasted body."""
        body = (content or "").strip()
        if not body:
            return {"error": "内容不能为空"}

        metadata = self._organize_editable_body(body, existing_folders=existing_folders) if organize else {}
        user_title = title.strip()
        requested_title_source = _normalize_editable_title_source(title_source)
        resolved_title_source = requested_title_source or ("user" if user_title else "ai")
        if resolved_title_source == "user" and user_title:
            draft_title = user_title
        else:
            draft_title = metadata.get("title") or _derive_capture_title(None, body, source_type)
            resolved_title_source = "ai"
        ai_suggestions_status = _editable_ai_suggestions_status(
            metadata,
            organize=organize,
            title=draft_title,
            validate_title=resolved_title_source != "user",
        )
        capture_intent = self._editable_capture_intent(
            instruction=instruction,
            title_source=resolved_title_source,
            folder_path=folder_path,
        )
        capture_intent["folder_suggestion"] = metadata.get("folder_suggestion") or {}
        capture_intent["card_suggestion"] = metadata.get("card_suggestion") or {}
        capture_intent["warnings"] = metadata.get("warnings") or []
        capture_intent["ai_suggestions_status"] = ai_suggestions_status
        if metadata.get("diagnostics"):
            capture_intent["diagnostics"] = metadata.get("diagnostics")
        draft_body = metadata.get("body_markdown") or body
        raise_if_cancelled("capture.editable.create.before_write")
        return self.draft_repo.create(
            raw_text=body,
            source=source,
            source_type=source_type or "manual",
            title=draft_title,
            word_count=len(draft_body),
            draft_text=draft_body,
            instruction=instruction,
            capture_intent=capture_intent,
            summary=metadata.get("summary", ""),
            tags=metadata.get("tags", []),
            status="draft_ready",
        )

    def update_editable_draft(
        self,
        draft_id: str,
        *,
        content: str,
        title: str = "",
        title_source: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
        folder_path: str | None = None,
        folder_suggestion: dict | None = None,
        card_suggestion: dict | None = None,
        existing_folders: list[str] | None = None,
        organize: bool = True,
    ) -> Optional[dict]:
        """Update the editable body and regenerate non-user-owned metadata."""
        draft = self.draft_repo.get_by_id(draft_id)
        if not draft:
            return None
        if draft.get("status") in ("committed", "abandoned"):
            return {"error": f"草稿已{'入库' if draft['status'] == 'committed' else '废弃'}，无法修改"}

        capture_intent = draft.get("capture_intent") or {}
        if capture_intent.get("entry_mode") != "editable_capture":
            return {"error": "这不是自由编辑草稿"}

        body = (content or "").strip()
        if not body:
            return {"error": "内容不能为空"}

        saved_title_source = str(capture_intent.get("title_source") or "ai")
        requested_title_source = _normalize_editable_title_source(title_source)
        resolved_title_source = requested_title_source or saved_title_source
        incoming_title = title.strip()
        updates: dict[str, object] = {
            "draft_text": body,
            "word_count": len(body),
            "status": "draft_ready",
        }
        if incoming_title and (not organize or resolved_title_source == "user" or requested_title_source is None):
            updates["title"] = incoming_title
            if requested_title_source != "ai":
                resolved_title_source = "user"

        metadata: dict = {}
        ai_suggestions_status = "not_generated"
        if organize:
            metadata = self._organize_editable_body(body, existing_folders=existing_folders)
            if summary is not None:
                updates["summary"] = summary.strip()
            else:
                updates["summary"] = metadata.get("summary", "")
            if tags is not None:
                updates["tags"] = tags
            else:
                updates["tags"] = metadata.get("tags", [])
            updates["draft_text"] = metadata.get("body_markdown") or body
            updates["word_count"] = len(updates["draft_text"])
            if resolved_title_source != "user":
                updates["title"] = metadata.get("title") or _derive_capture_title(None, body, draft.get("source_type", ""))
                resolved_title_source = "ai"
            ai_suggestions_status = _editable_ai_suggestions_status(
                metadata,
                organize=True,
                title=str(updates.get("title") or draft.get("title") or ""),
                validate_title=resolved_title_source != "user",
            )
        else:
            if summary is not None:
                updates["summary"] = summary.strip()
            if tags is not None:
                updates["tags"] = tags

        capture_intent = {
            **capture_intent,
            "title_source": resolved_title_source,
            "editable_source_of_truth": "draft_text",
            "pipeline_policy": "reuse_capture_library_rag",
            "ai_suggestions_status": ai_suggestions_status,
        }
        if folder_path is not None:
            capture_intent["folder_path"] = folder_path.strip() or "Inbox"
        if isinstance(folder_suggestion, dict):
            capture_intent["folder_suggestion"] = folder_suggestion
        elif organize:
            capture_intent["folder_suggestion"] = metadata.get("folder_suggestion") or {}
        if isinstance(card_suggestion, dict):
            capture_intent["card_suggestion"] = card_suggestion
        elif organize:
            capture_intent["card_suggestion"] = metadata.get("card_suggestion") or {}
        if organize:
            capture_intent["warnings"] = metadata.get("warnings") or []
            if metadata.get("diagnostics"):
                capture_intent["diagnostics"] = metadata.get("diagnostics")
            else:
                capture_intent.pop("diagnostics", None)
        updates["capture_intent"] = capture_intent
        raise_if_cancelled("capture.editable.update.before_write")
        return self.draft_repo.update(draft_id, **updates)

    def revise_draft(
        self,
        draft_id: str,
        instruction: str,
        *,
        reuse_saved_intent: bool = False,
    ) -> Optional[dict]:
        """按用户指令生成或更新草稿，不写入正式知识库。"""
        draft = self.draft_repo.get_by_id(draft_id)
        if not draft:
            draft = self._create_revision_draft_from_capture(draft_id)
            if not draft:
                return None
            draft_id = draft["id"]
        if draft.get("status") in ("committed", "abandoned"):
            return {"error": f"草稿已{'入库' if draft['status'] == 'committed' else '废弃'}，无法修改"}
        current_text = draft.get("draft_text") or draft.get("raw_text", "")
        timings = getattr(self, "_active_capture_stage_timings", None)
        saved_intent = draft.get("capture_intent") if isinstance(draft.get("capture_intent"), dict) else {}
        if reuse_saved_intent and saved_intent:
            capture_intent = dict(saved_intent)
        else:
            stage_started = time.perf_counter()
            capture_intent = self._build_capture_intent_with_planning(
                instruction,
                source_text=draft.get("raw_text", "") or current_text,
                title_hint=draft.get("title", ""),
            ).to_dict()
            _add_capture_stage_timing(timings, "intent_planning", stage_started)
        if self._is_raw_package_intent(capture_intent, instruction):
            source_text = draft.get("raw_text", "") or current_text
            draft_body = self._presentation_text(
                draft.get("source_type", ""),
                source_text,
                instruction=instruction,
            )
            existing_tags = self._get_existing_tags(Path(self.config.vault_path).expanduser())
            summary_source = clean_text(source_text) or source_text
            stage_started = time.perf_counter()
            tag_result = self.summarizer.summarize(summary_source, existing_tags)
            _add_capture_stage_timing(timings, "metadata_summary", stage_started)
            stage_started = time.perf_counter()
            updated = self.draft_repo.update(
                draft_id,
                draft_text=draft_body,
                instruction=instruction,
                capture_intent=capture_intent,
                summary=tag_result.get("summary", ""),
                tags=tag_result.get("tags", []),
                status="draft_ready",
            )
            _add_capture_stage_timing(timings, "repo_update", stage_started)
            return updated
        stage_started = time.perf_counter()
        llm_result = self._revise_with_llm(
            instruction=instruction,
            capture_intent=capture_intent,
            current_text=current_text,
            raw_text=draft.get("raw_text", ""),
            source_type=draft.get("source_type", ""),
        )
        _add_capture_stage_timing(timings, "revision_llm", stage_started)
        if isinstance(llm_result, LLMResult):
            new_text = _strip_draft_preamble(llm_result.text)
            incomplete = llm_result.incomplete
        else:
            new_text = _strip_draft_preamble(llm_result)
            incomplete = getattr(self, "_last_llm_incomplete", False)

        if incomplete:
            stage_started = time.perf_counter()
            updated = self.draft_repo.update(
                draft_id,
                draft_text=new_text,
                instruction=instruction,
                capture_intent=capture_intent,
                status="draft_incomplete",
            )
            _add_capture_stage_timing(timings, "repo_update", stage_started)
            if updated is not None:
                updated["revision_error"] = "llm_output_incomplete"
            return updated

        existing_tags = self._get_existing_tags(Path(self.config.vault_path).expanduser())
        stage_started = time.perf_counter()
        tag_result = self.summarizer.summarize(new_text, existing_tags)
        _add_capture_stage_timing(timings, "metadata_summary", stage_started)
        stage_started = time.perf_counter()
        updated = self.draft_repo.update(
            draft_id,
            draft_text=new_text,
            instruction=instruction,
            capture_intent=capture_intent,
            summary=tag_result.get("summary", ""),
            tags=tag_result.get("tags", []),
            status="draft_ready",
        )
        _add_capture_stage_timing(timings, "repo_update", stage_started)
        return updated

    def _build_capture_intent(self, instruction: str) -> CaptureIntent:
        user_request = (instruction or "").strip()
        coverage = (
            "selected_sections"
            if re.search(r"(只要|只看|仅看|仅保留|只保留|删除其他|不要其他|仅要|其他不用)", user_request)
            else "full_article"
        )
        if "翻译" in user_request:
            task_type = "translate"
        elif re.search(r"(改写|润色|扩写|压缩|缩短)", user_request):
            task_type = "rewrite"
        elif re.search(r"(提取|总结|整理|重点|概括)", user_request):
            task_type = "organize"
        else:
            task_type = "capture"

        emphasis = self._extract_emphasis(user_request)
        preserve = self._detect_preserve_items(user_request)
        material_intent = self._detect_material_intent(user_request, emphasis)
        is_full_text_save = material_intent == "verbatim_full_text"
        if is_full_text_save:
            task_type = "capture"
            if "original_wording" not in preserve:
                preserve.append("original_wording")
        must_preserve = [] if coverage == "selected_sections" else [
            "source_fidelity",
            "main_thread",
            "user_named_focus",
        ]
        if material_intent == "enhanced_note" and "source_layering" not in must_preserve:
            must_preserve.append("source_layering")
        output_shape = ["full_text"] if is_full_text_save else ["natural_draft"]
        return CaptureIntent(
            user_request=user_request,
            material_intent=material_intent,
            task_type=task_type,
            coverage=coverage,
            emphasis=emphasis,
            must_preserve=must_preserve,
            output_shape=output_shape,
            detail_level=self._detect_detail_level(user_request),
            draft_guidance="verbatim" if is_full_text_save else "let_model_decide",
            compression="none" if is_full_text_save else self._detect_compression(user_request),
            preserve=preserve,
            plan_source="deterministic_full_text" if is_full_text_save else "fallback",
        )

    def _build_capture_intent_with_planning(
        self,
        instruction: str,
        *,
        source_text: str = "",
        title_hint: str = "",
    ) -> CaptureIntent:
        fallback = self._build_capture_intent(instruction)
        if (
            not instruction.strip()
            or self._is_full_text_save_intent(fallback.to_dict())
            or not self._capture_planning_available()
        ):
            if self._is_full_text_save_intent(fallback.to_dict()):
                fallback.plan_source = "deterministic_full_text"
            else:
                fallback.plan_source = "fallback"
            return fallback

        try:
            plan = self._call_capture_planner(
                instruction=instruction,
                source_text=source_text,
                title_hint=title_hint,
            )
        except Exception as exc:
            logger.warning(f"Capture planning failed; using fallback intent: {exc}")
            fallback.plan_source = "fallback"
            return fallback

        planned = self._intent_from_capture_plan(fallback, plan)
        if planned is None:
            fallback.plan_source = "fallback"
            return fallback
        return planned

    def _capture_planning_available(self) -> bool:
        forced = getattr(self, "_capture_planning_enabled", None)
        if forced is not None:
            return bool(forced)
        return bool(self.config.llm_api_key and not self.config.llm_api_key.startswith("sk-test"))

    def _call_capture_planner(
        self,
        *,
        instruction: str,
        source_text: str,
        title_hint: str = "",
    ) -> dict:
        prompt = self._build_capture_planning_prompt(
            instruction=instruction,
            source_text=source_text,
            title_hint=title_hint,
        )
        raw = self._call_llm(prompt)
        if isinstance(raw, LLMResult):
            raw = raw.text
        parsed = _parse_json_object(str(raw))
        if not isinstance(parsed, dict):
            raise ValueError("planner did not return a JSON object")
        return parsed

    def _build_capture_planning_prompt(
        self,
        *,
        instruction: str,
        source_text: str,
        title_hint: str = "",
    ) -> str:
        preview = _build_planning_source_preview(source_text)
        return (
            "你是 Capture Agent 的内部任务规划器。你的职责是理解用户要如何整理材料，"
            "不是写最终草稿。\n\n"
            f"用户要求：{instruction}\n"
            f"当前标题候选：{title_hint or '无'}\n\n"
            "请只输出一个 JSON object，不要 Markdown，不要解释。字段：\n"
            "- task_type: capture | organize | rewrite | translate\n"
            "- coverage: full_article | selected_sections\n"
            "- focus: string[]，用户点名关切；没有就 []。不要写“内容/重点/全文”等泛词。\n"
            "- detail_level: brief | medium | deep | source_like\n"
            "- compression: high | medium | low\n"
            "- preserve: string[]，可包含 original_wording/data/examples/methodology\n"
            "- title: string，优先从正文选一个有真实含义的标题；没有把握就空字符串\n"
            "- detail_policy: string，用一句话说明最终整理应如何分配详略\n\n"
            "判断原则：语义由你理解，不要依赖固定关键词；用户说“某部分详细阐述”时，"
            "focus 是用户问题的重点关切，不等于整篇文章唯一重点。\n\n"
            f"材料预览：\n{preview}"
        )

    def _intent_from_capture_plan(
        self,
        fallback: CaptureIntent,
        plan: dict,
    ) -> CaptureIntent | None:
        if not isinstance(plan, dict):
            return None
        task_type = _enum_value(plan.get("task_type"), {"capture", "organize", "rewrite", "translate"})
        coverage = _enum_value(plan.get("coverage"), {"full_article", "selected_sections"})
        detail_level = _enum_value(plan.get("detail_level"), {"brief", "medium", "deep", "source_like"})
        compression = _enum_value(plan.get("compression"), {"high", "medium", "low"})
        focus = _string_list(plan.get("focus"))
        preserve = _string_list(plan.get("preserve"))
        if (
            task_type is None
            or coverage is None
            or detail_level is None
            or compression is None
            or focus is None
            or preserve is None
        ):
            return None

        if coverage == "selected_sections" and fallback.coverage != "selected_sections":
            coverage = "full_article"

        semantic_title = _clean_semantic_title(str(plan.get("title") or ""))
        detail_policy = str(plan.get("detail_policy") or "").strip()
        if self._is_high_fidelity_organize_request(fallback.user_request):
            detail_level = "source_like" if detail_level == "medium" else detail_level
            compression = "low" if compression == "medium" else compression
            if "original_wording" not in preserve:
                preserve.append("original_wording")
        return CaptureIntent(
            user_request=fallback.user_request,
            material_intent=fallback.material_intent,
            task_type=task_type,
            coverage=coverage,
            emphasis=focus or fallback.emphasis,
            must_preserve=(
                []
                if coverage == "selected_sections"
                else [
                    item
                    for item in [
                        "source_fidelity",
                        "main_thread",
                        "user_named_focus",
                        "source_layering" if fallback.material_intent == "enhanced_note" else "",
                    ]
                    if item
                ]
            ),
            output_shape=fallback.output_shape,
            detail_level=detail_level,
            draft_guidance=fallback.draft_guidance,
            compression=compression,
            preserve=preserve or fallback.preserve,
            plan_source="llm",
            detail_policy=detail_policy,
            semantic_title=semantic_title,
        )

    def _is_full_text_save_intent(self, capture_intent: dict) -> bool:
        return capture_intent.get("output_shape") == ["full_text"]

    def _is_literal_raw_save_intent(self, capture_intent: dict, instruction: str = "") -> bool:
        user_request = str(instruction or capture_intent.get("user_request") or "")
        return bool(re.search(r"(一字不改|原封不动|原样).{0,12}(保存|入库|存库|归档)", user_request))

    def _is_raw_package_intent(self, capture_intent: dict, instruction: str = "") -> bool:
        user_request = str(instruction or capture_intent.get("user_request") or "")
        return self._is_raw_package_request(user_request)

    def _is_high_fidelity_organize_request(self, user_request: str) -> bool:
        return bool(
            re.search(
                r"(全文整理|高保真整理|高保真全文|尽可能|尽量|最大程度).{0,12}(整理|提取|保留|原文)|"
                r"全文整理|"
                r"(贴近原文|原文质感|原文关键表述|少压缩)",
                user_request or "",
            )
        )

    def _editable_capture_intent(
        self,
        *,
        instruction: str = "",
        title_source: str = "ai",
        folder_path: str = "",
    ) -> dict:
        intent = self._build_capture_intent(instruction or "自由编辑后入库").to_dict()
        intent.update(
            {
                "entry_mode": "editable_capture",
                "title_source": title_source,
                "editable_source_of_truth": "draft_text",
                "pipeline_policy": "reuse_capture_library_rag",
            }
        )
        if folder_path.strip():
            intent["folder_path"] = folder_path.strip()
        return intent

    def _summarize_editable_body(self, body: str) -> dict:
        return self._organize_editable_body(body)

    def _organize_editable_body(
        self,
        body: str,
        *,
        existing_folders: list[str] | None = None,
    ) -> dict:
        cleaned_body = self._light_cleanup_editable_body(body)
        existing_tags = self._get_existing_tags(Path(self.config.vault_path).expanduser())
        folder_options = self._normalize_existing_folders(existing_folders)
        if not folder_options:
            folder_options = self._get_existing_folders(Path(self.config.vault_path).expanduser())
        try:
            organizer = getattr(self.summarizer, "organize_editable_draft", None)
            instance_attrs = getattr(self.summarizer, "__dict__", {})
            if callable(organizer) and (
                "organize_editable_draft" in instance_attrs
                or "summarize" not in instance_attrs
            ):
                result = organizer(
                    cleaned_body,
                    existing_tags=existing_tags,
                    existing_folders=folder_options,
                )
            else:
                result = self.summarizer.summarize(clean_text(cleaned_body) or cleaned_body, existing_tags)
        except Exception as exc:
            logger.warning(f"Editable draft metadata generation failed: {exc}")
            result = {
                "warnings": ["AI 建议生成失败，请检查模型配置后重试或手动填写。"],
                "diagnostics": {"error": str(exc)},
            }
        folder_suggestion = result.get("folder_suggestion") if isinstance(result.get("folder_suggestion"), dict) else {}
        folder_path = str(folder_suggestion.get("folder_path") or "").strip()
        if folder_path and folder_suggestion.get("mode") not in {"existing", "new"}:
            folder_suggestion["mode"] = "existing" if folder_path in folder_options else "new"
        elif folder_path and folder_suggestion.get("mode") == "existing" and folder_path not in folder_options:
            folder_suggestion["mode"] = "new"
        card_suggestion = result.get("card_suggestion") if isinstance(result.get("card_suggestion"), dict) else {}
        return {
            "title": _clean_semantic_title(str(result.get("title") or "")),
            "summary": str(result.get("summary") or ""),
            "tags": result.get("tags") if isinstance(result.get("tags"), list) else [],
            "body_markdown": str(result.get("body_markdown") or "").strip() or cleaned_body,
            "folder_suggestion": _normalize_editable_folder_suggestion(folder_suggestion),
            "card_suggestion": _normalize_editable_card_suggestion(card_suggestion),
            "warnings": result.get("warnings") if isinstance(result.get("warnings"), list) else [],
            "diagnostics": result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {},
        }

    def _light_cleanup_editable_body(self, body: str) -> str:
        text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.rstrip() for line in text.split("\n")]
        cleaned = "\n".join(lines).strip()
        cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
        return cleaned

    def _normalize_existing_folders(self, folders: list[str] | None) -> list[str]:
        normalized = ["Inbox"]
        for item in folders or []:
            folder = str(item or "").strip()
            if folder and folder not in normalized:
                normalized.append(folder)
        return normalized

    def _get_existing_folders(self, vault_path: Path) -> list[str]:
        folders = ["Inbox"]
        if vault_path.exists():
            for path in vault_path.iterdir():
                if path.is_dir() and not path.name.startswith(".") and path.name not in folders:
                    folders.append(path.name)
        return folders

    def _is_raw_package_request(self, user_request: str) -> bool:
        return bool(
            re.search(
                r"(保留|保存|入库).{0,12}(OCR|图片编号|本地图片|图片地址|原始抓取|原始包|抓取包)|"
                r"(OCR|图片编号|本地图片|图片地址|原始抓取|原始包|抓取包).{0,12}(保留|保存|入库)|"
                r"(不要|不许).{0,6}(清理|去噪|过滤)",
                user_request or "",
                flags=re.IGNORECASE,
            )
        )

    def _is_full_text_save_request(self, user_request: str) -> bool:
        return bool(
            re.search(
                r"(全文|原文).{0,12}(保存|入库|存库|归档|存进数据库|存到数据库|进数据库)|"
                r"(一字不改|原封不动|原样).{0,12}(保存|入库|存库|归档)",
                user_request or "",
            )
        )

    def _presentation_text(self, source_type: str, text: str, *, instruction: str = "") -> str:
        if source_type != "xiaohongshu":
            return text
        if self._is_raw_package_request(instruction):
            return text
        return _clean_xiaohongshu_author_content_text(text)

    def _detect_material_intent(self, user_request: str, emphasis: list[str]) -> str:
        if self._is_full_text_save_request(user_request) or self._is_raw_package_request(user_request):
            return "verbatim_full_text"
        if re.search(r"(原文增强版|增强版笔记|结合.{0,8}讨论|讨论.{0,8}整理进|学习讨论.{0,8}笔记)", user_request):
            return "enhanced_note"
        if emphasis:
            return "focused_note"
        return "refined_note"

    def _detect_detail_level(self, user_request: str) -> str:
        if self._is_high_fidelity_organize_request(user_request):
            return "source_like"
        if re.search(
            r"(尽可能|尽量|最大程度).{0,8}(提取|保留).{0,8}原文|贴近原文|原文质感|原文关键表述",
            user_request,
        ):
            return "source_like"
        if re.search(r"(一句话|几句话|简短|简要|短摘要|快速概括)", user_request):
            return "brief"
        if re.search(r"(详细整理|详细总结|深度总结|尽量保留细节|不要漏细节|完整整理成笔记|详细一些|详细点|展开一些)", user_request):
            return "deep"
        return "medium"

    def _detect_compression(self, user_request: str) -> str:
        if self._is_high_fidelity_organize_request(user_request):
            return "low"
        if re.search(
            r"(尽可能|尽量|最大程度).{0,8}(提取|保留).{0,8}原文|少压缩|稍加总结|贴近原文|原文质感",
            user_request,
        ):
            return "low"
        if re.search(r"(一句话|几句话|简短|简要|短摘要|快速概括|高度压缩)", user_request):
            return "high"
        return "medium"

    def _detect_preserve_items(self, user_request: str) -> list[str]:
        preserve: list[str] = []
        checks = [
            ("original_wording", r"(全文整理|高保真|原文|原句|关键表述|原文质感|贴近原文)"),
            ("data", r"(数据|数字|比例|金额|时间|日期|指标)"),
            ("examples", r"(案例|例子|例证|故事)"),
            ("methodology", r"(方法论|方法|做法|步骤|路径)"),
        ]
        for key, pattern in checks:
            if re.search(pattern, user_request) and key not in preserve:
                preserve.append(key)
        return preserve

    def _extract_emphasis(self, user_request: str) -> list[str]:
        if not user_request:
            return []
        generic_terms = {
            "内容", "重点内容", "重点", "核心观点", "要点",
            "展开", "阐述", "解释", "详细阐述", "详细解释",
            "着重展开", "这两部分", "这两个部分",
        }
        items: list[str] = []
        about_match = re.search(
            r"关于(.+?)(?:这两部分|这两个部分|两个部分|两部分|这部分|部分)(?:应该|需要|要|进行|$)",
            user_request,
        )
        if about_match:
            about_text = about_match.group(1).strip("：:，,。；; ")
            for item in re.split(r"\s*(?:和|以及|、|，|,)\s*", about_text):
                item = self._clean_emphasis_item(item)
                if item and item not in generic_terms and item not in items:
                    items.append(item)
        patterns = [
            r"(?:特别)?着重(?:阐述|解释)?([^。；;，,\n]+)",
            r"(?:重点|特别关注|聚焦)(?:阐述|解释|提取)?([^。；;，,\n]+)",
            r"([^。；;，,\n]+?)(?:那|这)?部分(?:详细一些|详细点|展开一些|重点讲|重点整理)",
            r"(?:只要|只看|仅看|仅保留|只保留)([^。；;，,\n]+)",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, user_request):
                item = self._clean_emphasis_item(match.group(1))
                if item and item not in generic_terms and item not in items:
                    items.append(item)
        return items

    def _clean_emphasis_item(self, item: str) -> str:
        item = (item or "").strip()
        item = item.strip("「」『』“”\"' ")
        item = re.sub(r"^(和|以及|：|:|\s)+", "", item).strip()
        item = re.sub(r"(这两部分|这两个部分|两个部分|两部分|这部分|部分)$", "", item).strip()
        item = item.strip("「」『』“”\"' ")
        return item

    def _needs_internal_plan(self, text: str) -> bool:
        return len(text or "") >= int(CAPTURE_SOURCE_CHAR_LIMIT * CAPTURE_INTERNAL_PLAN_BUDGET_RATIO)

    def _build_internal_plan_rule(self, source_text: str) -> str:
        if not self._needs_internal_plan(source_text):
            return (
                "- 先在内部判断用户真正想要的整理方式，再直接写最终稿。"
                "不要把内部计划输出给用户。\n"
            )
        return (
            "- 材料较长或接近上下文预算。先在内部制定阅读计划：判断主线、重点、支线、"
            "哪些部分要略读，哪些部分要精读。不要把内部计划输出给用户，只输出最终整理稿。\n"
        )

    def _build_density_rule(self, capture_intent: dict) -> str:
        detail_level = capture_intent.get("detail_level", "medium")
        if detail_level == "brief":
            return "- 用户要求偏简短：可以高度压缩，只保留结论和最核心理由。\n"
        if detail_level == "source_like":
            return (
                "- 用户要求尽量贴近原文：少压缩，保留原文关键表述、重要案例和数字，"
                "但仍要整理成可读文稿，不要逐段搬运原文。\n"
            )
        if detail_level == "deep":
            return (
                "- 用户要求偏详细：请保留更多分支、背景、案例、数字和论证细节，"
                "但仍然不要逐段搬运原文。\n"
            )
        return (
            "- 这不是几句话摘要。请根据材料复杂度决定篇幅；默认保留文章的"
            "主要论证层次、关键概念、重要转折和最终判断。"
            "对案例、数字、研究、人物和类比，不按数量机械保留；请判断它们在原文"
            "论证中的功能。凡是承担推进主线、支撑结论、区分概念或形成关键转折的内容，"
            "应自然保留；重复铺垫和装饰性表达可以压缩。\n"
        )

    def _build_detail_policy_rule(self, capture_intent: dict) -> str:
        detail_policy = str(capture_intent.get("detail_policy") or "").strip()
        if not detail_policy:
            return ""
        return f"- 内部规划给出的详略策略：{detail_policy}\n"

    def _build_material_intent_rule(self, capture_intent: dict) -> str:
        material_intent = capture_intent.get("material_intent", "refined_note")
        if material_intent == "enhanced_note":
            return (
                "- 这是原文增强版笔记：可以把原文、讨论形成的理解、知识库补充或外部资料补充组织成一篇笔记，"
                "但必须清楚区分来源层级。\n"
                "- 写作时可自然融合，不必机械分栏；涉及作者原意时标成原文依据，涉及本次对话推导时标成讨论形成的理解，"
                "不要把 AI 解释伪装成作者原意。\n"
            )
        if material_intent == "focused_note":
            return (
                "- 这是重点定向整理：全文主线要保留，用户点名关切要明显展开，"
                "不要因为重点展开而丢掉其他必要脉络。\n"
            )
        if material_intent == "verbatim_full_text":
            return (
                "- 这是全文高保真入库：raw 原文由系统另存为证据层；你负责把正式正文整理成可读 Markdown。\n"
                "- 默认不得改变原文原意、不得压缩成摘要、不得删除正文论证；可以添加分段、"
                "`##`/`###` heading、title-only `## 目录纲要` 和少量原文短语加粗。\n"
                "- 公众号关注、图源尾巴、声明、往期内容等明确页面噪音可以不放入正式正文。\n"
            )
        if material_intent == "refined_note":
            return "- 这是精炼整理稿：目标是形成可读、可信、便于入库的整理笔记。\n"
        return ""

    def _build_capture_body_structure_rule(self, source_text: str) -> str:
        text_len = len(source_text or "")
        if text_len >= CAPTURE_DIRECTORY_RENDER_CHAR_THRESHOLD:
            return (
                "- 这是长文入库整理：必须使用 `##`/`###` heading 承接主要章节。\n"
                "- 必须在正文开头生成 `## 目录纲要`，目录只能列章节标题，"
                "不要写“标题：摘要”；目录中的每一项必须在正文后续位置有同名 `##`/`###` heading，方便点击跳转。\n"
                "- 正文必须按语义自然分段，避免把长文塞成一个巨大段落；至少加粗一处原文已有的关键短语。\n"
            )
        return (
            "- 如果正文自然需要小标题，请使用 `##`/`###` Markdown heading；"
            "如果生成 `## 目录纲要`，目录项也必须能对应到正文同名 heading。\n"
        )

    def _clip_source_text(self, text: str) -> str:
        text = text or ""
        if len(text) <= CAPTURE_SOURCE_CHAR_LIMIT:
            return text
        head_limit = int(CAPTURE_SOURCE_CHAR_LIMIT * 0.7)
        tail_limit = CAPTURE_SOURCE_CHAR_LIMIT - head_limit
        return (
            text[:head_limit].rstrip()
            + "\n\n[中间内容因上下文预算截断，已保留开头和结尾。]\n\n"
            + text[-tail_limit:].lstrip()
        )

    def _revise_with_llm(
        self,
        *,
        instruction: str,
        capture_intent: dict,
        current_text: str,
        raw_text: str,
        source_type: str = "",
    ) -> str | LLMResult:
        source_text = (raw_text or current_text or "").strip()
        if self._should_use_chunked_reading(source_text, capture_intent):
            return self._revise_long_source(
                instruction=instruction,
                capture_intent=capture_intent,
                current_text=current_text,
                source_text=source_text,
                source_type=source_type,
            )
        prompt = self._build_revision_prompt(
            instruction=instruction,
            capture_intent=capture_intent,
            current_text=current_text,
            raw_text=raw_text,
            source_type=source_type,
        )
        result = self._call_llm(prompt)
        return self._retry_revision_for_directory_contract(result, prompt, source_text)

    def _should_use_chunked_reading(self, source_text: str, capture_intent: dict) -> bool:
        text_len = len(source_text or "")
        if text_len > CAPTURE_SOURCE_CHAR_LIMIT:
            return True
        if text_len >= CAPTURE_LONG_READING_CHAR_THRESHOLD:
            return capture_intent.get("detail_level", "medium") in ("medium", "deep")
        return False

    def _revise_long_source(
        self,
        *,
        instruction: str,
        capture_intent: dict,
        current_text: str,
        source_text: str,
        source_type: str = "",
    ) -> str | LLMResult:
        notes_result = self._build_long_source_notes(
            source_text=source_text,
            instruction=instruction,
            capture_intent=capture_intent,
            source_type=source_type,
        )
        if notes_result.incomplete:
            return notes_result
        prompt = self._build_long_revision_prompt(
            instruction=instruction,
            capture_intent=capture_intent,
            current_text="" if (current_text or "").strip() == source_text else current_text,
            section_notes=notes_result.text,
            source_type=source_type,
        )
        result = self._call_llm(prompt)
        return self._retry_revision_for_directory_contract(result, prompt, source_text)

    def _build_long_source_notes(
        self,
        *,
        source_text: str,
        instruction: str,
        capture_intent: dict,
        source_type: str = "",
    ) -> LLMResult:
        chunks = self._split_long_source_text(source_text)
        notes: list[str] = []
        incomplete = False
        for index, chunk in enumerate(chunks, start=1):
            prompt = self._build_section_note_prompt(
                instruction=instruction,
                capture_intent=capture_intent,
                chunk=chunk,
                index=index,
                total=len(chunks),
                source_type=source_type,
            )
            result = self._call_llm(prompt, return_meta=True)
            if not isinstance(result, LLMResult):
                result = LLMResult(
                    text=result,
                    incomplete=getattr(self, "_last_llm_incomplete", False),
                )
            note = self._clip_section_note(result.text)
            notes.append(f"## 分段 {index}/{len(chunks)}\n{note}")
            if result.incomplete:
                incomplete = True
                break
        joined_notes = "\n\n".join(notes).strip()
        if incomplete:
            return LLMResult(
                text=(
                    "长文分段整理未完整完成，暂不能作为最终草稿入库。\n\n"
                    f"{joined_notes}"
                ),
                incomplete=True,
            )
        return LLMResult(text=joined_notes, incomplete=False)

    def _split_long_source_text(
        self,
        text: str,
        max_chars: int = CAPTURE_LONG_CHUNK_CHAR_LIMIT,
    ) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        units = self._split_source_units(text, max_chars)
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for unit in units:
            unit_len = len(unit)
            separator_len = 2 if current else 0
            if current and current_len + separator_len + unit_len > max_chars:
                chunks.append("\n\n".join(current).strip())
                current = [unit]
                current_len = unit_len
            else:
                current.append(unit)
                current_len += separator_len + unit_len
        if current:
            chunks.append("\n\n".join(current).strip())
        return chunks

    def _split_source_units(self, text: str, max_chars: int) -> list[str]:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if len(paragraphs) <= 1:
            paragraphs = [text]
        units: list[str] = []
        for paragraph in paragraphs:
            if len(paragraph) <= max_chars:
                units.append(paragraph)
            else:
                units.extend(self._split_oversized_unit(paragraph, max_chars))
        return units

    def _split_oversized_unit(self, text: str, max_chars: int) -> list[str]:
        sentences = [s for s in re.split(r"(?<=[。！？!?；;])", text) if s.strip()]
        if len(sentences) <= 1:
            return [
                text[i:i + max_chars].strip()
                for i in range(0, len(text), max_chars)
                if text[i:i + max_chars].strip()
            ]
        units: list[str] = []
        current = ""
        for sentence in sentences:
            if len(sentence) > max_chars:
                if current:
                    units.append(current.strip())
                    current = ""
                units.extend(
                    sentence[i:i + max_chars].strip()
                    for i in range(0, len(sentence), max_chars)
                    if sentence[i:i + max_chars].strip()
                )
                continue
            if current and len(current) + len(sentence) > max_chars:
                units.append(current.strip())
                current = sentence
            else:
                current += sentence
        if current:
            units.append(current.strip())
        return units

    def _clip_section_note(self, note: str) -> str:
        note = (note or "").strip()
        if len(note) <= CAPTURE_SECTION_NOTE_CHAR_LIMIT:
            return note
        return note[:CAPTURE_SECTION_NOTE_CHAR_LIMIT].rstrip() + "\n[分段笔记已压缩截断]"

    def _build_section_note_prompt(
        self,
        *,
        instruction: str,
        capture_intent: dict,
        chunk: str,
        index: int,
        total: int,
        source_type: str = "",
    ) -> str:
        emphasis = capture_intent.get("emphasis", [])
        emphasis_rule = ""
        if emphasis:
            emphasis_rule = (
                f"- 用户点名关注：{', '.join(emphasis)}。如果本段相关，明确记下依据；"
                "如果不相关，简单说明无明显相关即可。\n"
            )
        source_like_rule = ""
        if capture_intent.get("detail_level") == "source_like":
            source_like_rule = (
                "- 用户要求尽量贴近原文：本段笔记要少压缩，保留能支撑判断的原文关键表述、"
                "案例和数字；不要只抽象成一句结论。\n"
            )
        return (
            f"用户要求：{instruction}\n\n"
            "任务信息：\n"
            f"{json.dumps(capture_intent, ensure_ascii=False)}\n\n"
            f"这是长文分段阅读，第 {index}/{total} 段。"
            "这一步不是写最终稿，而是为最终整理保留本段信息。\n"
            "请输出紧凑分段笔记，覆盖：本段主题、关键事实/数字/实体、"
            "和全文主线的关系、与用户要求相关的信息。\n"
            "- 不要逐句搬运原文。\n"
            "- 不要写成最终文章。\n"
            "- 控制在约 800 中文字以内。\n"
            f"{self._build_detail_policy_rule(capture_intent)}"
            f"{emphasis_rule}"
            f"{source_like_rule}"
            f"{self._build_source_type_rule(source_type)}\n"
            f"分段原文：\n{chunk}\n\n"
            "请输出这一段的分段阅读笔记。"
        )

    def _build_long_revision_prompt(
        self,
        *,
        instruction: str,
        capture_intent: dict,
        current_text: str,
        section_notes: str,
        source_type: str = "",
    ) -> str:
        current = (current_text or "").strip()
        current_context = ""
        if current:
            current_context = (
                "当前草稿或已有整理（仅供判断用户想怎么改，不要被它限制）：\n"
                f"{current[:6000].strip()}\n\n"
            )
        emphasis = capture_intent.get("emphasis", [])
        emphasis_rule = ""
        if emphasis:
            emphasis_rule = (
                f"- 用户点名关注：{', '.join(emphasis)}。这些是用户问题的重点关切，"
                "不是整篇文章标题意义上的“重点”。请在最终整理稿里回应到位，但不要因此丢掉全文主线。\n"
            )
        return (
            f"用户要求：{instruction}\n\n"
            "任务信息：\n"
            f"{json.dumps(capture_intent, ensure_ascii=False)}\n\n"
            "你已经按原文顺序得到的分段阅读笔记，笔记覆盖了全文，不要再假设只有开头和结尾。\n"
            f"{current_context}"
            "工作方式：\n"
            f"{self._build_density_rule(capture_intent)}"
            f"{self._build_detail_policy_rule(capture_intent)}"
            f"{self._build_material_intent_rule(capture_intent)}"
            f"{self._build_capture_body_structure_rule(section_notes)}"
            "- 基于分段笔记直接写最终整理稿，由你决定详略、结构和自然小标题。\n"
            "- 不要机械套模板；不要输出内部计划；不要输出标签、摘要或任务合同。\n"
            "- 不要逐段搬运分段笔记；把它们综合成一篇可读的整理稿。\n"
            "- 不要输出“好的，我们开始”这类对话开场白；第一行就进入正文。\n"
            "- 如果用户明确说“只要/仅保留/删除其他内容”，才只处理指定部分。\n"
            f"{self._build_source_type_rule(source_type)}"
            f"{emphasis_rule}\n"
            f"分段阅读笔记：\n{section_notes}\n\n"
            "请直接输出最终整理稿。"
        )

    def _build_revision_prompt(
        self,
        *,
        instruction: str,
        capture_intent: dict,
        current_text: str,
        raw_text: str,
        source_type: str = "",
    ) -> str:
        """Build a revision prompt without duplicating the same source text."""
        current = (current_text or "").strip()
        raw = (raw_text or "").strip()
        source_text = self._clip_source_text(current or raw)
        if current and raw and current != raw:
            material = f"当前草稿：\n{self._clip_source_text(current)}\n\n原文：\n{self._clip_source_text(raw)}"
        else:
            material = f"待处理文本：\n{source_text}"
        emphasis = capture_intent.get("emphasis", [])
        emphasis_rule = ""
        if emphasis:
            emphasis_rule = (
                f"- 用户点名关注：{', '.join(emphasis)}。这些不是唯一内容，"
                "但你需要在内部计划里给它们更高权重。\n"
            )
        return (
            f"用户要求：{instruction}\n\n"
            "任务信息：\n"
            f"{json.dumps(capture_intent, ensure_ascii=False)}\n\n"
            "工作方式：\n"
            f"{self._build_internal_plan_rule(source_text)}"
            f"{self._build_density_rule(capture_intent)}"
            f"{self._build_detail_policy_rule(capture_intent)}"
            f"{self._build_material_intent_rule(capture_intent)}"
            f"{self._build_capture_body_structure_rule(source_text)}"
            "- 由你决定最终整理稿的详略和结构：可以精炼，可以保留必要细节，也可以使用少量自然小标题。\n"
            "- 不要逐段搬运原文；不要为了格式而格式化；不要机械套用固定模板。\n"
            "- 不要输出标签、摘要或任务合同；这些元信息由系统另行处理。\n"
            "- 不要输出“好的，我们开始”这类对话开场白；第一行就进入正文。\n"
            "- 如果用户明确说“只要/仅保留/删除其他内容”，才只处理指定部分。\n"
            f"{self._build_source_type_rule(source_type)}"
            f"{emphasis_rule}\n"
            f"{material}\n\n"
            "请直接输出最终整理稿。"
        )

    def _retry_revision_for_directory_contract(
        self,
        result: str | LLMResult,
        original_prompt: str,
        source_text: str,
    ) -> str | LLMResult:
        text = result.text if isinstance(result, LLMResult) else str(result)
        if isinstance(result, LLMResult) and result.incomplete:
            return result
        warning = _capture_presentation_contract_warning(text, source_text)
        if not warning:
            return result
        retry_prompt = (
            f"{original_prompt}\n\n"
            "上一次输出没有通过正式 Capture 的可读性合同："
            f"{warning}。请重新输出完整整理稿。长文必须包含 `## 目录纲要`、"
            "与目录对应的 `##`/`###` heading、自然分段和至少一处原文重点加粗；"
            "这些是入库硬合同，不能省略。"
        )
        return self._call_llm(retry_prompt)

    def _build_source_type_rule(self, source_type: str) -> str:
        if source_type != "xiaohongshu":
            return ""
        return (
            "- 这是小红书图文素材：先区分作者正文、图片卡片 OCR、评论、话题标签、来源链接和纯视觉描述。\n"
            "- 如果用户要求全文保存或一字不改，保留的是作者表达和图片卡片里的作者文字，"
            "不是 OCR/抓取运行包；不要保留本地图片路径、图片地址、OCR 空结果、下载失败、图片编号等过程噪音。\n"
            "- 图片 OCR 不要按“图片 1/图片 2”机械罗列；把承载观点的卡片文字并入正文结构。"
            "重复信息只保留更完整的一版。\n"
            "- 头像、人物外观、背景风景、封面装饰等没有知识观点的视觉描述默认丢弃。\n"
            "- 来源链接、话题标签和评论不要混进正文主线；除非用户明确要求整理评论，否则评论区只作为参考。\n"
        )

    def _create_revision_draft_from_capture(self, capture_id: str) -> Optional[dict]:
        capture = self.repo.get_by_id(capture_id)
        if not capture:
            return None

        vault_path = Path(self.config.vault_path).expanduser()
        capture_note = read_note(vault_path, capture.get("file_path", ""))
        raw_note = read_note(vault_path, capture.get("raw_path", ""))
        if not capture_note and not raw_note:
            return None

        capture_frontmatter, capture_body = capture_note if capture_note else ({}, "")
        raw_frontmatter, raw_body = raw_note if raw_note else ({}, "")
        title = (
            capture_frontmatter.get("title")
            or raw_frontmatter.get("title")
            or _derive_title("", capture_body or raw_body)
        )
        source = capture_frontmatter.get("source") or raw_frontmatter.get("source") or ""
        source_type = capture_frontmatter.get("source_type") or raw_frontmatter.get("source_type") or "note"
        raw_text = raw_body or capture_body
        draft_text = capture_body if raw_body else ""
        draft = self.draft_repo.create(
            raw_text=raw_text,
            source=source,
            source_type=source_type,
            title=title,
            word_count=len(raw_text),
            draft_text=draft_text,
            status="revision_requested",
        )
        return self.draft_repo.update(
            draft["id"],
            committed_capture_id=capture_id,
        )

    def commit_draft(self, draft_id: str) -> Optional[dict]:
        """确认草稿入库：写 _archive/raw/ 与 captures/，再登记正式 captures 表。"""
        total_started = time.perf_counter()
        timings = _empty_capture_stage_timings()
        previous_timings = getattr(self, "_active_capture_stage_timings", None)
        self._active_capture_stage_timings = timings
        try:
            return self._commit_draft_with_timings(draft_id, timings, total_started)
        finally:
            if previous_timings is None:
                try:
                    delattr(self, "_active_capture_stage_timings")
                except AttributeError:
                    pass
            else:
                self._active_capture_stage_timings = previous_timings

    def _commit_draft_with_timings(
        self,
        draft_id: str,
        timings: dict[str, int],
        total_started: float,
    ) -> Optional[dict]:
        draft = self.draft_repo.get_by_id(draft_id)
        if not draft:
            return None
        if draft.get("status") == "committed":
            return {"error": "草稿已入库，请勿重复提交"}
        if draft.get("status") == "abandoned":
            return {"error": "草稿已废弃，无法入库"}
        if draft.get("status") == "draft_incomplete":
            return {"error": "草稿还没生成完整，请先继续整理后再存库"}
        if (
            draft.get("status") == "committing"
            and not self.draft_repo.commit_claim_is_stale(draft_id)
        ):
            return {"error": "草稿正在入库，请勿重复提交"}

        stage_started = time.perf_counter()
        draft = self._finalize_draft_before_commit(draft)
        _add_capture_stage_timing(timings, "finalize_for_commit", stage_started)
        raise_if_cancelled("capture.commit.after_finalize")
        if draft is None:
            return None
        if "error" in draft:
            return draft
        if draft.get("status") == "draft_incomplete":
            return {"error": "草稿还没生成完整，请先继续整理后再存库"}

        presentation_error = self._capture_presentation_error(draft)
        if presentation_error:
            self.draft_repo.update(draft_id, status="draft_incomplete")
            return {"error": f"整理稿可读性合同未通过：{presentation_error}"}

        previous_status = str(draft.get("status") or "draft_ready")
        if previous_status == "committing":
            if draft.get("draft_text"):
                previous_status = "draft_ready"
            elif draft.get("committed_capture_id"):
                previous_status = "revision_requested"
            else:
                previous_status = "raw_extracted"
        claimed_draft = self.draft_repo.claim_for_commit(draft_id)
        if claimed_draft is None:
            current = self.draft_repo.get_by_id(draft_id)
            if current is None:
                return None
            if current.get("status") == "committed":
                return {"error": "草稿已入库，请勿重复提交"}
            if current.get("status") == "committing":
                return {"error": "草稿正在入库，请勿重复提交"}
            if current.get("status") == "abandoned":
                return {"error": "草稿已废弃，无法入库"}
            if current.get("status") == "draft_incomplete":
                return {"error": "草稿还没生成完整，请先继续整理后再存库"}
            return {"error": "草稿当前状态无法入库"}

        try:
            return self._commit_claimed_draft(
                draft_id,
                draft,
                timings,
                total_started,
            )
        finally:
            self.draft_repo.restore_commit_status(draft_id, previous_status)

    def _commit_claimed_draft(
        self,
        draft_id: str,
        draft: dict,
        timings: dict[str, int],
        total_started: float,
    ) -> dict:

        vault_path = Path(self.config.vault_path).expanduser()
        now = datetime.now()
        body = draft.get("draft_text") or draft.get("raw_text", "")
        draft_body_before_write = body
        summary = draft.get("summary", "")
        tags = draft.get("tags", [])
        capture_intent = draft.get("capture_intent") or {}
        is_editable_capture = capture_intent.get("entry_mode") == "editable_capture"
        is_raw_package = self._is_raw_package_intent(capture_intent)
        if not is_editable_capture and not is_raw_package and not summary and body.strip():
            tag_result = self.summarizer.summarize(body, self._get_existing_tags(vault_path))
            summary = tag_result.get("summary", "")
            tags = tag_result.get("tags", [])

        raise_if_cancelled("capture.commit.before_write")
        capture_id = self.draft_repo.reserve_capture_for_commit(draft_id)
        capture = self.repo.get_by_id(capture_id)
        if capture is None:
            raise RuntimeError("reserved capture was not created")
        meta = NoteMeta(
            id=capture["id"],
            title=draft.get("title", "") or "未命名",
            date=now.strftime("%Y-%m-%d"),
            created=now.isoformat(),
            source=draft.get("source", ""),
            source_type=draft.get("source_type", "note"),
        )
        raw_path = self._write_capture_note(
            vault_path,
            meta,
            draft.get("raw_text", ""),
            stage="raw",
            kind="capture_raw",
        )
        capture_meta = NoteMeta(
            id=capture["id"],
            title=meta.title,
            date=meta.date,
            created=meta.created,
            source=meta.source,
            source_type=meta.source_type,
            tags=tags,
            story=summary,
            word_count=len(body),
        )
        body_to_write = (
            body
            if self._is_full_text_save_intent(capture_intent)
            else _remove_redundant_capture_title(body, capture_meta.title)
        )
        file_path = self._write_capture_note(
            vault_path,
            capture_meta,
            body_to_write,
            stage="captures",
            kind="capture_note",
            extra_frontmatter=_capture_note_intent_frontmatter(capture_intent),
        )
        self.repo.update(capture["id"], raw_path=raw_path, file_path=file_path)
        if is_raw_package:
            library_postprocess = {
                "skipped": True,
                "reason": "raw_package_verbatim",
                "pipeline": {},
            }
        else:
            self._index_committed_identity(capture["id"], file_path)
            library_postprocess = self._postprocess_committed_library(
                capture["id"],
                render_outline=True,
            )
        final_note = read_note(vault_path, file_path)
        if final_note is not None:
            _final_frontmatter, final_body = final_note
            body = final_body
        draft_body_after_commit = body
        if (
            self._is_literal_raw_save_intent(capture_intent)
            and body.strip() == draft_body_before_write.strip()
        ):
            draft_body_after_commit = draft_body_before_write
        pipeline_error = self._long_capture_commit_pipeline_error(
            raw_text=draft.get("raw_text", ""),
            body=body,
            library_postprocess=library_postprocess,
            capture_intent=capture_intent,
        )
        if pipeline_error:
            self.draft_repo.update(
                draft_id,
                draft_text=draft_body_after_commit,
                word_count=len(draft_body_after_commit),
                status="committed",
                committed_capture_id=capture["id"],
                pending_capture_id="",
                summary=summary,
                tags=tags,
            )
            if summary:
                self._enrich_async(capture["id"], summary)
            return _attach_capture_stage_timings({
                "status": "committed",
                "draft_id": draft_id,
                "capture_id": capture["id"],
                "title": meta.title,
                "summary": summary,
                "tags": tags,
                "raw_path": raw_path,
                "file_path": file_path,
                "library_postprocess": library_postprocess,
                "warnings": [f"入库后验收警告：{pipeline_error}"],
            }, timings, total_started)
        self.draft_repo.update(
            draft_id,
            draft_text=draft_body_after_commit,
            word_count=len(draft_body_after_commit),
            status="committed",
            committed_capture_id=capture["id"],
            pending_capture_id="",
            summary=summary,
            tags=tags,
        )
        if summary:
            self._enrich_async(capture["id"], summary)
        return _attach_capture_stage_timings({
            "status": "committed",
            "draft_id": draft_id,
            "capture_id": capture["id"],
            "title": meta.title,
            "summary": summary,
            "tags": tags,
            "raw_path": raw_path,
            "file_path": file_path,
            "library_postprocess": library_postprocess,
        }, timings, total_started)

    def _long_capture_commit_pipeline_error(
        self,
        *,
        raw_text: str,
        body: str,
        library_postprocess: dict,
        capture_intent: dict,
    ) -> str:
        if self._is_raw_package_intent(capture_intent):
            return ""
        if max(len(raw_text or ""), len(body or "")) < CAPTURE_DIRECTORY_RENDER_CHAR_THRESHOLD:
            return ""
        if "## 目录纲要" not in (body or ""):
            return "长文缺少目录纲要"
        directory_warning = _directory_contract_warning(body)
        if directory_warning:
            return directory_warning
        if _has_capture_processing_note(body):
            return "长文正文仍包含整理说明"
        if not _directory_absolute_url_free(body):
            return "长文目录包含绝对链接"
        if _has_directory_content_heading_coverage_gap(body):
            return "长文目录未覆盖全部正文主章节"
        if not _directory_links_match_content_headings(body):
            return "长文目录链接未匹配正文 heading"
        pipeline = (
            library_postprocess.get("pipeline")
            if isinstance(library_postprocess, dict)
            and isinstance(library_postprocess.get("pipeline"), dict)
            else {}
        )
        if library_postprocess.get("error"):
            return f"Library 后处理失败：{library_postprocess['error']}"
        if pipeline and not pipeline.get("directory_rendered"):
            return "长文目录未渲染到正式正文"
        if pipeline and not pipeline.get("directory_heading_coverage_ready"):
            return "长文目录未覆盖全部正文主章节"
        if pipeline and not pipeline.get("directory_link_ready"):
            return "长文目录链接未匹配正文 heading"
        if pipeline and not pipeline.get("directory_absolute_url_free", True):
            return "长文目录包含绝对链接"
        if pipeline and not pipeline.get("processing_note_free", True):
            return "长文正文仍包含整理说明"
        if pipeline and not pipeline.get("outline_metadata_ready"):
            return "长文大纲 metadata 未写入"
        if pipeline and int(pipeline.get("section_embedding_count") or 0) <= 0:
            return "长文章节索引未写入"
        if pipeline and not pipeline.get("rag_section_ready"):
            return "长文章节 RAG 未就绪"
        return ""

    def _finalize_draft_before_commit(self, draft: dict) -> Optional[dict]:
        capture_intent = draft.get("capture_intent") if isinstance(draft.get("capture_intent"), dict) else {}
        if capture_intent.get("entry_mode") == "editable_capture":
            return draft
        raw_text = draft.get("raw_text", "")
        if not raw_text.strip():
            return draft
        instruction = (draft.get("instruction") or capture_intent.get("user_request") or "").strip()
        if self._is_raw_package_intent(capture_intent, instruction):
            if draft.get("draft_text"):
                return draft
            return self._finalize_verbatim_full_text_draft(draft, capture_intent, instruction)
        overcompressed_error = self._overcompressed_commit_error(draft)
        if overcompressed_error:
            self.draft_repo.update(draft["id"], status="draft_incomplete")
            return {"error": overcompressed_error}
        if draft.get("draft_text"):
            return draft
        revised = self.revise_draft(
            draft["id"],
            instruction or "整理成可读的知识文件",
            reuse_saved_intent=True,
        )
        if isinstance(revised, dict):
            overcompressed_error = self._overcompressed_commit_error(revised)
            if overcompressed_error:
                self.draft_repo.update(draft["id"], status="draft_incomplete")
                return {"error": overcompressed_error}
        return revised

    def _capture_presentation_error(self, draft: dict) -> str:
        capture_intent = draft.get("capture_intent") if isinstance(draft.get("capture_intent"), dict) else {}
        if capture_intent.get("entry_mode") == "editable_capture":
            return ""
        if self._is_raw_package_intent(capture_intent, str(draft.get("instruction") or "")):
            return ""
        return _capture_presentation_contract_warning(
            str(draft.get("draft_text") or ""),
            str(draft.get("raw_text") or ""),
        )

    def _finalize_verbatim_full_text_draft(
        self,
        draft: dict,
        capture_intent: dict,
        instruction: str,
    ) -> dict:
        draft_body = self._presentation_text(
            draft.get("source_type", ""),
            draft.get("raw_text", ""),
            instruction=instruction,
        )
        tag_result = {"summary": "", "tags": []}
        if not self._is_raw_package_intent(capture_intent, instruction):
            existing_tags = self._get_existing_tags(Path(self.config.vault_path).expanduser())
            summary_source = clean_text(draft.get("raw_text", "")) or draft.get("raw_text", "")
            stage_started = time.perf_counter()
            tag_result = self.summarizer.summarize(summary_source, existing_tags)
            _add_capture_stage_timing(
                getattr(self, "_active_capture_stage_timings", None),
                "metadata_summary",
                stage_started,
            )
        stage_started = time.perf_counter()
        updated = self.draft_repo.update(
            draft["id"],
            draft_text=draft_body,
            instruction=instruction,
            capture_intent=capture_intent,
            summary=tag_result.get("summary", ""),
            tags=tag_result.get("tags", []),
            status="draft_ready",
        )
        _add_capture_stage_timing(
            getattr(self, "_active_capture_stage_timings", None),
            "repo_update",
            stage_started,
        )
        return updated or draft

    def _overcompressed_commit_error(self, draft: dict) -> str:
        raw_text = str(draft.get("raw_text") or "")
        draft_text = str(draft.get("draft_text") or "")
        if not raw_text.strip() or not draft_text.strip():
            return ""
        capture_intent = draft.get("capture_intent") if isinstance(draft.get("capture_intent"), dict) else {}
        user_request = str(draft.get("instruction") or capture_intent.get("user_request") or "")
        if not self._is_high_fidelity_organize_request(user_request):
            return ""
        if len(raw_text) < CAPTURE_DIRECTORY_RENDER_CHAR_THRESHOLD:
            return ""
        if len(draft_text) >= int(len(raw_text) * CAPTURE_OVERCOMPRESSED_RATIO):
            return ""
        return "整理稿疑似压缩过度，请先修订为高保真全文整理后再入库"

    def _write_capture_note(
        self,
        vault_path: Path,
        meta: NoteMeta,
        body: str,
        *,
        stage: str,
        kind: str,
        extra_frontmatter: dict | None = None,
    ) -> str:
        relative_stage = "_archive/raw" if stage == "raw" else stage
        filename = make_note_filename(meta.title, meta.date, meta.id)
        relative_path = f"{relative_stage}/{filename}"
        if stage == "raw":
            frontmatter = {
                "id": meta.id,
                "title": meta.title,
                "date": meta.date,
                "source": meta.source,
                "source_type": meta.source_type,
                "status": "raw",
            }
        else:
            frontmatter = {
                "id": meta.id,
                "title": meta.title,
                "date": meta.date,
                "created": meta.created,
                "source": meta.source,
                "source_type": meta.source_type,
                "tags": meta.tags,
                "story": meta.story,
                "word_count": meta.word_count,
            }
            frontmatter.update(extra_frontmatter or {})
        content = (
            "---\n"
            f"{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
            "---\n\n"
            f"{body}"
        )
        return KnowledgeWriteService(vault_path).write_markdown_artifact(
            MarkdownWriteRequest(
                kind=kind,
                relative_path=relative_path,
                content=content,
                db_object_id=meta.id,
                db_table="captures",
            )
        ).file_path

    def _index_committed_identity(self, capture_id: str, file_path: str) -> None:
        """Build the identity embedding needed by Theme/RAG immediately after commit."""
        if not self.config.qwen_api_key:
            logger.warning(f"Identity embedding skipped for {capture_id}: qwen_api_key_not_configured")
            return
        vault_path = Path(self.config.vault_path).expanduser()
        note = read_note(vault_path, file_path)
        if not note:
            return
        frontmatter, body = note
        index_item = build_identity_index(capture_id, file_path, frontmatter, body)
        try:
            embedding = Embedder(api_key=self.config.qwen_api_key).embed(index_item.index_text)
            CaptureEmbeddingRepo(self.repo.conn).upsert(
                capture_id=index_item.capture_id,
                kind=index_item.kind,
                section_id=index_item.section_id,
                section_title=index_item.section_title,
                index_text=index_item.index_text,
                embedding=embedding,
                source_hash=index_item.source_hash,
                file_path=index_item.file_path,
            )
        except Exception as exc:
            logger.warning(f"Identity embedding failed for {capture_id}: {exc}")

    def _postprocess_committed_library(self, capture_id: str, *, render_outline: bool = True) -> dict:
        """Run one-capture Library maintenance after a successful commit."""
        try:
            outliner = Outliner(
                api_key=self.config.llm_api_key,
                model="deepseek-v4-flash",
                base_url=self.config.llm_base_url or "https://api.deepseek.com",
            )
            agent = LibraryAgent(
                self.config.vault_path,
                outliner=outliner,
                db_conn=self.conn,
                embedder=(
                    Embedder(api_key=self.config.qwen_api_key)
                    if self.config.qwen_api_key
                    else None
                ),
                min_outline_chars=2000,
            )
            return agent.maintain_capture(capture_id, apply=True, render_outline=render_outline)
        except Exception as exc:
            logger.warning(f"Library postprocess failed for {capture_id}: {exc}")
            return {"error": str(exc)}

    def _extract_and_clean(self, raw_input: str, source_url: str = "",
                           source_type: str = ""):
        """Extract and clean input, returning (ExtractionResult, cleaned) or error dict."""

        # Step 1: Extract
        extractor = next(e for e in self.extractors if e.can_handle(raw_input))
        extracted = extractor.extract(raw_input, source_url=source_url, source_type=source_type)

        # Step 2: Clean
        if getattr(extracted, "extraction_failed", False):
            failure = getattr(extracted, "failure", None)
            failure_message = (
                getattr(failure, "message", "")
                or extracted.raw_text.strip().strip("[]")
                or "无法获取正文"
            )
            failure_payload = (
                failure.to_dict()
                if failure is not None and hasattr(failure, "to_dict")
                else {
                    "code": "external_service_error",
                    "message": failure_message,
                    "retryable": False,
                }
            )
            return {
                "error": f"内容提取失败：{failure_message}",
                "extraction_failed": True,
                "failure": failure_payload,
                "hint": extracted.raw_text or "可以换成文本文件，或把正文复制粘贴给我重试。",
            }, ""

        if not extracted.raw_text.strip():
            return {"error": "内容提取失败：无法获取正文"}, ""

        cleaned = clean_text(extracted.raw_text)
        if not cleaned.strip():
            return {"error": "内容清洗后为空"}, ""
        if not is_meaningful_content(cleaned):
            return {
                "error": "提取内容不完整", "extraction_failed": True,
                "hint": "可以把内容复制粘贴给我重试。",
            }, ""
        return extracted, cleaned

    def finalize(self, capture_id: str, instruction: str) -> Optional[dict]:
        """Backward-compatible alias for updating a draft."""
        return self.revise_draft(capture_id, instruction)

    def reprocess(self, capture_id: str, instruction: str) -> Optional[dict]:
        """Backward-compatible alias for the second-step finalize operation."""
        return self.finalize(capture_id, instruction)

    def _get_existing_tags(self, vault_path: Path) -> list[str]:
        """从 captures/ 目录的 .md 文件中收集所有已有标签。"""
        tags = set()
        captures_dir = vault_path / "captures"
        if captures_dir.exists():
            for md_file in captures_dir.glob("*.md"):
                note = read_note(vault_path, f"captures/{md_file.name}")
                if note:
                    fm, _ = note
                    for t in fm.get("tags", []):
                        tags.add(t)
        return list(tags)

    def _enrich_async(self, capture_id: str, summary: str) -> None:
        import threading
        t = threading.Thread(target=self._do_enrich, args=(capture_id, summary), daemon=True)
        t.start()

    def _do_enrich(self, capture_id: str, summary: str, retries: int = 3) -> None:
        import time
        from agents.embedder import Embedder
        embedder = Embedder(api_key=self.config.qwen_api_key)
        for attempt in range(retries):
            try:
                emb = embedder.embed(summary)
                with connection_lock(self.conn):
                    self.repo.update(capture_id, embedding=emb)
                break
            except Exception as e:
                if attempt == retries - 1:
                    logger.warning(f"Embedding failed for {capture_id}: {e}")
                else:
                    time.sleep([60, 300, 900][attempt])

    def _call_llm(self, prompt: str, *, return_meta: bool = False) -> str | LLMResult:
        base_url = self.config.llm_base_url or "https://api.deepseek.com"
        headers = {
            "Authorization": f"Bearer {self.config.llm_api_key}",
            "Content-Type": "application/json",
        }
        messages = [
            {"role": "system", "content": "你是文本处理助手，按要求处理文本。"},
            {"role": "user", "content": prompt},
        ]
        parts: list[str] = []
        incomplete = False
        with httpx.Client(timeout=CAPTURE_LLM_TIMEOUT_SECONDS) as client:
            for attempt in range(CAPTURE_LLM_MAX_CONTINUATIONS + 1):
                body = {
                    "model": self.config.llm_model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": CAPTURE_LLM_MAX_TOKENS,
                }
                raise_if_cancelled("capture.before_llm")
                resp = client.post(
                    f"{base_url}/chat/completions",
                    headers=headers, json=body,
                )
                resp.raise_for_status()
                choice = resp.json()["choices"][0]
                raise_if_cancelled("capture.after_llm")
                content = choice["message"]["content"]
                parts.append(content)
                if choice.get("finish_reason") != "length":
                    incomplete = False
                    break
                incomplete = True
                if attempt >= CAPTURE_LLM_MAX_CONTINUATIONS:
                    break
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        "继续从上一条末尾写，不要重复已经写过的内容。"
                        "保持同一篇整理稿的结构和语气，直接续写。"
                    ),
                })
        text = "\n".join(part.strip() for part in parts if part.strip())
        self._last_llm_incomplete = incomplete
        if return_meta:
            return LLMResult(text=text, incomplete=incomplete)
        return text


def _parse_json_object(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    elif not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _enum_value(value: object, allowed: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized in allowed else None


def _string_list(value: object) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    result: list[str] = []
    generic_terms = {"内容", "重点", "重点内容", "全文", "文章", "材料", "核心内容"}
    for item in value:
        if not isinstance(item, str):
            return None
        cleaned = item.strip(" 「」『』“”\"'")
        if cleaned and cleaned not in generic_terms and cleaned not in result:
            result.append(cleaned)
    return result


def _clean_semantic_title(title: str) -> str:
    title = (title or "").strip().strip("#> -")
    title = re.sub(r"\s+", " ", title)
    if not title or title in {"无", "无标题", "未命名"}:
        return ""
    if _looks_like_generic_document_title(title):
        return ""
    return title[:80]


def _build_planning_source_preview(text: str, max_chars: int = 6000) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.65)
    tail = max_chars - head
    return (
        text[:head].rstrip()
        + "\n\n[中间内容省略，规划时仍需意识到这可能是长文。]\n\n"
        + text[-tail:].lstrip()
    )


def _derive_title(extracted_title: str | None, text: str) -> str:
    if extracted_title and extracted_title.strip():
        return extracted_title.strip()

    for line in text.splitlines():
        candidate = line.strip().strip("#> -")
        if candidate:
            candidate = re.split(r"[。！？!?；;\n]", candidate, maxsplit=1)[0].strip()
            return candidate[:40] or "未命名"
    return "未命名"


def _derive_capture_title(extracted_title: str | None, text: str, source_type: str = "") -> str:
    if source_type == "document":
        markdown_title = _first_markdown_h1(text)
        if markdown_title:
            return markdown_title
        if extracted_title and _looks_like_generic_document_title(extracted_title):
            return _derive_title("", text)
    return _derive_title(extracted_title, text)


def _looks_like_generic_document_title(title: str) -> bool:
    cleaned = re.sub(r"\s+", " ", (title or "").strip())
    if not cleaned:
        return True
    return bool(
        re.fullmatch(
            r"(?:doc|document|file)[_-]?[\w-]+.*|"
            r"(?:整理稿|文字稿)(?:[_ -]?(?:副本|copy)|[（(]\d+[）)]|[_ -]?\d+)?",
            cleaned,
            flags=re.I,
        )
    )


def _remove_leading_duplicate_title(body: str, title: str) -> str:
    if not body or not title:
        return body
    lines = body.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return body
    first = lines[index].strip()
    normalized_first = _normalize_title_for_duplicate_check(re.sub(r"^#{1,6}\s+", "", first))
    normalized_title = _normalize_title_for_duplicate_check(title)
    if normalized_first != normalized_title:
        return body
    del lines[index]
    while index < len(lines) and not lines[index].strip():
        del lines[index]
    result = "\n".join(lines).lstrip("\n")
    if body.endswith("\n") and result and not result.endswith("\n"):
        result += "\n"
    return result


def _normalize_title_for_duplicate_check(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip().strip("#> -"))
    return value.casefold()


def _remove_redundant_capture_title(body: str, title: str) -> str:
    cleaned = _remove_leading_duplicate_title(body, title)
    cleaned = _remove_redundant_directory_root_title(cleaned, title)
    cleaned = _remove_first_duplicate_markdown_heading(cleaned, title)
    return cleaned


def _remove_redundant_directory_root_title(body: str, title: str) -> str:
    if "## 目录纲要" not in (body or ""):
        return body
    lines = body.splitlines()
    in_directory = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.fullmatch(r"##\s+目录纲要", stripped):
            in_directory = True
            continue
        if not in_directory:
            continue
        if not stripped:
            continue
        if not re.match(r"^[-*+]\s+", stripped):
            break
        item_title = re.sub(r"^[-*+]\s+", "", stripped).strip()
        if _normalize_title_for_duplicate_check(item_title) == _normalize_title_for_duplicate_check(title):
            del lines[index]
            while index < len(lines):
                next_line = lines[index]
                if not next_line.strip():
                    index += 1
                    continue
                if not re.match(r"^\s+[-*+]\s+", next_line):
                    break
                lines[index] = re.sub(r"^\s{2}", "", next_line, count=1)
                index += 1
        break
    result = "\n".join(lines)
    if body.endswith("\n") and result and not result.endswith("\n"):
        result += "\n"
    return result


def _remove_first_duplicate_markdown_heading(body: str, title: str) -> str:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or re.fullmatch(r"##\s+目录纲要", stripped) or re.match(r"^[-*+]\s+", stripped):
            continue
        match = re.match(r"^#{2,6}\s+(.+?)\s*$", stripped)
        if match and _normalize_title_for_duplicate_check(match.group(1)) == _normalize_title_for_duplicate_check(title):
            del lines[index]
            while index < len(lines) and not lines[index].strip():
                del lines[index]
            break
        break
    result = "\n".join(lines)
    if body.endswith("\n") and result and not result.endswith("\n"):
        result += "\n"
    return result


def _capture_note_intent_frontmatter(capture_intent: dict) -> dict:
    if not isinstance(capture_intent, dict):
        return {}
    if capture_intent.get("entry_mode") != "editable_capture":
        return {}
    return {
        "entry_mode": "editable_capture",
        "title_source": capture_intent.get("title_source") or "ai",
        "folder_path": capture_intent.get("folder_path") or "Inbox",
    }


def _first_markdown_h1(text: str) -> str:
    for line in (text or "").splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line.strip())
        if match:
            return match.group(1).strip()[:80]
    return ""


def _strip_draft_preamble(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    paragraphs = re.split(r"\n\s*\n", text)
    while paragraphs:
        first = paragraphs[0].strip()
        normalized = re.sub(r"[\W_]+", "", first.strip("。.!！"))
        if normalized in {"好的我们开始", "好的开始", "好我们开始", "开始"}:
            paragraphs.pop(0)
            continue
        if first in {"---", "----", "——"}:
            paragraphs.pop(0)
            continue
        break
    return "\n\n".join(paragraphs).strip()


def _directory_contract_warning(text: str) -> str:
    directory_titles = _draft_directory_titles(text)
    if not directory_titles:
        return ""
    heading_titles = _draft_heading_titles(text)
    for title in directory_titles:
        if title in heading_titles:
            continue
        prefix = re.split(r"[：:]", title, maxsplit=1)[0].strip()
        if prefix and prefix in heading_titles:
            return "目录项包含摘要式冒号说明"
    missing = [title for title in directory_titles if title not in heading_titles]
    if missing:
        return "目录项缺少同名正文 heading: " + ", ".join(missing[:3])
    return ""


def _capture_presentation_contract_warning(text: str, source_text: str) -> str:
    if max(len(text or ""), len(source_text or "")) < CAPTURE_DIRECTORY_RENDER_CHAR_THRESHOLD:
        return ""
    if "## 目录纲要" not in (text or ""):
        return "长文缺少目录纲要"
    if not _draft_heading_titles(text):
        return "长文缺少正文分章节"
    directory_warning = _directory_contract_warning(text)
    if directory_warning:
        return directory_warning
    if not re.search(r"\*\*[^*\n]+\*\*", text or ""):
        return "长文缺少重点加粗"
    prose_blocks = [
        block.strip()
        for block in re.split(r"\n\s*\n", text or "")
        if block.strip()
        and not block.lstrip().startswith("#")
        and not re.match(r"^[-*+]\s+", block.lstrip())
    ]
    if prose_blocks and max(len(block) for block in prose_blocks) > 1200:
        return "长文缺少自然分段"
    return ""


def _draft_directory_titles(text: str) -> list[str]:
    lines = (text or "").splitlines()
    titles: list[str] = []
    in_directory = False
    saw_item = False
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"##\s+目录纲要", stripped):
            in_directory = True
            saw_item = False
            continue
        if not in_directory:
            continue
        if stripped and saw_item and not re.match(r"^[-*+]\s+", stripped):
            break
        item = re.match(r"^[-*+]\s+(.+?)\s*$", stripped)
        if item:
            titles.append(_plain_draft_directory_title(item.group(1)))
            saw_item = True
    return titles


def _draft_heading_titles(text: str) -> set[str]:
    return {
        match.group(1).strip()
        for match in re.finditer(r"^#{2,3}\s+(.+?)\s*$", text or "", flags=re.MULTILINE)
        if match.group(1).strip() != "目录纲要"
    }


def _plain_draft_directory_title(value: str) -> str:
    title = re.sub(r"^#+\s*", "", value or "").strip()
    link = re.fullmatch(r"\[([^\]]+)\]\(#[^)]+\)", title)
    if link:
        title = link.group(1).strip()
    return title


def _clean_xiaohongshu_author_content_text(text: str) -> str:
    """Build a loyal author-content draft from a Xiaohongshu OCR evidence pack."""
    title = _first_markdown_title(text)
    kept: list[str] = []
    skip_comments = False
    seen_blocks: set[str] = set()

    for raw_block in re.split(r"\n\s*\n", text or ""):
        block = raw_block.strip()
        if not block:
            continue

        if block.startswith("## 评论区摘录"):
            skip_comments = True
            continue
        if skip_comments:
            if re.match(r"^##\s+", block):
                skip_comments = False
            else:
                continue

        cleaned = _clean_xiaohongshu_author_block(block, title=title)
        if not cleaned:
            continue
        normalized = re.sub(r"\s+", "", cleaned)
        if normalized in seen_blocks:
            continue
        seen_blocks.add(normalized)
        kept.append(cleaned)

    if title:
        body = [item for item in kept if item.strip() != f"# {title}"]
        return "\n\n".join([f"# {title}", *body]).strip()
    return "\n\n".join(kept).strip() or text


def _clean_xiaohongshu_author_block(block: str, *, title: str = "") -> str:
    lines: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _is_xiaohongshu_runtime_noise(line):
            continue
        if _is_xiaohongshu_topic_line(line):
            continue
        if _is_xiaohongshu_visual_noise(line):
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", line)
        if heading:
            heading_text = heading.group(1).strip()
            if heading_text in {"正文文案", "图片内容", "评论区摘录"}:
                continue
            if re.fullmatch(r"图片\s*\d+", heading_text):
                continue
        if title and line == title:
            continue
        if line in {"观察"}:
            continue
        if re.fullmatch(r"\d+\s*/\s*\d+", line):
            continue
        lines.append(line)

    cleaned = "\n".join(lines).strip()
    if _is_xiaohongshu_visual_noise(cleaned):
        return ""
    return cleaned


def _empty_capture_stage_timings() -> dict[str, int]:
    return {key: 0 for key in CAPTURE_STAGE_TIMING_KEYS}


def _add_capture_stage_timing(
    timings: dict[str, int] | None,
    key: str,
    started: float,
) -> None:
    if timings is None or key not in CAPTURE_STAGE_TIMING_KEYS:
        return
    elapsed_ms = max(int((time.perf_counter() - started) * 1000), 0)
    timings[key] = max(int(timings.get(key) or 0), 0) + elapsed_ms


def _attach_capture_stage_timings(
    result: dict | None,
    timings: dict[str, int],
    total_started: float,
) -> dict | None:
    _add_capture_stage_timing(timings, "total", total_started)
    if isinstance(result, dict):
        result["stage_timings_ms"] = dict(timings)
    return result


def _normalize_editable_folder_suggestion(suggestion: dict) -> dict:
    mode = str(suggestion.get("mode") or "").strip()
    folder_path = str(suggestion.get("folder_path") or "").strip()
    reason = str(suggestion.get("reason") or "").strip()
    if mode not in {"existing", "new"}:
        mode = "existing" if not folder_path or folder_path == "Inbox" else "new"
    if not folder_path:
        folder_path = "Inbox"
        mode = "existing"
    if not reason:
        reason = "AI 未返回明确文件夹建议，先放入 Inbox 等待人工确认。"
    return {
        "mode": mode,
        "folder_path": folder_path,
        "reason": reason,
    }


def _normalize_editable_card_suggestion(suggestion: dict) -> dict:
    should_generate = bool(suggestion.get("should_generate"))
    reason = str(suggestion.get("reason") or "").strip()
    cards = suggestion.get("cards") if isinstance(suggestion.get("cards"), list) else []
    if not reason:
        should_generate = False
        reason = "AI 未返回有效卡片建议，默认暂不生成 Garden Card。"
    return {
        "should_generate": should_generate,
        "reason": reason,
        "cards": cards,
    }


def _normalize_editable_title_source(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    if normalized in {"ai", "user"}:
        return normalized
    return None


def _editable_ai_suggestions_status(
    metadata: dict,
    *,
    organize: bool,
    title: str = "",
    validate_title: bool = True,
) -> str:
    if not organize:
        return "not_generated"
    diagnostics = metadata.get("diagnostics") if isinstance(metadata, dict) else {}
    if isinstance(diagnostics, dict) and diagnostics:
        return "failed"
    if validate_title and not _clean_semantic_title(title):
        return "failed"
    if not str(metadata.get("summary") or "").strip():
        return "failed"
    tags = metadata.get("tags") if isinstance(metadata, dict) else []
    if not isinstance(tags, list) or not [str(tag).strip() for tag in tags if str(tag).strip()]:
        return "failed"
    folder_suggestion = metadata.get("folder_suggestion") if isinstance(metadata, dict) else {}
    if not isinstance(folder_suggestion, dict) or not str(folder_suggestion.get("folder_path") or "").strip():
        return "failed"
    card_suggestion = metadata.get("card_suggestion") if isinstance(metadata, dict) else {}
    if not isinstance(card_suggestion, dict) or not str(card_suggestion.get("reason") or "").strip():
        return "failed"
    return "generated"


def _is_xiaohongshu_runtime_noise(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("来源："):
        return True
    if stripped.startswith("本地图片："):
        return True
    if stripped.startswith("图片地址："):
        return True
    if stripped in {"图片 OCR 结果为空。", "图片下载失败，无法 OCR。"}:
        return True
    if re.fullmatch(r"https?://\S+", stripped):
        return True
    return False


def _is_xiaohongshu_topic_line(line: str) -> bool:
    stripped = line.strip()
    if "[话题]" in stripped:
        return True
    tags = re.findall(r"#\S+", stripped)
    return bool(tags) and len("".join(tags)) >= len(stripped.replace(" ", "")) * 0.7


def _is_xiaohongshu_visual_noise(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 140:
        return False
    if re.search(r"图片\s*\d+\s*描述[:：]", stripped):
        return True
    visual_terms = (
        "男子", "女性", "男人", "女人", "人物", "人像", "身穿", "穿着",
        "面带微笑", "站在", "坐在", "栏杆", "背景", "天际线", "海面",
        "纯白色", "日落", "城市", "配图", "照片", "封面",
    )
    knowledge_terms = (
        "AI", "agent", "Agent", "Codex", "Claude", "Cursor", "subagent",
        "模型", "训练", "RL", "prompt", "harness", "工具", "上下文",
    )
    has_visual = any(term in stripped for term in visual_terms)
    has_knowledge = any(term in stripped for term in knowledge_terms)
    return has_visual and not has_knowledge


def _first_markdown_title(text: str) -> str:
    for line in (text or "").splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            return match.group(1).strip()
    return ""

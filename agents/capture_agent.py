"""Capture Agent — two-step capture flow: extract→raw, then finalize→captures."""
from __future__ import annotations

import logging
import json
import re
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
import yaml

from agents.cleaner import clean_text, is_meaningful_content
from agents.markdown_writer import write_note, read_note, NoteMeta
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
from agents.library_agent import LibraryAgent
from agents.outliner import Outliner
from db.repo import CaptureDraftRepo, CaptureRepo
from db.embedding_repo import CaptureEmbeddingRepo
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


@dataclass
class CaptureConfig:
    llm_api_key: str = ""
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_base_url: str = ""
    qwen_api_key: str = ""
    vault_path: str = "~/Obsidian/hermes-vault"
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
        vault_path=vault.get("path", "~/Obsidian/hermes-vault"),
        db_path=database.get("path", "./data/captures.db"),
    )


class CaptureAgent:
    def __init__(self, config: CaptureConfig):
        self.config = config
        self.conn = init_db(config.db_path)
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
        """提取内容并保存为 SQLite 草稿；只有 commit 后才写 Obsidian。"""
        extracted, cleaned = self._extract_and_clean(raw_input, source_url, source_type)
        if isinstance(extracted, dict):
            return extracted

        title = _derive_capture_title(extracted.title, cleaned, extracted.source_type)
        source_body = extracted.raw_text
        capture_intent_obj = self._build_capture_intent_with_planning(
            instruction,
            source_text=cleaned,
            title_hint=title,
        )
        title = capture_intent_obj.semantic_title or title
        capture_intent = capture_intent_obj.to_dict()
        stored_raw_text = source_body if self._is_full_text_save_intent(capture_intent) else cleaned
        draft = self.draft_repo.create(
            raw_text=stored_raw_text,
            source=extracted.source_url or source_url,
            source_type=extracted.source_type or source_type or "note",
            title=title,
            word_count=len(stored_raw_text),
            instruction=instruction,
            capture_intent=capture_intent,
        )
        if instruction.strip():
            if self._is_full_text_save_intent(capture_intent):
                existing_tags = self._get_existing_tags(Path(self.config.vault_path).expanduser())
                tag_result = self.summarizer.summarize(cleaned, existing_tags)
                return self.draft_repo.update(
                    draft["id"],
                    draft_text=stored_raw_text,
                    instruction=instruction,
                    capture_intent=capture_intent,
                    summary=tag_result.get("summary", ""),
                    tags=tag_result.get("tags", []),
                    status="draft_ready",
                )
            try:
                return self.revise_draft(draft["id"], instruction)
            except (httpx.TimeoutException, TimeoutError) as exc:
                saved = self.draft_repo.update(
                    draft["id"],
                    instruction=instruction,
                    capture_intent=capture_intent,
                    status="raw_extracted",
                ) or draft
                saved["revision_error"] = str(exc) or type(exc).__name__
                return saved
        return draft

    def revise_draft(self, draft_id: str, instruction: str) -> Optional[dict]:
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
        capture_intent = self._build_capture_intent_with_planning(
            instruction,
            source_text=draft.get("raw_text", "") or current_text,
            title_hint=draft.get("title", ""),
        ).to_dict()
        if self._is_full_text_save_intent(capture_intent):
            source_text = draft.get("raw_text", "") or current_text
            existing_tags = self._get_existing_tags(Path(self.config.vault_path).expanduser())
            summary_source = clean_text(source_text) or source_text
            tag_result = self.summarizer.summarize(summary_source, existing_tags)
            return self.draft_repo.update(
                draft_id,
                draft_text=source_text,
                instruction=instruction,
                capture_intent=capture_intent,
                summary=tag_result.get("summary", ""),
                tags=tag_result.get("tags", []),
                status="draft_ready",
            )
        llm_result = self._revise_with_llm(
            instruction=instruction,
            capture_intent=capture_intent,
            current_text=current_text,
            raw_text=draft.get("raw_text", ""),
        )
        if isinstance(llm_result, LLMResult):
            new_text = _strip_draft_preamble(llm_result.text)
            incomplete = llm_result.incomplete
        else:
            new_text = _strip_draft_preamble(llm_result)
            incomplete = getattr(self, "_last_llm_incomplete", False)

        if incomplete:
            updated = self.draft_repo.update(
                draft_id,
                draft_text=new_text,
                instruction=instruction,
                capture_intent=capture_intent,
                status="draft_incomplete",
            )
            if updated is not None:
                updated["revision_error"] = "llm_output_incomplete"
            return updated

        existing_tags = self._get_existing_tags(Path(self.config.vault_path).expanduser())
        tag_result = self.summarizer.summarize(new_text, existing_tags)
        return self.draft_repo.update(
            draft_id,
            draft_text=new_text,
            instruction=instruction,
            capture_intent=capture_intent,
            summary=tag_result.get("summary", ""),
            tags=tag_result.get("tags", []),
            status="draft_ready",
        )

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
        must_preserve = [] if coverage == "selected_sections" else [
            "source_fidelity",
            "main_thread",
            "user_named_focus",
        ]
        if material_intent == "enhanced_note" and "source_layering" not in must_preserve:
            must_preserve.append("source_layering")
        output_shape = ["full_text"] if material_intent == "verbatim_full_text" else ["natural_draft"]
        return CaptureIntent(
            user_request=user_request,
            material_intent=material_intent,
            task_type=task_type,
            coverage=coverage,
            emphasis=emphasis,
            must_preserve=must_preserve,
            output_shape=output_shape,
            detail_level=self._detect_detail_level(user_request),
            compression=self._detect_compression(user_request),
            preserve=preserve,
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

    def _is_full_text_save_request(self, user_request: str) -> bool:
        return bool(
            re.search(
                r"(全文|原文).{0,12}(保存|入库|存库|归档|存进数据库|存到数据库|进数据库)|"
                r"(一字不改|原封不动|原样).{0,12}(保存|入库|存库|归档)",
                user_request or "",
            )
        )

    def _detect_material_intent(self, user_request: str, emphasis: list[str]) -> str:
        if self._is_full_text_save_request(user_request):
            return "verbatim_full_text"
        if re.search(r"(原文增强版|增强版笔记|结合.{0,8}讨论|讨论.{0,8}整理进|学习讨论.{0,8}笔记)", user_request):
            return "enhanced_note"
        if emphasis:
            return "focused_note"
        return "refined_note"

    def _detect_detail_level(self, user_request: str) -> str:
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
            ("original_wording", r"(原文|原句|关键表述|原文质感|贴近原文)"),
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
        if material_intent == "refined_note":
            return "- 这是精炼整理稿：目标是形成可读、可信、便于入库的整理笔记。\n"
        return ""

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
    ) -> str | LLMResult:
        source_text = (raw_text or current_text or "").strip()
        if self._should_use_chunked_reading(source_text, capture_intent):
            return self._revise_long_source(
                instruction=instruction,
                capture_intent=capture_intent,
                current_text=current_text,
                source_text=source_text,
            )
        prompt = self._build_revision_prompt(
            instruction=instruction,
            capture_intent=capture_intent,
            current_text=current_text,
            raw_text=raw_text,
        )
        return self._call_llm(prompt)

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
    ) -> str | LLMResult:
        notes_result = self._build_long_source_notes(
            source_text=source_text,
            instruction=instruction,
            capture_intent=capture_intent,
        )
        if notes_result.incomplete:
            return notes_result
        prompt = self._build_long_revision_prompt(
            instruction=instruction,
            capture_intent=capture_intent,
            current_text="" if (current_text or "").strip() == source_text else current_text,
            section_notes=notes_result.text,
        )
        return self._call_llm(prompt)

    def _build_long_source_notes(
        self,
        *,
        source_text: str,
        instruction: str,
        capture_intent: dict,
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
            f"{source_like_rule}\n"
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
            "- 基于分段笔记直接写最终整理稿，由你决定详略、结构和自然小标题。\n"
            "- 不要机械套模板；不要输出内部计划；不要输出标签、摘要、目录或任务合同。\n"
            "- 不要逐段搬运分段笔记；把它们综合成一篇可读的整理稿。\n"
            "- 不要输出“好的，我们开始”这类对话开场白；第一行就进入正文。\n"
            "- 如果用户明确说“只要/仅保留/删除其他内容”，才只处理指定部分。\n"
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
            "- 由你决定最终整理稿的详略和结构：可以精炼，可以保留必要细节，也可以使用少量自然小标题。\n"
            "- 不要逐段搬运原文；不要为了格式而格式化；不要机械套用固定模板。\n"
            "- 不要输出标签、摘要、提纲、目录或任务合同；这些元信息由系统另行处理。\n"
            "- 不要输出“好的，我们开始”这类对话开场白；第一行就进入正文。\n"
            "- 如果用户明确说“只要/仅保留/删除其他内容”，才只处理指定部分。\n"
            f"{emphasis_rule}\n"
            f"{material}\n\n"
            "请直接输出最终整理稿。"
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
        draft = self.draft_repo.get_by_id(draft_id)
        if not draft:
            return None
        if draft.get("status") == "committed":
            return {"error": "草稿已入库，请勿重复提交"}
        if draft.get("status") == "abandoned":
            return {"error": "草稿已废弃，无法入库"}
        if draft.get("status") == "draft_incomplete":
            return {"error": "草稿还没生成完整，请先继续整理后再存库"}

        vault_path = Path(self.config.vault_path).expanduser()
        now = datetime.now()
        body = draft.get("draft_text") or draft.get("raw_text", "")
        summary = draft.get("summary", "")
        tags = draft.get("tags", [])
        if not summary and body.strip():
            tag_result = self.summarizer.summarize(body, self._get_existing_tags(vault_path))
            summary = tag_result.get("summary", "")
            tags = tag_result.get("tags", [])

        capture = self.repo.insert()
        meta = NoteMeta(
            id=capture["id"],
            title=draft.get("title", "") or "未命名",
            date=now.strftime("%Y-%m-%d"),
            created=now.isoformat(),
            source=draft.get("source", ""),
            source_type=draft.get("source_type", "note"),
        )
        raw_path = write_note(vault_path, meta, draft.get("raw_text", ""), stage="raw")
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
        file_path = write_note(vault_path, capture_meta, body, stage="captures")
        self.repo.update(capture["id"], raw_path=raw_path, file_path=file_path)
        self._index_committed_identity(capture["id"], file_path)
        library_postprocess = self._postprocess_committed_library(
            capture["id"],
            render_outline=True,
        )
        self.draft_repo.update(
            draft_id,
            status="committed",
            committed_capture_id=capture["id"],
            summary=summary,
            tags=tags,
        )
        if summary:
            self._enrich_async(capture["id"], summary)
        return {
            "status": "committed",
            "draft_id": draft_id,
            "capture_id": capture["id"],
            "title": meta.title,
            "summary": summary,
            "tags": tags,
            "raw_path": raw_path,
            "file_path": file_path,
            "library_postprocess": library_postprocess,
        }

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
        if not extracted.raw_text.strip():
            return {"error": "内容提取失败：无法获取正文"}, ""

        # Step 2: Clean
        if getattr(extracted, "extraction_failed", False):
            return {
                "error": "内容提取失败：无法获取正文",
                "extraction_failed": True,
                "hint": extracted.raw_text or "可以换成文本文件，或把正文复制粘贴给我重试。",
            }, ""

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
                resp = client.post(
                    f"{base_url}/chat/completions",
                    headers=headers, json=body,
                )
                resp.raise_for_status()
                choice = resp.json()["choices"][0]
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
    if re.fullmatch(r"doc_[\w-]+.*|整理稿(?:\(\d+\))?", title, flags=re.I):
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
    return _derive_title(extracted_title, text)


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

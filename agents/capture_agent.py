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
from agents.extractors.base import BaseExtractor
from agents.extractors.link_extractor import LinkExtractor
from agents.extractors.text_extractor import TextExtractor
from agents.extractors.voice_extractor import VoiceExtractor
from agents.extractors.image_extractor import ImageExtractor
from db.repo import CaptureDraftRepo, CaptureRepo
from db.schema import init_db

logger = logging.getLogger(__name__)
CAPTURE_LLM_TIMEOUT_SECONDS = 300
CAPTURE_LLM_MAX_TOKENS = 8000
CAPTURE_LLM_MAX_CONTINUATIONS = 2
CAPTURE_SOURCE_CHAR_LIMIT = 24000
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
    task_type: str
    coverage: str
    emphasis: list[str]
    must_preserve: list[str]
    output_shape: list[str]
    draft_guidance: str = "let_model_decide"

    def to_dict(self) -> dict:
        return {
            "user_request": self.user_request,
            "task_type": self.task_type,
            "coverage": self.coverage,
            "emphasis": self.emphasis,
            "must_preserve": self.must_preserve,
            "output_shape": self.output_shape,
            "draft_guidance": self.draft_guidance,
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
            LinkExtractor(),
            VoiceExtractor(api_key=config.qwen_api_key),
            ImageExtractor(api_key=config.qwen_api_key),
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

        title = _derive_title(extracted.title, cleaned)
        capture_intent = self._build_capture_intent(instruction).to_dict()
        draft = self.draft_repo.create(
            raw_text=cleaned,
            source=extracted.source_url or source_url,
            source_type=extracted.source_type or source_type or "note",
            title=title,
            word_count=len(cleaned),
            instruction=instruction,
            capture_intent=capture_intent,
        )
        if instruction.strip():
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
        capture_intent = self._build_capture_intent(instruction).to_dict()
        prompt = self._build_revision_prompt(
            instruction=instruction,
            capture_intent=capture_intent,
            current_text=current_text,
            raw_text=draft.get("raw_text", ""),
        )
        llm_result = self._call_llm(prompt)
        if isinstance(llm_result, LLMResult):
            new_text = llm_result.text
            incomplete = llm_result.incomplete
        else:
            new_text = llm_result
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
            if re.search(r"(只要|仅保留|只保留|删除其他|不要其他|仅要)", user_request)
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
        must_preserve = [] if coverage == "selected_sections" else [
            "source_fidelity",
            "main_thread",
            "user_named_focus",
        ]
        return CaptureIntent(
            user_request=user_request,
            task_type=task_type,
            coverage=coverage,
            emphasis=emphasis,
            must_preserve=must_preserve,
            output_shape=["natural_draft"],
        )

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
            r"(?:只要|仅保留|只保留)([^。；;，,\n]+)",
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
            "- 由你决定最终整理稿的详略和结构：可以精炼，可以保留必要细节，也可以使用少量自然小标题。\n"
            "- 不要逐段搬运原文；不要为了格式而格式化；不要机械套用固定模板。\n"
            "- 不要输出标签、摘要、提纲、目录或任务合同；这些元信息由系统另行处理。\n"
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
        """确认草稿入库：写 raw/ 与 captures/，再登记正式 captures 表。"""
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
        }

    def _extract_and_clean(self, raw_input: str, source_url: str = "",
                           source_type: str = ""):
        """Extract and clean input, returning (ExtractionResult, cleaned) or error dict."""

        # Step 1: Extract
        extractor = next(e for e in self.extractors if e.can_handle(raw_input))
        extracted = extractor.extract(raw_input, source_url=source_url, source_type=source_type)
        if not extracted.raw_text.strip():
            return {"error": "内容提取失败：无法获取正文"}, ""

        # Step 2: Clean
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


def _derive_title(extracted_title: str | None, text: str) -> str:
    if extracted_title and extracted_title.strip():
        return extracted_title.strip()

    for line in text.splitlines():
        candidate = line.strip().strip("#> -")
        if candidate:
            candidate = re.split(r"[。！？!?；;\n]", candidate, maxsplit=1)[0].strip()
            return candidate[:40] or "未命名"
    return "未命名"

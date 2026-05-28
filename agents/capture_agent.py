"""Capture Agent — two-step capture flow: extract→raw, then finalize→captures."""
from __future__ import annotations

import logging
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


@dataclass
class CaptureConfig:
    llm_api_key: str = ""
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_base_url: str = ""
    qwen_api_key: str = ""
    vault_path: str = "~/Obsidian/hermes-vault"
    db_path: str = "./data/captures.db"


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
        draft = self.draft_repo.create(
            raw_text=cleaned,
            source=extracted.source_url or source_url,
            source_type=extracted.source_type or source_type or "note",
            title=title,
            word_count=len(cleaned),
        )
        if instruction.strip():
            return self.revise_draft(draft["id"], instruction)
        return draft

    def revise_draft(self, draft_id: str, instruction: str) -> Optional[dict]:
        """按用户指令生成或更新草稿，不写入正式知识库。"""
        draft = self.draft_repo.get_by_id(draft_id)
        if not draft:
            return None
        if draft.get("status") in ("committed", "abandoned"):
            return {"error": f"草稿已{'入库' if draft['status'] == 'committed' else '废弃'}，无法修改"}
        current_text = draft.get("draft_text") or draft.get("raw_text", "")

        prompt = (
            f"用户要求对以下文本执行操作：{instruction}\n\n"
            f"当前草稿：\n{current_text}\n\n"
            f"原文：\n{draft.get('raw_text', '')[:7000]}\n\n"
            f"请执行用户要求的操作，直接输出处理后的文本。"
        )
        new_text = self._call_llm(prompt)
        existing_tags = self._get_existing_tags(Path(self.config.vault_path).expanduser())
        tag_result = self.summarizer.summarize(new_text, existing_tags)
        return self.draft_repo.update(
            draft_id,
            draft_text=new_text,
            instruction=instruction,
            summary=tag_result.get("summary", ""),
            tags=tag_result.get("tags", []),
            status="draft_ready",
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

    def _call_llm(self, prompt: str) -> str:
        base_url = self.config.llm_base_url or "https://api.deepseek.com"
        headers = {
            "Authorization": f"Bearer {self.config.llm_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.config.llm_model,
            "messages": [
                {"role": "system", "content": "你是文本处理助手，按要求处理文本。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 4000,
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers=headers, json=body,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]


def _derive_title(extracted_title: str | None, text: str) -> str:
    if extracted_title and extracted_title.strip():
        return extracted_title.strip()

    for line in text.splitlines():
        candidate = line.strip().strip("#> -")
        if candidate:
            candidate = re.split(r"[。！？!?；;\n]", candidate, maxsplit=1)[0].strip()
            return candidate[:40] or "未命名"
    return "未命名"

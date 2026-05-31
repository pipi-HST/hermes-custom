"""RAG Agent bridge."""
from __future__ import annotations
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.markdown_writer import read_note
from agents.capture_agent import load_config
from agents.managed_sections import is_managed_section_title
from agents.rag_agent import RagAgent, format_answer
from agents.embedder import Embedder
from bridge.result_envelope import source_ref, success_envelope
from db.embedding_repo import CaptureEmbeddingRepo
from db.vector_search import search as vector_search
from db.schema import init_db
from db.repo import CaptureRepo

logger = logging.getLogger(__name__)

_LOG_PATH = Path(__file__).parent.parent / "data" / "rag.log"

def _log(msg: str) -> None:
    """Append a timestamped line to data/rag.log (always writes, regardless of logging config)."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

_config = None
_repo = None

_HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$", re.MULTILINE)

def _init():
    global _config, _repo
    if _config is None:
        _config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
        _repo = CaptureRepo(init_db(_config.db_path))
    return _config, _repo

def search_knowledge_base(query: str) -> dict:
    config, repo = _init()
    _log(f"QUERY: {query}")
    logger.info("[RAG] query=%r", query)

    embedder = Embedder(api_key=config.qwen_api_key)
    used_fallback = False
    used_index = False
    try:
        results = _indexed_search(query, repo, embedder, limit=5)
        used_index = bool(results)
        _log(f"INDEX: {len(results)} hits")
        logger.info("[RAG] index_search returned %d results", len(results))
    except Exception as e:
        _log(f"INDEX_FAIL: {e}")
        logger.warning("[RAG] index_search failed: %s", e)
        results = []

    if not results:
        try:
            results = vector_search(query, repo, embedder, top_k=5)
            _log(f"VECTOR: {len(results)} hits")
            logger.info("[RAG] vector_search returned %d results", len(results))
        except Exception as e:
            _log(f"VECTOR_FAIL: {e}")
            logger.warning("[RAG] vector_search failed: %s", e)
            results = []

    results = [_hydrate_capture(config.vault_path, item) for item in results]

    if not results:
        used_fallback = True
        results = _fallback_text_search(query, config.vault_path, repo)
        _log(f"FALLBACK: {len(results)} hits")
        logger.info("[RAG] fallback text_search returned %d results", len(results))

    if not results:
        _log("NO_RESULTS")
        logger.info("[RAG] no results found")
        return success_envelope(
            agent="rag",
            intent="search_knowledge_base",
            chat_preview="知识库中没有找到相关信息。",
            meta={"query": query, "result_count": 0},
        )

    titles = [r.get("title") or r.get("file_path", "?") for r in results]
    _log(f"SOURCES: {json.dumps(titles, ensure_ascii=False)}")
    logger.info("[RAG] passing %d sources to LLM: %s", len(results), titles)

    rag = RagAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    resp = rag.query(query, results)
    _log(f"ANSWER: {len(resp.get('answer', ''))} chars")
    logger.info("[RAG] answer generated (%d chars)", len(resp.get("answer", "")))

    return success_envelope(
        agent="rag",
        intent="search_knowledge_base",
        chat_preview=format_answer(resp["answer"], resp["sources"]),
        source_map=[
            source_ref(
                role="evidence",
                type=(
                    "capture_section"
                    if item.get("matched_kind") == "section"
                    else "capture_note"
                ),
                id=item.get("id", ""),
                title=item.get("title") or item.get("file_path", ""),
                path=item.get("file_path", ""),
                section=item.get("section_title", ""),
            )
            for item in results
        ],
        meta={
            "query": query,
            "answer": resp["answer"],
            "used_index": used_index,
            "used_fallback": used_fallback,
            "result_count": len(results),
        },
    )


def _indexed_search(
    query: str,
    repo: CaptureRepo,
    embedder: Embedder,
    *,
    limit: int = 5,
) -> list[dict]:
    query_embedding = embedder.embed(query)
    rows = CaptureEmbeddingRepo(repo.conn).search(
        query_embedding,
        kinds=("identity", "section"),
        limit=limit * 4,
    )
    if not rows:
        return []

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("kind") == "section" and is_managed_section_title(
            str(row.get("section_title") or "")
        ):
            continue
        grouped.setdefault(row["capture_id"], []).append(row)

    results: list[dict] = []
    for capture_id, items in sorted(
        grouped.items(),
        key=lambda pair: max(item.get("score", 0) for item in pair[1]),
        reverse=True,
    ):
        capture = repo.get_by_id(capture_id)
        if not capture:
            continue
        section_hits = [item for item in items if item.get("kind") == "section"]
        best = max(
            section_hits or items,
            key=lambda item: item.get("score", 0),
        )
        results.append(
            {
                **capture,
                "matched_kind": best.get("kind", ""),
                "section_id": best.get("section_id", ""),
                "section_title": best.get("section_title", ""),
                "index_text": best.get("index_text", ""),
                "score": best.get("score", 0),
            }
        )
        if len(results) >= limit:
            break
    return results


def _hydrate_capture(vault_path: str, row: dict) -> dict:
    vault = Path(vault_path).expanduser()
    file_path = row.get("file_path") or row.get("raw_path") or ""
    frontmatter: dict = {}
    body = ""
    if file_path:
        note = read_note(vault, file_path)
        if note:
            frontmatter, body = note
    section_title = row.get("section_title", "")
    if row.get("matched_kind") == "section" and section_title:
        body = _extract_section_body(body, section_title) or body
    return {
        **row,
        "title": frontmatter.get("title", ""),
        "story": frontmatter.get("story", ""),
        "tags": frontmatter.get("tags", []),
        "body": body,
    }


def _extract_section_body(body: str, section_title: str) -> str:
    headings = list(_HEADING_RE.finditer(body))
    if not headings:
        return ""
    wanted = section_title.strip()
    for index, match in enumerate(headings):
        level = len(match.group(1))
        title = match.group(2).strip()
        if title != wanted:
            continue
        start = match.start()
        end = len(body)
        for later in headings[index + 1:]:
            if len(later.group(1)) <= level:
                end = later.start()
                break
        return body[start:end].strip()
    return ""


def _fallback_text_search(query: str, vault_path: str, repo: CaptureRepo) -> list[dict]:
    terms = [term.lower() for term in query.split() if term.strip()]
    if not terms:
        return []
    matches = []
    for row in repo.list_all(limit=200):
        item = _hydrate_capture(vault_path, row)
        haystack = " ".join(
            str(item.get(key, ""))
            for key in ("title", "story", "body")
        ).lower()
        score = sum(1 for term in terms if term in haystack)
        if score:
            matches.append((score, item))
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in matches[:5]]

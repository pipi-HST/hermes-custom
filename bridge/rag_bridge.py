"""RAG Agent bridge."""
from __future__ import annotations
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.markdown_writer import read_note
from agents.capture_agent import load_config
from agents.rag_agent import RagAgent, format_answer
from agents.embedder import Embedder
from bridge.result_envelope import source_ref, success_envelope
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
                type="capture_note",
                id=item.get("id", ""),
                title=item.get("title") or item.get("file_path", ""),
                path=item.get("file_path", ""),
            )
            for item in results
        ],
        meta={
            "query": query,
            "answer": resp["answer"],
            "used_fallback": used_fallback,
            "result_count": len(results),
        },
    )


def _hydrate_capture(vault_path: str, row: dict) -> dict:
    vault = Path(vault_path).expanduser()
    file_path = row.get("file_path") or row.get("raw_path") or ""
    frontmatter: dict = {}
    body = ""
    if file_path:
        note = read_note(vault, file_path)
        if note:
            frontmatter, body = note
    return {
        **row,
        "title": frontmatter.get("title", ""),
        "story": frontmatter.get("story", ""),
        "tags": frontmatter.get("tags", []),
        "body": body,
    }


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

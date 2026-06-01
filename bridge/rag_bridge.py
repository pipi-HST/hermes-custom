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
from agents.rag_context import build_rag_context_pack
from agents.rag_graph import build_graph_retrieval
from agents.embedder import Embedder
from bridge.file_artifacts import write_markdown_export
from bridge.result_envelope import artifact_ref, source_ref, success_envelope
from bridge.run_ledger import run_with_ledger
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

def search_knowledge_base(query: str, export_file: bool = False) -> dict:
    config, repo = _init()
    return run_with_ledger(
        repo.conn,
        agent="rag",
        intent="search_knowledge_base",
        trigger="wechat",
        input_ref={"query": query, "export_file": export_file},
        operation=lambda: _search_knowledge_base(
            query,
            config,
            repo,
            export_file=export_file,
        ),
    )


def _search_knowledge_base(
    query: str,
    config,
    repo: CaptureRepo,
    *,
    export_file: bool = False,
) -> dict:
    _log(f"QUERY: {query}")
    logger.info("[RAG] query=%r", query)

    embedder = Embedder(api_key=config.qwen_api_key)
    used_fallback = False
    used_index = False
    graph = None
    query_embedding = None
    try:
        query_embedding = embedder.embed(query)
        results = _indexed_search(
            query,
            repo,
            embedder,
            limit=5,
            query_embedding=query_embedding,
        )
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

    if results:
        if query_embedding is None:
            query_embedding = embedder.embed(query)
        graph = build_graph_retrieval(
            repo.conn,
            query_embedding=query_embedding,
            seed_hits=results,
            vault_path=config.vault_path,
            final_limit=6,
        )
        if graph["final_hits"]:
            results = graph["final_hits"]
        _log(f"GRAPH: {json.dumps(graph['trace'], ensure_ascii=False)}")

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
    context_pack = build_rag_context_pack(
        repo.conn,
        capture_hits=results,
        vault_path=config.vault_path,
        graph=graph,
    )

    rag = RagAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    resp = rag.query(query, results, context_pack=context_pack)
    _log(f"ANSWER: {len(resp.get('answer', ''))} chars")
    logger.info("[RAG] answer generated (%d chars)", len(resp.get("answer", "")))

    artifacts = []
    if export_file:
        artifacts.append(
            _rag_report_artifact(
                query=query,
                answer=resp["answer"],
                sources=resp["sources"],
                results=results,
            )
        )
    chat_preview = format_answer(resp["answer"], resp["sources"])
    if export_file:
        chat_preview = "已生成 RAG 完整回答与引用报告文件。我会把文件发给你。"

    return success_envelope(
        agent="rag",
        intent="search_knowledge_base",
        chat_preview=chat_preview,
        durable_artifacts=artifacts,
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
            "context_pack": {
                "capture_count": context_pack["capture_count"],
                "garden_count": context_pack["garden_count"],
                "theme_count": context_pack["theme_count"],
                "theme_group_count": context_pack.get("theme_group_count", 0),
            },
            "graph_trace": (graph or {}).get("trace", {}),
            "trace": {
                "retrieval": "indexed" if used_index else "fallback" if used_fallback else "vector",
                "source_titles": titles,
            },
            "export_file": export_file,
        },
    )


def _rag_report_artifact(
    *,
    query: str,
    answer: str,
    sources: list[dict],
    results: list[dict],
    export_dir: Path | None = None,
) -> dict:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_dir = export_dir or (Path(__file__).parent.parent / "data" / "exports" / "rag")
    lines = [
        "# RAG 完整回答与引用报告",
        "",
        "## 问题",
        "",
        query.strip(),
        "",
        "## 完整回答",
        "",
        answer.strip(),
        "",
        "## 完整引用",
        "",
    ]
    if sources:
        for index, source in enumerate(sources, 1):
            title = str(source.get("title") or "").strip()
            section = str(source.get("section") or source.get("section_title") or "").strip()
            path = str(source.get("path") or source.get("file_path") or "").strip()
            label = f"[{index}] {title or path or '未命名'}"
            if section:
                label += f" / {section}"
            lines.extend([f"- {label}", f"  - 路径：{path or '无'}"])
    else:
        for index, result in enumerate(results, 1):
            title = str(result.get("title") or "").strip()
            section = str(result.get("section_title") or "").strip()
            path = str(result.get("file_path") or "").strip()
            label = f"[{index}] {title or path or '未命名'}"
            if section:
                label += f" / {section}"
            lines.extend([f"- {label}", f"  - 路径：{path or '无'}"])

    file_path = write_markdown_export(
        base_dir=base_dir,
        stem=f"rag-answer-{timestamp}",
        content="\n".join(lines),
    )
    return artifact_ref(
        type="rag_answer_report",
        id=f"rag-answer-{timestamp}",
        title="RAG 完整回答与引用报告",
        path=file_path,
        status="created",
    )


def _indexed_search(
    query: str,
    repo: CaptureRepo,
    embedder: Embedder,
    *,
    limit: int = 5,
    query_embedding: bytes | None = None,
) -> list[dict]:
    if query_embedding is None:
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

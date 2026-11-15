"""RAG Agent bridge."""
from __future__ import annotations
import json
import logging
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import load_config
from agents.managed_sections import is_managed_section_title
from agents.rag_agent import (
    SELECTED_BODY_KEY,
    RagAgent,
    _dedupe_captures,
    format_answer,
    select_prompt_sources,
)
from agents.rag_citations import validate_citations
from agents.rag_context import build_rag_context_pack
from agents.rag_graph import build_graph_retrieval
from agents.embedder import Embedder
from bridge.capture_view import build_capture_view, hydrate_capture, read_capture_note
from bridge.file_artifacts import write_markdown_export
from bridge.context import KnowledgeContext
from bridge.knowledge_search import search_captures
from bridge.result_envelope import artifact_ref, source_ref, success_envelope
from bridge.run_ledger import run_with_ledger
from db.embedding_repo import CaptureEmbeddingRepo
from db.fts_tokenizer import (
    build_fts_match_query,
    build_fts_or_query,
    keyword_bigram_hits,
)
from db.vector_search import search as vector_search
from db.schema import init_db
from db.repo import CaptureRepo
from bridge.config_paths import load_bridge_config

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

# Relevance gate for pure vector hits.  Anything below this is only kept when
# it contains a literal query keyword (see ``_indexed_search``); otherwise old,
# loosely-related notes crowd out the material that actually answers the
# question.
# Calibrated on DashScope text-embedding-v4 (docs/calibration-dashscope.json:
# positive P10=0.4286, negative P95=0.4887 -> midpoint 0.46).  ``separation`` is
# negative, i.e. the distributions overlap: this is a first-pass filter only,
# the literal-keyword exemption and the synthesis guard handle the edges.
MIN_VECTOR_SCORE = 0.46

# Retrieval slots handed to the prompt.  Keyword hits get a reserved share so
# a literal match can never be pushed out by weaker vector neighbours.
MAX_RAG_SOURCES = 7
KEYWORD_RESERVED_SLOTS = 2

# Question scaffolding carries no retrieval signal, but it must be removed
# from the *edges* of a token only.  Replacing characters inside a word (the
# previous implementation) shredded real terms: 转介绍的目的 -> "和边界是",
# 什么是传话筒 -> "是传话筒", 解释器模式 -> "器模式".
_QUERY_LEAD_WORDS = re.compile(
    r"^(?:请问|帮我|麻烦|一下|说说|讲讲|介绍一下|解释一下|告诉我|"
    r"了解了|了解|读了|看了)+"
)
_QUERY_SHELL_PREFIX = re.compile(r"^(?:什么是|何为|为什么|怎么理解|如何理解|怎么|如何)")
_QUERY_SHELL_SUFFIX = re.compile(
    r"(?:之后应该怎么用|应该怎么用|是什么|是什么意思|有什么用|有哪些|"
    r"怎么理解|如何理解|怎么用|怎么设计|是多少|在哪里)+$"
)
_QUERY_STOPWORDS = frozenset(
    {
        "什么", "怎么", "如何", "哪些", "一下", "帮我", "请问", "关于",
        "之后", "应该", "了解", "理解", "解释", "介绍", "说说", "讲讲",
        "告诉", "知道",
    }
)
_QUERY_EDGE_PARTICLES = re.compile(r"^(?:是|有)|(?:了|吗|呢|呀|吧)$")
_KEYWORD_SPLIT_RE = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")


def _clean_query_token(token: str) -> str:
    """Strip question scaffolding from one token without touching its stem."""
    token = _QUERY_LEAD_WORDS.sub("", token)
    token = _QUERY_SHELL_PREFIX.sub("", token)
    token = _QUERY_SHELL_SUFFIX.sub("", token)
    if len(token) >= 3:
        token = _QUERY_EDGE_PARTICLES.sub("", token)
    return token.strip("-_. ")


def _extract_query_keywords(query: str) -> list[str]:
    """Extract meaningful 2+ character terms from a question.

    Splitting happens first and stopwords are matched against the **whole**
    token, so ``解释器模式`` keeps its ``解释`` while a lone ``怎么`` is dropped.
    ``请问一下，MCP 的 130秒 是什么？`` therefore yields ``["mcp", "130秒"]``.
    """
    text = str(query or "").strip().lower()
    if not text:
        return []
    keywords: list[str] = []
    for raw in _KEYWORD_SPLIT_RE.split(text):
        token = _clean_query_token(raw)
        if len(token) < 2 or token in _QUERY_STOPWORDS:
            continue
        if token not in keywords:
            keywords.append(token)
    return keywords


def _init():
    global _config, _repo
    if _config is None:
        _config = load_bridge_config(loader=load_config)
        _repo = CaptureRepo(init_db(_config.db_path))
    return _config, _repo

def search_knowledge_base(
    query: str,
    export_file: bool = False,
    context: KnowledgeContext | None = None,
) -> dict:
    if context is None:
        config, repo = _init()
    else:
        config = context.config
        repo = context.capture_repo()
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
            threshold=getattr(config, "min_vector_score", None),
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

    results = [
        _hydrate_capture(config.vault_path, item)
        for item in results
        if item.get("matched_kind") != "garden_card"
    ]

    # Literal keyword top-up: embeddings rank paraphrases well but can bury
    # exact technical terms (MCP, 130秒, 传话筒) behind loosely-related notes.
    seen_capture_ids = {str(item.get("id") or "") for item in results}
    keyword_hits = [
        item
        for item in _keyword_match_captures(query, repo, config.vault_path, limit=3)
        if str(item.get("id") or "") not in seen_capture_ids
    ][:KEYWORD_RESERVED_SLOTS]
    if keyword_hits:
        _log(f"KEYWORD: {len(keyword_hits)} hits")
        logger.info("[RAG] keyword top-up returned %d results", len(keyword_hits))

    # Curated Knowledge Cards are first-class RAG sources: they encode the
    # user's own conclusions, so they lead the context instead of trailing it.
    existing_ids = {str(item.get("id") or "") for item in results}
    new_cards = [
        card
        for card in _search_garden_cards(query, repo.conn, limit=3)
        if str(card.get("id") or "") not in existing_ids
    ]
    if new_cards:
        _log(f"CARDS: {len(new_cards)} hits")
        logger.info("[RAG] knowledge cards matched %d results", len(new_cards))

    # Assemble with reserved seats: cards lead, literal keyword hits are
    # guaranteed a slot, the rest is vector/graph evidence.  Appending them at
    # the tail and slicing results[:7] silently dropped the exact-term hits.
    slots_for_evidence = max(
        0, MAX_RAG_SOURCES - len(new_cards) - len(keyword_hits)
    )
    results = new_cards + results[:slots_for_evidence] + keyword_hits

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
            chat_preview="无知识库资料：当前本地知识库没有可引用的资料。",
            source_map=[],
            meta={"query": query, "result_count": 0, "answer": "无知识库资料：当前本地知识库没有可引用的资料。"},
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
    # Single point of numbering: dedupe + budget-trim ONCE, then use the
    # returned list for both the prompt and ``source_map``.  Rendering a
    # different (re-deduped) list is what used to shift every [n] by one.
    deduped_total = len(_dedupe_captures(results))
    results = select_prompt_sources(
        results, question=query, context_pack=context_pack
    )
    omitted_source_count = max(0, deduped_total - len(results))
    titles = [r.get("title") or r.get("file_path", "?") for r in results]
    if omitted_source_count:
        logger.info(
            "[RAG] prompt budget dropped %d sources", omitted_source_count
        )
    resp = rag.query(query, results, context_pack=context_pack)
    for item in results:  # internal field must never reach API/artifact
        item.pop(SELECTED_BODY_KEY, None)
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

    source_map = [
        _source_entry(item, index) for index, item in enumerate(results, 1)
    ]
    citation_validation = validate_citations(resp["answer"], source_map)
    repaired = False
    if not citation_validation.valid and source_map and hasattr(rag, "repair_citations"):
        try:
            repaired_answer = rag.repair_citations(resp["answer"], source_map)
            repaired_validation = validate_citations(repaired_answer, source_map)
            if repaired_validation.valid:
                resp["answer"] = repaired_answer
                citation_validation = repaired_validation
                repaired = True
                chat_preview = format_answer(resp["answer"], resp["sources"])
                if export_file:
                    chat_preview = "已生成 RAG 完整回答与引用报告文件。我会把文件发给你。"
        except Exception as exc:
            logger.warning("[RAG] citation repair failed: %s", exc)

    try:
        record_card_citations(repo.conn, resp["answer"], source_map, query=query)
    except Exception as exc:  # never fail an answer because bookkeeping failed
        logger.warning("[RAG] citation recording failed: %s", exc)

    return success_envelope(
        agent="rag",
        intent="search_knowledge_base",
        chat_preview=chat_preview,
        durable_artifacts=artifacts,
        source_map=source_map,
        warnings=citation_validation.warnings,
        meta={
            "query": query,
            "answer": resp["answer"],
            "used_index": used_index,
            "used_fallback": used_fallback,
            "result_count": len(results),
            "omitted_source_count": omitted_source_count,
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
            "citation_validation": citation_validation.to_meta(),
            "citation_repaired": repaired,
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


_CARD_SOURCE_TYPES = {"knowledge_card", "garden_card"}
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
# ``[1]`` inside a code block or a markdown link (``[1](url)``) is not a cite.
_CITED_REF_RE = re.compile(r"\[(\d+)\](?!\()")


def _extract_cited_refs(answer: str) -> set[int]:
    """Citation numbers actually used in prose, ignoring code and links."""
    text = _FENCED_CODE_RE.sub(" ", str(answer or ""))
    text = _INLINE_CODE_RE.sub(" ", text)
    return {int(match.group(1)) for match in _CITED_REF_RE.finditer(text)}


def record_card_citations(
    conn,
    answer: str,
    source_map: list[dict],
    *,
    query: str = "",
    run_id: str = "",
) -> list[str]:
    """Record only the cards the answer actually cited with ``[n]``.

    Retrieval hit is not usage: a card that merely matched a keyword must not
    accumulate ``citation_count``.  ``citation_events`` is append-only so the
    counter can always be recomputed and a read path never rewrites a row it
    just read (SQLite has one writer).
    """
    cited_refs = _extract_cited_refs(answer)
    if not cited_refs or conn is None:
        return []
    run_id = str(run_id or uuid.uuid4().hex)
    recorded: list[str] = []
    for item in source_map or []:
        if str(item.get("type") or "") not in _CARD_SOURCE_TYPES:
            continue
        try:
            ref = int(str(item.get("ref") or ""))
        except (TypeError, ValueError):
            continue
        card_id = str(item.get("id") or "")
        if ref not in cited_refs or not card_id:
            continue
        try:
            conn.execute(
                "INSERT INTO citation_events (id, card_id, run_id, query) VALUES (?, ?, ?, ?)",
                (uuid.uuid4().hex, card_id, str(run_id or ""), str(query or "")[:200]),
            )
        except sqlite3.OperationalError:
            continue
        recorded.append(card_id)
    if not recorded:
        return []
    # One transaction: the ledger row and the counter must never disagree.
    for card_id in recorded:
        conn.execute(
            "UPDATE garden_cards SET citation_count = COALESCE(citation_count, 0) + 1, "
            "last_cited_at = datetime('now') WHERE id = ?",
            (card_id,),
        )
    conn.commit()
    return recorded


def recompute_card_citation_counts(conn) -> int:
    """Rebuild ``citation_count`` / ``last_cited_at`` from ``citation_events``.

    The event table is the source of truth; the counters are a cache.  Any
    migration or demo re-seed that recreates cards must call this, otherwise
    the cache silently drifts back to zero while the ledger keeps its rows.
    """
    if conn is None:
        return 0
    conn.execute(
        """UPDATE garden_cards SET
             citation_count = COALESCE(
                 (SELECT COUNT(*) FROM citation_events ce
                   WHERE ce.card_id = garden_cards.id), 0),
             last_cited_at = (
                 SELECT MAX(ce.cited_at) FROM citation_events ce
                  WHERE ce.card_id = garden_cards.id
             )"""
    )
    conn.commit()
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM garden_cards WHERE COALESCE(citation_count, 0) > 0"
        ).fetchone()[0]
    )


def _source_entry(item: dict, index: int) -> dict:
    """Build one ``source_map`` entry for a capture hit or a knowledge card."""
    if (
        item.get("matched_kind") == "garden_card"
        or item.get("source_kind") == "garden_card"
    ):
        role = "viewpoint"
        source_type = "knowledge_card"
    else:
        role = "evidence"
        source_type = (
            "capture_section"
            if item.get("matched_kind") == "section"
            else "capture_note"
        )
    return source_ref(
        role=role,
        type=source_type,
        id=item.get("id", ""),
        title=item.get("title") or item.get("file_path", ""),
        path=item.get("file_path", ""),
        section=item.get("section_title", ""),
        ref=str(index),
        file_path=item.get("file_path", ""),
        excerpt=_source_excerpt(item),
        source_kind=str(item.get("source_kind") or ""),
        reading_session_id=str(item.get("reading_session_id") or ""),
        reading_draft_id=str(item.get("reading_draft_id") or ""),
    )


def _garden_card_match_score(
    keywords: list[str],
    needle: str,
    *,
    title: str,
    theme: str,
    one_liner: str,
    content: str,
    arguments: str,
) -> int:
    medium = " ".join(part for part in (theme, one_liner) if part)
    deep = " ".join(part for part in (content, arguments) if part)
    score = 0
    for keyword in keywords:
        if keyword in title:
            score += 3
        elif keyword in medium:
            score += 2
        elif keyword in deep:
            score += 1
    if needle and needle in title:
        score += 2
    return score


def _search_garden_cards(query: str, conn, limit: int = 3) -> list[dict]:
    """Recall the user's curated Knowledge Cards by literal keyword match.

    Cards are stored in the slim ``garden_cards`` table, so richer fields
    (``source_theme``/``content_markdown``/``file_path``) are read opportunistically
    via ``dict(row)``: older databases only have title/one_liner and still work.
    """
    if conn is None or limit <= 0:
        return []
    keywords = _extract_query_keywords(query)
    needle = str(query or "").strip().lower()
    if not keywords and not needle:
        return []
    try:
        rows = conn.execute(
            """SELECT * FROM garden_cards
               WHERE COALESCE(status, 'accepted') != 'archived'"""
        ).fetchall()
    except Exception as exc:
        logger.debug("[RAG] garden_cards lookup unavailable: %s", exc)
        return []

    scored: list[tuple[int, dict]] = []
    for row in rows:
        card = dict(row)
        title = str(card.get("title") or "")
        score = _garden_card_match_score(
            keywords,
            needle,
            title=title.lower(),
            theme=str(card.get("source_theme") or "").lower(),
            one_liner=str(card.get("one_liner") or "").lower(),
            content=str(card.get("content_markdown") or "").lower(),
            arguments=str(card.get("core_arguments") or "").lower(),
        )
        if score <= 0:
            continue
        scored.append((score, card))
    if not scored:
        return []
    scored.sort(key=lambda pair: pair[0], reverse=True)

    cards: list[dict] = []
    for _score, row in scored[:limit]:
        one_liner = str(row.get("one_liner") or "")
        cards.append(
            {
                "id": row.get("id", ""),
                "title": f"【知识卡片】{row.get('title') or ''}",
                "raw_title": row.get("title") or "",
                "body": row.get("content_markdown") or one_liner or "",
                "story": one_liner,
                "summary": one_liner,
                "section_title": row.get("source_theme")
                or row.get("card_kind")
                or "知识卡片",
                "matched_kind": "garden_card",
                "source_kind": "garden_card",
                "file_path": row.get("file_path")
                or f"garden/{row.get('id', '')}.md",
                "score": 0.85,
            }
        )
    return cards


def _row_text(row, key: str) -> str:
    """Read one column from a sqlite3.Row / dict without blowing up on mocks."""
    try:
        value = row[key]
    except (KeyError, IndexError):
        return ""
    return str(value or "")


def _keyword_match_captures(
    query: str,
    repo: CaptureRepo,
    vault_path: str,
    limit: int = 3,
) -> list[dict]:
    """Top up retrieval with literal keyword hits from the FTS5 index.

    The index stores ``capture_embeddings.index_text`` (identity + every
    section body), so a query can hit a sentence in section 3 without touching
    a single file.  Only the winning rows are hydrated from disk.
    """
    keywords = _extract_query_keywords(query)
    if not keywords or limit <= 0:
        return []
    rows = _fts_keyword_rows(repo.conn, keywords, limit=max(limit * 3, 6))
    if rows is None:
        # No FTS5 in this SQLite build: keep the pre-migration behaviour.
        return _legacy_keyword_match_captures(query, repo, vault_path, limit=limit)
    results: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        capture_id = _row_text(row, "capture_id")
        if not capture_id or capture_id in seen:
            continue
        seen.add(capture_id)
        # Fill the section fields BEFORE hydration: ``_hydrate_capture`` only
        # slices the body per section when ``matched_kind`` is already set.
        item = _hydrate_capture(
            vault_path,
            {
                "id": capture_id,
                "file_path": _row_text(row, "file_path"),
                "raw_path": _row_text(row, "raw_path"),
                "created_at": _row_text(row, "row_created_at"),
                "matched_kind": _row_text(row, "kind"),
                "section_id": _row_text(row, "section_id"),
                "section_title": _row_text(row, "section_title"),
                "index_text": _row_text(row, "index_text"),
            },
        )
        item["source_kind"] = _row_text(row, "kind")
        item["score"] = _bm25_rank_to_score(row["rank"])
        results.append(item)
        if len(results) >= limit:
            break
    return results


def _bm25_rank_to_score(rank: object) -> float:
    """Map bm25 (negative, lower is better) onto a 0..1 display score."""
    try:
        value = abs(float(rank))
    except (TypeError, ValueError):
        return 0.5
    return round(1.0 / (1.0 + value), 3)


def _fts_keyword_rows(conn, keywords: list[str], *, limit: int):
    """Query the CJK keyword index; ``None`` means FTS5 is unavailable.

    Exact phrases are tried first.  A compound Chinese question often yields a
    phrase that no document can contain (the bigrams span word boundaries), so
    when the phrase query under-fills the limit we top it up with a bigram OR
    query and merge by concrete row identity.
    """
    match = build_fts_match_query(keywords)
    if not match:
        return []
    rows = _run_fts_query(conn, match, limit=limit)
    if rows is None or len(rows) >= limit:
        return rows
    loose_match = build_fts_or_query(keywords)
    if not loose_match or loose_match == match:
        return rows
    extra = _run_fts_query(conn, loose_match, limit=limit)
    if extra is None:
        return rows
    merged = list(rows)
    seen = {_row_identity(row) for row in rows}
    for row in extra:
        if len(merged) >= limit:
            break
        identity = _row_identity(row)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(row)
    return merged


def _row_identity(row) -> tuple[str, str, str]:
    return (
        _row_text(row, "capture_id"),
        _row_text(row, "kind"),
        _row_text(row, "section_id"),
    )


def _run_fts_query(conn, match: str, *, limit: int):
    """Run one FTS MATCH; ``None`` only when this build has no FTS5 at all."""
    try:
        return conn.execute(
            """SELECT capture_search_fts.capture_id AS capture_id,
                      capture_search_fts.kind AS kind,
                      capture_search_fts.section_id AS section_id,
                      ce.section_title AS section_title,
                      ce.index_text AS index_text,
                      c.file_path AS file_path,
                      c.raw_path AS raw_path,
                      c.created_at AS row_created_at,
                      bm25(capture_search_fts, 0.0, 0.0, 0.0, 3.0, 1.0) AS rank
               FROM capture_search_fts
               JOIN captures c ON c.id = capture_search_fts.capture_id
               LEFT JOIN capture_embeddings ce
                 ON ce.capture_id = capture_search_fts.capture_id
                AND ce.kind       = capture_search_fts.kind
                AND ce.section_id = capture_search_fts.section_id
               WHERE capture_search_fts MATCH ?
                 AND COALESCE(c.review_state, '') != 'archived'
               ORDER BY rank
               LIMIT ?""",
            (match, int(limit)),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        # Only "this build has no FTS5" may fall back to the 500-file scan;
        # a malformed MATCH must not turn into a disk crawl on every query.
        message = str(exc).lower()
        if "no such table" in message or "no such module" in message:
            return None
        logger.warning("[RAG] keyword query failed: %s", exc)
        return []


def _legacy_keyword_match_captures(
    query: str,
    repo: CaptureRepo,
    vault_path: str,
    limit: int = 3,
) -> list[dict]:
    """Pre-FTS fallback: scan recent captures and hydrate each one.

    Kept only for SQLite builds without FTS5.  This is the implementation that
    read up to 500 Markdown files per query; it must never be the hot path.
    """
    keywords = _extract_query_keywords(query)
    if not keywords or limit <= 0:
        return []
    rows = []
    try:
        rows = repo.conn.execute(
            """SELECT c.*, m.metadata_json AS _capture_metadata_json
               FROM captures c
               LEFT JOIN capture_metadata m ON m.capture_id = c.id
               WHERE COALESCE(c.review_state, '') != 'archived'
               ORDER BY c.created_at DESC
               LIMIT 500"""
        ).fetchall()
    except Exception:
        try:
            rows = repo.conn.execute(
                """SELECT * FROM captures
                   WHERE COALESCE(review_state, '') != 'archived'
                   ORDER BY created_at DESC
                   LIMIT 500"""
            ).fetchall()
        except Exception:
            return []

    matches: list[tuple[int, dict]] = []
    for row in rows:
        raw = dict(row)
        metadata_text = str(raw.pop("_capture_metadata_json", "") or "").lower()
        item = hydrate_capture(vault_path, raw)
        # The slim captures table has no text columns, but mock/legacy callers
        # may supply title/summary/tags directly on the row.  Use both sources.
        raw_title = str(raw.get("title") or "")
        raw_summary = " ".join(
            [str(raw.get("summary") or ""), str(raw.get("tags") or "")]
        )
        if raw_title and not item.get("title"):
            item["title"] = raw_title
        if raw_summary.strip() and not item.get("summary"):
            item["summary"] = raw_summary.strip()
        title = str(item.get("title") or raw_title or "").lower()
        summary = " ".join(
            [
                str(item.get("summary") or ""),
                " ".join(str(tag) for tag in item.get("tags") or []),
                raw_summary,
                metadata_text,
            ]
        ).lower()
        score = 0
        for keyword in keywords:
            if keyword in title:
                score += 3
            elif keyword in summary:
                score += 2
        if score <= 0:
            continue
        matches.append((score, item))
    if not matches:
        return []
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            **item,
            "story": item.get("story") or item.get("summary") or "",
            "matched_kind": item.get("matched_kind") or "keyword",
            "source_kind": str(item.get("source_kind") or ""),
            "score": float(score) / 10.0,
        }
        for score, item in matches[:limit]
    ]


def _indexed_search(
    query: str,
    repo: CaptureRepo,
    embedder: Embedder,
    *,
    limit: int = 5,
    query_embedding: bytes | None = None,
    threshold: float | None = None,
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

    keywords = _extract_query_keywords(query)

    def _has_literal_keyword(row: dict) -> bool:
        haystack = f"{row.get('index_text') or ''} {row.get('section_title') or ''}".lower()
        for keyword in keywords:
            if keyword in haystack:
                return True
            # A compound CJK term rarely appears verbatim; two of its bigrams
            # inside one document is strong enough to count as a literal hit.
            if len(keyword) >= 5 and keyword_bigram_hits(keyword, haystack) >= 2:
                return True
        return False

    # Relevance gate: drop weak vector hits unless they contain a literal query
    # term.  Keeps stale/loosely-related notes out of the prompt without losing
    # exact technical matches.
    gate = MIN_VECTOR_SCORE if threshold is None else float(threshold)
    rows = [
        row
        for row in rows
        if float(row.get("score") or 0.0) >= gate or _has_literal_keyword(row)
    ]
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
    """RAG view: the shared capture view plus the aliases the prompt uses."""
    frontmatter, body = read_capture_note(vault_path, row)
    item = build_capture_view(row, frontmatter, body)
    section_title = row.get("section_title", "")
    if row.get("matched_kind") == "section" and section_title:
        item["body"] = _extract_section_body(body, section_title) or body
    return {
        **item,
        "story": frontmatter.get("story", ""),
        "source_kind": str(frontmatter.get("source_kind") or frontmatter.get("source_type") or ""),
        "reading_session_id": str(frontmatter.get("reading_session_id") or ""),
        "reading_draft_id": str(frontmatter.get("reading_draft_id") or ""),
        "output_type": str(frontmatter.get("output_type") or ""),
    }


def _source_excerpt(item: dict, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(item.get("body") or item.get("story") or "")).strip()
    return text[:limit]


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
    return search_captures(vault_path, repo, query, limit=5)

"""Reproducible retrieval benchmark for the Hermes knowledge base.

Why this script exists
----------------------
The first demo measured document-level Recall@5 on 6 documents — top-5 out of 6
is ~83% by pure chance, so the number could not distinguish a working retriever
from a random one.  This benchmark builds a 20-note / ~85-chunk / 12-card corpus
and scores four ablation groups on 20 labelled queries.

It is honest about the embedding engine: with no API key it uses a deterministic
char-ngram HashingVectorizer (offline, lexical) and labels the report
accordingly.  Numbers from that engine measure lexical overlap, not semantics;
run with a real embedding provider for semantic claims.

Usage::

    python scripts/eval_rag_benchmark.py
    python scripts/eval_rag_benchmark.py --json-out out/bench.json --md-out docs/BENCHMARK.md
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.capture_indexer import build_identity_index, build_section_indexes  # noqa: E402
from db.embedding_repo import CaptureEmbeddingRepo  # noqa: E402
from db.repo import CaptureRepo  # noqa: E402
from db.schema import init_db  # noqa: E402
from db.fts_tokenizer import build_fts_match_query  # noqa: E402

EMBED_DIM = 1024
ENGINE_OFFLINE = "char-ngram-blake2b-hash-1024"

# 6 deep notes, 4 sections each.
DOCS: list[dict[str, Any]] = [
    {
        "id": "cap_aipm_demand",
        "title": "AI 产品经理面试避坑指南：真需求与伪需求",
        "theme": "AI 产品面试",
        "summary": "用三步法判断一个 AI 功能是真实用户诉求还是为了 AI 而 AI。",
        "sections": [
            ("真需求的三层验证", "先看有没有人愿意为这件事付出时间或金钱，再看现有替代方案是否真的糟糕，最后看 AI 是否带来十倍而不是十 percent 的改善。"),
            ("伪需求的四种典型话术", "老板说要 AI、竞品都有、投资人喜欢、技术很酷，这四种都不是需求证据。把话术翻译成用户任务，任务说不清就是伪需求。"),
            ("面试中的反问框架", "面试官问你怎么判断需求真假，先给判断标准，再给一个反例，最后给出如果错了怎么快速止损。"),
            ("常见边界", "强监管场景、低容错场景、极低频场景，即使需求真实也不适合先上 AI。"),
        ],
    },
    {
        "id": "cap_telephone_game",
        "title": "多级 Agent 架构的隐形杀手：大模型传话筒效应",
        "theme": "Agent 与大模型",
        "summary": "每多一级 LLM 转发，原文就被有损压缩一次，路由 Agent 会擅自摘要工具参数。",
        "sections": [
            ("传话筒效应是怎么发生的", "主 Agent 读完长文后要生成 tool call，为了省输出 token，会把用户原文概括成三百字再塞进参数，原文在第一关就已经失真。"),
            ("四道调用的损耗叠加", "路由一次、意图规划一次、正文重写一次、结果总结一次，四道串起来等于四次有损压缩。"),
            ("破局三条铁律", "原文禁止经过模型参数；摘要与原文分区存放；编辑稿设置保留率护栏，低于阈值直接回退原文。"),
            ("适用边界", "短消息和指令型任务不受影响，只有长材料搬运才需要这套护栏。"),
        ],
    },
    {
        "id": "cap_latency_roi",
        "title": "从 130 秒到 20 秒：重构重型 Research 管线的延迟复盘",
        "theme": "Agent 与大模型",
        "summary": "七阶段串行审计把 P95 推到百秒级，砍掉四阶段后延迟下降而引用质量不降。",
        "sections": [
            ("七个阶段各自多慢", "规划、多路搜索、网页抽取、逐条审计、二次补搜、合成、引用校验，每一段都要一次模型调用或者一次网络往返。"),
            ("哪些阶段可以砍", "逐条 Evidence Critic 审计和 Repair Loop 的边际收益最低，检索结果的 rerank 分数和权威度已经承担了大部分筛选工作。"),
            ("并发抓取的收益", "把串行的逐 URL 抓取改成四路并发，抓取阶段从累加变成取最大值。"),
            ("ROI 结论", "延迟下降八成，引用准确率只下降个位数百分点，对个人知识库场景是正收益。"),
        ],
    },
    {
        "id": "cap_pkm_compare",
        "title": "个人知识库竞品拆解：Notion AI、NotebookLM、IMA 与 Readwise",
        "theme": "行业与产品拆解",
        "summary": "四类产品的差异在于证据层与内化层的边界怎么划。",
        "sections": [
            ("四类定位", "Notion 是工作台、NotebookLM 是资料问答、IMA 是中文轻量收藏、Readwise 是阅读高亮回流。"),
            ("证据层的保真差异", "NotebookLM 强制引用原文片段，Readwise 只保留高亮，工作台类产品最容易被 AI 改写污染。"),
            ("内化层的空缺", "四家都没有解决收藏之后如何变成自己的判断，卡片与专题是空位。"),
            ("可迁移的设计", "把资料当只读证据、把卡片当可写认知、把专题当输出单元，三层分开。"),
        ],
    },
    {
        "id": "cap_mcp",
        "title": "一文搞懂 MCP 与 Agent 工具调用收敛策略",
        "theme": "Agent 与大模型",
        "summary": "MCP 统一了工具接入协议，但工具数量膨胀会让路由准确率下降，需要收敛到少量意图。",
        "sections": [
            ("MCP 解决什么问题", "把每个工具的数据源适配从 M×N 变成 M+N，客户端只需要实现一次协议。"),
            ("工具数量与路由准确率", "工具描述边界重叠时，模型的工具选择准确率随工具数量增加而下降，二十个以上会明显抖动。"),
            ("收敛策略", "按用户意图收敛到八到十四个主干工具，细粒度能力收进参数枚举或者 facade。"),
            ("适用边界", "工具高度同质时收敛收益最大，工具差异极大时过早收敛会丢能力。"),
        ],
    },
    {
        "id": "cap_model_routing",
        "title": "大模型成本与延迟权衡：Model Routing 设计",
        "theme": "AI 产品面试",
        "summary": "大小模型协同的关键是把任务分档，而不是按 token 数拍脑袋。",
        "sections": [
            ("任务分档", "把请求分成抽取、改写、推理、创作四档，前三档小模型足够，创作与多步推理才上大模型。"),
            ("降级与兜底", "小模型置信度低时升级到大模型，比一开始就用大模型便宜一个数量级。"),
            ("成本测算", "按每千次请求计算，路由层本身也是一次模型调用，要算进成本。"),
            ("常见误区", "用平均 token 数决定路由，忽略了任务难度分布是长尾的。"),
        ],
    },
]

DISTRACTOR_TOPICS = [
    ("提拉米苏的家庭做法", "马斯卡彭、手指饼干、咖啡酒与冷藏时间。"),
    ("足球越位规则详解", "越位位置、参与进攻、判罚例外。"),
    ("阳台番茄种植指南", "光照、授粉、追肥与病虫害。"),
    ("城市骑行路线规划", "坡度、补给点、避开车流。"),
    ("基金定投的止盈策略", "目标收益、分批止盈、再平衡。"),
    ("咖啡手冲参数入门", "水温、粉水比、研磨度。"),
    ("露营装备清单", "帐篷、睡袋、炊具与照明。"),
    ("英语听力提升方法", "精听、跟读、影子练习。"),
    ("家庭收纳动线设计", "玄关、厨房、衣柜分区。"),
    ("手机摄影构图技巧", "三分法、引导线、留白。"),
    ("跑步膝伤预防", "跑量、力量训练、恢复。"),
    ("红酒入门产区", "波尔多、勃艮第、新世界。"),
    ("猫砂盆清洁频率", "结团砂、除臭、位置。"),
    ("旅行签证材料准备", "在职证明、流水、行程单。"),
]


@dataclass
class Unit:
    """One retrievable unit: a note (identity + sections) or a card."""

    unit_id: str
    kind: str  # capture | card
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


def _hash_vector(text: str):
    """Deterministic char-ngram hash vector (no sklearn, no network)."""
    import hashlib
    import re

    vector = np.zeros(EMBED_DIM, dtype=np.float32)
    normalized = re.sub(r"\s+", " ", str(text or "").lower())
    for n in (2, 3, 4):
        for index in range(len(normalized) - n + 1):
            gram = normalized[index : index + n]
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest, "big") % EMBED_DIM
            vector[bucket] += 1.0
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


_ACTIVE_ENGINE = "hash"
_EMBED_CACHE: dict[str, bytes] = {}


def _dashscope_vector(text: str):
    """Real semantic embedding via the configured DashScope key."""
    from agents.capture_agent import load_config
    from agents.embedder import Embedder

    config = load_config()
    key = str(getattr(config, "qwen_api_key", "") or "")
    if not key:
        raise RuntimeError("qwen.api_key is empty; cannot run dashscope engine")
    blob = Embedder(api_key=key).embed(str(text))
    return np.frombuffer(blob[: EMBED_DIM * 4], dtype=np.float32)


def _active_vector(text: str):
    key = str(text or "")
    cached = _EMBED_CACHE.get(key)
    if cached is not None:
        return np.frombuffer(cached, dtype=np.float32)
    vector = _dashscope_vector(key) if _ACTIVE_ENGINE == "dashscope" else _hash_vector(key)
    _EMBED_CACHE[key] = vector.tobytes()
    return vector


def _blob(text: str) -> bytes:
    return _active_vector(text).tobytes()


def build_corpus(work_dir: Path) -> tuple[Any, list[Unit], list[dict[str, Any]]]:
    """Materialise the 20-note / 12-card corpus into a throwaway database."""
    vault = work_dir / "vault"
    (vault / "captures").mkdir(parents=True, exist_ok=True)
    db_path = work_dir / "bench.db"
    conn = init_db(str(db_path))
    repo = CaptureRepo(conn)
    emb_repo = CaptureEmbeddingRepo(conn)
    docs = list(DOCS)
    for index, (title, summary) in enumerate(DISTRACTOR_TOPICS, 1):
        docs.append(
            {
                "id": f"cap_distractor_{index:02d}",
                "title": title,
                "theme": "干扰笔记",
                "summary": summary,
                "sections": [
                    ("背景", f"{summary} 这里记录一些与知识管理无关的日常经验。"),
                    ("要点", f"关于{title}，最重要的是先确认自己的实际场景，再决定投入多少时间。"),
                    ("复盘", f"{title}的常见问题是开始得太快、复盘得太少，建议每次只改一个变量。"),
                ],
            }
        )

    units: list[Unit] = []
    for doc in docs:
        body_parts = [f"## {title}\n\n{text}\n" for title, text in doc["sections"]]
        body = "\n".join(body_parts)
        frontmatter = {
            "title": doc["title"],
            "story": doc["summary"],
            "tags": [doc["theme"]],
        }
        note_path = f"captures/{doc['id']}.md"
        (vault / note_path).write_text(
            "---\n"
            f"title: {doc['title']}\nstory: {doc['summary']}\ntags: [{doc['theme']}]\n"
            "---\n\n" + body,
            encoding="utf-8",
        )
        capture = repo.insert(file_path=note_path)
        capture_id = capture["id"]
        # Keep the corpus id stable so queries can name their gold document.
        conn.execute(
            "UPDATE captures SET id = ? WHERE id = ?", (doc["id"], capture_id)
        )
        conn.commit()
        capture_id = doc["id"]

        identity = build_identity_index(capture_id, note_path, frontmatter, body)
        sections = build_section_indexes(capture_id, note_path, frontmatter, body)
        for item in [identity, *sections]:
            emb_repo.upsert(
                capture_id=item.capture_id,
                kind=item.kind,
                section_id=item.section_id,
                section_title=item.section_title,
                index_text=item.index_text,
                embedding=_blob(item.index_text),
                source_hash=item.source_hash,
                file_path=item.file_path,
            )
        units.append(
            Unit(capture_id, "capture", identity.index_text, {"title": doc["title"], "theme": doc["theme"]})
        )
        for item in sections:
            units.append(
                Unit(
                    f"{capture_id}#{item.section_id}",
                    "capture",
                    item.index_text,
                    {"title": doc["title"], "section": item.section_title},
                )
            )

    cards = _seed_cards(conn, emb_repo, work_dir)
    return conn, units, cards


def _seed_cards(conn, emb_repo, work_dir: Path) -> list[dict[str, Any]]:
    cards = [
        ("card_demo_demand", "真需求先看替代方案", "判断需求真假，先看用户现在怎么凑合", "cap_aipm_demand"),
        ("card_demo_telephone", "传话筒效应", "每多一级 LLM 转发，原文就被有损压缩一次", "cap_telephone_game"),
        ("card_demo_latency", "延迟预算拆解", "把端到端延迟拆到每个阶段，才能判断砍哪里", "cap_latency_roi"),
        ("card_demo_mcp", "工具收敛原则", "工具数量超过二十个，路由准确率明显下降", "cap_mcp"),
        ("card_demo_routing", "任务分档路由", "按任务难度分档，而不是按 token 数", "cap_model_routing"),
        ("card_demo_pkm", "三层分离", "资料是证据层，卡片是内化层，专题是输出层", "cap_pkm_compare"),
        ("card_demo_boundary", "强监管先别上", "高容错要求的场景不适合先用生成式 AI", "cap_aipm_demand"),
        ("card_demo_quote", "收藏不等于学会", "收藏只是输入，能讲出来才是内化", "cap_pkm_compare"),
        ("card_demo_eval", "没有评测的重构是重构", "检索改动必须有 Recall 或 MRR 对照", "cap_latency_roi"),
        ("card_demo_concurrency", "串行变并发", "多 URL 抓取应并发，延迟从求和变成取最大值", "cap_latency_roi"),
        ("card_demo_context", "上下文预算", "给 prompt 设硬上限，避免来源堆叠导致成本失控", "cap_mcp"),
        ("card_demo_silence", "沉默转发默认入库", "只发链接不提问，默认是收藏而非伴读", "cap_aipm_demand"),
    ]
    for card_id, title, one_liner, capture_id in cards:
        conn.execute(
            "INSERT INTO garden_cards (id, title, one_liner, capture_ids, status, source_theme) "
            "VALUES (?, ?, ?, ?, 'accepted', ?)",
            (card_id, title, one_liner, json.dumps([capture_id]), "Benchmark"),
        )
        text = f"标题：{title}\n结论：{one_liner}"
        conn.execute(
            "UPDATE garden_cards SET embedding = ? WHERE id = ?",
            (_blob(text), card_id),
        )
    conn.commit()
    return [{"id": card[0], "title": card[1], "capture_ids": [card[3]]} for card in cards]


QUERIES: list[dict[str, Any]] = [
    {"id": "q01", "query": "怎么判断一个 AI 功能是真需求还是伪需求", "gold": ["cap_aipm_demand"], "kind": "semantic"},
    {"id": "q02", "query": "传话筒效应为什么会让原文失真", "gold": ["cap_telephone_game", "card_demo_telephone"], "kind": "semantic"},
    {"id": "q03", "query": "Research 管线哪几个阶段可以砍掉", "gold": ["cap_latency_roi"], "kind": "detail"},
    {"id": "q04", "query": "MCP 解决了什么问题", "gold": ["cap_mcp"], "kind": "term"},
    {"id": "q05", "query": "Model Routing 怎么按任务分档", "gold": ["cap_model_routing", "card_demo_routing"], "kind": "semantic"},
    {"id": "q06", "query": "NotebookLM 和 Readwise 在证据层有什么差异", "gold": ["cap_pkm_compare"], "kind": "detail"},
    {"id": "q07", "query": "工具收敛策略建议保留多少个工具", "gold": ["cap_mcp", "card_demo_mcp"], "kind": "detail"},
    {"id": "q08", "query": "130 秒到 20 秒砍掉了哪些低 ROI 阶段", "gold": ["cap_latency_roi", "card_demo_latency"], "kind": "term"},
    {"id": "q09", "query": "面试中被问需求真假应该怎么反问", "gold": ["cap_aipm_demand"], "kind": "semantic"},
    {"id": "q10", "query": "卡片和资料在知识库里应该怎么分工", "gold": ["card_demo_pkm", "cap_pkm_compare"], "kind": "card"},
    {"id": "q11", "query": "为什么工具太多模型会选错", "gold": ["cap_mcp", "card_demo_mcp"], "kind": "semantic"},
    {"id": "q12", "query": "并发抓取能省多少时间", "gold": ["card_demo_concurrency", "cap_latency_roi"], "kind": "card"},
    {"id": "q13", "query": "强监管场景适不适合先上生成式 AI", "gold": ["card_demo_boundary", "cap_aipm_demand"], "kind": "detail"},
    {"id": "q14", "query": "怎么证明检索重构有效", "gold": ["card_demo_eval"], "kind": "card"},
    {"id": "q15", "query": "上下文太多会有什么问题", "gold": ["card_demo_context"], "kind": "card"},
    {"id": "q16", "query": "用户只发一个链接不说话应该怎么处理", "gold": ["card_demo_silence"], "kind": "card"},
    {"id": "q17", "query": "收藏了很多文章但记不住怎么办", "gold": ["card_demo_quote", "cap_pkm_compare"], "kind": "semantic"},
    {"id": "q18", "query": "大小模型协同的成本怎么测算", "gold": ["cap_model_routing"], "kind": "semantic"},
    {"id": "q19", "query": "怎么给猫做绝育手术恢复快", "gold": [], "kind": "negative"},
    {"id": "q20", "query": "量子计算机的退相干时间怎么延长", "gold": [], "kind": "negative"},
]


def _keywords(query: str) -> list[str]:
    from bridge.rag_bridge import _extract_query_keywords

    return _extract_query_keywords(query)


def _vector_hits(conn, query: str, limit: int, threshold: float | None = None) -> list[str]:
    from bridge.rag_bridge import MIN_VECTOR_SCORE

    gate = MIN_VECTOR_SCORE if threshold is None else float(threshold)

    query_vec = _blob(query)
    keywords = _keywords(query)
    rows = CaptureEmbeddingRepo(conn).search(query_vec, limit=limit * 4, two_stage_threshold=40)
    seen: list[str] = []
    for row in rows:
        # Mirror the product's relevance gate: weak vector hits only survive
        # when the index text contains a literal query keyword.
        haystack = f"{row.get('index_text') or ''} {row.get('section_title') or ''}".lower()
        if float(row.get("score") or 0) < gate and not any(
            keyword in haystack for keyword in keywords
        ):
            continue
        capture_id = str(row.get("capture_id") or "")
        if capture_id and capture_id not in seen:
            seen.append(capture_id)
    return seen[:limit]


def _legacy_hits(conn, query: str, limit: int) -> list[str]:
    """Pre-refactor baseline: literal substring on title/summary, no FTS, no sections.

    This must NOT use the new FTS table or the new tokenizer — otherwise the
    "old" group is just the new index with half the rows removed and the
    measured improvement is meaningless.
    """
    keywords = _keywords(query)
    if not keywords:
        return []
    rows = conn.execute(
        "SELECT capture_id, index_text FROM capture_embeddings WHERE kind = 'identity'"
    ).fetchall()
    scored: list[tuple[int, str]] = []
    for row in rows:
        haystack = str(row["index_text"] or "").lower()
        hits = sum(1 for keyword in keywords if keyword in haystack)
        if hits:
            scored.append((hits, str(row["capture_id"] or "")))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [capture_id for _hits, capture_id in scored[:limit]]


def _keyword_hits(conn, query: str, limit: int) -> list[str]:
    from bridge.rag_bridge import _fts_keyword_rows

    match = build_fts_match_query(_keywords(query))
    if not match:
        return []
    rows = conn.execute(
        "SELECT capture_id FROM capture_search_fts "
        "WHERE capture_search_fts MATCH ? AND kind != 'card' LIMIT ?",
        (match, limit),
    ).fetchall() or []
    return [str(row["capture_id"]) for row in rows]


def _card_hits(conn, query: str, limit: int) -> list[str]:
    from bridge.rag_bridge import _search_garden_cards

    cards = _search_garden_cards(query, conn, limit=limit)
    return [str(card.get("id") or "") for card in cards if card.get("id")]


def _card_hop(conn, seed_capture_ids: list[str], limit: int = 3) -> list[str]:
    """1-hop: cards already distilled from the seed notes."""
    if not seed_capture_ids:
        return []
    seeds = set(seed_capture_ids)
    rows = conn.execute(
        "SELECT id, capture_ids FROM garden_cards "
        "WHERE COALESCE(status, '') != 'archived'"
    ).fetchall()
    hits: list[str] = []
    for row in rows:
        try:
            ids = set(json.loads(row["capture_ids"] or "[]"))
        except Exception:
            continue
        if ids & seeds:
            hits.append(str(row["id"]))
        if len(hits) >= limit:
            break
    return hits


def _dedupe(ids: list[str], limit: int) -> list[str]:
    seen: list[str] = []
    for item in ids:
        if item and item not in seen:
            seen.append(item)
    return seen[:limit]


def run_group(group: str, conn, query: str, limit: int = 5, threshold: float | None = None) -> list[str]:
    if group == "G0_legacy_identity_keyword":
        return _legacy_hits(conn, query, limit)
    vector = _vector_hits(conn, query, limit, threshold)
    if group == "G1_vector_only":
        return vector[:limit]
    keyword = _keyword_hits(conn, query, limit)
    merged = _dedupe(vector + keyword, limit)
    if group == "G2_vector_plus_fts":
        return merged
    cards = _card_hits(conn, query, 3)
    hop = _card_hop(conn, [item for item in merged if not item.startswith("card_")], 2)
    return _dedupe(cards + merged + hop, limit)


GROUPS = [
    "G0_title_summary_substring",
    "G1_vector_only",
    "G2_vector_plus_fts",
    "G3_full_v3",
]


def _score(hits: list[str], gold: list[str], k: int) -> tuple[float, float]:
    if not gold:
        return 0.0, 0.0
    top = hits[:k]
    hit_count = len(set(top) & set(gold))
    recall = 1.0 if hit_count else 0.0
    rr = 0.0
    for rank, item in enumerate(top, 1):
        if item in gold:
            rr = 1.0 / rank
            break
    return recall, rr


def evaluate(conn, units: list[Unit], cards: list[dict[str, Any]], threshold: float | None = None) -> dict[str, Any]:
    results: dict[str, Any] = {"groups": {}, "engine": ENGINE_OFFLINE}
    positives = [q for q in QUERIES if q["gold"]]
    negatives = [q for q in QUERIES if not q["gold"]]
    # Warm the embedding cache for every query *before* timing anything.  G1
    # used to pay the API round trip while G2/G3 read a warm cache, which made
    # the latency column look like a 300x algorithmic win.
    embed_times: list[float] = []
    for query in QUERIES:
        started = time.perf_counter()
        _blob(query["query"])
        embed_times.append((time.perf_counter() - started) * 1000)
    results["embed_api_mean_ms"] = round(statistics.mean(embed_times), 2)
    for group in GROUPS:
        latencies: list[float] = []
        r3 = r5 = mrr = 0.0
        for query in positives:
            started = time.perf_counter()
            hits = run_group(group, conn, query["query"], threshold=threshold)
            latencies.append((time.perf_counter() - started) * 1000)
            recall3, rr = _score(hits, query["gold"], 3)
            recall5, _ = _score(hits, query["gold"], 5)
            r3 += recall3
            r5 += recall5
            mrr += rr
        rejected = 0
        for query in negatives:
            hits = run_group(group, conn, query["query"], threshold=threshold)
            if not hits:
                rejected += 1
        results["groups"][group] = {
            "recall_at_3": round(r3 / len(positives), 3),
            "recall_at_5": round(r5 / len(positives), 3),
            "mrr_at_5": round(mrr / len(positives), 3),
            "negative_rejection": round(rejected / len(negatives), 3),
            "latency_p50_ms": round(statistics.median(latencies), 2),
            "latency_p95_ms": round(
                sorted(latencies)[max(0, math.ceil(len(latencies) * 0.95) - 1)], 2
            ),
        }
    per_query: list[dict[str, Any]] = []
    for query in QUERIES:
        entry = {
            "id": query["id"],
            "query": query["query"],
            "kind": query.get("kind", ""),
            "gold": list(query["gold"]),
            "hits": {},
        }
        for group in GROUPS:
            entry["hits"][group] = run_group(
                group, conn, query["query"], threshold=threshold
            )
        per_query.append(entry)
    results["per_query_details"] = per_query

    prompt_sizes: list[int] = []
    for query in positives:
        hits = run_group("G3_full_v3", conn, query["query"])
        captures = []
        for capture_id in hits:
            row = conn.execute(
                "SELECT index_text FROM capture_embeddings "
                "WHERE capture_id = ? "
                "ORDER BY CASE kind WHEN 'section' THEN 0 ELSE 1 END LIMIT 1",
                (capture_id,),
            ).fetchone()
            body = str(row["index_text"]) if row else ""
            if not body:
                card = conn.execute(
                    "SELECT one_liner FROM garden_cards WHERE id = ?", (capture_id,)
                ).fetchone()
                body = str(card["one_liner"]) if card else ""
            captures.append(
                {
                    "id": capture_id,
                    "matched_kind": "section",
                    "title": capture_id,
                    "body": body,
                }
            )
        from agents.rag_agent import build_rag_prompt

        prompt_sizes.append(len(build_rag_prompt(query["query"], captures)))
    results["avg_prompt_chars"] = (
        int(round(sum(prompt_sizes) / len(prompt_sizes))) if prompt_sizes else 0
    )
    results["corpus"] = {
        "notes": len(DOCS) + len(DISTRACTOR_TOPICS),
        "chunks": len(units),
        "cards": len(cards),
        "units": len(units) + len(cards),
        "queries": len(QUERIES),
        "positives": len(positives),
        "negatives": len(negatives),
    }
    return results


def _markdown(report: dict[str, Any]) -> str:
    corpus = report["corpus"]
    lines = [
        "# RAG 检索消融评测（V3.0）",
        "",
        f"- Embedding engine: `{report['engine']}`",
        f"- 生效阈值 MIN_VECTOR_SCORE = `{report.get('threshold')}`（与 bridge/rag_bridge.py 一致）",
        f"- 语料：{corpus['notes']} 篇资料 / {corpus['chunks']} chunk / {corpus['cards']} 张卡片 "
        f"= {corpus['units']} 个检索单元",
        f"- Query：{corpus['queries']} 条（{corpus['positives']} 正样本 + {corpus['negatives']} 负样本）",
        f"- G3 单次 RAG Prompt 平均字符数：{report.get('avg_prompt_chars', 0)}"
        f"（上限 {report.get('max_prompt_chars', 14000)}）",
        "",
        "| 组 | Recall@3 | Recall@5 | MRR@5 | 负样本拦截 | P50 ms | P95 ms |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, row in report["groups"].items():
        lines.append(
            f"| {name} | {row['recall_at_3']} | {row['recall_at_5']} | {row['mrr_at_5']} "
            f"| {row['negative_rejection']} | {row['latency_p50_ms']} | {row['latency_p95_ms']} |"
        )
    negatives = report["corpus"]["negatives"]
    lines += [
        "",
        "口径说明：",
        "- 正样本命中按 capture/card 级判定（随机基线约 1/32）；负样本要求零召回。",
        "- 计时前已预热 embedding 缓存，四组都只测检索层，不含 LLM 合成。",
        f"- 负样本只有 {negatives} 条，拦截率只能写成 {negatives}/{negatives}，不能写成百分比。",
        "- 逐条命中见同目录 JSON 的 `per_query_details`。",
        "",
        "已知限制（必须写进面试口径）：",
        _engine_caveat(report),
        "- 阈值按 DashScope v4 标定（docs/calibration-dashscope.json）；"
        "separation<0 说明正负分布重叠，单阈值只是首轮粗筛。",
        "- G0 是真实的旧版字面基线（标题/摘要子串，不用 FTS）。"
        "若它在本语料上与 V3 打平，说明这套 query 的收益已被字面匹配吃掉，"
        "不能宣称语义检索带来提升。",
        "",
    ]
    return "\n".join(lines)


def _engine_caveat(report: dict[str, Any]) -> str:
    row = report["groups"]["G3_full_v3"]
    if report.get("engine") != ENGINE_OFFLINE:
        return (
            f"- 语义 embedding 引擎实测：G3 Recall@5 {row['recall_at_5']:.3f}、"
            f"MRR@5 {row['mrr_at_5']:.3f}、负样本 {report['corpus']['negatives']} 条全拦。"
            "结论只对本语料成立，换语料必须重跑。"
        )
    return (
        f"- 离线 hash 引擎只衡量字面重合：G3 Recall@5 {row['recall_at_5']:.3f}、"
        f"MRR@5 {row['mrr_at_5']:.3f}。真实语义指标需配置 embedding key 后重跑，"
        "**阈值不可跨引擎迁移**。"
    )


def run_benchmark(work_dir: Path | None = None, threshold: float | None = None) -> dict[str, Any]:
    base = Path(work_dir) if work_dir else Path(
        tempfile.mkdtemp(prefix="hermes-bench-", dir=_tmp_root())
    )
    base.mkdir(parents=True, exist_ok=True)
    conn, units, cards = build_corpus(base)
    try:
        return evaluate(conn, units, cards, threshold=threshold)
    finally:
        conn.close()


def _tmp_root() -> str:
    import os

    return os.environ.get("HERMES_EVAL_TMP") or tempfile.gettempdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    parser.add_argument("--work-dir", default="")
    parser.add_argument("--threshold", type=float, default=None, help="override MIN_VECTOR_SCORE for this run")
    parser.add_argument(
        "--engine",
        choices=("hash", "dashscope"),
        default="hash",
        help="hash = offline char-ngram; dashscope = real semantic embeddings (needs qwen.api_key)",
    )
    args = parser.parse_args(argv)

    global _ACTIVE_ENGINE
    _ACTIVE_ENGINE = args.engine
    report = run_benchmark(Path(args.work_dir) if args.work_dir else None, threshold=args.threshold)
    report["engine"] = (
        "dashscope-text-embedding-v4" if args.engine == "dashscope" else ENGINE_OFFLINE
    )
    from bridge.rag_bridge import MIN_VECTOR_SCORE

    report["threshold"] = (
        args.threshold if args.threshold is not None else MIN_VECTOR_SCORE
    )
    report["max_prompt_chars"] = 14000
    markdown = _markdown(report)
    print(markdown)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md_out:
        path = Path(args.md_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

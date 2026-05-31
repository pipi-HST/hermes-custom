"""Tests for deterministic Theme relationship graph building."""
from __future__ import annotations

import struct

from agents.embedder import DIM
from agents.theme_graph import (
    ThemeEdge,
    ThemeNode,
    ThemeSection,
    build_relationship_edges,
    cluster_relationship_graph,
    filter_theme_nodes,
)


def _emb(seed: float) -> bytes:
    vec = [seed + (i % 100) * 0.01 for i in range(DIM)]
    return struct.pack(f"{DIM}f", *vec)


def test_filter_theme_nodes_excludes_test_garbage():
    nodes = [
        ThemeNode(
            capture_id="junk",
            title="这是一段足够长的原始内容，用来生成待确认草稿",
            file_path="captures/junk.md",
            summary="长草稿摘要",
        ),
        ThemeNode(
            capture_id="real",
            title="AI Agent 工程化",
            file_path="captures/agent.md",
            summary="Agent 可靠性来自工具和流程约束",
        ),
    ]

    kept, stats = filter_theme_nodes(nodes)

    assert [n.capture_id for n in kept] == ["real"]
    assert stats["skipped_test_garbage"] == 1


def test_build_relationship_edges_uses_identity_similarity():
    nodes = [
        ThemeNode("a", "Agent A", "captures/a.md", "Agent 工程", identity_embedding=_emb(1.0)),
        ThemeNode("b", "Agent B", "captures/b.md", "Agent 评估", identity_embedding=_emb(1.0)),
        ThemeNode("c", "读书", "captures/c.md", "阅读方法", identity_embedding=_emb(-1.0)),
    ]

    edges = build_relationship_edges(nodes, identity_threshold=0.72)

    assert [(e.source_id, e.target_id) for e in edges] == [("a", "b")]
    assert edges[0].score >= 0.72
    assert "identity_similarity" in edges[0].reasons


def test_build_relationship_edges_can_use_section_similarity():
    nodes = [
        ThemeNode(
            "a",
            "长文 A",
            "captures/a.md",
            "宏观文章",
            identity_embedding=_emb(-1.0),
            section_embeddings=(ThemeSection("s1", "Agent 部分", "讲 Agent 工程", _emb(1.0)),),
        ),
        ThemeNode(
            "b",
            "长文 B",
            "captures/b.md",
            "另一篇长文",
            identity_embedding=_emb(-0.9),
            section_embeddings=(ThemeSection("s2", "工程部分", "讲 Agent 工程", _emb(1.0)),),
        ),
    ]

    edges = build_relationship_edges(
        nodes,
        identity_threshold=0.99,
        section_threshold=0.78,
    )

    assert len(edges) == 1
    assert "section_similarity" in edges[0].reasons
    assert edges[0].section_matches == ("Agent 部分 ↔ 工程部分",)


def test_build_relationship_edges_skips_malformed_embeddings():
    nodes = [
        ThemeNode("a", "Agent A", "captures/a.md", "Agent 工程", identity_embedding=b"bad"),
        ThemeNode("b", "Agent B", "captures/b.md", "Agent 评估", identity_embedding=_emb(1.0)),
        ThemeNode("c", "Agent C", "captures/c.md", "Agent 测试", identity_embedding=_emb(1.0)),
    ]

    edges = build_relationship_edges(nodes, identity_threshold=0.72)

    assert [(e.source_id, e.target_id) for e in edges] == [("b", "c")]


def test_cluster_relationship_graph_groups_connected_edges():
    edges = [
        ThemeEdge("a", "b", 0.83, ("identity_similarity",)),
        ThemeEdge("b", "c", 0.79, ("identity_similarity",)),
        ThemeEdge("x", "y", 0.60, ("identity_similarity",)),
    ]

    clusters = cluster_relationship_graph(edges, min_edge_score=0.72)

    assert len(clusters) == 1
    assert clusters[0].capture_ids == ["a", "b", "c"]
    assert clusters[0].score > 0.8


def test_cluster_relationship_graph_allows_strong_pair():
    edges = [ThemeEdge("a", "b", 0.86, ("section_similarity",))]

    clusters = cluster_relationship_graph(edges, min_edge_score=0.72)

    assert len(clusters) == 1
    assert clusters[0].capture_ids == ["a", "b"]

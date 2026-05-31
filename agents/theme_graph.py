"""Deterministic Theme relationship graph builder."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from agents.embedder import cosine_similarity

TEST_GARBAGE_MARKERS = (
    "这是一段足够长的原始内容，用来生成待确认草稿",
)

GENERIC_TAGS = {"draft", "source-archive", "raw", "capture"}


@dataclass(frozen=True)
class ThemeSection:
    section_id: str
    section_title: str
    index_text: str
    embedding: bytes


@dataclass(frozen=True)
class ThemeNode:
    capture_id: str
    title: str
    file_path: str
    summary: str
    tags: tuple[str, ...] = ()
    created_at: str = ""
    identity_embedding: bytes | None = None
    section_embeddings: tuple[ThemeSection, ...] = ()


@dataclass(frozen=True)
class ThemeEdge:
    source_id: str
    target_id: str
    score: float
    reasons: tuple[str, ...]
    section_matches: tuple[str, ...] = ()


@dataclass
class ThemeCluster:
    cluster_id: str
    capture_ids: list[str]
    score: float
    edges: list[ThemeEdge] = field(default_factory=list)
    title_hint: str = ""
    relation_hint: str = ""


def filter_theme_nodes(nodes: list[ThemeNode]) -> tuple[list[ThemeNode], dict]:
    kept: list[ThemeNode] = []
    stats = {"skipped_test_garbage": 0}
    for node in nodes:
        text = "\n".join([node.title, node.file_path, node.summary])
        if any(marker in text for marker in TEST_GARBAGE_MARKERS):
            stats["skipped_test_garbage"] += 1
            continue
        kept.append(node)
    return kept, stats


def build_relationship_edges(
    nodes: list[ThemeNode],
    *,
    identity_threshold: float = 0.72,
    section_threshold: float = 0.78,
    max_edges_per_node: int = 6,
) -> list[ThemeEdge]:
    edges: list[ThemeEdge] = []
    by_source_count: dict[str, int] = {}

    for i, left in enumerate(nodes):
        for right in nodes[i + 1:]:
            score = 0.0
            reasons: list[str] = []
            section_matches: list[str] = []

            if left.identity_embedding and right.identity_embedding:
                identity_score = _safe_cosine_similarity(
                    left.identity_embedding,
                    right.identity_embedding,
                )
                if identity_score is not None and identity_score >= identity_threshold:
                    score = max(score, identity_score)
                    reasons.append("identity_similarity")

            best_section = 0.0
            best_section_label = ""
            for left_section in left.section_embeddings:
                for right_section in right.section_embeddings:
                    section_score = _safe_cosine_similarity(
                        left_section.embedding,
                        right_section.embedding,
                    )
                    if section_score is None:
                        continue
                    if section_score > best_section:
                        best_section = section_score
                        best_section_label = (
                            f"{left_section.section_title} ↔ "
                            f"{right_section.section_title}"
                        )
            if best_section >= section_threshold:
                score = max(score, best_section)
                reasons.append("section_similarity")
                section_matches.append(best_section_label)

            shared_tags = _shared_meaningful_tags(left.tags, right.tags)
            if shared_tags and score >= min(identity_threshold, section_threshold) - 0.08:
                score = min(1.0, score + 0.04)
                reasons.append("shared_tags")

            if not reasons:
                continue

            if by_source_count.get(left.capture_id, 0) >= max_edges_per_node:
                continue
            by_source_count[left.capture_id] = by_source_count.get(left.capture_id, 0) + 1
            edges.append(
                ThemeEdge(
                    source_id=left.capture_id,
                    target_id=right.capture_id,
                    score=round(score, 4),
                    reasons=tuple(reasons),
                    section_matches=tuple(section_matches),
                )
            )

    edges.sort(key=lambda edge: edge.score, reverse=True)
    return edges


def cluster_relationship_graph(
    edges: list[ThemeEdge],
    *,
    min_edge_score: float = 0.72,
    max_clusters: int = 5,
) -> list[ThemeCluster]:
    strong_edges = [edge for edge in edges if edge.score >= min_edge_score]
    adjacency: dict[str, set[str]] = {}
    edge_map: dict[frozenset[str], ThemeEdge] = {}
    for edge in strong_edges:
        adjacency.setdefault(edge.source_id, set()).add(edge.target_id)
        adjacency.setdefault(edge.target_id, set()).add(edge.source_id)
        edge_map[frozenset([edge.source_id, edge.target_id])] = edge

    visited: set[str] = set()
    clusters: list[ThemeCluster] = []
    for node_id in sorted(adjacency):
        if node_id in visited:
            continue
        stack = [node_id]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency.get(current, set()) - component)
        visited.update(component)

        component_edges = [
            edge for key, edge in edge_map.items()
            if key.issubset(component)
        ]
        if not component_edges:
            continue
        avg_score = sum(edge.score for edge in component_edges) / len(component_edges)
        capture_ids = sorted(component)
        clusters.append(
            ThemeCluster(
                cluster_id=f"theme-{capture_ids[0][:8]}",
                capture_ids=capture_ids,
                score=round(avg_score, 4),
                edges=sorted(component_edges, key=lambda edge: edge.score, reverse=True),
            )
        )

    clusters.sort(key=lambda cluster: (cluster.score, len(cluster.capture_ids)), reverse=True)
    return clusters[:max_clusters]


def _shared_meaningful_tags(left: tuple[str, ...], right: tuple[str, ...]) -> set[str]:
    return (set(left) & set(right)) - GENERIC_TAGS


def _safe_cosine_similarity(left: bytes, right: bytes) -> float | None:
    try:
        return cosine_similarity(left, right)
    except (struct.error, ValueError):
        return None

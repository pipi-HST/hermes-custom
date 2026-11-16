"""Deterministic one-hop knowledge graph service."""
from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from agents.markdown_writer import read_note
from agents.theme_graph import (
    ThemeNode,
    ThemeSection,
    build_relationship_edges,
    filter_theme_nodes,
)

logger = logging.getLogger(__name__)

SOURCE_KINDS = {"capture", "garden_card", "theme_candidate"}
RELATION_TYPES = {
    "semantic_similarity",
    "card_uses_capture",
    "card_links_card",
    "theme_groups_capture",
}


def refresh_graph_after_write(conn, vault_path) -> dict[str, Any] | None:
    """Rebuild auto-generated edges after a card/capture write.

    Never raises: a bookkeeping failure must not fail the user's write.  The
    refresh reads vault notes and is O(edges), so callers invoke it once per
    write, not per retrieved row.
    """
    if conn is None or not vault_path:
        return None
    try:
        return KnowledgeGraphService(conn, vault_path).refresh()
    except Exception as exc:  # bookkeeping must not fail the user's write
        logger.warning("[graph] refresh failed: %s", exc, exc_info=True)
        return None


class KnowledgeGraphService:
    def __init__(self, conn, vault_path: str | Path):
        self.conn = conn
        self.vault_path = Path(vault_path).expanduser()

    def refresh(self) -> dict[str, Any]:
        edges = self._build_edges()
        from db.concurrency import transaction

        with transaction(self.conn):
            self.conn.execute("DELETE FROM knowledge_edges WHERE generated_by = 'auto'")
            self.conn.executemany(
                """INSERT INTO knowledge_edges
                   (id, source_kind, source_id, target_kind, target_id,
                    relation_type, score, evidence_json, generated_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'auto', ?, ?)""",
                [
                    (
                        edge["id"],
                        edge["source_kind"],
                        edge["source_id"],
                        edge["target_kind"],
                        edge["target_id"],
                        edge["relation_type"],
                        edge["score"],
                        json.dumps(edge["evidence"], ensure_ascii=False),
                        edge["created_at"],
                        edge["updated_at"],
                    )
                    for edge in edges
                ],
            )
        node_keys = {
            (edge["source_kind"], edge["source_id"])
            for edge in edges
        } | {
            (edge["target_kind"], edge["target_id"])
            for edge in edges
        }
        return {
            "node_count": len(node_keys),
            "edge_count": len(edges),
            "relation_counts": dict(Counter(edge["relation_type"] for edge in edges)),
        }

    def focus_graph(
        self,
        *,
        focus_kind: str = "",
        focus_id: str = "",
        depth: int = 1,
        limit: int = 80,
    ) -> dict[str, Any]:
        selected_kind, selected_id = self._resolve_focus(focus_kind, focus_id)
        edges = self._edges_for_focus(selected_kind, selected_id, limit=max(1, limit))
        node_keys = {
            (edge["source_kind"], edge["source_id"])
            for edge in edges
        } | {
            (edge["target_kind"], edge["target_id"])
            for edge in edges
        }
        if selected_kind and selected_id:
            node_keys.add((selected_kind, selected_id))

        nodes = self._hydrate_nodes(node_keys)
        nodes.sort(key=lambda node: (node["kind"] != selected_kind or node["id"] != selected_id, node["kind"], node["title"]))
        return {
            "nodes": nodes,
            "edges": edges,
            "meta": {
                "focus_kind": selected_kind,
                "focus_id": selected_id,
                "depth": 1 if depth else 0,
                "edge_count": len(edges),
            },
        }

    def _build_edges(self) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        edges: dict[str, dict[str, Any]] = {}
        for edge in self._semantic_edges(now):
            edges[edge["id"]] = edge
        for edge in self._garden_edges(now):
            edges[edge["id"]] = edge
        for edge in self._theme_candidate_edges(now):
            edges[edge["id"]] = edge
        return sorted(edges.values(), key=lambda item: (item["relation_type"], item["source_id"], item["target_id"]))

    def _semantic_edges(self, now: str) -> list[dict[str, Any]]:
        nodes = self._theme_nodes()
        nodes, _stats = filter_theme_nodes(nodes)
        edges = []
        for item in build_relationship_edges(nodes, max_edges_per_node=6):
            evidence = {
                "reasons": list(item.reasons),
                "section_matches": list(item.section_matches),
            }
            edges.append(
                _edge(
                    source_kind="capture",
                    source_id=item.source_id,
                    target_kind="capture",
                    target_id=item.target_id,
                    relation_type="semantic_similarity",
                    score=item.score,
                    evidence=evidence,
                    now=now,
                )
            )
        return edges

    def _theme_nodes(self) -> list[ThemeNode]:
        rows = self._valid_capture_rows()
        embeddings = self.conn.execute("SELECT * FROM capture_embeddings").fetchall()
        by_capture: dict[str, list[dict[str, Any]]] = {}
        for row in embeddings:
            by_capture.setdefault(str(row["capture_id"]), []).append(dict(row))

        nodes = []
        for row in rows:
            capture_id = str(row["id"])
            file_path = str(row["file_path"] or row["raw_path"] or "")
            note = self._read_note(file_path)
            if note is None:
                continue
            frontmatter, _body = note
            identity_embedding = None
            section_embeddings: list[ThemeSection] = []
            summary = ""
            for emb in by_capture.get(capture_id, []):
                if emb["kind"] == "identity":
                    identity_embedding = emb["embedding"]
                    summary = _identity_summary(emb.get("index_text") or "")
                elif emb["kind"] == "section" and emb.get("embedding"):
                    section_embeddings.append(
                        ThemeSection(
                            section_id=emb.get("section_id") or "",
                            section_title=emb.get("section_title") or "",
                            index_text=emb.get("index_text") or "",
                            embedding=emb["embedding"],
                        )
                    )
            nodes.append(
                ThemeNode(
                    capture_id=capture_id,
                    title=frontmatter.get("title") or Path(file_path).stem,
                    file_path=file_path,
                    summary=frontmatter.get("story") or summary,
                    tags=tuple(frontmatter.get("tags") or ()),
                    created_at=row["created_at"] or "",
                    identity_embedding=identity_embedding,
                    section_embeddings=tuple(section_embeddings),
                )
            )
        return nodes

    def _garden_edges(self, now: str) -> list[dict[str, Any]]:
        valid_capture_ids = self._valid_capture_ids()
        rows = self.conn.execute(
            """SELECT id, title, capture_ids, knowledge_links
               FROM garden_cards
               WHERE COALESCE(status, '') != 'archived'
               ORDER BY updated_at DESC"""
        ).fetchall()
        known_cards = {str(row["id"]) for row in rows}
        edges = []
        for row in rows:
            card_id = str(row["id"])
            title = str(row["title"] or "")
            for capture_id in _json_list(row["capture_ids"]):
                if str(capture_id) not in valid_capture_ids:
                    continue
                edges.append(
                    _edge(
                        source_kind="garden_card",
                        source_id=card_id,
                        target_kind="capture",
                        target_id=str(capture_id),
                        relation_type="card_uses_capture",
                        score=1.0,
                        evidence={"title": title},
                        now=now,
                    )
                )
            for linked_id in _json_list(row["knowledge_links"]):
                linked_id = str(linked_id)
                if linked_id not in known_cards:
                    continue
                edges.append(
                    _edge(
                        source_kind="garden_card",
                        source_id=card_id,
                        target_kind="garden_card",
                        target_id=linked_id,
                        relation_type="card_links_card",
                        score=1.0,
                        evidence={"title": title},
                        now=now,
                    )
                )
        return edges

    def _theme_candidate_edges(self, now: str) -> list[dict[str, Any]]:
        valid_capture_ids = self._valid_capture_ids()
        rows = self.conn.execute(
            """SELECT id, question_text, related_capture_ids, status
               FROM pending_questions
               WHERE agent_type = 'theme'
               ORDER BY created_at DESC"""
        ).fetchall()
        edges = []
        for row in rows:
            question_id = str(row["id"])
            question = str(row["question_text"] or "")
            for capture_id in _json_list(row["related_capture_ids"]):
                if str(capture_id) not in valid_capture_ids:
                    continue
                edges.append(
                    _edge(
                        source_kind="theme_candidate",
                        source_id=question_id,
                        target_kind="capture",
                        target_id=str(capture_id),
                        relation_type="theme_groups_capture",
                        score=1.0,
                        evidence={"question": question, "status": row["status"] or ""},
                        now=now,
                    )
                )
        return edges

    def _edges_for_focus(self, focus_kind: str, focus_id: str, *, limit: int) -> list[dict[str, Any]]:
        if focus_kind and focus_id:
            rows = self.conn.execute(
                """SELECT * FROM knowledge_edges
                   WHERE (source_kind = ? AND source_id = ?)
                      OR (target_kind = ? AND target_id = ?)
                   ORDER BY relation_type ASC, score DESC, updated_at DESC
                   LIMIT ?""",
                (focus_kind, focus_id, focus_kind, focus_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM knowledge_edges
                   ORDER BY score DESC, updated_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [_edge_row_to_dict(row) for row in rows]

    def _resolve_focus(self, focus_kind: str, focus_id: str) -> tuple[str, str]:
        focus_kind = focus_kind if focus_kind in SOURCE_KINDS else ""
        focus_id = (focus_id or "").strip()
        if focus_kind and focus_id and self._node_is_valid(focus_kind, focus_id):
            return focus_kind, focus_id
        rows = self.conn.execute(
            """SELECT id, file_path, raw_path FROM captures
               WHERE file_path IS NOT NULL AND file_path != ''
                 AND COALESCE(review_state, '') != 'archived'
               ORDER BY created_at DESC
               LIMIT 20"""
        ).fetchall()
        for row in rows:
            if self._read_note(str(row["file_path"] or row["raw_path"] or "")) is not None:
                return "capture", str(row["id"])
        return "", ""

    def _hydrate_nodes(self, node_keys: set[tuple[str, str]]) -> list[dict[str, Any]]:
        captures = self._capture_nodes({node_id for kind, node_id in node_keys if kind == "capture"})
        cards = self._garden_card_nodes({node_id for kind, node_id in node_keys if kind == "garden_card"})
        themes = self._theme_candidate_nodes({node_id for kind, node_id in node_keys if kind == "theme_candidate"})
        return captures + cards + themes

    def _capture_nodes(self, ids: set[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        rows = self._rows_by_ids("captures", ids)
        nodes = []
        for row in rows:
            if str(row["review_state"] or "") == "archived":
                continue
            file_path = str(row["file_path"] or row["raw_path"] or "")
            note = self._read_note(file_path)
            if note is None:
                continue
            frontmatter, body = note
            title = frontmatter.get("title") or Path(file_path).stem or row["id"]
            nodes.append(
                {
                    "id": row["id"],
                    "kind": "capture",
                    "title": title,
                    "summary": frontmatter.get("story") or _plain_excerpt(body),
                    "file_path": file_path,
                    "open_url": f"/knowledge-library?kind=capture&item={quote(str(row['id']))}",
                    "file_url": _file_url(self.vault_path, file_path),
                }
            )
        return nodes

    def _garden_card_nodes(self, ids: set[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        rows = self._rows_by_ids("garden_cards", ids)
        return [
            {
                "id": row["id"],
                "kind": "garden_card",
                "title": row["title"] or row["id"],
                "summary": row["one_liner"] or "",
                "file_path": "",
                "open_url": f"/cards-board/{quote(str(row['id']))}",
                "file_url": "",
            }
            for row in rows
            if str(row["status"] or "") != "archived"
        ]

    def _theme_candidate_nodes(self, ids: set[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        rows = self._rows_by_ids("pending_questions", ids)
        return [
            {
                "id": row["id"],
                "kind": "theme_candidate",
                "title": row["question_text"] or row["id"],
                "summary": row["status"] or "",
                "file_path": "",
                "open_url": "",
                "file_url": "",
            }
            for row in rows
        ]

    def _rows_by_ids(self, table: str, ids: set[str]):
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        return self.conn.execute(
            f"SELECT * FROM {table} WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()

    def _valid_capture_rows(self):
        return self.conn.execute(
            """SELECT c.id, c.file_path, c.raw_path, c.created_at, c.review_state
               FROM captures c
               WHERE c.file_path IS NOT NULL
                 AND c.file_path != ''
                 AND COALESCE(c.review_state, '') != 'archived'"""
        ).fetchall()

    def _valid_capture_ids(self) -> set[str]:
        ids: set[str] = set()
        for row in self._valid_capture_rows():
            file_path = str(row["file_path"] or row["raw_path"] or "")
            if self._read_note(file_path) is not None:
                ids.add(str(row["id"]))
        return ids

    def _node_is_valid(self, kind: str, node_id: str) -> bool:
        if kind == "capture":
            row = self.conn.execute(
                """SELECT file_path, raw_path FROM captures
                   WHERE id = ?
                     AND file_path IS NOT NULL
                     AND file_path != ''
                     AND COALESCE(review_state, '') != 'archived'""",
                (node_id,),
            ).fetchone()
            if row is None:
                return False
            return self._read_note(str(row["file_path"] or row["raw_path"] or "")) is not None
        if kind == "garden_card":
            row = self.conn.execute(
                "SELECT id FROM garden_cards WHERE id = ? AND COALESCE(status, '') != 'archived'",
                (node_id,),
            ).fetchone()
            return row is not None
        if kind == "theme_candidate":
            row = self.conn.execute(
                "SELECT related_capture_ids FROM pending_questions WHERE id = ? AND agent_type = 'theme'",
                (node_id,),
            ).fetchone()
            if row is None:
                return False
            valid_capture_ids = self._valid_capture_ids()
            return any(str(capture_id) in valid_capture_ids for capture_id in _json_list(row["related_capture_ids"]))
        return False

    def _read_note(self, file_path: str) -> tuple[dict[str, Any], str] | None:
        if not file_path:
            return None
        note = read_note(self.vault_path, file_path)
        return note if note else None


def _edge(
    *,
    source_kind: str,
    source_id: str,
    target_kind: str,
    target_id: str,
    relation_type: str,
    score: float,
    evidence: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    edge_id = _edge_id(source_kind, source_id, target_kind, target_id, relation_type)
    return {
        "id": edge_id,
        "source_kind": source_kind,
        "source_id": source_id,
        "target_kind": target_kind,
        "target_id": target_id,
        "relation_type": relation_type,
        "score": round(float(score), 4),
        "evidence": evidence,
        "generated_by": "auto",
        "created_at": now,
        "updated_at": now,
    }


def _edge_id(
    source_kind: str,
    source_id: str,
    target_kind: str,
    target_id: str,
    relation_type: str,
) -> str:
    raw = "|".join([source_kind, source_id, target_kind, target_id, relation_type])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _edge_row_to_dict(row) -> dict[str, Any]:
    try:
        evidence = json.loads(row["evidence_json"] or "{}")
    except json.JSONDecodeError:
        evidence = {}
    return {
        "id": row["id"],
        "source_kind": row["source_kind"],
        "source_id": row["source_id"],
        "target_kind": row["target_kind"],
        "target_id": row["target_id"],
        "relation_type": row["relation_type"],
        "score": row["score"],
        "evidence": evidence,
        "generated_by": row["generated_by"],
        "updated_at": row["updated_at"],
    }


def _json_list(text: Any) -> list[Any]:
    try:
        value = json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return value if isinstance(value, list) else []


def _identity_summary(index_text: str) -> str:
    for line in (index_text or "").splitlines():
        if line.startswith("摘要："):
            return line.removeprefix("摘要：").strip()
    return (index_text or "")[:120]


def _plain_excerpt(text: str, limit: int = 120) -> str:
    return " ".join((text or "").split())[:limit]


def _file_url(vault_path: Path, file_path: str) -> str:
    if not file_path:
        return ""
    absolute = (vault_path / file_path).expanduser()
    return absolute.as_uri()

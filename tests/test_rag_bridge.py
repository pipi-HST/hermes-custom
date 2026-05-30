"""Tests for RAG bridge indexed retrieval."""
from __future__ import annotations

import struct
from pathlib import Path

from agents.capture_agent import CaptureConfig
from db.embedding_repo import CaptureEmbeddingRepo
from db.schema import init_db


def _vec(first: float, second: float = 0.0) -> bytes:
    values = [0.0] * 1024
    values[0] = first
    values[1] = second
    return struct.pack("1024f", *values)


def _write_capture(vault: Path) -> str:
    rel_path = "captures/long.md"
    path = vault / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """---
id: cap1
title: 长文
story: 这是一篇长文。
tags:
  - ai
---

# 长文

## 第一部分
这里讨论背景材料。

## 第二部分
这里讨论节奏狙击和 Terminal-Bench 的故意留坑。

## 第三部分
这里讨论后续影响。
""",
        encoding="utf-8",
    )
    return rel_path


def test_search_knowledge_base_uses_best_section_embedding(monkeypatch, tmp_path):
    from bridge import rag_bridge

    db_path = tmp_path / "captures.db"
    vault = tmp_path / "vault"
    rel_path = _write_capture(vault)
    conn = init_db(str(db_path))
    conn.execute(
        "INSERT INTO captures (id, file_path) VALUES (?, ?)",
        ("cap1", rel_path),
    )
    repo = CaptureEmbeddingRepo(conn)
    repo.upsert(
        capture_id="cap1",
        kind="identity",
        index_text="长文整体身份",
        embedding=_vec(0.1, 1.0),
        source_hash="identity",
        file_path=rel_path,
    )
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="first",
        section_title="第一部分",
        index_text="背景材料",
        embedding=_vec(0.2, 1.0),
        source_hash="first",
        file_path=rel_path,
    )
    repo.upsert(
        capture_id="cap1",
        kind="section",
        section_id="second",
        section_title="第二部分",
        index_text="节奏狙击 Terminal-Bench 故意留坑",
        embedding=_vec(1.0, 0.0),
        source_hash="second",
        file_path=rel_path,
    )

    class FakeEmbedder:
        def __init__(self, **_kwargs):
            pass

        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    class FakeRagAgent:
        seen_captures: list[dict] = []

        def __init__(self, **_kwargs):
            pass

        def query(self, _question: str, captures: list[dict]) -> dict:
            FakeRagAgent.seen_captures = captures
            return {
                "answer": "第二部分是重点。[1]",
                "sources": [
                    {
                        "title": captures[0]["title"],
                        "story": captures[0].get("story", ""),
                        "file_path": captures[0]["file_path"],
                        "section_title": captures[0].get("section_title", ""),
                    }
                ],
            }

    monkeypatch.setattr(
        rag_bridge,
        "load_config",
        lambda _path: CaptureConfig(
            vault_path=str(vault),
            db_path=str(db_path),
            qwen_api_key="qwen-test",
            llm_api_key="llm-test",
        ),
    )
    monkeypatch.setattr(rag_bridge, "Embedder", FakeEmbedder)
    monkeypatch.setattr(rag_bridge, "RagAgent", FakeRagAgent)
    rag_bridge._config = None
    rag_bridge._repo = None

    envelope = rag_bridge.search_knowledge_base("Terminal-Bench 的坑是什么？")

    assert envelope["source_map"][0]["type"] == "capture_section"
    assert envelope["source_map"][0]["section"] == "第二部分"
    assert FakeRagAgent.seen_captures[0]["section_title"] == "第二部分"
    assert "Terminal-Bench" in FakeRagAgent.seen_captures[0]["body"]
    assert "背景材料" not in FakeRagAgent.seen_captures[0]["body"]

"""Non-destructive smoke test for an empty Hermes Knowledge workflow."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import struct
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.capture_agent import CaptureAgent, CaptureConfig
from agents.extractors.text_extractor import TextExtractor
from bridge import rag_bridge
from bridge.context import KnowledgeContext
from db.embedding_repo import CaptureEmbeddingRepo
from db.schema import init_db

SMOKE_TEXT = (
    "reset_smoke 验证 Hermes Knowledge 从空库重新开始。"
    "材料说明 Knowledge File 只保留 Capture Note 和 Raw Evidence，"
    "结构化对象留在 SQLite 和 Web 工作台中。"
    "这个样本还包含 RAG 检索关键词 reset_smoke_workflow。"
) * 3


def run_reset_smoke(
    *,
    work_dir: str | Path | None = None,
    keep: bool = False,
    include_card: bool = False,
) -> dict[str, Any]:
    temp_dir = None
    if work_dir is None:
        if keep:
            root = Path(tempfile.mkdtemp(prefix="hermes-reset-smoke-"))
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="hermes-reset-smoke-")
            root = Path(temp_dir.name)
    else:
        root = Path(work_dir).expanduser()
        root.mkdir(parents=True, exist_ok=True)

    try:
        result = _run_smoke_in(root, include_card=include_card)
        result["work_dir"] = str(root)
        result["kept"] = keep or work_dir is not None
        return result
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def _run_smoke_in(root: Path, *, include_card: bool) -> dict[str, Any]:
    logging.getLogger("agents.capture_agent").setLevel(logging.ERROR)
    db_path = root / "captures.db"
    vault_path = root / "knowledge"
    conn = init_db(str(db_path))
    config = CaptureConfig(
        llm_api_key="reset-smoke-llm",
        llm_provider="openai",
        llm_model="reset-smoke-model",
        llm_base_url="https://example.invalid",
        qwen_api_key="",
        vault_path=str(vault_path),
        db_path=str(db_path),
    )
    agent = CaptureAgent(config, conn=conn)
    agent.extractors = [TextExtractor()]
    agent._call_llm = lambda _prompt: (
        "## 目录纲要\n\n- [工作流验证](#工作流验证)\n\n"
        "## 工作流验证\n\n"
        "**reset_smoke_workflow** 保留 Knowledge Workflow 能力。"
    )
    agent.summarizer.summarize = lambda _text, existing_tags=None: {
        "title": "reset_smoke Knowledge Workflow",
        "summary": "验证空库下 Capture、Knowledge File、索引和 RAG 仍能工作。",
        "tags": ["reset_smoke", "knowledge-workflow"],
    }

    draft = agent.create_draft(SMOKE_TEXT, instruction="整理后存库")
    committed = agent.commit_draft(draft["id"])
    if not committed or committed.get("error"):
        raise RuntimeError(f"commit failed: {committed}")

    capture_id = str(committed["capture_id"])
    file_path = str(committed["file_path"])
    raw_path = str(committed["raw_path"])
    _upsert_smoke_embedding(conn, capture_id=capture_id, file_path=file_path)

    card = _create_smoke_card(conn, capture_id) if include_card else {}
    rag_result = _run_smoke_rag(conn, config)
    files = sorted(
        path.relative_to(vault_path).as_posix()
        for path in vault_path.rglob("*")
        if path.is_file()
    )
    counts = _table_counts(conn)
    conn.close()

    return {
        "ok": True,
        "db_path": str(db_path),
        "vault_path": str(vault_path),
        "draft": {
            "id": draft["id"],
            "status": draft["status"],
            "title": draft["title"],
        },
        "capture": {
            "id": capture_id,
            "file_path": file_path,
            "raw_path": raw_path,
            "file_exists": (vault_path / file_path).exists(),
            "raw_exists": (vault_path / raw_path).exists(),
        },
        "embedding": {
            "identity_count": counts["identity_embeddings"],
        },
        "rag": {
            "status": rag_result["status"],
            "result_count": rag_result["meta"]["result_count"],
            "source_map": rag_result["source_map"],
            "answer": rag_result["meta"]["answer"],
        },
        "card": card,
        "files": files,
        "counts": counts,
    }


def _vec(first: float = 1.0, second: float = 0.0) -> bytes:
    values = [0.0] * 1024
    values[0] = first
    values[1] = second
    return struct.pack("1024f", *values)


def _upsert_smoke_embedding(conn: sqlite3.Connection, *, capture_id: str, file_path: str) -> None:
    CaptureEmbeddingRepo(conn).upsert(
        capture_id=capture_id,
        kind="identity",
        index_text="reset_smoke_workflow Knowledge File empty-store workflow",
        embedding=_vec(1.0, 0.0),
        source_hash="reset-smoke",
        file_path=file_path,
    )


def _run_smoke_rag(conn: sqlite3.Connection, config: CaptureConfig) -> dict:
    original_embedder = rag_bridge.Embedder
    original_rag_agent = rag_bridge.RagAgent

    class FakeEmbedder:
        def __init__(self, **_kwargs):
            pass

        def embed(self, _text: str) -> bytes:
            return _vec(1.0, 0.0)

    class FakeRagAgent:
        def __init__(self, **_kwargs):
            pass

        def query(self, question: str, captures: list[dict], *, context_pack: dict | None = None) -> dict:
            return {
                "answer": f"{question} 已通过 reset_smoke_workflow 样本验证。[1]",
                "sources": [
                    {
                        "title": captures[0].get("title") or captures[0].get("file_path", ""),
                        "story": captures[0].get("story", ""),
                        "file_path": captures[0].get("file_path", ""),
                    }
                ],
            }

    try:
        rag_bridge.Embedder = FakeEmbedder
        rag_bridge.RagAgent = FakeRagAgent
        return rag_bridge.search_knowledge_base(
            "reset_smoke_workflow 是否能被检索？",
            context=KnowledgeContext(config=config, conn=conn, vault_path=Path(config.vault_path)),
        )
    finally:
        rag_bridge.Embedder = original_embedder
        rag_bridge.RagAgent = original_rag_agent


def _create_smoke_card(conn: sqlite3.Connection, capture_id: str) -> dict[str, Any]:
    card_id = "reset_smoke_card"
    now = datetime.now(timezone.utc).isoformat()
    capture_ids = json.dumps([capture_id], ensure_ascii=False)
    payload = {
        "title": "reset_smoke Knowledge Card",
        "summary": "验证 Knowledge Card 是结构化对象。",
    }
    conn.execute(
        """INSERT INTO garden_cards
           (id, title, one_liner, card_kind, payload_json, core_arguments,
            knowledge_links, key_materials, status, capture_ids, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            card_id,
            "reset_smoke Knowledge Card",
            "空库 reset 后仍能创建结构化 Knowledge Card。",
            "method",
            json.dumps(payload, ensure_ascii=False),
            json.dumps([{"claim": "Knowledge Card 以 SQLite 结构化对象为主。"}], ensure_ascii=False),
            "[]",
            "[]",
            "seed",
            capture_ids,
            now,
            now,
        ),
    )
    conn.commit()
    return {
        "id": card_id,
        "file_path": "",
        "file_exists": False,
        "storage": "sqlite",
    }


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "captures": int(conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]),
        "capture_drafts": int(conn.execute("SELECT COUNT(*) FROM capture_drafts").fetchone()[0]),
        "identity_embeddings": int(
            conn.execute(
                "SELECT COUNT(*) FROM capture_embeddings WHERE kind = 'identity'"
            ).fetchone()[0]
        ),
        "garden_cards": int(conn.execute("SELECT COUNT(*) FROM garden_cards").fetchone()[0]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a non-destructive Hermes Knowledge reset smoke in a temporary workspace."
    )
    parser.add_argument("--work-dir", default="", help="Optional workspace directory. Defaults to a temp dir.")
    parser.add_argument("--keep", action="store_true", help="Keep the generated temp workspace.")
    parser.add_argument("--include-card", action="store_true", help="Also create a sample structured Knowledge Card.")
    args = parser.parse_args(argv)

    result = run_reset_smoke(
        work_dir=args.work_dir or None,
        keep=args.keep,
        include_card=args.include_card,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)

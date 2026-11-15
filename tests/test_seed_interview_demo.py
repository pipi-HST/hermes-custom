"""Golden Demo dataset must be complete, rich and idempotent."""
from __future__ import annotations

import json

from agents.embedder import cosine_similarity
from db.schema import init_db
from scripts.seed_interview_demo import CAPTURES, CARD_DEFINITIONS, seed, verify


def test_seed_interview_demo_is_complete_and_idempotent(tmp_path):
    conn = init_db(str(tmp_path / "captures.db"))
    vault = tmp_path / "vault"
    try:
        first = seed(conn, vault)
        second = seed(conn, vault)
        result = verify(conn, vault)

        assert first["captures"] == second["captures"] == len(CAPTURES) == 6
        assert result["library_total"] == 6
        assert result["card_total"] == len(CARD_DEFINITIONS) == 9
        assert result["card_kinds"] == ["fact", "method", "risk", "viewpoint"]
        assert result["card_themes"] == ["AI 产品面试", "Agent 与大模型", "行业与产品拆解"]
        assert result["cards_without_sources"] == []
        assert result["embedding_count"] == 30
        assert result["graph_edges"] >= 30
        assert all(count > 0 for count in result["graph_relation_counts"].values())

        for capture in CAPTURES:
            assert 1500 <= len(capture["body"]) <= 2500
            assert "## 💡 AI 核心导读" in capture["body"]
            assert "## 目录纲要" in capture["body"]
            note = vault / "captures" / f"{capture['slug']}.md"
            raw = vault / "_archive" / "raw" / f"{capture['slug']}.md"
            assert note.is_file() and raw.is_file()
            assert capture["raw"][:20] in raw.read_text(encoding="utf-8")

        folder_store = json.loads(
            (vault / ".hermes" / "knowledge_folders.json").read_text(encoding="utf-8")
        )
        assert folder_store["folders"] == [
            "Inbox",
            "AI 产品面试",
            "Agent 与大模型",
            "行业与产品拆解",
            "读书与随笔",
        ]

        embeddings = {
            row["capture_id"]: row["embedding"]
            for row in conn.execute(
                "SELECT capture_id, embedding FROM capture_embeddings WHERE kind = 'identity'"
            ).fetchall()
        }
        agent_1 = embeddings["cap_agent_arch_01"]
        agent_2 = embeddings["cap_agent_mcp_01"]
        pm_1 = embeddings["cap_pm_interview_01"]
        assert cosine_similarity(agent_1, agent_2) > 0.85
        assert cosine_similarity(agent_1, pm_1) < 0.5
    finally:
        conn.close()

"""Saving an answer keeps the selected run's body and source map together."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bridge import answer_bridge, rag_bridge, research_bridge
from bridge.result_envelope import success_envelope
from db.reading_repo import ReadingRepo
from db.schema import init_db


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    conn = init_db(str(tmp_path / "answer-binding.db"))
    repo = ReadingRepo(conn)
    monkeypatch.setattr(answer_bridge, "_init", lambda: (SimpleNamespace(), repo))
    monkeypatch.setattr(rag_bridge, "_init", lambda: (SimpleNamespace(), repo))
    yield repo
    conn.close()


def _draft(repo, result):
    return repo.get_draft(result["meta"]["reading_draft_id"])


@pytest.mark.parametrize(("answer_source", "send_other_run"), [
    ("auto", False), ("latest_rag_full_answer", False), ("auto", True),
])
def test_save_never_uses_another_turn(ledger, monkeypatch, answer_source, send_other_run):
    other = "车站周末的开放时间从早上九点到下午五点。" * 8
    monkeypatch.setattr(
        rag_bridge, "_search_knowledge_base",
        lambda *_args, **_kwargs: success_envelope(
            agent="rag", intent="search_knowledge_base", chat_preview=other,
            meta={"query": "周末出门", "answer": other},
            source_map=[{"type": "capture_note", "id": "station", "title": "车站公告"}],
        ),
    )
    other_result = rag_bridge.search_knowledge_base("周末出门")

    result = answer_bridge.save_answer_to_knowledge(
        hermes_session_id="garden-session",
        user_message="周末出门",
        assistant_answer="植物园的玻璃温室周末开放。（已省略后续内容）",
        assistant_message_id="garden-answer",
        sources=[{"type": "web", "id": "garden", "title": "植物园公告"}],
        answer_source=answer_source,
        source_run_id=other_result["meta"]["source_run_id"] if send_other_run else "",
    )

    draft = _draft(ledger, result)
    assert "玻璃温室" in draft["draft_text"]
    assert other not in draft["draft_text"]
    assert [source["id"] for source in draft["meta_json"]["sources"]] == ["garden"]
    assert result["meta"]["answer_source"] == "provided"
    if answer_source == "latest_rag_full_answer":
        assert result["meta"]["answer_source_fallback"] == "missing_source_run_id"
    if send_other_run:
        assert result["meta"]["answer_source_fallback"] == "source_run_answer_mismatch"


def test_web_ledger_and_draft_preserve_exact_body_and_sources(ledger, monkeypatch):
    query = "请介绍市立植物园的参观路线"
    answer = "从东门沿着步道到达温室 [W1]。\n\n" + "馆内有无障碍通道 [K1]。\n\n" * 150
    sources = [
        {"type": "web", "id": "garden", "ref": "W1", "title": "参观路线",
         "url": "https://garden.example.test/route", "excerpt": "温室位于东门附近。"},
        {"type": "knowledge", "id": "access", "ref": "K1", "title": "无障碍手册",
         "source_kind": "capture_note", "section": "温室", "path": "captures/access.md"},
    ]
    monkeypatch.setattr(
        research_bridge, "_web_context_answer",
        lambda **_kwargs: success_envelope(
            agent="research", intent="web_context_answer", chat_preview=answer,
            meta={"query": query, "retrieved_web_sources": [{"id": "not-used"}]},
            source_map=sources,
        ),
    )
    # The production ledger wrapper skips unrelated fake-adapter tests; this
    # test opts in with the isolated database above.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    web_result = research_bridge.web_assist_answer(query=query)
    source_run_id = web_result["meta"]["source_run_id"]
    assert web_result["meta"]["answer"] == answer
    row = ledger.conn.execute(
        "SELECT status, output_json FROM agent_runs WHERE id = ?", (source_run_id,),
    ).fetchone()
    assert row["status"] == "success"
    payload = json.loads(row["output_json"])
    assert payload["answer"] == answer
    assert payload["sources"] == sources

    result = answer_bridge.save_answer_to_knowledge(
        user_message=query,
        assistant_answer=web_result["chat_preview"],
        output_type="knowledge_card",
        source_run_id=source_run_id,
        sources=[{"type": "web", "id": "unrelated"}],
    )
    draft = _draft(ledger, result)
    assert answer.strip() in draft["draft_text"]
    assert "[W1] 参观路线 — https://garden.example.test/route" in draft["draft_text"]
    assert "[K1] 无障碍手册" in draft["draft_text"]
    assert draft["meta_json"]["sources"] == sources
    assert draft["meta_json"]["source_run_id"] == source_run_id
    save_run = ledger.conn.execute(
        "SELECT output_json FROM agent_runs WHERE intent = 'save_answer_to_knowledge'",
    ).fetchone()
    assert json.loads(save_run["output_json"])["source_run_id"] == source_run_id

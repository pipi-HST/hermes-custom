from agents.run_evidence import extract_run_evidence
from bridge.result_envelope import artifact_ref, source_ref, success_envelope


def test_extracts_garden_draft_completion_evidence():
    envelope = success_envelope(
        agent="garden",
        intent="synthesize_garden",
        chat_preview="已生成草稿。",
        durable_artifacts=[
            artifact_ref(
                type="garden_draft",
                id="draft1",
                title="AI 工程化",
                status="draft_ready",
            )
        ],
        meta={"capture_ids": ["c1", "c2"], "draft_id": "draft1"},
    )

    result = extract_run_evidence("garden", "synthesize_garden", envelope)

    assert result["status"] == "success"
    assert result["evidence"]["draft_created"] is True
    assert result["evidence"]["draft_id"] == "draft1"
    assert result["evidence"]["capture_count"] == 2


def test_extracts_garden_card_completion_evidence():
    envelope = success_envelope(
        agent="garden",
        intent="commit_garden_draft",
        chat_preview="已存入知识花园。",
        durable_artifacts=[
            artifact_ref(
                type="garden_card",
                id="card1",
                title="AI 工程化",
                path="garden/AI.md",
                status="seed",
            )
        ],
        meta={"card_id": "card1", "path": "garden/AI.md"},
    )

    result = extract_run_evidence("garden", "commit_garden_draft", envelope)

    assert result["status"] == "success"
    assert result["evidence"]["card_created"] is True
    assert result["evidence"]["card_id"] == "card1"
    assert result["evidence"]["path"] == "garden/AI.md"


def test_rag_no_result_search_is_success_not_incomplete():
    envelope = success_envelope(
        agent="rag",
        intent="search_knowledge_base",
        chat_preview="知识库中没有找到相关信息。",
        source_map=[],
        meta={"query": "不存在的问题", "result_count": 0, "used_index": False},
    )

    result = extract_run_evidence("rag", "search_knowledge_base", envelope)

    assert result["status"] == "success"
    assert result["evidence"]["result_count"] == 0
    assert result["evidence"]["source_count"] == 0


def test_extracts_rag_source_evidence():
    envelope = success_envelope(
        agent="rag",
        intent="search_knowledge_base",
        chat_preview="答案 [1]",
        source_map=[
            source_ref(
                role="evidence",
                type="capture_note",
                id="cap1",
                title="材料",
                path="captures/a.md",
            )
        ],
        meta={"query": "AI", "result_count": 1, "used_index": True},
    )

    result = extract_run_evidence("rag", "search_knowledge_base", envelope)

    assert result["status"] == "success"
    assert result["evidence"]["result_count"] == 1
    assert result["evidence"]["source_count"] == 1
    assert result["evidence"]["used_index"] is True


def test_extracts_rag_source_names_paths_and_sections():
    envelope = success_envelope(
        agent="rag",
        intent="search_knowledge_base",
        chat_preview="答案 [1]",
        source_map=[
            source_ref(
                role="evidence",
                type="capture_section",
                id="cap1",
                title="Building Effective AI Agents",
                path="captures/Building Effective AI Agents.md",
                section="构建大语言模型代理的经验与方法",
            )
        ],
        meta={"query": "AI Agent 工程化", "result_count": 1, "used_index": True},
    )

    result = extract_run_evidence("rag", "search_knowledge_base", envelope)

    assert result["evidence"]["source_titles"] == ["Building Effective AI Agents"]
    assert result["output_ref"]["sources"] == [
        {
            "id": "cap1",
            "title": "Building Effective AI Agents",
            "path": "captures/Building Effective AI Agents.md",
            "section": "构建大语言模型代理的经验与方法",
            "type": "capture_section",
        }
    ]


def test_rag_evidence_extracts_graph_trace():
    envelope = success_envelope(
        agent="rag",
        intent="search_knowledge_base",
        chat_preview="答案 [1]",
        meta={
            "query": "AI Agent 工程化",
            "result_count": 4,
            "used_index": True,
            "graph_trace": {
                "seed_count": 3,
                "garden_count": 1,
                "theme_count": 1,
                "final_count": 4,
                "stopped_reason": "enough_evidence",
            },
        },
    )

    result = extract_run_evidence("rag", "search_knowledge_base", envelope)

    assert result["evidence"]["graph_seed_count"] == 3
    assert result["evidence"]["graph_final_count"] == 4
    assert result["evidence"]["graph_stopped_reason"] == "enough_evidence"

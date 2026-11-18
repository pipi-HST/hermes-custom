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
    assert {
        "object_type": "garden_draft",
        "object_id": "draft1",
        "role": "subject",
        "source": "durable_artifacts",
    } in result["object_refs"]
    assert {
        "object_type": "capture",
        "object_id": "c1",
        "role": "source",
        "source": "meta.capture_ids",
    } in result["object_refs"]


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
    assert {
        "object_type": "garden_card",
        "object_id": "card1",
        "role": "subject",
        "source": "durable_artifacts",
    } in result["object_refs"]


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


def test_extracts_capture_postprocess_pipeline_evidence():
    envelope = success_envelope(
        agent="capture",
        intent="commit_capture_draft",
        chat_preview="已存入知识库。",
        durable_artifacts=[
            artifact_ref(
                type="capture_note",
                id="cap1",
                title="长文",
                path="captures/a.md",
                status="created",
            )
        ],
        meta={
            "capture_id": "cap1",
            "library_postprocess": {
                "pipeline": {
                    "directory_rendered": True,
                    "section_embedding_count": 3,
                    "rag_section_ready": True,
                    "outline_metadata_ready": True,
                    "warnings": ["capture_body_noise_blocks_removed:2"],
                }
            },
        },
        warnings=["capture_body_noise_blocks_removed:2"],
    )

    result = extract_run_evidence("capture", "commit_capture_draft", envelope)

    assert result["evidence"]["directory_rendered"] is True
    assert result["evidence"]["section_embedding_count"] == 3
    assert result["evidence"]["rag_section_ready"] is True
    assert result["evidence"]["outline_metadata_ready"] is True
    assert result["evidence"]["warning_count"] == 1


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
    assert result["output_ref"]["sources"] == envelope["source_map"]
    assert {
        "object_type": "capture",
        "object_id": "cap1",
        "role": "source",
        "source": "source_map",
    } in result["object_refs"]


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


def test_continue_rag_answer_records_paging_output():
    envelope = success_envelope(
        agent="rag",
        intent="continue_rag_answer",
        chat_preview="第二页正文",
        source_map=[
            source_ref(
                role="evidence",
                type="capture_note",
                id="cap1",
                title="材料",
                path="captures/a.md",
            )
        ],
        meta={"source_run_id": "run1", "page": 2, "has_more": False},
    )

    result = extract_run_evidence("rag", "continue_rag_answer", envelope)

    assert result["status"] == "success"
    assert result["evidence"]["source_run_id"] == "run1"
    assert result["evidence"]["page"] == 2
    assert result["evidence"]["has_more"] is False
    assert result["output_ref"] == {
        "source_run_id": "run1",
        "page": 2,
        "has_more": False,
    }


# ---------------------------------------------------------------------------
# Reading session evidence
# ---------------------------------------------------------------------------


def test_start_reading_session_success():
    envelope = success_envelope(
        agent="reading",
        intent="start_reading_session",
        chat_preview="已开启阅读会话。",
        durable_artifacts=[
            artifact_ref(
                type="reading_session",
                id="sess1",
                title="Test Article",
            )
        ],
        meta={
            "reading_session_id": "sess1",
            "source_type": "url",
            "output_type": "enhance_capture",
            "turn_index": 0,
            "turn_count": 1,
            "evidence_count": 2,
            "source_title": "Test Article",
        },
    )

    result = extract_run_evidence("reading", "start_reading_session", envelope)

    assert result["status"] == "success"
    assert result["evidence"]["reading_session_id"] == "sess1"
    assert result["evidence"]["source_type"] == "url"
    assert result["evidence"]["output_type"] == "enhance_capture"
    assert result["evidence"]["turn_count"] == 1
    assert result["evidence"]["evidence_count"] == 2
    assert result["output_ref"]["reading_session_id"] == "sess1"
    assert result["output_ref"]["source_title"] == "Test Article"


def test_start_reading_session_incomplete_no_session_id():
    envelope = success_envelope(
        agent="reading",
        intent="start_reading_session",
        chat_preview="尝试开启阅读会话但失败。",
        meta={},
    )

    result = extract_run_evidence("reading", "start_reading_session", envelope)

    assert result["status"] == "incomplete"
    assert result["evidence"]["reading_session_id"] == ""


def test_discuss_reading_extracts_turn_info():
    envelope = success_envelope(
        agent="reading",
        intent="discuss_reading",
        chat_preview="回答了第三个问题。",
        meta={
            "reading_session_id": "sess1",
            "turn_index": 3,
            "turn_count": 3,
            "evidence_count": 5,
            "needs_clarification": False,
        },
    )

    result = extract_run_evidence("reading", "discuss_reading", envelope)

    assert result["status"] == "success"
    assert result["evidence"]["reading_session_id"] == "sess1"
    assert result["evidence"]["turn_index"] == 3
    assert result["evidence"]["turn_count"] == 3
    assert result["evidence"]["evidence_count"] == 5
    assert result["evidence"]["needs_clarification"] is False


def test_show_reading_session_from_meta():
    envelope = success_envelope(
        agent="reading",
        intent="show_reading_session",
        chat_preview="当前阅读会话概览。",
        meta={
            "reading_session_id": "sess2",
            "source_type": "capture",
            "turn_count": 5,
            "evidence_count": 8,
        },
    )

    result = extract_run_evidence("reading", "show_reading_session", envelope)

    assert result["status"] == "success"
    assert result["evidence"]["reading_session_id"] == "sess2"
    assert result["evidence"]["source_type"] == "capture"
    assert result["evidence"]["turn_count"] == 5


def test_summarize_reading_session_with_draft():
    envelope = success_envelope(
        agent="reading",
        intent="summarize_reading_session",
        chat_preview="已生成阅读总结草稿。",
        durable_artifacts=[
            artifact_ref(
                type="reading_draft",
                id="rdraft1",
                title="阅读总结",
            )
        ],
        meta={
            "reading_session_id": "sess1",
            "reading_draft_id": "rdraft1",
            "output_type": "local_learning_note",
        },
    )

    result = extract_run_evidence("reading", "summarize_reading_session", envelope)

    assert result["status"] == "success"
    assert result["evidence"]["reading_draft_id"] == "rdraft1"
    assert result["evidence"]["output_type"] == "local_learning_note"
    assert result["output_ref"]["reading_draft_id"] == "rdraft1"


def test_summarize_reading_session_incomplete_no_draft_no_clarification():
    envelope = success_envelope(
        agent="reading",
        intent="summarize_reading_session",
        chat_preview="无法生成总结。",
        meta={"reading_session_id": "sess1"},
    )

    result = extract_run_evidence("reading", "summarize_reading_session", envelope)

    assert result["status"] == "incomplete"
    assert result["evidence"]["reading_draft_id"] == ""


def test_summarize_reading_session_success_with_clarification():
    """needs_clarification=True is a valid completion — agent asked user for more info."""
    envelope = success_envelope(
        agent="reading",
        intent="summarize_reading_session",
        chat_preview="需要更多信息才能总结。",
        meta={
            "reading_session_id": "sess1",
            "needs_clarification": True,
        },
    )

    result = extract_run_evidence("reading", "summarize_reading_session", envelope)

    assert result["status"] == "success"
    assert result["evidence"]["needs_clarification"] is True


def test_reading_session_error_status():
    envelope = success_envelope(
        agent="reading",
        intent="start_reading_session",
        chat_preview="出错了。",
        meta={},
    )
    envelope["status"] = "error"

    result = extract_run_evidence("reading", "start_reading_session", envelope)

    assert result["status"] == "error"


def test_sqlite_garden_card_without_markdown_path_is_success():
    envelope = success_envelope(
        agent="garden",
        intent="commit_garden_draft",
        chat_preview="已存入知识卡片。",
        durable_artifacts=[
            artifact_ref(
                type="garden_card",
                id="card1",
                title="只存 SQLite 的卡片",
                status="seed",
            )
        ],
        meta={"card_id": "card1", "path": "", "storage": "sqlite"},
    )

    result = extract_run_evidence("garden", "commit_garden_draft", envelope)

    assert result["status"] == "success"
    assert result["evidence"]["card_created"] is True
    assert result["evidence"]["card_id"] == "card1"
    assert result["evidence"]["path"] == ""
    assert result["evidence"]["storage"] == "sqlite"
    assert result["output_ref"]["storage"] == "sqlite"


def test_garden_card_commit_without_card_id_is_incomplete():
    envelope = success_envelope(
        agent="garden",
        intent="commit_garden_draft",
        chat_preview="没有保存成功。",
        meta={"path": "garden/not-written.md", "storage": "sqlite"},
    )

    result = extract_run_evidence("garden", "commit_garden_draft", envelope)

    assert result["status"] == "incomplete"
    assert result["evidence"]["card_created"] is False

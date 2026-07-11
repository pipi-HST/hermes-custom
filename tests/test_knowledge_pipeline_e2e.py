from __future__ import annotations


def test_isolated_cross_process_pipeline_e2e(tmp_path):
    from scripts.knowledge_pipeline_e2e import run_pipeline_e2e

    result = run_pipeline_e2e(work_dir=tmp_path)

    failed = {
        item["name"]: item["failed_checks"]
        for item in result.get("pipelines", [])
        if not item["ok"]
    }
    assert result["ok"] is True, {
        "orchestrator_error": result.get("orchestrator_error"),
        "failed_pipelines": failed,
        "logs": result.get("logs"),
    }
    pipelines = {item["name"]: item for item in result["pipelines"]}
    assert {
        "capture_http_to_storage",
        "runtime_library_visibility",
        "reading_http_to_capture",
        "garden_http_to_card",
        "rag_retrieval",
        "theme_scan",
        "daily_review",
        "canvas_generation",
        "knowledge_graph_refresh",
    } <= set(pipelines)
    assert all(item["ok"] for item in pipelines.values())
    assert all(item["checks"] for item in pipelines.values())
    assert all(all(check["ok"] for check in item["checks"]) for item in pipelines.values())


def test_pipeline_e2e_reports_injected_embedding_break(tmp_path):
    from scripts.knowledge_pipeline_e2e import run_pipeline_e2e

    result = run_pipeline_e2e(work_dir=tmp_path, faults={"embeddings"})

    assert result["ok"] is False
    pipelines = {item["name"]: item for item in result["pipelines"]}
    assert "identity_and_section_embeddings_written" in pipelines["capture_http_to_storage"]["failed_checks"]
    assert "rag_used_index" in pipelines["rag_retrieval"]["failed_checks"]

from __future__ import annotations
from tests.conftest import requires_runtime, requires_symlink  # noqa: E402


def test_knowledge_pipeline_acceptance_matrix_covers_all_major_pipelines(tmp_path):
    from scripts.knowledge_pipeline_acceptance_matrix import run_pipeline_acceptance_matrix

    result = run_pipeline_acceptance_matrix(work_dir=tmp_path)

    assert result["ok"] is True, result
    scenarios = {item["name"]: item for item in result["scenarios"]}
    assert {
        "editable_long_paste",
        "editable_import_markdown",
        "editable_parse_failure_diagnostics",
        "editable_update_and_commit",
        "capture_plain_text",
        "capture_link_default",
        "capture_link_organize",
        "capture_link_full_text_commit",
        "capture_link_extract_failure",
        "capture_revise_and_discard",
        "committed_capture_revise_preview_and_apply",
        "reading_answer_draft_commit",
        "reading_answer_enhance_capture",
        "reading_draft_revision_guards",
        "garden_draft_commit",
        "garden_duplicate_blocks_creation",
        "frontend_contracts_present",
    } <= set(scenarios)
    assert all(item["ok"] for item in scenarios.values())


def test_capture_content_success_and_extract_failure_are_observable(tmp_path):
    import json

    from agents.capture_agent import CaptureAgent, CaptureConfig
    from agents.extractors.base import BaseExtractor, ExtractionResult
    from bridge import capture_bridge
    from db.agent_run_repo import AgentRunRepo
    from db.schema import init_db

    class FakeExtractor(BaseExtractor):
        def can_handle(self, source: str) -> bool:
            return source.startswith("https://observable.test/")

        def extract(self, source: str, **_kwargs):
            if source.endswith("/fail"):
                return ExtractionResult.failed(
                    code="parse_error",
                    message="测试提取失败",
                    source_type="link",
                    source_url=source,
                )
            return ExtractionResult(
                raw_text=(
                    "可观察成功正文，长度足够用于创建草稿。"
                    "这段内容说明链接提取成功后应该生成待审草稿，"
                    "并且 API meta 里必须带 draft_id，方便前端继续展开、修改和入库。"
                    "同时运行记录需要保留阶段耗时，后续排查慢链路时才能知道问题发生在哪一步。"
                ),
                source_type="link",
                source_url=source,
                title="可观察成功",
            )

    conn = init_db(str(tmp_path / "captures.db"))
    config = CaptureConfig(
        llm_api_key="test-key",
        llm_provider="openai",
        llm_model="test-model",
        llm_base_url="https://example.invalid",
        qwen_api_key="",
        vault_path=str(tmp_path / "vault"),
        db_path=str(tmp_path / "captures.db"),
    )
    agent = CaptureAgent(config, conn=conn)
    agent.extractors = [FakeExtractor()]
    agent._call_llm = lambda *_args, **_kwargs: "## 草稿\n\n保留原文。"
    agent.summarizer.summarize = lambda *_args, **_kwargs: {
        "title": "可观察成功",
        "summary": "摘要",
        "tags": ["observable"],
    }
    original_config = capture_bridge._config
    original_agent = capture_bridge._agent
    try:
        capture_bridge._config = config
        capture_bridge._agent = agent

        success = capture_bridge.capture_content("https://observable.test/success")
        assert success["status"] == "success"
        assert success["meta"]["draft_id"]

        failed = capture_bridge.capture_content("https://observable.test/fail")
        assert failed["status"] == "error"
        assert failed["meta"]["extraction_failed"] is True
        assert failed["meta"]["failure"]["code"] == "parse_error"

        error_runs = [row for row in AgentRunRepo(conn).latest(limit=5) if row["status"] == "error"]
        assert error_runs
        evidence = json.loads(error_runs[0]["evidence_json"])
        assert evidence["extraction_failed"] is True
        assert evidence["failure"]["code"] == "parse_error"
        assert "extract_clean" in evidence["stage_timings_ms"]
    finally:
        capture_bridge._config = original_config
        capture_bridge._agent = original_agent
        conn.close()


def test_live_smoke_reports_skipped_without_live_llm_opt_in(tmp_path, monkeypatch):
    from scripts.knowledge_pipeline_live_smoke import run_live_smoke

    monkeypatch.delenv("HERMES_LIVE_LLM_SMOKE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    result = run_live_smoke(pipeline="editable-long", work_dir=tmp_path)

    assert result["pipeline"] == "editable-long"
    assert result["status"] == "skipped"
    assert result["file_written"] is False
    assert result["db_rows"] == {}
    assert "reason" in result


def test_live_smoke_can_read_llm_settings_from_config(tmp_path, monkeypatch):
    from scripts.knowledge_pipeline_live_smoke import _live_llm_settings

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "llm:",
                "  provider: deepseek",
                "  model: deepseek-v4-pro",
                "  base_url: https://api.deepseek.com",
                "  api_key: config-key",
                "qwen:",
                "  api_key: qwen-key",
                "vault:",
                f"  path: {tmp_path / 'vault'}",
                "database:",
                f"  path: {tmp_path / 'captures.db'}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("HERMES_LLM_API_KEY", raising=False)
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(config_path))

    settings = _live_llm_settings()

    assert settings["api_key"] == "config-key"
    assert settings["model"] == "deepseek-v4-pro"
    assert settings["base_url"] == "https://api.deepseek.com"


def test_live_smoke_body_len_reads_committed_markdown_body(tmp_path):
    from scripts.knowledge_pipeline_live_smoke import _body_len_from_envelope

    captures = tmp_path / "captures"
    captures.mkdir()
    note = captures / "sample.md"
    note.write_text(
        "---\n"
        "title: Sample\n"
        "---\n\n"
        "这是正式 Markdown 正文。\n\n第二段继续说明内容。\n",
        encoding="utf-8",
    )

    body_len = _body_len_from_envelope(
        tmp_path,
        {
            "meta": {"file_path": "captures/sample.md"},
            "durable_artifacts": [],
        },
    )

    assert body_len == len("这是正式 Markdown 正文。\n\n第二段继续说明内容。")


def test_live_smoke_counts_preserved_markers():
    from scripts.knowledge_pipeline_live_smoke import _count_preserved_markers

    markers = ["HERMES-LIVE-SIGNAL-01", "HERMES-LIVE-SIGNAL-02"]

    assert _count_preserved_markers("保留 HERMES-LIVE-SIGNAL-01", markers) == 1

pytestmark = requires_runtime

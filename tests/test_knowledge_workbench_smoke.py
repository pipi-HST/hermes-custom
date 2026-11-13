from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.capture_agent import CaptureAgent, CaptureConfig
from agents.extractors.base import ExtractionResult
from agents.extractors.text_extractor import TextExtractor


def _config(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return SimpleNamespace(
        db_path=str(tmp_path / "captures.db"),
        vault_path=str(vault),
        llm_api_key="test-key",
        llm_provider="openai",
        llm_model="test-model",
        llm_base_url="https://example.test",
        qwen_api_key="",
    )


def test_long_material_capture_smoke_creates_draft_commits_and_writes_artifacts(tmp_path, monkeypatch):
    from bridge import capture_bridge

    config = CaptureConfig(
        llm_api_key="test-key",
        llm_provider="openai",
        llm_model="test-model",
        llm_base_url="https://example.test",
        vault_path=str(tmp_path / "vault"),
        db_path=str(tmp_path / "captures.db"),
    )
    agent = CaptureAgent(config)
    agent.extractors = [TextExtractor()]
    monkeypatch.setattr(capture_bridge, "_init", lambda: (config, agent))
    monkeypatch.setattr(
        agent,
        "_call_llm",
        lambda _prompt: (
            "## 目录纲要\n\n- [关键判断](#关键判断)\n\n"
            "## 关键判断\n\n**长材料的关键判断**需要经过完整入库链路验证。"
        ),
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda _text, existing_tags=None: {
            "summary": "长材料的关键判断。",
            "tags": ["smoke"],
        },
    )

    draft = capture_bridge.capture_content(
        "这是一段足够长的测试材料，用于验证 Capture 先草稿后确认入库的产品路径。" * 4,
        instruction="整理后存库",
    )
    committed = capture_bridge.commit_capture_draft(
        draft["durable_artifacts"][0]["id"],
        confirmation_source="api",
        token_verified=True,
    )

    assert draft["status"] == "success"
    assert committed["status"] == "success"
    assert committed["meta"]["raw_path"].startswith("_archive/raw/")
    assert committed["meta"]["file_path"].startswith("captures/")
    assert (Path(config.vault_path) / committed["meta"]["raw_path"]).exists()
    assert (Path(config.vault_path) / committed["meta"]["file_path"]).exists()


def test_sourced_qa_smoke_saves_answer_draft_revises_and_commits(tmp_path, monkeypatch):
    from bridge import answer_bridge, reading_bridge

    config = _config(tmp_path)
    monkeypatch.setattr(answer_bridge, "load_config", lambda _path: config)
    monkeypatch.setattr(reading_bridge, "load_config", lambda _path: config)
    monkeypatch.setattr(
        answer_bridge,
        "_call_revision_llm",
        lambda **_kwargs: "# 修改后文稿\n\n保留引用判断，并补充后续动作。",
    )
    monkeypatch.setattr(
        reading_bridge,
        "_postprocess_committed_learning_note",
        lambda *_args, **_kwargs: {"embedding_index": {"updated": 1}},
    )
    answer_bridge._config = None
    answer_bridge._repo = None
    reading_bridge._config = None
    reading_bridge._repo = None

    draft = answer_bridge.save_answer_to_knowledge(
        hermes_session_id="session-smoke",
        user_message="这个判断怎么复用？",
        assistant_answer="先确认来源，再沉淀判断框架。[1]",
        instruction="把这个回答存为学习笔记",
        output_type="local_learning_note",
        sources=[{"type": "capture_note", "id": "cap1", "title": "材料一"}],
    )
    revised = answer_bridge.revise_answer_draft(
        draft["meta"]["reading_draft_id"],
        "补充后续动作",
    )
    committed = reading_bridge.commit_reading_artifact(draft["meta"]["reading_draft_id"], confirmation="确认整理")

    assert draft["status"] == "success"
    assert revised["status"] == "success"
    assert committed["status"] == "success"
    assert committed["meta"]["write_service"] == "knowledge_write_service"
    assert committed["durable_artifacts"][0]["type"] == "capture_note"
    assert (Path(config.vault_path) / committed["meta"]["file_path"]).exists()


def test_reading_to_knowledge_card_smoke_writes_garden_card(tmp_path, monkeypatch):
    from bridge import reading_bridge

    config = _config(tmp_path)
    monkeypatch.setattr(reading_bridge, "load_config", lambda _path: config)
    reading_bridge._config = None
    reading_bridge._repo = None
    with patch.object(reading_bridge.httpx, "Client", side_effect=RuntimeError("no model")):
        session = reading_bridge.start_reading_session(
            message="讨论",
            excerpt="先判断结构，再决定整理粒度。",
        )
        reading_bridge.discuss_reading(
            session_id=session["meta"]["reading_session_id"],
            message="这个方法以后怎么复用？",
            assistant_answer="它可以作为长文整理前先判断结构的工作流。",
        )
        draft = reading_bridge.summarize_reading_session(
            session_id=session["meta"]["reading_session_id"],
            output_type="knowledge_card",
        )
        committed = reading_bridge.commit_reading_artifact(draft["meta"]["reading_draft_id"], confirmation="确认整理")

    assert committed["status"] == "success"
    assert committed["durable_artifacts"][0]["type"] == "knowledge_card"
    assert committed["meta"]["storage"] == "sqlite"
    assert committed["durable_artifacts"][0].get("path", "") == ""
    assert not (Path(config.vault_path) / "garden").exists()


def test_extractor_failure_smoke_does_not_create_failed_placeholder_draft(tmp_path):
    class FailingExtractor:
        def can_handle(self, _source: str) -> bool:
            return True

        def extract(self, _source: str, **_kwargs) -> ExtractionResult:
            return ExtractionResult.failed(
                code="auth_error",
                message="图片提取未配置 API key",
                source_type="image",
                source_url="/tmp/image.png",
                retryable=False,
            )

    config = CaptureConfig(
        llm_api_key="test-key",
        llm_provider="openai",
        llm_model="test-model",
        llm_base_url="https://example.test",
        vault_path=str(tmp_path / "vault"),
        db_path=str(tmp_path / "captures.db"),
    )
    agent = CaptureAgent(config)
    agent.extractors = [FailingExtractor()]

    result = agent.create_draft("/tmp/image.png")

    assert result["extraction_failed"] is True
    assert agent.draft_repo.list_open(limit=10) == []

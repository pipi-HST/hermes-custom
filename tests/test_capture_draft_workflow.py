from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agents.capture_agent import CaptureAgent, CaptureConfig, CaptureIntent, LLMResult
from agents.extractors.base import ExtractionFailure, ExtractionResult
from agents.extractors.text_extractor import TextExtractor
from agents.markdown_writer import read_note
from bridge.ai_cancel import AIOperationCancelled, cancel_operation, operation_context


TEST_CONTENT = (
    "AI时代的电力协议讨论了算力中心、电网扩容和中国变压器供应链之间的关系。"
    "文章指出AI发展的瓶颈不仅是芯片和模型，也包括稳定电力基础设施。"
    "其中关于中国变压器供应链的部分尤其重要，因为电力设备交付周期长，"
    "会直接影响数据中心扩张速度。补充文字确保内容长度足够通过质量检测。"
)


@pytest.fixture
def agent(tmp_path: Path) -> CaptureAgent:
    config = CaptureConfig(
        llm_api_key="sk-test",
        llm_provider="openai",
        llm_model="gpt-4o",
        llm_base_url="https://api.deepseek.com",
        vault_path=str(tmp_path),
        db_path=str(tmp_path / "test.db"),
    )
    agent = CaptureAgent(config)
    agent.extractors = [TextExtractor()]
    return agent


def test_create_draft_without_instruction_does_not_write_obsidian(agent: CaptureAgent, tmp_path: Path):
    draft = agent.create_draft(TEST_CONTENT)

    assert draft["status"] == "raw_extracted"
    assert draft["raw_text"]
    assert draft["draft_text"] == ""
    assert not (tmp_path / "raw").exists()
    assert not (tmp_path / "captures").exists()


def test_create_editable_draft_generates_title_and_keeps_body_editable(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "AI知识库入口",
            "summary": "自由编辑入口接入知识库检索。",
            "tags": ["ai-product"],
        },
    )

    draft = agent.create_editable_draft(
        "用户粘贴并编辑后的正文，可以像飞书一样自由修改。",
        source="manual paste",
    )

    assert draft["status"] == "draft_ready"
    assert draft["title"] == "AI知识库入口"
    assert draft["raw_text"] == "用户粘贴并编辑后的正文，可以像飞书一样自由修改。"
    assert draft["draft_text"] == "用户粘贴并编辑后的正文，可以像飞书一样自由修改。"
    assert draft["summary"] == "自由编辑入口接入知识库检索。"
    assert draft["tags"] == ["ai-product"]
    assert draft["capture_intent"]["entry_mode"] == "editable_capture"
    assert draft["capture_intent"]["title_source"] == "ai"
    assert draft["capture_intent"]["ai_suggestions_status"] == "generated"
    assert not (tmp_path / "captures").exists()


def test_create_editable_draft_accepts_user_title(
    agent: CaptureAgent,
    monkeypatch,
):
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "AI 不应覆盖",
            "summary": "用户先填标题，AI 只补摘要和标签。",
            "tags": ["ai-product"],
        },
    )

    draft = agent.create_editable_draft(
        "用户粘贴正文，但一开始就输入了自己的标题。",
        title="我的 AI 产品笔记",
    )

    assert draft["title"] == "我的 AI 产品笔记"
    assert draft["summary"] == "用户先填标题，AI 只补摘要和标签。"
    assert draft["tags"] == ["ai-product"]
    assert draft["capture_intent"]["title_source"] == "user"
    assert draft["capture_intent"]["ai_suggestions_status"] == "generated"


def test_create_editable_draft_ai_title_source_overrides_placeholder(
    agent: CaptureAgent,
    monkeypatch,
):
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "Cursor Agent Harness 实践笔记",
            "summary": "整理 Cursor 构建 Agent Harness 的核心方法。",
            "tags": ["ai-agent"],
        },
    )

    draft = agent.create_editable_draft(
        "Cursor 如何构建 Agent Harness\n\n正文讨论 eval、工具和执行环境。",
        title="整理稿",
        title_source="ai",
        source_type="document",
    )

    assert draft["title"] == "Cursor Agent Harness 实践笔记"
    assert draft["capture_intent"]["title_source"] == "ai"
    assert draft["capture_intent"]["ai_suggestions_status"] == "generated"


def test_create_editable_draft_derives_title_when_ai_title_missing(
    agent: CaptureAgent,
    monkeypatch,
):
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "",
            "summary": "从正文标题派生标题。",
            "tags": ["ai-product"],
        },
    )

    draft = agent.create_editable_draft(
        "# 成为 AI-Native 产品经理\n\n正文讨论评测和产品迭代。",
        title="整理稿",
        title_source="ai",
        source_type="document",
    )

    assert draft["title"] == "成为 AI-Native 产品经理"
    assert draft["capture_intent"]["title_source"] == "ai"
    assert draft["capture_intent"]["ai_suggestions_status"] == "generated"


def test_create_editable_draft_cancelled_after_ai_does_not_write(
    agent: CaptureAgent,
    monkeypatch,
):
    def fake_organize(_text, *, existing_tags=None, existing_folders=None):
        cancel_operation("editable-create-op")
        return {
            "title": "AI 建议标题",
            "summary": "AI 建议摘要。",
            "tags": ["ai"],
            "body_markdown": "整理后的正文",
            "folder_suggestion": {"mode": "existing", "folder_path": "Inbox", "reason": "默认收件箱。"},
            "card_suggestion": {"should_generate": False, "reason": "暂不生成卡片。", "cards": []},
            "warnings": [],
            "diagnostics": {},
        }

    monkeypatch.setattr(agent.summarizer, "organize_editable_draft", fake_organize)

    with operation_context("editable-create-op"):
        with pytest.raises(AIOperationCancelled):
            agent.create_editable_draft("待整理正文", title="整理稿", title_source="ai")

    assert agent.draft_repo.list_open() == []


def test_commit_removes_leading_duplicate_body_title(agent: CaptureAgent, monkeypatch) -> None:
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "Agent Harness 整理。", "tags": ["ai-agent"]},
    )
    monkeypatch.setattr(
        agent,
        "_postprocess_committed_library",
        lambda capture_id, render_outline=True: {"pipeline": {"directory_rendered": False}},
    )
    draft = agent.draft_repo.create(
        raw_text="Cursor 如何构建 Agent Harness\n\n原文正文。",
        source_type="document",
        title="Cursor 如何构建 Agent Harness",
        draft_text=(
            "# Cursor 如何构建 Agent Harness\n\n"
            "## 目录纲要\n\n"
            "- Cursor 如何构建 Agent Harness\n"
            "  - 三层方法\n\n"
            "## Cursor 如何构建 Agent Harness\n\n"
            "## 三层方法\n\n正文。"
        ),
        status="draft_ready",
        capture_intent={"material_intent": "refined_note", "output_shape": ["natural_draft"]},
    )

    committed = agent.commit_draft(draft["id"])
    note = read_note(Path(agent.config.vault_path), committed["file_path"])

    assert note is not None
    _frontmatter, body = note
    assert not body.startswith("# Cursor 如何构建 Agent Harness")
    assert body.startswith("## 目录纲要")
    assert "- Cursor 如何构建 Agent Harness" not in body
    assert "\n- 三层方法" in body
    assert "\n  - 三层方法" not in body
    assert "\n## Cursor 如何构建 Agent Harness" not in body
    assert "\n## 三层方法" in body


def test_create_editable_draft_stores_light_body_and_suggestions(
    agent: CaptureAgent,
    monkeypatch,
):
    def fake_organize(_text, *, existing_tags=None, existing_folders=None):
        assert existing_folders == ["Inbox", "知识库自定义文件夹"]
        return {
            "title": "智能问数落地",
            "summary": "智能问数需要数据治理、语义层和产品运营协同。",
            "tags": ["data-agent", "ai-product"],
            "body_markdown": "# 智能问数落地\n\n正文只做格式修正。",
            "folder_suggestion": {
                "mode": "new",
                "folder_path": "智能问数",
                "reason": "这是一个可持续积累的独立主题。",
            },
            "card_suggestion": {
                "should_generate": True,
                "reason": "材料包含可复用的方法判断。",
            },
            "warnings": ["确认案例是否完整。"],
        }

    monkeypatch.setattr(agent.summarizer, "organize_editable_draft", fake_organize)

    draft = agent.create_editable_draft(
        "智能问数落地正文",
        existing_folders=["知识库自定义文件夹"],
    )

    assert draft["title"] == "智能问数落地"
    assert draft["draft_text"] == "# 智能问数落地\n\n正文只做格式修正。"
    assert draft["summary"] == "智能问数需要数据治理、语义层和产品运营协同。"
    assert draft["tags"] == ["data-agent", "ai-product"]
    assert draft["capture_intent"]["folder_suggestion"]["folder_path"] == "智能问数"
    assert draft["capture_intent"]["card_suggestion"]["should_generate"] is True
    assert draft["capture_intent"]["warnings"] == ["确认案例是否完整。"]
    assert draft["capture_intent"]["ai_suggestions_status"] == "generated"


def test_create_editable_draft_marks_failed_ai_suggestions(
    agent: CaptureAgent,
    monkeypatch,
):
    def fail_organize(*_args, **_kwargs):
        return {
            "title": "",
            "summary": "",
            "tags": [],
            "body_markdown": "",
            "folder_suggestion": {},
            "card_suggestion": {},
            "warnings": ["AI 整理结果解析失败，请手动填写摘要、标签和建议。"],
            "diagnostics": {"parse_error_kind": "json_parse_failed"},
        }

    monkeypatch.setattr(agent.summarizer, "organize_editable_draft", fail_organize)

    draft = agent.create_editable_draft("用户导入的 Markdown 正文")

    assert draft["draft_text"] == "用户导入的 Markdown 正文"
    assert draft["capture_intent"]["ai_suggestions_status"] == "failed"
    assert draft["capture_intent"]["diagnostics"]["parse_error_kind"] == "json_parse_failed"


def test_create_editable_draft_can_save_without_organizing(
    agent: CaptureAgent,
    monkeypatch,
):
    monkeypatch.setattr(
        agent.summarizer,
        "organize_editable_draft",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("保存空白草稿不应整理")),
    )

    draft = agent.create_editable_draft(
        "用户直接写作的正文",
        title="用户标题",
        folder_path="Inbox",
        organize=False,
    )

    assert draft["status"] == "draft_ready"
    assert draft["title"] == "用户标题"
    assert draft["summary"] == ""
    assert draft["tags"] == []
    assert draft["draft_text"] == "用户直接写作的正文"
    assert draft["capture_intent"]["title_source"] == "user"
    assert draft["capture_intent"]["folder_path"] == "Inbox"
    assert draft["capture_intent"]["ai_suggestions_status"] == "not_generated"


def test_create_editable_draft_uses_ai_structured_body_markdown(
    agent: CaptureAgent,
    monkeypatch,
):
    original = "第一段保留。\n\n第二段保留。\n\n第三段也必须保留。"
    structured = "## 第一段\n\n第一段保留。\n\n## 第二段\n\n第二段保留。\n\n第三段也必须保留。"

    def fake_organize(_text, *, existing_tags=None, existing_folders=None):
        return {
            "title": "截断保护",
            "summary": "测试正文不能被 AI 缩短。",
            "tags": ["ai-product"],
            "body_markdown": structured,
            "folder_suggestion": {},
            "card_suggestion": {},
            "warnings": [],
        }

    monkeypatch.setattr(agent.summarizer, "organize_editable_draft", fake_organize)

    draft = agent.create_editable_draft(original)

    assert draft["draft_text"] == structured


def test_create_editable_draft_preserves_body_when_metadata_has_no_body(
    agent: CaptureAgent,
    monkeypatch,
):
    original = "第一段讲知织库。\n\n第二段也要保留。"

    def fake_organize(_text, *, existing_tags=None, existing_folders=None):
        return {
            "title": "长文整理",
            "summary": "长文也要返回整理后的正文。",
            "tags": ["ai-product"],
            "body_markdown": "",
            "folder_suggestion": {},
            "card_suggestion": {},
            "warnings": [],
        }

    monkeypatch.setattr(agent.summarizer, "organize_editable_draft", fake_organize)
    monkeypatch.setattr(
        agent.summarizer,
        "cleanup_editable_body",
        lambda _text: (_ for _ in ()).throw(AssertionError("正文不应再调用 AI 整理")),
        raising=False,
    )

    draft = agent.create_editable_draft(original)

    assert draft["draft_text"] == original


def test_create_editable_draft_normalizes_empty_suggestions(
    agent: CaptureAgent,
    monkeypatch,
):
    def fake_organize(_text, *, existing_tags=None, existing_folders=None):
        return {
            "title": "建议兜底",
            "summary": "空建议也要变成可展示对象。",
            "tags": ["ai-product"],
            "body_markdown": "",
            "folder_suggestion": {},
            "card_suggestion": {},
            "warnings": [],
        }

    monkeypatch.setattr(agent.summarizer, "organize_editable_draft", fake_organize)

    draft = agent.create_editable_draft("正文内容")

    assert draft["capture_intent"]["folder_suggestion"]["folder_path"] == "Inbox"
    assert draft["capture_intent"]["folder_suggestion"]["reason"]
    assert draft["capture_intent"]["card_suggestion"]["should_generate"] is False
    assert draft["capture_intent"]["card_suggestion"]["reason"]


def test_editable_draft_commits_folder_path_to_capture_note(
    agent: CaptureAgent,
    monkeypatch,
):
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "文件夹笔记",
            "summary": "新建文件保留用户选择的文件夹。",
            "tags": ["workspace"],
        },
    )

    draft = agent.create_editable_draft(
        "从 AI 产品文件夹中新建的正文。",
        folder_path="AI 产品",
    )
    committed = agent.commit_draft(draft["id"])
    note = read_note(Path(agent.config.vault_path).expanduser(), committed["file_path"])

    assert draft["capture_intent"]["folder_path"] == "AI 产品"
    assert note is not None
    frontmatter, note_body = note
    assert frontmatter["folder_path"] == "AI 产品"
    assert note_body == "从 AI 产品文件夹中新建的正文。"


def test_update_editable_draft_preserves_user_title_override(
    agent: CaptureAgent,
    monkeypatch,
):
    summaries = [
        {"title": "AI自动标题", "summary": "初始摘要。", "tags": ["ai-product"]},
        {"title": "AI新标题", "summary": "更新摘要。", "tags": ["ai-rag"]},
        {"title": "AI不应覆盖", "summary": "再次更新。", "tags": ["ai-agent"]},
    ]

    def fake_summarize(_text, existing_tags=None):
        return summaries.pop(0)

    monkeypatch.setattr(agent.summarizer, "summarize", fake_summarize)
    draft = agent.create_editable_draft("第一版正文")

    updated = agent.update_editable_draft(
        draft["id"],
        content="第二版正文",
        title="用户自己的标题",
    )
    assert updated is not None
    assert updated["title"] == "用户自己的标题"
    assert updated["draft_text"] == "第二版正文"
    assert updated["capture_intent"]["title_source"] == "user"

    updated_again = agent.update_editable_draft(
        draft["id"],
        content="第三版正文",
    )
    assert updated_again is not None
    assert updated_again["title"] == "用户自己的标题"
    assert updated_again["summary"] == "再次更新。"
    assert updated_again["capture_intent"]["title_source"] == "user"


def test_update_editable_draft_can_save_without_organizing(
    agent: CaptureAgent,
    monkeypatch,
):
    organize_calls = 0

    def fake_organize(_text, *, existing_tags=None, existing_folders=None):
        nonlocal organize_calls
        organize_calls += 1
        return {
            "title": "AI 初始标题",
            "summary": "AI 初始摘要。",
            "tags": ["ai-product"],
            "body_markdown": "AI 整理后的正文",
            "folder_suggestion": {
                "mode": "existing",
                "folder_path": "AI 产品",
                "reason": "初始建议。",
            },
            "card_suggestion": {
                "should_generate": True,
                "reason": "初始卡片建议。",
                "cards": [{"title": "初始卡片", "card_kind": "method", "reason": "可复用。"}],
            },
            "warnings": [],
        }

    monkeypatch.setattr(agent.summarizer, "organize_editable_draft", fake_organize)
    draft = agent.create_editable_draft("原始正文")
    assert organize_calls == 1

    updated = agent.update_editable_draft(
        draft["id"],
        content="用户手动改过的最终正文",
        title="用户确认标题",
        summary="用户确认摘要。",
        tags=["user-tag"],
        folder_path="Inbox",
        folder_suggestion={
            "mode": "existing",
            "folder_path": "Inbox",
            "reason": "用户确认。",
        },
        card_suggestion={
            "should_generate": False,
            "reason": "用户暂不生成。",
            "cards": [],
        },
        organize=False,
    )

    assert organize_calls == 1
    assert updated is not None
    assert updated["draft_text"] == "用户手动改过的最终正文"
    assert updated["title"] == "用户确认标题"
    assert updated["summary"] == "用户确认摘要。"
    assert updated["tags"] == ["user-tag"]
    assert updated["capture_intent"]["folder_suggestion"]["reason"] == "用户确认。"
    assert updated["capture_intent"]["card_suggestion"]["should_generate"] is False
    assert updated["capture_intent"]["ai_suggestions_status"] == "not_generated"


def test_commit_editable_draft_uses_edited_body_and_frontmatter(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "自由编辑入库",
            "summary": "自由编辑后的内容进入原有检索 Pipeline。",
            "tags": ["ai-product"],
        },
    )
    draft = agent.create_editable_draft("原始粘贴正文")
    updated = agent.update_editable_draft(draft["id"], content="用户最终编辑后的正文")
    assert updated is not None

    committed = agent.commit_draft(draft["id"])

    capture_note = read_note(tmp_path, committed["file_path"])
    raw_note = read_note(tmp_path, committed["raw_path"])
    assert capture_note is not None
    assert raw_note is not None
    capture_frontmatter, capture_body = capture_note
    _raw_frontmatter, raw_body = raw_note
    assert raw_body == "原始粘贴正文"
    assert capture_body == "用户最终编辑后的正文"
    assert capture_frontmatter["title"] == "自由编辑入库"
    assert capture_frontmatter["entry_mode"] == "editable_capture"


def test_commit_editable_draft_does_not_generate_missing_metadata(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        agent.summarizer,
        "organize_editable_draft",
        lambda text, *, existing_tags=None, existing_folders=None: {
            "title": "无摘要草稿",
            "summary": "",
            "tags": [],
            "body_markdown": text,
            "folder_suggestion": {},
            "card_suggestion": {},
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("editable 入库不应补跑摘要 LLM")),
    )

    draft = agent.create_editable_draft("用户确认直接入库的正文")
    committed = agent.commit_draft(draft["id"])

    capture_note = read_note(tmp_path, committed["file_path"])
    assert capture_note is not None
    capture_frontmatter, capture_body = capture_note
    assert committed["summary"] == ""
    assert committed["tags"] == []
    assert capture_frontmatter["story"] == ""
    assert capture_frontmatter["tags"] == []
    assert capture_body == "用户确认直接入库的正文"
    assert capture_frontmatter["title_source"] == "ai"


def test_commit_draft_reuses_existing_summary_and_tags_without_resummarizing(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        agent.summarizer,
        "organize_editable_draft",
        lambda _text, *, existing_tags=None, existing_folders=None: {
            "title": "整理草稿标题",
            "summary": "整理草稿阶段生成的摘要。",
            "tags": ["ai-product", "knowledge-workflow"],
            "body_markdown": "",
            "folder_suggestion": {
                "mode": "existing",
                "folder_path": "Inbox",
                "reason": "默认分类。",
            },
            "card_suggestion": {
                "should_generate": False,
                "reason": "无需生成卡片。",
            },
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("入库不应重复生成摘要标签")),
    )
    draft = agent.create_editable_draft("正文内容")

    committed = agent.commit_draft(draft["id"])

    capture_note = read_note(tmp_path, committed["file_path"])
    assert capture_note is not None
    frontmatter, _body = capture_note
    assert committed["summary"] == "整理草稿阶段生成的摘要。"
    assert committed["tags"] == ["ai-product", "knowledge-workflow"]
    assert frontmatter["story"] == "整理草稿阶段生成的摘要。"
    assert frontmatter["tags"] == ["ai-product", "knowledge-workflow"]


def test_commit_draft_generates_summary_and_tags_when_missing(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "兜底标题不覆盖",
            "summary": "入库兜底生成摘要。",
            "tags": ["fallback-tag"],
        },
    )
    draft = agent.draft_repo.create(
        raw_text="原文",
        source="manual",
        source_type="manual",
        title="待入库草稿",
        word_count=4,
        draft_text="正式正文",
        summary="",
        tags=[],
        status="draft_ready",
    )

    committed = agent.commit_draft(draft["id"])

    capture_note = read_note(tmp_path, committed["file_path"])
    assert capture_note is not None
    frontmatter, _body = capture_note
    assert committed["summary"] == "入库兜底生成摘要。"
    assert committed["tags"] == ["fallback-tag"]
    assert frontmatter["story"] == "入库兜底生成摘要。"
    assert frontmatter["tags"] == ["fallback-tag"]


def test_commit_short_editable_draft_uses_identity_index(
    agent: CaptureAgent,
    monkeypatch,
):
    class FakeEmbedder:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def embed(self, text: str) -> bytes:
            assert "标题：短笔记检索" in text
            return b"identity"

    agent.config.qwen_api_key = "sk-test-qwen"
    monkeypatch.setattr("agents.capture_agent.Embedder", FakeEmbedder, raising=False)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "短笔记检索",
            "summary": "短自由笔记按整体检索。",
            "tags": ["ai-rag"],
        },
    )
    draft = agent.create_editable_draft("这是一条短自由笔记，讨论知识库入口体验。")

    committed = agent.commit_draft(draft["id"])

    rows = agent.repo.conn.execute(
        """SELECT kind, section_title, embedding
           FROM capture_embeddings
           WHERE capture_id = ?
           ORDER BY kind, section_title""",
        (committed["capture_id"],),
    ).fetchall()
    assert [(row["kind"], row["section_title"], row["embedding"]) for row in rows] == [
        ("identity", "", b"identity")
    ]


def test_commit_long_editable_draft_reuses_library_section_index(
    agent: CaptureAgent,
    monkeypatch,
):
    class FakeEmbedder:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def embed(self, text: str) -> bytes:
            if "章节：入口自由度" in text:
                return b"section-entry"
            if "章节：检索复用" in text:
                return b"section-rag"
            return b"identity"

    class FakeOutliner:
        def __init__(self, **_kwargs):
            pass

        def generate(self, _body: str, _length: int):
            return [
                {"level": 1, "title": "入口自由度", "summary": "用户像飞书一样自由编辑。"},
                {"level": 1, "title": "检索复用", "summary": "系统复用 Library Agent 和 RAG。"},
            ]

    long_body = (
        "## 入口自由度\n\n"
        + "入口自由度是关键，用户需要先自由粘贴和修改。" * 80
        + "\n\n"
        + "## 检索复用\n\n"
        + "检索复用也很关键，入库之后仍然应该使用原来的短长文本规则。" * 80
    )
    agent.config.qwen_api_key = "sk-test-qwen"
    monkeypatch.setattr("agents.capture_agent.Embedder", FakeEmbedder, raising=False)
    monkeypatch.setattr("agents.capture_agent.Outliner", FakeOutliner, raising=False)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "长笔记检索",
            "summary": "长自由笔记按章节检索。",
            "tags": ["ai-rag"],
        },
    )
    draft = agent.create_editable_draft(long_body)

    committed = agent.commit_draft(draft["id"])

    rows = agent.repo.conn.execute(
        """SELECT kind, section_title, embedding
           FROM capture_embeddings
           WHERE capture_id = ?
           ORDER BY kind, section_title""",
        (committed["capture_id"],),
    ).fetchall()
    values = [(row["kind"], row["section_title"], row["embedding"]) for row in rows]
    assert ("identity", "", b"identity") in values
    assert ("section", "入口自由度", b"section-entry") in values
    assert ("section", "检索复用", b"section-rag") in values
    assert committed["library_postprocess"]["outlines"]["updated"] == 1


def test_commit_long_editable_draft_rebuilds_bad_middle_only_directory(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    headings = [
        "开场：模型和 Harness 不是简单分离",
        "工具格式会影响模型行动",
        "提示词也要按模型适配",
        "中途切模型的三层代价",
        "更好的方式：用 subagent 开新现场",
        "多 agent 的编排问题",
    ]

    class FakeOutliner:
        def __init__(self, **_kwargs):
            pass

        def generate(self, _body: str, _length: int):
            return [
                {
                    "level": 1,
                    "title": "中途切模型的三层代价",
                    "summary": "LLM 只识别到中间章节。",
                }
            ]

    class FakeEmbedder:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def embed(self, text: str) -> bytes:
            return f"embed:{text[:12]}".encode("utf-8")

    bad_body = (
        "## 目录纲要\n\n"
        "- [中途切模型的三层代价]"
        "(http://127.0.0.1:5173/knowledge-library#中途切模型的三层代价)："
        "中途切换模型会带来 KV 缓存失效、连续性丢失和旧现场继承困难。\n\n"
        "整理说明： 以下为音频转写后的可读性整理稿，只修正明显转写错误、术语、标点和段落结构。\n\n"
        + "\n\n".join(f"## {title}\n\n" + f"{title}正文。" * 40 for title in headings)
    )

    def fake_organize(_text, *, existing_tags=None, existing_folders=None):
        return {
            "title": "别在任务过程中随便切模型",
            "summary": "讨论任务过程中切模型的问题。",
            "tags": ["agent-architecture"],
            "body_markdown": bad_body,
            "folder_suggestion": {},
            "card_suggestion": {},
            "warnings": [],
        }

    agent.config.qwen_api_key = "sk-test-qwen"
    monkeypatch.setattr(agent.summarizer, "organize_editable_draft", fake_organize)
    monkeypatch.setattr("agents.capture_agent.Outliner", FakeOutliner, raising=False)
    monkeypatch.setattr("agents.capture_agent.Embedder", FakeEmbedder, raising=False)

    raw_input = "整理说明： 以下为音频转写后的可读性整理稿，只修正明显转写错误。\n\n原始长文"
    draft = agent.create_editable_draft(raw_input)
    committed = agent.commit_draft(draft["id"])

    assert "error" not in committed
    capture_note = read_note(tmp_path, committed["file_path"])
    assert capture_note is not None
    _frontmatter, body = capture_note
    directory = body.split(f"## {headings[0]}", maxsplit=1)[0]
    for title in headings:
        assert f"- [{title}]" in directory
        assert f"## {title}" in body
    assert "127.0.0.1" not in directory
    assert "整理说明" not in body
    assert "LLM 只识别到中间章节" not in directory
    assert committed["library_postprocess"]["pipeline"]["directory_rendered"] is True
    assert committed["library_postprocess"]["pipeline"]["directory_heading_coverage_ready"] is True
    assert committed["library_postprocess"]["pipeline"]["directory_link_ready"] is True
    assert committed["library_postprocess"]["pipeline"]["directory_absolute_url_free"] is True
    assert committed["library_postprocess"]["pipeline"]["section_embedding_count"] == len(headings)

    raw_note = read_note(tmp_path, committed["raw_path"])
    assert raw_note is not None
    _raw_frontmatter, raw_body = raw_note
    assert "整理说明" in raw_body

    updated = agent.draft_repo.get_by_id(draft["id"])
    assert updated is not None
    assert updated["status"] == "committed"
    assert updated["draft_text"] == body


def test_create_draft_stops_on_typed_extraction_failure(agent: CaptureAgent):
    class FailingExtractor:
        def can_handle(self, _source: str) -> bool:
            return True

        def extract(self, _source: str, **_kwargs) -> ExtractionResult:
            return ExtractionResult(
                raw_text="[图片提取未配置 API key]",
                source_type="image",
                source_url="/tmp/image.png",
                extraction_failed=True,
                failure=ExtractionFailure(
                    code="auth_error",
                    message="图片提取未配置 API key",
                    retryable=False,
                ),
            )

    agent.extractors = [FailingExtractor()]

    result = agent.create_draft("/tmp/image.png")

    assert result["error"] == "内容提取失败：图片提取未配置 API key"
    assert result["extraction_failed"] is True
    assert result["failure"] == {
        "code": "auth_error",
        "message": "图片提取未配置 API key",
        "retryable": False,
    }
    assert agent.draft_repo.list_open(limit=10) == []


def test_create_draft_with_instruction_returns_draft_text(agent: CaptureAgent, tmp_path: Path, monkeypatch):
    prompts: list[str] = []

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return "核心观点：AI瓶颈在电力。"

    monkeypatch.setattr(agent, "_call_llm", fake_llm)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "AI电力协议",
            "summary": "AI发展受电力基础设施制约。",
            "tags": ["ai-energy"],
        },
    )

    draft = agent.create_draft(TEST_CONTENT, instruction="总结全文核心观点")

    assert draft["status"] == "raw_extracted"
    assert draft["draft_text"] == ""

    revised = agent.revise_draft(draft["id"], "总结全文核心观点")

    assert revised["status"] == "draft_ready"
    assert revised["draft_text"] == "核心观点：AI瓶颈在电力。"
    assert revised["summary"] == "AI发展受电力基础设施制约。"
    assert revised["tags"] == ["ai-energy"]
    assert not (tmp_path / "captures").exists()
    assert prompts[0].count(TEST_CONTENT) == 1


def test_create_draft_uses_llm_planning_for_unenumerated_user_focus(
    agent: CaptureAgent,
    monkeypatch,
):
    prompts: list[str] = []
    agent._capture_planning_enabled = True

    monkeypatch.setattr(
        agent,
        "_call_capture_planner",
        lambda **kwargs: {
            "task_type": "organize",
            "coverage": "full_article",
            "focus": ["世界杯效应"],
            "detail_level": "deep",
            "compression": "medium",
            "preserve": ["methodology"],
            "title": "写在世界杯之前：科技牛市与短期判断",
            "detail_policy": "全文正常整理，用户点名的世界杯效应部分重点展开。",
        },
        raising=False,
    )

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return "整理稿：世界杯效应是文章的方法论重点。"

    monkeypatch.setattr(agent, "_call_llm", fake_llm)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "世界杯效应。", "tags": ["market"]},
    )

    draft = agent.create_draft(
        "整理稿(2)\n\n"
        + ("文章讨论科技牛市，其中世界杯效应用来解释短期行情判断。" * 8),
        instruction="整理文章的核心内容，特别是世界杯效应那部分要详细阐述",
    )

    assert draft["title"] == "写在世界杯之前：科技牛市与短期判断"
    assert draft["capture_intent"]["emphasis"] == ["世界杯效应"]
    assert draft["capture_intent"]["detail_level"] == "deep"
    assert draft["capture_intent"]["detail_policy"] == "全文正常整理，用户点名的世界杯效应部分重点展开。"
    assert draft["capture_intent"]["plan_source"] == "llm"

    agent.revise_draft(draft["id"], "整理文章的核心内容，特别是世界杯效应那部分要详细阐述")

    assert "世界杯效应" in prompts[0]
    assert "detail_policy" in prompts[0]


def test_capture_planning_invalid_plan_uses_conservative_fallback(
    agent: CaptureAgent,
    monkeypatch,
):
    monkeypatch.setattr(
        agent,
        "_call_capture_planner",
        lambda **kwargs: {"focus": "not-a-list", "detail_level": "extreme"},
        raising=False,
    )

    intent = agent._build_capture_intent_with_planning(
        "总结全文核心内容，方法论那部分详细一些",
        source_text=TEST_CONTENT,
        title_hint="",
    ).to_dict()

    assert intent["plan_source"] == "fallback"
    assert intent["emphasis"] == ["方法论"]
    assert intent["detail_level"] == "deep"


def test_planner_cannot_turn_special_focus_into_selected_sections(agent: CaptureAgent, monkeypatch):
    agent._capture_planning_enabled = True
    monkeypatch.setattr(
        agent,
        "_call_capture_planner",
        lambda **kwargs: {
            "task_type": "organize",
            "coverage": "selected_sections",
            "focus": ["世界杯期间市场节奏"],
            "detail_level": "medium",
            "compression": "high",
            "preserve": ["data"],
            "title": "冰火两重天",
            "detail_policy": "全文正常整理，用户点名部分重点展开。",
        },
        raising=False,
    )

    intent = agent._build_capture_intent_with_planning(
        "总结这篇文章的核心内容，特别是世界杯期间市场节奏那部分",
        source_text=TEST_CONTENT,
        title_hint="整理稿(1)",
    ).to_dict()

    assert intent["plan_source"] == "llm"
    assert intent["coverage"] == "full_article"
    assert intent["emphasis"] == ["世界杯期间市场节奏"]
    assert "source_fidelity" in intent["must_preserve"]


def test_planned_detail_policy_becomes_explicit_prompt_rule(agent: CaptureAgent):
    intent = agent._build_capture_intent("总结全文").to_dict()
    intent["plan_source"] = "llm"
    intent["detail_policy"] = "全文正常整理，用户点名的世界杯效应部分重点展开。"

    prompt = agent._build_revision_prompt(
        instruction="总结全文",
        capture_intent=intent,
        current_text=TEST_CONTENT,
        raw_text=TEST_CONTENT,
    )

    assert "内部规划给出的详略策略" in prompt
    assert "全文正常整理，用户点名的世界杯效应部分重点展开。" in prompt


def test_full_text_save_keeps_full_coverage_and_builds_readable_capture(
    agent: CaptureAgent,
    monkeypatch,
):
    readable_body = "## 正文\n\n**中国变压器供应链** 是全文中的关键内容。\n\n" + TEST_CONTENT
    monkeypatch.setattr(
        agent,
        "_revise_with_llm",
        lambda **kwargs: readable_body,
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "summary": "全文保存。",
            "tags": ["source-archive"],
        },
    )

    draft = agent.create_draft(TEST_CONTENT, instruction="全文存进数据库")

    assert draft["status"] == "raw_extracted"
    assert draft["draft_text"] == ""
    assert draft["summary"] == ""
    assert draft["tags"] == []
    assert draft["capture_intent"]["task_type"] == "capture"
    assert draft["capture_intent"]["output_shape"] == ["full_text"]
    assert draft["capture_intent"]["material_intent"] == "verbatim_full_text"
    assert draft["capture_intent"]["draft_guidance"] == "verbatim"
    assert draft["capture_intent"]["compression"] == "none"
    assert draft["capture_intent"]["plan_source"] == "deterministic_full_text"

    committed = agent.commit_draft(draft["id"])
    updated = agent.draft_repo.get_by_id(draft["id"])

    assert committed["status"] == "committed"
    assert "## 目录纲要" in updated["draft_text"]
    assert "## 正文" in updated["draft_text"]
    assert "**中国变压器供应链**" in updated["draft_text"]
    assert TEST_CONTENT in updated["draft_text"]
    assert updated["summary"] == "全文保存。"
    assert updated["tags"] == ["source-archive"]


def test_full_text_save_keeps_original_archive_and_formats_capture_text(
    agent: CaptureAgent,
    monkeypatch,
):
    extracted_text = (
        "  # 原文标题  \n\n"
        "第一段原文保留  行内空格。这里继续补充足够长的正文，"
        "确保质量检测认为它是一篇真实材料。\n\n\n"
        "关注我们，获取更多精彩内容\n\n"
        "第二段原文也要保留。它包含作者的完整表达、例子和结论，"
        "全文入库时都不能被清洗器改写。  "
    )

    class FakeExtractor:
        def can_handle(self, _source: str) -> bool:
            return True

        def extract(self, _source: str, **_kwargs) -> ExtractionResult:
            return ExtractionResult(
                raw_text=extracted_text,
                source_type="note",
                source_url="",
                title="原文标题",
            )

    agent.extractors = [FakeExtractor()]
    observed: dict[str, str] = {}

    def fake_revise(**kwargs):
        observed["raw_text"] = kwargs["raw_text"]
        return "## 正文\n\n**第一段原文** 保留完整证据，同时整理正式正文。"

    monkeypatch.setattr(
        agent,
        "_revise_with_llm",
        fake_revise,
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "全文保存。", "tags": ["source-archive"]},
    )

    draft = agent.create_draft("ignored", instruction="这篇原文一字不改全文入库")

    assert draft["raw_text"] == extracted_text
    assert draft["draft_text"] == ""

    committed = agent.commit_draft(draft["id"])
    updated = agent.draft_repo.get_by_id(draft["id"])

    assert observed["raw_text"] == extracted_text
    assert updated["raw_text"] == extracted_text
    assert updated["draft_text"] != updated["raw_text"]
    raw_note = read_note(Path(agent.config.vault_path), committed["raw_path"])
    assert raw_note is not None
    assert raw_note[1].strip() == extracted_text.strip()


def test_long_literal_raw_save_adds_directory_without_changing_raw(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    class FakeOutliner:
        def __init__(self, **_kwargs):
            pass

        def generate(self, _body: str, _length: int):
            return [
                {
                    "level": 1,
                    "title": "第一部分",
                    "start_paragraph_index": 1,
                    "summary": "第一部分摘要。",
                },
                {
                    "level": 1,
                    "title": "第二部分",
                    "start_paragraph_index": 3,
                    "summary": "第二部分摘要。",
                },
            ]

    class FakeEmbedder:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def embed(self, text: str) -> bytes:
            if "章节：第一部分" in text:
                return b"section-first"
            if "章节：第二部分" in text:
                return b"section-second"
            return b"identity"

    first = "\n\n".join(["第一部分原文一字不改。" * 12 for _ in range(8)])
    second = "\n\n".join(["第二部分原文一字不改。" * 12 for _ in range(8)])
    raw_text = first + "\n\n" + second
    readable_body = (
        "## 目录纲要\n\n"
        "- 第一部分\n"
        "- 第二部分\n\n"
        "## 第一部分\n\n"
        "**第一部分原文**\n\n"
        f"{first}\n\n"
        "## 第二部分\n\n"
        "**第二部分原文**\n\n"
        f"{second}"
    )

    class FakeExtractor:
        def can_handle(self, _source: str) -> bool:
            return True

        def extract(self, _source: str, **_kwargs) -> ExtractionResult:
            return ExtractionResult(
                raw_text=raw_text,
                source_type="note",
                source_url="",
                title="长文原文",
            )

    agent.extractors = [FakeExtractor()]
    agent.config.qwen_api_key = "sk-test-qwen"
    monkeypatch.setattr("agents.capture_agent.Outliner", FakeOutliner, raising=False)
    monkeypatch.setattr("agents.capture_agent.Embedder", FakeEmbedder, raising=False)
    monkeypatch.setattr(
        agent,
        "_revise_with_llm",
        lambda **kwargs: readable_body,
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "全文保存。", "tags": ["source-archive"]},
    )

    draft = agent.create_draft("ignored", instruction="这篇原文一字不改全文入库")
    committed = agent.commit_draft(draft["id"])

    assert "error" not in committed
    raw_note = read_note(tmp_path, committed["raw_path"])
    capture_note = read_note(tmp_path, committed["file_path"])
    assert raw_note is not None
    assert capture_note is not None
    _raw_frontmatter, raw_body = raw_note
    _capture_frontmatter, capture_body = capture_note
    assert raw_body == raw_text
    assert "## 目录纲要" in capture_body
    assert "- [第一部分](#第一部分)" in capture_body
    assert "- [第二部分](#第二部分)" in capture_body
    assert "## 第一部分" in capture_body
    assert "## 第二部分" in capture_body
    assert "第一部分原文一字不改。" in capture_body
    updated = agent.draft_repo.get_by_id(draft["id"])
    assert updated["status"] == "committed"
    assert updated["draft_text"] == capture_body


def test_xiaohongshu_full_text_save_keeps_raw_evidence_but_hides_local_paths(
    agent: CaptureAgent,
    monkeypatch,
):
    class FakeExtractor:
        def can_handle(self, _source: str) -> bool:
            return True

        def extract(self, _source: str, **_kwargs) -> ExtractionResult:
            return ExtractionResult(
                raw_text=(
                    "# Subagent 的三岔路\n\n"
                    "来源：https://www.xiaohongshu.com/explore/test\n\n"
                    "## 正文文案\n\n"
                    "作者正文足够长，讨论 subagent 委派策略和模型训练分布之间的关系。"
                    "这里继续补充主线、证据和结论，确保内容质量检测通过。\n\n"
                    "#AI[话题]# #Agent[话题]# #Subagent[话题]#\n\n"
                    "## 图片内容\n\n"
                    "## 图片 1\n"
                    "本地图片：/tmp/01.jpg\n"
                    "图片 OCR 结果为空。\n\n"
                    "## 图片 2\n"
                    "本地图片：/tmp/02.jpg\n"
                    "图片里的正文继续展开 Codex、Claude Code、Cursor 的差异。\n\n"
                    "## 图片 3\n"
                    "本地图片：/tmp/03.jpg\n"
                    "一名年轻男子身穿白色夹克，站在栏杆前，背景是日落时分的城市天际线和海面。"
                ),
                source_type="xiaohongshu",
                source_url="https://www.xiaohongshu.com/explore/test",
                title="Subagent 的三岔路",
            )

    agent.extractors = [FakeExtractor()]
    monkeypatch.setattr(
        agent,
        "_revise_with_llm",
        lambda **kwargs: (
            "## 正文\n\n"
            "作者正文足够长，讨论 **subagent 委派策略** 和模型训练分布之间的关系。\n\n"
            "图片里的正文继续展开 Codex、Claude Code、Cursor 的差异。"
        ),
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "全文保存。", "tags": ["source-archive"]},
    )

    draft = agent.create_draft("ignored", instruction="全文提取出来，入库")

    assert draft["status"] == "raw_extracted"
    assert "本地图片：/tmp/01.jpg" in draft["raw_text"]
    assert draft["draft_text"] == ""

    agent.commit_draft(draft["id"])
    updated = agent.draft_repo.get_by_id(draft["id"])

    assert "本地图片：/tmp/01.jpg" not in updated["draft_text"]
    assert "图片 OCR 结果为空" not in updated["draft_text"]
    assert "来源：https://www.xiaohongshu.com" not in updated["draft_text"]
    assert "## 图片 1" not in updated["draft_text"]
    assert "## 图片 2" not in updated["draft_text"]
    assert "#AI[话题]#" not in updated["draft_text"]
    assert "年轻男子身穿白色夹克" not in updated["draft_text"]
    assert "作者正文足够长" in updated["draft_text"]
    assert "图片里的正文继续展开" in updated["draft_text"]


def test_commit_raw_extracted_refined_note_generates_draft_with_saved_intent(
    agent: CaptureAgent,
    monkeypatch,
):
    planning_calls = {"count": 0}
    revision_calls: list[dict] = []

    def fake_plan(instruction: str, *, source_text: str = "", title_hint: str = "") -> CaptureIntent:
        planning_calls["count"] += 1
        return CaptureIntent(
            user_request=instruction,
            material_intent="refined_note",
            task_type="organize",
            coverage="full_article",
            emphasis=[],
            must_preserve=["source_fidelity", "main_thread"],
            output_shape=["natural_draft"],
            detail_level="source_like",
            draft_guidance="let_model_decide",
            compression="low",
            preserve=["original_wording"],
            plan_source="llm",
            detail_policy="保留原文结构，轻度整理。",
            semantic_title=title_hint,
        )

    def fake_revise(**kwargs):
        revision_calls.append(kwargs)
        return "整理稿：保留原文结构，轻度总结。"

    monkeypatch.setattr(agent, "_build_capture_intent_with_planning", fake_plan)
    monkeypatch.setattr(agent, "_revise_with_llm", fake_revise)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "整理稿摘要。", "tags": ["capture"]},
    )

    draft = agent.create_draft(TEST_CONTENT, instruction="稍加总结，尽可能地提取原文")

    assert draft["status"] == "raw_extracted"
    assert draft["draft_text"] == ""
    assert planning_calls["count"] == 1

    committed = agent.commit_draft(draft["id"])
    updated = agent.draft_repo.get_by_id(draft["id"])

    assert committed["status"] == "committed"
    assert planning_calls["count"] == 1
    assert len(revision_calls) == 1
    assert revision_calls[0]["capture_intent"]["plan_source"] == "llm"
    assert updated["draft_text"] == "整理稿：保留原文结构，轻度总结。"
    assert updated["summary"] == "整理稿摘要。"


def test_xiaohongshu_raw_package_request_preserves_ocr_structure(
    agent: CaptureAgent,
    monkeypatch,
):
    class FakeExtractor:
        def can_handle(self, _source: str) -> bool:
            return True

        def extract(self, _source: str, **_kwargs) -> ExtractionResult:
            return ExtractionResult(
                raw_text=(
                    "# Subagent 的三岔路\n\n"
                    "来源：https://www.xiaohongshu.com/explore/test\n\n"
                    "## 正文文案\n\n"
                    "作者正文足够长，讨论 subagent 委派策略和模型训练分布之间的关系。"
                    "这里继续补充主线、证据和结论，确保内容质量检测通过。\n\n"
                    "## 图片内容\n\n"
                    "## 图片 1\n"
                    "本地图片：/tmp/01.jpg\n"
                    "图片 OCR 结果为空。"
                ),
                source_type="xiaohongshu",
                source_url="https://www.xiaohongshu.com/explore/test",
                title="Subagent 的三岔路",
            )

    agent.extractors = [FakeExtractor()]
    monkeypatch.setattr(
        agent,
        "_call_llm",
        lambda prompt, *args, **kwargs: pytest.fail("raw package save should not call LLM"),
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: pytest.fail("raw package save should not summarize with LLM"),
    )

    draft = agent.create_draft("ignored", instruction="保留原始 OCR 抓取包并入库")

    assert draft["status"] == "raw_extracted"
    assert draft["draft_text"] == ""

    agent.commit_draft(draft["id"])
    updated = agent.draft_repo.get_by_id(draft["id"])

    assert "## 图片 1" in updated["draft_text"]
    assert "本地图片：/tmp/01.jpg" in updated["draft_text"]
    assert "图片 OCR 结果为空。" in updated["draft_text"]


def test_long_raw_package_skips_llm_library_and_embedding_processing(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    raw_package = (
        "# 原始 OCR 抓取包\n\n"
        "## 图片 1\n本地图片：/tmp/01.jpg\n"
        + "OCR 原始行，必须逐行保留。\n" * 180
    )

    class FakeExtractor:
        def can_handle(self, _source: str) -> bool:
            return True

        def extract(self, _source: str, **_kwargs) -> ExtractionResult:
            return ExtractionResult(
                raw_text=raw_package,
                source_type="xiaohongshu",
                source_url="https://www.xiaohongshu.com/explore/test",
                title="原始 OCR 抓取包",
            )

    agent.extractors = [FakeExtractor()]
    agent.config.qwen_api_key = "sk-test-qwen"
    monkeypatch.setattr(
        agent,
        "_call_llm",
        lambda prompt, *args, **kwargs: pytest.fail("raw package save should not call LLM"),
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: pytest.fail("raw package save should not summarize with LLM"),
    )
    monkeypatch.setattr(
        "agents.capture_agent.Outliner",
        lambda **kwargs: pytest.fail("raw package save should not create an outliner"),
    )
    monkeypatch.setattr(
        "agents.capture_agent.Embedder",
        lambda **kwargs: pytest.fail("raw package save should not create an embedder"),
    )

    draft = agent.create_draft("ignored", instruction="保留原始 OCR 抓取包并入库")
    committed = agent.commit_draft(draft["id"])
    capture_text = (tmp_path / committed["file_path"]).read_text(encoding="utf-8")

    assert committed["status"] == "committed"
    assert committed["summary"] == ""
    assert committed["tags"] == []
    assert committed["library_postprocess"]["reason"] == "raw_package_verbatim"
    assert capture_text.split("---", 2)[2] == "\n\n" + raw_package


def test_revise_draft_full_text_save_runs_readability_revision(
    agent: CaptureAgent,
    monkeypatch,
):
    raw = TEST_CONTENT + "这是后续补充原文，也应该一起保留。"
    draft = agent.create_draft(raw)
    calls: list[dict] = []

    def fake_revise(**kwargs):
        calls.append(kwargs)
        return (
            "## 目录纲要\n\n"
            "- 全文主线\n\n"
            "## 全文主线\n\n"
            "这是经过可读性整理的全文，保留 **中国变压器供应链** 等原文重点。"
        )

    monkeypatch.setattr(
        agent,
        "_revise_with_llm",
        fake_revise,
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "全文保存。", "tags": ["source-archive"]},
    )

    revised = agent.revise_draft(draft["id"], "整理过度了，要求全文入库")

    assert revised["status"] == "draft_ready"
    assert len(calls) == 1
    assert calls[0]["raw_text"] == draft["raw_text"]
    assert "## 目录纲要" in revised["draft_text"]
    assert "## 全文主线" in revised["draft_text"]
    assert "**中国变压器供应链**" in revised["draft_text"]
    assert revised["raw_text"] == draft["raw_text"]
    assert revised["capture_intent"]["task_type"] == "capture"
    assert revised["capture_intent"]["material_intent"] == "verbatim_full_text"
    assert revised["capture_intent"]["output_shape"] == ["full_text"]
    assert revised["capture_intent"]["draft_guidance"] == "verbatim"
    assert revised["capture_intent"]["compression"] == "none"
    assert revised["capture_intent"]["plan_source"] == "deterministic_full_text"


def test_commit_long_capture_rejects_missing_bold_before_reserving_capture(
    agent: CaptureAgent,
    monkeypatch,
):
    body = (
        "## 目录纲要\n\n"
        "- 第一部分\n\n"
        "## 第一部分\n\n"
        + ("正文已经分段并且有目录，但没有任何重点加粗。" * 120)
    )
    draft = agent.draft_repo.create(
        raw_text=body,
        source_type="note",
        title="缺少重点加粗",
        draft_text=body,
        status="draft_ready",
        summary="摘要",
        tags=["contract"],
        capture_intent={"material_intent": "refined_note", "coverage": "full_article"},
    )
    monkeypatch.setattr(
        agent.draft_repo,
        "reserve_capture_for_commit",
        lambda *_args, **_kwargs: pytest.fail("presentation contract must fail before capture reservation"),
    )

    result = agent.commit_draft(draft["id"])

    assert result == {"error": "整理稿可读性合同未通过：长文缺少重点加粗"}
    assert agent.draft_repo.get_by_id(draft["id"])["status"] == "draft_incomplete"


def test_long_capture_prompt_makes_presentation_contract_mandatory(agent: CaptureAgent):
    long_text = "长文正文。" * 500
    prompt = agent._build_revision_prompt(
        instruction="全文入库",
        capture_intent=agent._build_capture_intent("全文入库").to_dict(),
        current_text=long_text,
        raw_text=long_text,
    )

    assert "必须在正文开头生成 `## 目录纲要`" in prompt
    assert "必须使用 `##`/`###` heading" in prompt
    assert "至少加粗一处" in prompt


def test_capture_intent_classifies_four_material_contracts(agent: CaptureAgent):
    full_text = agent._build_capture_intent("这篇文章全文入库，一字不改保存").to_dict()
    raw_package = agent._build_capture_intent("保留原始 OCR 抓取包并入库").to_dict()
    refined = agent._build_capture_intent("稍加总结，整理成一篇精炼笔记").to_dict()
    focused = agent._build_capture_intent("总结全文核心内容，方法论那部分详细一些").to_dict()
    enhanced = agent._build_capture_intent("结合刚才讨论，整理成原文增强版笔记").to_dict()

    assert full_text["material_intent"] == "verbatim_full_text"
    assert full_text["output_shape"] == ["full_text"]
    assert raw_package["material_intent"] == "verbatim_full_text"
    assert raw_package["output_shape"] == ["full_text"]
    assert refined["material_intent"] == "refined_note"
    assert focused["material_intent"] == "focused_note"
    assert focused["emphasis"] == ["方法论"]
    assert enhanced["material_intent"] == "enhanced_note"
    assert "source_layering" in enhanced["must_preserve"]


def test_enhanced_note_prompt_requires_source_layering(agent: CaptureAgent):
    intent = agent._build_capture_intent("结合刚才讨论，整理成原文增强版笔记").to_dict()

    prompt = agent._build_revision_prompt(
        instruction="结合刚才讨论，整理成原文增强版笔记",
        capture_intent=intent,
        current_text=TEST_CONTENT,
        raw_text=TEST_CONTENT,
    )

    assert "原文依据" in prompt
    assert "讨论形成的理解" in prompt
    assert "不要把 AI 解释伪装成作者原意" in prompt


def test_xiaohongshu_revision_prompt_adds_multimodal_cleanup_rules(agent: CaptureAgent):
    intent = agent._build_capture_intent("整理成一篇可读笔记").to_dict()

    prompt = agent._build_revision_prompt(
        instruction="整理成一篇可读笔记",
        capture_intent=intent,
        current_text="",
        raw_text="## 正文文案\n作者观点。\n\n## 图片 1\n图片 OCR 文字。",
        source_type="xiaohongshu",
    )

    assert "这是小红书图文素材" in prompt
    assert "作者表达和图片卡片里的作者文字" in prompt
    assert "不要保留本地图片路径" in prompt
    assert "不要按“图片 1/图片 2”机械罗列" in prompt
    assert "视觉描述默认丢弃" in prompt


def test_prompt_allows_model_to_choose_draft_strategy(agent: CaptureAgent, monkeypatch):
    prompts: list[str] = []

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return "整理稿：AI 的关键瓶颈是电力，尤其是中国变压器供应链。"

    monkeypatch.setattr(agent, "_call_llm", fake_llm)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "summary": "AI发展受电力基础设施制约。",
            "tags": ["ai-energy"],
        },
    )

    draft = agent.create_draft(TEST_CONTENT, instruction="重新提取重点，特别着重阐述中国变压器供应链")
    agent.revise_draft(draft["id"], "重新提取重点，特别着重阐述中国变压器供应链")

    prompt = prompts[0]
    assert '"user_request": "重新提取重点，特别着重阐述中国变压器供应链"' in prompt
    assert "先在内部判断用户真正想要的整理方式" in prompt
    assert "由你决定最终整理稿的详略和结构" in prompt
    assert "不要把内部计划输出给用户" in prompt
    assert "不要输出标签、摘要或任务合同" in prompt
    assert "必须保留全文的主要脉络和其他关键要点" not in prompt
    assert "输出应是可入库的完整整理稿" not in prompt
    assert "30%" not in prompt
    assert "50%" not in prompt
    assert "不要机械套用固定模板" in prompt
    assert prompt.count(TEST_CONTENT) == 1


def test_medium_density_prompt_preserves_argument_function_without_hard_rules(agent: CaptureAgent):
    intent = agent._build_capture_intent("总结全文核心内容").to_dict()

    prompt = agent._build_revision_prompt(
        instruction="总结全文核心内容",
        capture_intent=intent,
        current_text=TEST_CONTENT,
        raw_text=TEST_CONTENT,
    )

    assert "这不是几句话摘要" in prompt
    assert "主要论证层次" in prompt
    assert "重要转折" in prompt
    assert "论证中的功能" in prompt
    assert "推进主线" in prompt
    assert "支撑结论" in prompt
    assert "区分概念" in prompt
    assert "根据材料复杂度决定篇幅" in prompt
    assert "至少一个" not in prompt
    assert "每个模块" not in prompt
    assert "2000" not in prompt
    assert "8 个小标题" not in prompt
    assert "是什么 / 为什么 / 边界" not in prompt


def test_only_instruction_sets_selected_sections_coverage(agent: CaptureAgent, monkeypatch):
    prompts: list[str] = []

    agent._capture_planning_enabled = True
    monkeypatch.setattr(
        agent,
        "_call_capture_planner",
        lambda **kwargs: {
            "task_type": "organize",
            "coverage": "selected_sections",
            "focus": ["中国变压器供应链"],
            "detail_level": "medium",
            "compression": "medium",
            "preserve": [],
            "title": "",
            "detail_policy": "",
        },
        raising=False,
    )
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: prompts.append(prompt) or "只保留供应链。")
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "供应链", "tags": ["supply-chain"]},
    )

    draft = agent.create_draft(TEST_CONTENT, instruction="只要中国变压器供应链部分")

    assert draft["capture_intent"]["coverage"] == "selected_sections"
    agent.revise_draft(draft["id"], "只要中国变压器供应链部分")
    assert '"coverage": "selected_sections"' in prompts[0]


def test_revision_prompt_keeps_more_than_first_7000_chars(agent: CaptureAgent):
    long_text = "开头" + ("长文材料" * 3000) + "末尾关键观点"

    prompt = agent._build_revision_prompt(
        instruction="总结全文",
        capture_intent=agent._build_capture_intent("总结全文").to_dict(),
        current_text=long_text,
        raw_text=long_text,
    )

    assert "末尾关键观点" in prompt


def test_capture_intent_does_not_force_visible_output_sections(agent: CaptureAgent):
    intent = agent._build_capture_intent("提取全文重点内容").to_dict()

    assert intent["task_type"] == "organize"
    assert intent["coverage"] == "full_article"
    assert intent["output_shape"] == ["natural_draft"]
    assert "outline" not in intent["output_shape"]
    assert "tags" not in intent["output_shape"]
    assert "summary" not in intent["output_shape"]


def test_capture_intent_keeps_user_request_as_guidance_not_fixed_mode(agent: CaptureAgent):
    intent = agent._build_capture_intent("完整整理成一篇详细笔记，尽量保留细节").to_dict()

    assert intent["user_request"] == "完整整理成一篇详细笔记，尽量保留细节"
    assert intent["draft_guidance"] == "let_model_decide"
    assert intent["output_shape"] == ["natural_draft"]


def test_capture_intent_defaults_summary_full_content_to_medium(agent: CaptureAgent):
    intent = agent._build_capture_intent("总结全文核心内容").to_dict()

    assert intent["detail_level"] == "medium"


def test_capture_intent_planner_sets_detail_and_preserve_fields(agent: CaptureAgent, monkeypatch):
    agent._capture_planning_enabled = True
    monkeypatch.setattr(
        agent,
        "_call_capture_planner",
        lambda **kwargs: {
            "task_type": "organize",
            "coverage": "full_article",
            "focus": ["方法论"],
            "detail_level": "source_like",
            "compression": "low",
            "preserve": ["original_wording", "data", "examples"],
            "title": "",
            "detail_policy": "用户点名部分重点展开。",
        },
        raising=False,
    )

    intent = agent._build_capture_intent_with_planning(
        "总结全文核心内容，方法论那部分详细一些，案例和数据别删",
        source_text=TEST_CONTENT,
    ).to_dict()

    assert intent["plan_source"] == "llm"
    assert intent["task_type"] == "organize"
    assert intent["coverage"] == "full_article"
    assert intent["detail_level"] == "source_like"
    assert intent["compression"] == "low"
    assert intent["emphasis"] == ["方法论"]
    assert intent["preserve"] == ["original_wording", "data", "examples"]


def test_capture_intent_planner_can_choose_selected_sections(agent: CaptureAgent, monkeypatch):
    agent._capture_planning_enabled = True
    monkeypatch.setattr(
        agent,
        "_call_capture_planner",
        lambda **kwargs: {
            "task_type": "organize",
            "coverage": "selected_sections",
            "focus": ["商业模式"],
            "detail_level": "medium",
            "compression": "medium",
            "preserve": [],
            "title": "",
            "detail_policy": "",
        },
        raising=False,
    )

    intent = agent._build_capture_intent_with_planning(
        "只看商业模式部分，其他不用整理",
        source_text=TEST_CONTENT,
    ).to_dict()

    assert intent["plan_source"] == "llm"
    assert intent["coverage"] == "selected_sections"
    assert intent["emphasis"] == ["商业模式"]


def test_near_budget_content_gets_internal_planning_instruction(agent: CaptureAgent):
    long_text = "开头。" + ("长文材料。" * 5000) + "末尾关键观点。"
    intent = agent._build_capture_intent("提取全文重点").to_dict()

    prompt = agent._build_revision_prompt(
        instruction="提取全文重点",
        capture_intent=intent,
        current_text=long_text,
        raw_text=long_text,
    )

    assert "材料较长或接近上下文预算" in prompt
    assert "先在内部制定阅读计划" in prompt
    assert "不要把内部计划输出给用户" in prompt
    assert "只输出最终整理稿" in prompt
    assert "末尾关键观点" in prompt


def test_long_source_is_read_in_chunks_before_final_draft(agent: CaptureAgent, monkeypatch):
    long_text = (
        "开头主线：文章讨论AI产品竞争。\n\n"
        + ("第一部分讲模型能力和产品节奏。" * 900)
        + "\n\nMIDDLE_UNIQUE：中段讨论Terminal-Bench故意没补的坑。\n\n"
        + ("第三部分讲开发者生态和商业化选择。" * 900)
        + "\n\n结尾判断：战略转向Dynamic Workflows。"
    )
    draft = agent.create_draft(long_text)
    calls: list[tuple[str, bool]] = []

    def fake_llm(prompt: str, return_meta: bool = False):
        calls.append((prompt, return_meta))
        if "长文分段阅读" in prompt:
            marker = "MIDDLE_UNIQUE" if "MIDDLE_UNIQUE" in prompt else "普通段落"
            return LLMResult(text=f"分段笔记：{marker}", incomplete=False)
        assert "按原文顺序得到的分段阅读笔记" in prompt
        assert "MIDDLE_UNIQUE" in prompt
        assert "中间内容因上下文预算截断" not in prompt
        return "最终整理稿：Terminal-Bench 是关键线索。"

    monkeypatch.setattr(agent, "_call_llm", fake_llm)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "summary": "长文整理。",
            "tags": ["ai-agent"],
        },
    )

    revised = agent.revise_draft(
        draft["id"],
        "提取重点，关于Terminal-Bench故意没补的坑要详细阐述",
    )

    section_calls = [prompt for prompt, return_meta in calls if return_meta]
    assert revised["status"] == "draft_ready"
    assert len(section_calls) >= 2
    assert any("MIDDLE_UNIQUE" in prompt for prompt in section_calls)
    assert calls[-1][1] is False
    assert revised["draft_text"] == "最终整理稿：Terminal-Bench 是关键线索。"


def test_medium_density_longish_source_uses_chunked_reading(agent: CaptureAgent, monkeypatch):
    longish_text = (
        "开头主线：生命不只是语言模型。\n\n"
        + ("中心法则与序列化范式的局限。" * 300)
        + "\n\nMIDDLE_LIFE_MODEL：4D全细胞模型和虚拟果蝇案例。\n\n"
        + ("工具决定论与多模态数据的重要性。" * 300)
        + "\n\n结尾判断：未来AI生物学要从预测生命走向运行生命。"
    )
    draft = agent.create_draft(longish_text)
    calls: list[tuple[str, bool]] = []

    def fake_llm(prompt: str, return_meta: bool = False):
        calls.append((prompt, return_meta))
        if "长文分段阅读" in prompt:
            marker = "MIDDLE_LIFE_MODEL" if "MIDDLE_LIFE_MODEL" in prompt else "普通分段"
            return LLMResult(text=f"分段笔记：{marker}", incomplete=False)
        assert "按原文顺序得到的分段阅读笔记" in prompt
        assert "MIDDLE_LIFE_MODEL" in prompt
        return "中密度整理稿：覆盖4D全细胞模型和虚拟果蝇。"

    monkeypatch.setattr(agent, "_call_llm", fake_llm)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "生命模型整理。", "tags": ["ai-biology"]},
    )

    revised = agent.revise_draft(draft["id"], "总结全文核心内容")

    assert revised["status"] == "draft_ready"
    assert any(return_meta for _prompt, return_meta in calls)
    assert any("MIDDLE_LIFE_MODEL" in prompt for prompt, return_meta in calls if return_meta)
    assert revised["draft_text"] == "中密度整理稿：覆盖4D全细胞模型和虚拟果蝇。"


def test_source_like_prompt_asks_model_to_preserve_original_texture(agent: CaptureAgent):
    intent = agent._build_capture_intent("尽可能地提取原文，案例和数据别删").to_dict()
    intent["plan_source"] = "llm"
    intent["detail_level"] = "source_like"
    intent["compression"] = "low"
    intent["preserve"] = ["original_wording", "data", "examples"]

    prompt = agent._build_revision_prompt(
        instruction="尽可能地提取原文，案例和数据别删",
        capture_intent=intent,
        current_text=TEST_CONTENT,
        raw_text=TEST_CONTENT,
    )

    assert "用户要求尽量贴近原文" in prompt
    assert "少压缩" in prompt
    assert "原文关键表述" in prompt
    assert "案例" in prompt
    assert "数字" in prompt
    assert "不要逐段搬运原文" in prompt


def test_source_like_long_section_notes_preserve_texture_before_final(agent: CaptureAgent):
    intent = agent._build_capture_intent("尽可能地提取原文，案例和数据别删").to_dict()
    intent["plan_source"] = "llm"
    intent["detail_level"] = "source_like"
    intent["compression"] = "low"
    intent["preserve"] = ["original_wording", "data", "examples"]

    prompt = agent._build_section_note_prompt(
        instruction="尽可能地提取原文，案例和数据别删",
        capture_intent=intent,
        chunk=TEST_CONTENT,
        index=1,
        total=3,
    )

    assert "少压缩" in prompt
    assert "原文关键表述" in prompt
    assert "案例" in prompt
    assert "数字" in prompt
    assert "不要只抽象成一句结论" in prompt


def test_long_source_incomplete_section_note_marks_draft_incomplete(
    agent: CaptureAgent,
    monkeypatch,
):
    long_text = (
        "开头。\n\n"
        + ("第一段材料。" * 1500)
        + "\n\n"
        + ("第二段材料。" * 1500)
        + "\n\n"
        + ("第三段材料。" * 1500)
        + "\n\n结尾。"
    )
    draft = agent.create_draft(long_text)

    def fake_llm(prompt: str, return_meta: bool = False):
        if "第 2/" in prompt:
            return LLMResult(text="第二段半截笔记", incomplete=True)
        return LLMResult(text="完整分段笔记", incomplete=False)

    monkeypatch.setattr(agent, "_call_llm", fake_llm)

    revised = agent.revise_draft(draft["id"], "总结全文")

    assert revised["status"] == "draft_incomplete"
    assert revised["revision_error"] == "llm_output_incomplete"
    assert "长文分段整理未完整完成" in revised["draft_text"]
    assert "第二段半截笔记" in revised["draft_text"]


def test_create_draft_with_instruction_does_not_run_revision_llm(
    agent: CaptureAgent,
    monkeypatch,
):
    def timeout(_prompt: str) -> str:
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(agent, "_call_llm", timeout)

    draft = agent.create_draft(TEST_CONTENT, instruction="总结全文核心观点")

    assert draft["status"] == "raw_extracted"
    assert draft["id"]
    assert draft["raw_text"] == TEST_CONTENT
    assert draft["draft_text"] == ""
    assert "revision_error" not in draft


def test_revise_draft_retries_once_when_directory_cannot_jump(
    agent: CaptureAgent,
    monkeypatch,
):
    draft = agent.create_draft(TEST_CONTENT)
    bad_body = (
        "## 目录纲要\n\n"
        "- 第一部分：摘要说明\n\n"
        "## 第一部分\n\n"
        "正文。"
    )
    good_body = (
        "## 目录纲要\n\n"
        "- 第一部分\n\n"
        "## 第一部分\n\n"
        "正文。"
    )
    calls: list[str] = []

    def fake_llm(prompt: str, *args, **kwargs):
        calls.append(prompt)
        return bad_body if len(calls) == 1 else good_body

    monkeypatch.setattr(agent, "_call_llm", fake_llm)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "整理完成。", "tags": ["capture"]},
    )

    revised = agent.revise_draft(draft["id"], "整理全文")

    assert revised["draft_text"] == good_body
    assert len(calls) == 2
    assert "没有通过正式 Capture 的可读性合同" in calls[1]


def test_commit_high_fidelity_full_article_blocks_overcompressed_draft(
    agent: CaptureAgent,
):
    raw = "第一部分原文内容，需要保留论证细节。" * 120
    draft = agent.create_draft(raw, instruction="全文整理")
    agent.draft_repo.update(
        draft["id"],
        draft_text="短摘要：只保留一点点内容。",
        status="draft_ready",
        instruction="全文整理",
    )

    result = agent.commit_draft(draft["id"])

    assert result == {"error": "整理稿疑似压缩过度，请先修订为高保真全文整理后再入库"}


def test_capture_llm_uses_five_minute_timeout(agent: CaptureAgent, monkeypatch):
    observed: dict[str, int] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "整理稿"}, "finish_reason": "stop"}]}

    class FakeClient:
        def __init__(self, timeout: int):
            observed["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("agents.capture_agent.httpx.Client", FakeClient)

    assert agent._call_llm("整理这篇文章") == "整理稿"
    assert observed["timeout"] == 300


def test_capture_llm_continues_when_output_hits_length_limit(agent: CaptureAgent, monkeypatch):
    calls: list[dict] = []
    responses = [
        {"choices": [{"message": {"content": "第一段，"}, "finish_reason": "length"}]},
        {"choices": [{"message": {"content": "第二段写完。"}, "finish_reason": "stop"}]},
    ]

    class FakeResponse:
        def __init__(self, payload: dict):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    class FakeClient:
        def __init__(self, timeout: int):
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def post(self, *args, **kwargs):
            calls.append(kwargs["json"])
            return FakeResponse(responses.pop(0))

    monkeypatch.setattr("agents.capture_agent.httpx.Client", FakeClient)

    result = agent._call_llm("整理这篇文章", return_meta=True)

    assert result == LLMResult(text="第一段，\n第二段写完。", incomplete=False)
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 8000
    assert "继续从上一条末尾写" in calls[1]["messages"][-1]["content"]


def test_revise_draft_marks_incomplete_when_llm_still_truncated(
    agent: CaptureAgent,
    monkeypatch,
):
    draft = agent.create_draft(TEST_CONTENT)
    monkeypatch.setattr(
        agent,
        "_call_llm",
        lambda prompt, return_meta=False: LLMResult(text="半截草稿，而是", incomplete=True),
    )

    revised = agent.revise_draft(draft["id"], "总结全文核心观点")

    assert revised["status"] == "draft_incomplete"
    assert revised["draft_text"] == "半截草稿，而是"
    assert revised["revision_error"] == "llm_output_incomplete"


def test_commit_incomplete_draft_rejected(agent: CaptureAgent):
    draft = agent.create_draft(TEST_CONTENT)
    agent.draft_repo.update(
        draft["id"],
        draft_text="半截草稿，而是",
        status="draft_incomplete",
    )

    result = agent.commit_draft(draft["id"])

    assert result is not None
    assert "error" in result
    assert "草稿还没生成完整" in result["error"]


def test_revise_draft_uses_raw_text_and_updates_current_draft(agent: CaptureAgent, monkeypatch):
    draft = agent.create_draft(TEST_CONTENT)

    def fake_llm(prompt: str) -> str:
        assert "中国变压器" in prompt
        return "局部提取：中国变压器是电力供应链关键瓶颈。"

    monkeypatch.setattr(agent, "_call_llm", fake_llm)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "中国变压器瓶颈",
            "summary": "中国变压器供应链影响AI数据中心扩张。",
            "tags": ["power-grid"],
        },
    )

    revised = agent.revise_draft(draft["id"], "提取中国变压器部分")

    assert revised["id"] == draft["id"]
    assert revised["status"] == "draft_ready"
    assert "中国变压器" in revised["draft_text"]
    assert revised["instruction"] == "提取中国变压器部分"


def test_commit_draft_writes_raw_and_capture_files(agent: CaptureAgent, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "最终草稿：AI瓶颈在电力。")
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "AI电力协议",
            "summary": "AI发展受电力基础设施制约。",
            "tags": ["ai-energy"],
        },
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结全文核心观点")

    committed = agent.commit_draft(draft["id"])

    assert committed["status"] == "committed"
    assert committed["capture_id"]
    assert committed["raw_path"].startswith("_archive/raw/")
    assert committed["file_path"].startswith("captures/")
    assert (tmp_path / committed["raw_path"]).exists()
    assert (tmp_path / committed["file_path"]).exists()

    record = agent.repo.get_by_id(committed["capture_id"])
    assert record["raw_path"] == committed["raw_path"]
    assert record["file_path"] == committed["file_path"]


def test_commit_draft_uses_shared_write_service_for_raw_and_capture_files(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    calls: list[tuple[str, str]] = []

    class RecordingWriteService:
        def __init__(self, vault_path):
            self.vault_path = Path(vault_path)

        def write_markdown_artifact(self, request):
            calls.append((request.kind, request.relative_path))
            target = self.vault_path / request.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(request.content, encoding="utf-8")
            from bridge.knowledge_write_service import KnowledgeWriteResult

            return KnowledgeWriteResult(
                kind=request.kind,
                file_path=request.relative_path,
                db_object_id=request.db_object_id,
                db_table=request.db_table,
            )

    monkeypatch.setattr("agents.capture_agent.KnowledgeWriteService", RecordingWriteService, raising=False)
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "最终草稿：AI瓶颈在电力。")
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "AI电力协议",
            "summary": "AI发展受电力基础设施制约。",
            "tags": ["ai-energy"],
        },
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结全文核心观点")

    committed = agent.commit_draft(draft["id"])

    assert calls == [
        ("capture_raw", committed["raw_path"]),
        ("capture_note", committed["file_path"]),
    ]
    assert (tmp_path / committed["raw_path"]).exists()
    assert (tmp_path / committed["file_path"]).exists()


def test_commit_draft_builds_identity_embedding_index(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """入库后应立即生成 Theme/RAG 可用的 capture identity 索引。"""

    class FakeEmbedder:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def embed(self, text: str) -> bytes:
            assert "标题：" in text
            assert "摘要：AI发展受电力基础设施制约。" in text
            return b"fake-identity-embedding"

    agent.config.qwen_api_key = "sk-test-qwen"
    monkeypatch.setattr("agents.capture_agent.Embedder", FakeEmbedder, raising=False)
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "最终草稿：AI瓶颈在电力。")
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "AI电力协议",
            "summary": "AI发展受电力基础设施制约。",
            "tags": ["ai-energy"],
        },
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结全文核心观点")

    committed = agent.commit_draft(draft["id"])

    row = agent.repo.conn.execute(
        """SELECT kind, index_text, embedding, file_path
           FROM capture_embeddings
           WHERE capture_id = ? AND kind = 'identity'""",
        (committed["capture_id"],),
    ).fetchone()
    assert row is not None
    assert row["kind"] == "identity"
    assert "标题：" in row["index_text"]
    assert row["embedding"] == b"fake-identity-embedding"
    assert row["file_path"] == committed["file_path"]


def test_commit_draft_runs_targeted_library_postprocess(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """入库后应只为当前 capture 补 Library 元数据和章节索引。"""

    class FakeEmbedder:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def embed(self, text: str) -> bytes:
            if "章节：Codex 路线" in text:
                return b"section-codex"
            if "章节：Claude 路线" in text:
                return b"section-claude"
            return b"identity"

    class FakeOutliner:
        def __init__(self, **_kwargs):
            pass

        def generate(self, _body: str, _length: int):
            return [
                {"level": 1, "title": "Codex 路线", "summary": "用户显式触发。"},
                {"level": 1, "title": "Claude 路线", "summary": "模型主动委派。"},
            ]

    long_body = (
        "## 目录纲要\n\n"
        "- Codex 路线\n"
        "- Claude 路线\n\n"
        "## Codex 路线\n\n"
        "**Codex 的显式触发路径**\n\n"
        + ("Codex 要求用户显式触发 subagent，避免奖励归因模糊。" * 30)
        + "\n\n## Claude 路线\n\n"
        + "**Claude 的主动委派路径**\n\n"
        + ("Claude Code 把上下文视为稀缺资源，让模型主动判断何时委派。" * 30)
    )
    agent.config.qwen_api_key = "sk-test-qwen"
    monkeypatch.setattr("agents.capture_agent.Embedder", FakeEmbedder, raising=False)
    monkeypatch.setattr("agents.capture_agent.Outliner", FakeOutliner, raising=False)
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: long_body)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "Subagent 的三岔路",
            "summary": "比较 Codex、Claude Code、Cursor 的委派策略。",
            "tags": ["ai-agent"],
        },
    )

    draft = agent.create_draft(TEST_CONTENT, instruction="总结全文核心内容")
    committed = agent.commit_draft(draft["id"])

    capture_frontmatter, capture_body = read_note(tmp_path, committed["file_path"])
    raw_frontmatter, _raw_body = read_note(tmp_path, committed["raw_path"])
    assert capture_frontmatter["type"] == "capture"
    assert capture_frontmatter["status"] == "processed"
    assert raw_frontmatter["type"] == "raw"
    assert raw_frontmatter["status"] == "raw"
    assert raw_frontmatter["capture_id"] == committed["capture_id"]
    assert "## 目录纲要" in capture_body

    metadata = agent.repo.conn.execute(
        "SELECT outline_json FROM capture_metadata WHERE capture_id = ?",
        (committed["capture_id"],),
    ).fetchone()
    assert metadata is not None
    assert "Codex 路线" in metadata["outline_json"]

    rows = agent.repo.conn.execute(
        """SELECT kind, section_title, embedding
           FROM capture_embeddings
           WHERE capture_id = ?
           ORDER BY kind, section_title""",
        (committed["capture_id"],),
    ).fetchall()
    assert ("identity", "", b"identity") in [
        (row["kind"], row["section_title"], row["embedding"]) for row in rows
    ]
    assert ("section", "Codex 路线", b"section-codex") in [
        (row["kind"], row["section_title"], row["embedding"]) for row in rows
    ]
    assert ("section", "Claude 路线", b"section-claude") in [
        (row["kind"], row["section_title"], row["embedding"]) for row in rows
    ]
    assert committed["library_postprocess"]["outlines"]["updated"] == 1
    assert committed["library_postprocess"]["embedding_index"]["updated"] >= 2
    assert committed["library_postprocess"]["pipeline"]["directory_rendered"] is True
    assert committed["library_postprocess"]["pipeline"]["section_embedding_count"] == 2
    assert committed["library_postprocess"]["pipeline"]["rag_section_ready"] is True


def test_commit_short_full_text_save_writes_high_fidelity_capture(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    readable_body = "## 正文\n\n**中国变压器供应链** 是全文重点。\n\n" + TEST_CONTENT
    monkeypatch.setattr(agent, "_revise_with_llm", lambda **kwargs: readable_body)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "全文保存。", "tags": ["source-archive"]},
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="全文存进数据库")

    committed = agent.commit_draft(draft["id"])
    result = read_note(tmp_path, committed["file_path"])

    assert result is not None
    frontmatter, body = result
    assert "## 目录纲要" in body
    assert "## 正文" in body
    assert "**中国变压器供应链**" in body
    assert TEST_CONTENT in body
    assert "outline" not in frontmatter


def test_commit_long_full_text_save_generates_clickable_sections_and_rag(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    class FakeOutliner:
        def __init__(self, **_kwargs):
            pass

        def generate(self, _body: str, _length: int):
            return [
                {
                    "level": 1,
                    "title": "第一部分",
                    "start_paragraph_index": 1,
                    "summary": "LLM 生成的目录摘要。",
                },
                {
                    "level": 1,
                    "title": "第二部分",
                    "start_paragraph_index": 3,
                    "summary": "LLM 生成的目录摘要。",
                },
            ]

    class FakeEmbedder:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def embed(self, text: str) -> bytes:
            if "章节：" in text:
                return b"section"
            return b"identity"

    first_chunks = [
        "第一部分原文内容，不应该被目录纲要或总结改写。" * 35,
        "第一部分继续保留作者的完整论证、数据和转折。" * 35,
    ]
    second_chunks = [
        "第二部分原文内容，也必须原样留在 capture 正文里。" * 35,
        "第二部分继续保留作者的完整表达、例子和结论。" * 35,
    ]
    long_body = "# 长文标题\n\n" + "\n\n".join(first_chunks + second_chunks)
    readable_body = (
        "## 目录纲要\n\n"
        "- 第一部分\n"
        "- 第二部分\n\n"
        "## 第一部分\n\n"
        "**第一部分重点**\n\n"
        + "\n\n".join(first_chunks)
        + "\n\n## 第二部分\n\n"
        "**第二部分重点**\n\n"
        + "\n\n".join(second_chunks)
    )
    agent.config.qwen_api_key = "sk-test-qwen"
    monkeypatch.setattr("agents.capture_agent.Outliner", FakeOutliner, raising=False)
    monkeypatch.setattr("agents.capture_agent.Embedder", FakeEmbedder, raising=False)
    monkeypatch.setattr(agent, "_revise_with_llm", lambda **kwargs: readable_body)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "全文保存。", "tags": ["source-archive"]},
    )
    draft = agent.create_draft(long_body, instruction="全文入库")
    committed = agent.commit_draft(draft["id"])
    result = read_note(tmp_path, committed["file_path"])
    raw_result = read_note(tmp_path, committed["raw_path"])

    assert result is not None
    assert raw_result is not None
    _frontmatter, body = result
    _raw_frontmatter, raw_body = raw_result
    assert raw_body == long_body
    assert "## 目录纲要" in body
    assert "- [第一部分](#第一部分)" in body
    assert "- [第二部分](#第二部分)" in body
    assert "## 第一部分" in body
    assert "## 第二部分" in body
    assert "LLM 生成的目录摘要" not in body
    assert "第一部分原文内容，不应该被目录纲要或总结改写。" in body
    assert "第二部分原文内容，也必须原样留在 capture 正文里。" in body

    metadata = agent.repo.conn.execute(
        "SELECT outline_json, directory_rendered_at FROM capture_metadata WHERE capture_id = ?",
        (committed["capture_id"],),
    ).fetchone()
    assert metadata is not None
    assert "第一部分" in metadata["outline_json"]
    assert "LLM 生成的目录摘要" in metadata["outline_json"]
    assert metadata["directory_rendered_at"] is not None

    section_rows = agent.repo.conn.execute(
        "SELECT section_title FROM capture_embeddings WHERE capture_id = ? AND kind = 'section'",
        (committed["capture_id"],),
    ).fetchall()
    assert {row["section_title"] for row in section_rows} == {"第一部分", "第二部分"}
    assert committed["library_postprocess"]["pipeline"]["directory_rendered"] is True
    assert committed["library_postprocess"]["pipeline"]["section_embedding_count"] == 2
    assert committed["library_postprocess"]["pipeline"]["rag_section_ready"] is True


def test_commit_long_capture_repairs_directory_to_cover_primary_headings(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    class FakeOutliner:
        def __init__(self, **_kwargs):
            pass

        def generate(self, _body: str, _length: int):
            return [{"level": 1, "title": "第二部分", "summary": "只识别到中间章节。"}]

    class FakeEmbedder:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def embed(self, text: str) -> bytes:
            if "章节：第一部分" in text:
                return b"section-first"
            if "章节：第二部分" in text:
                return b"section-second"
            return b"identity"

    first = "\n\n".join(["第一部分正文需要被目录覆盖。" * 12 for _ in range(10)])
    second = "\n\n".join(["第二部分正文也需要被目录覆盖。" * 12 for _ in range(10)])
    raw_body = (
        "# 长文标题\n\n"
        "## 第一部分\n\n"
        f"{first}\n\n"
        "## 第二部分\n\n"
        f"{second}"
    )
    readable_body = (
        "## 目录纲要\n\n"
        "- 第一部分\n"
        "- 第二部分\n\n"
        "## 第一部分\n\n"
        "**第一部分重点**\n\n"
        f"{first}\n\n"
        "## 第二部分\n\n"
        "**第二部分重点**\n\n"
        f"{second}"
    )
    agent.config.qwen_api_key = "sk-test-qwen"
    monkeypatch.setattr("agents.capture_agent.Outliner", FakeOutliner, raising=False)
    monkeypatch.setattr("agents.capture_agent.Embedder", FakeEmbedder, raising=False)
    monkeypatch.setattr(agent, "_revise_with_llm", lambda **kwargs: readable_body)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "全文保存。", "tags": ["source-archive"]},
    )

    draft = agent.create_draft(raw_body, instruction="全文入库")
    committed = agent.commit_draft(draft["id"])

    result = read_note(tmp_path, committed["file_path"])
    assert result is not None
    _frontmatter, body = result
    directory = body.split("## 第一部分", maxsplit=1)[0]
    assert "- [第一部分](#第一部分)" in directory
    assert "- [第二部分](#第二部分)" in directory
    assert "只识别到中间章节" not in directory
    assert "## 第一部分" in body
    assert "## 第二部分" in body

    section_rows = agent.repo.conn.execute(
        "SELECT section_title FROM capture_embeddings WHERE capture_id = ? AND kind = 'section'",
        (committed["capture_id"],),
    ).fetchall()
    assert {row["section_title"] for row in section_rows} == {"第一部分", "第二部分"}


def test_commit_long_capture_contract_fails_before_capture_is_created(
    agent: CaptureAgent,
    monkeypatch,
):
    long_body = "\n\n".join(["长文正文没有目录。" * 40 for _ in range(10)])
    monkeypatch.setattr(
        agent.draft_repo,
        "reserve_capture_for_commit",
        lambda *_args, **_kwargs: pytest.fail("invalid presentation must fail before capture creation"),
    )
    draft = agent.draft_repo.create(
        raw_text=long_body,
        source_type="note",
        title="长文无目录",
        draft_text=long_body,
        status="draft_ready",
        summary="摘要",
        tags=["ai"],
        capture_intent={"material_intent": "refined_note"},
    )

    result = agent.commit_draft(draft["id"])
    updated = agent.draft_repo.get_by_id(draft["id"])

    assert result == {"error": "整理稿可读性合同未通过：长文缺少目录纲要"}
    assert updated["status"] == "draft_incomplete"
    assert updated["committed_capture_id"] == ""


def test_commit_long_unstructured_full_text_builds_readable_capture(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    first = "第一部分原文内容，不应该被压缩。" * 90
    second = "第二部分原文内容，也不应该被压缩。" * 90
    first_paragraphs = "\n\n".join(["第一部分原文内容，不应该被压缩。" * 45] * 2)
    second_paragraphs = "\n\n".join(["第二部分原文内容，也不应该被压缩。" * 45] * 2)
    long_body = "# 长文标题\n\n" + first + second
    readable_body = (
        "## 目录纲要\n\n"
        "- 第一部分\n"
        "- 第二部分\n\n"
        "## 第一部分\n\n"
        "**第一部分重点**\n\n"
        f"{first_paragraphs}\n\n"
        "## 第二部分\n\n"
        "**第二部分重点**\n\n"
        f"{second_paragraphs}"
    )
    monkeypatch.setattr(agent, "_revise_with_llm", lambda **kwargs: readable_body)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "全文保存。", "tags": ["source-archive"]},
    )
    draft = agent.create_draft(long_body, instruction="全文原文入库，一字不改保存正文")
    committed = agent.commit_draft(draft["id"])
    updated = agent.draft_repo.get_by_id(draft["id"])

    assert committed["status"] == "committed"
    assert "error" not in committed
    assert updated["status"] == "committed"
    assert updated["committed_capture_id"] == committed["capture_id"]
    assert updated["pending_capture_id"] == ""
    note = read_note(tmp_path, committed["file_path"])
    assert note is not None
    _frontmatter, body = note
    assert "## 目录纲要" in body
    assert "## 第一部分" in body
    assert "## 第二部分" in body
    assert "**第一部分重点**" in body
    assert body.count("第一部分原文内容，不应该被压缩。") == 90
    assert body.count("第二部分原文内容，也不应该被压缩。") == 90


def test_revise_committed_capture_id_creates_revision_draft(
    agent: CaptureAgent,
    monkeypatch,
):
    calls: list[str] = []

    def fake_llm(prompt: str) -> str:
        calls.append(prompt)
        return "修订稿：重点放在电力基础设施。"

    monkeypatch.setattr(agent, "_call_llm", fake_llm)
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {
            "title": "AI电力协议",
            "summary": "AI发展受电力基础设施制约。",
            "tags": ["ai-energy"],
        },
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结全文核心观点")
    committed = agent.commit_draft(draft["id"])

    revised = agent.revise_draft(committed["capture_id"], "重新提取重点")

    assert revised is not None
    assert revised["status"] == "draft_ready"
    assert revised["id"] != draft["id"]
    assert revised["committed_capture_id"] == committed["capture_id"]
    assert revised["draft_text"] == "修订稿：重点放在电力基础设施。"
    assert calls[-1].count(TEST_CONTENT) == 1


def test_revise_committed_draft_rejected(agent: CaptureAgent, monkeypatch):
    """已入库的草稿不允许再修改。"""
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "草稿内容")
    monkeypatch.setattr(
        agent.summarizer, "summarize",
        lambda text, existing_tags=None: {"summary": "摘要", "tags": ["test"]},
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结")
    agent.commit_draft(draft["id"])

    result = agent.revise_draft(draft["id"], "再改改")
    assert result is not None
    assert "error" in result
    assert "入库" in result["error"]


def test_commit_already_committed_draft_rejected(agent: CaptureAgent, monkeypatch):
    """已入库的草稿不允许重复提交。"""
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "草稿内容")
    monkeypatch.setattr(
        agent.summarizer, "summarize",
        lambda text, existing_tags=None: {"summary": "摘要", "tags": ["test"]},
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结")
    agent.commit_draft(draft["id"])

    result = agent.commit_draft(draft["id"])
    assert result is not None
    assert "error" in result
    assert "已入库" in result["error"]


def test_discard_capture_draft_marks_abandoned(agent: CaptureAgent, monkeypatch):
    """待审草稿删除后进入废弃状态。"""
    from bridge import capture_bridge

    draft = agent.create_draft(TEST_CONTENT)
    monkeypatch.setattr(capture_bridge, "_config", agent.config)
    monkeypatch.setattr(capture_bridge, "_agent", agent)

    result = capture_bridge.discard_capture_draft(draft["id"])

    assert result["status"] == "success"
    assert result["intent"] == "discard_capture_draft"
    assert result["meta"]["draft_status"] == "abandoned"
    assert agent.draft_repo.get_by_id(draft["id"])["status"] == "abandoned"


def test_discard_committed_capture_draft_rejected(agent: CaptureAgent, monkeypatch):
    """已入库草稿不能删除。"""
    from bridge import capture_bridge

    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "草稿内容")
    monkeypatch.setattr(
        agent.summarizer, "summarize",
        lambda text, existing_tags=None: {"summary": "摘要", "tags": ["test"]},
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结")
    agent.commit_draft(draft["id"])
    monkeypatch.setattr(capture_bridge, "_config", agent.config)
    monkeypatch.setattr(capture_bridge, "_agent", agent)

    result = capture_bridge.discard_capture_draft(draft["id"])

    assert result["status"] == "error"
    assert "入库" in result["errors"][0]


def test_revise_abandoned_draft_rejected(agent: CaptureAgent, monkeypatch):
    """已废弃的草稿不允许修改。"""
    monkeypatch.setattr(agent, "_call_llm", lambda prompt: "草稿内容")
    monkeypatch.setattr(
        agent.summarizer, "summarize",
        lambda text, existing_tags=None: {"summary": "摘要", "tags": ["test"]},
    )
    draft = agent.create_draft(TEST_CONTENT, instruction="总结")
    agent.draft_repo.update(draft["id"], status="abandoned")

    result = agent.revise_draft(draft["id"], "再改改")
    assert result is not None
    assert "error" in result
    assert "废弃" in result["error"]

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agents.capture_agent import CaptureAgent, CaptureConfig, LLMResult
from agents.extractors.text_extractor import TextExtractor


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

    assert draft["status"] == "draft_ready"
    assert draft["draft_text"] == "核心观点：AI瓶颈在电力。"
    assert draft["summary"] == "AI发展受电力基础设施制约。"
    assert draft["tags"] == ["ai-energy"]
    assert not (tmp_path / "captures").exists()
    assert prompts[0].count(TEST_CONTENT) == 1


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

    agent.create_draft(TEST_CONTENT, instruction="重新提取重点，特别着重阐述中国变压器供应链")

    prompt = prompts[0]
    assert '"user_request": "重新提取重点，特别着重阐述中国变压器供应链"' in prompt
    assert "先在内部判断用户真正想要的整理方式" in prompt
    assert "由你决定最终整理稿的详略和结构" in prompt
    assert "不要把内部计划输出给用户" in prompt
    assert "不要输出标签、摘要、提纲、目录或任务合同" in prompt
    assert "必须保留全文的主要脉络和其他关键要点" not in prompt
    assert "输出应是可入库的完整整理稿" not in prompt
    assert "30%" not in prompt
    assert "50%" not in prompt
    assert "不要机械套用固定模板" in prompt
    assert prompt.count(TEST_CONTENT) == 1


def test_only_instruction_sets_selected_sections_coverage(agent: CaptureAgent, monkeypatch):
    prompts: list[str] = []

    monkeypatch.setattr(agent, "_call_llm", lambda prompt: prompts.append(prompt) or "只保留供应链。")
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "供应链", "tags": ["supply-chain"]},
    )

    draft = agent.create_draft(TEST_CONTENT, instruction="只要中国变压器供应链部分")

    assert draft["capture_intent"]["coverage"] == "selected_sections"
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


def test_about_two_parts_instruction_extracts_real_emphasis(agent: CaptureAgent):
    intent = agent._build_capture_intent(
        "提取重点内容，而且关于41天发版的另一层是节奏狙击和"
        "Terminal-Bench那个故意没补的坑这两部分应该要详细去阐述和解释"
    ).to_dict()

    assert intent["emphasis"] == [
        "41天发版的另一层是节奏狙击",
        "Terminal-Bench那个故意没补的坑",
    ]


def test_emphasis_extraction_removes_quotes_and_action_words(agent: CaptureAgent):
    intent = agent._build_capture_intent(
        "提取全文重点内容，其中关于「41天发版的另一层是节奏狙击」和"
        "「Terminal-Bench那个故意没补的坑」这两部分要详细阐述和解释，着重展开这两部分。"
    ).to_dict()

    assert intent["emphasis"] == [
        "41天发版的另一层是节奏狙击",
        "Terminal-Bench那个故意没补的坑",
    ]
    assert "展开" not in intent["emphasis"]


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


def test_create_draft_with_instruction_keeps_raw_draft_when_llm_times_out(
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
    assert draft["revision_error"] == "timed out"


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

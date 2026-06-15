from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from agents.capture_agent import CaptureAgent, CaptureConfig, LLMResult
from agents.extractors.base import ExtractionResult
from agents.extractors.text_extractor import TextExtractor
from agents.markdown_writer import read_note


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
    assert "世界杯效应" in prompts[0]
    assert "detail_policy" in prompts[0]


def test_capture_planning_falls_back_to_rules_when_llm_plan_is_invalid(
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


def test_create_draft_full_text_save_preserves_source_without_llm(
    agent: CaptureAgent,
    monkeypatch,
):
    monkeypatch.setattr(
        agent,
        "_call_llm",
        lambda prompt, *args, **kwargs: pytest.fail("full text save should not call LLM"),
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

    assert draft["status"] == "draft_ready"
    assert draft["draft_text"] == TEST_CONTENT
    assert draft["summary"] == "全文保存。"
    assert draft["tags"] == ["source-archive"]
    assert draft["capture_intent"]["task_type"] == "capture"
    assert draft["capture_intent"]["output_shape"] == ["full_text"]
    assert draft["capture_intent"]["material_intent"] == "verbatim_full_text"


def test_full_text_save_uses_original_extracted_text_not_cleaned_text(
    agent: CaptureAgent,
    monkeypatch,
):
    class FakeExtractor:
        def can_handle(self, _source: str) -> bool:
            return True

        def extract(self, _source: str, **_kwargs) -> ExtractionResult:
            return ExtractionResult(
                raw_text=(
                    "  # 原文标题  \n\n"
                    "第一段原文保留  行内空格。这里继续补充足够长的正文，"
                    "确保质量检测认为它是一篇真实材料。\n\n\n"
                    "关注我们，获取更多精彩内容\n\n"
                    "第二段原文也要保留。它包含作者的完整表达、例子和结论，"
                    "全文入库时都不能被清洗器改写。  "
                ),
                source_type="note",
                source_url="",
                title="原文标题",
            )

    agent.extractors = [FakeExtractor()]
    monkeypatch.setattr(
        agent,
        "_call_llm",
        lambda prompt, *args, **kwargs: pytest.fail("full text save should not call LLM"),
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "全文保存。", "tags": ["source-archive"]},
    )

    draft = agent.create_draft("ignored", instruction="这篇原文一字不改全文入库")

    assert draft["raw_text"] == (
        "  # 原文标题  \n\n"
        "第一段原文保留  行内空格。这里继续补充足够长的正文，"
        "确保质量检测认为它是一篇真实材料。\n\n\n"
        "关注我们，获取更多精彩内容\n\n"
        "第二段原文也要保留。它包含作者的完整表达、例子和结论，"
        "全文入库时都不能被清洗器改写。  "
    )
    assert draft["draft_text"] == draft["raw_text"]


def test_revise_draft_full_text_save_uses_existing_raw_without_llm(
    agent: CaptureAgent,
    monkeypatch,
):
    raw = TEST_CONTENT + "这是后续补充原文，也应该一起保留。"
    draft = agent.create_draft(raw)
    monkeypatch.setattr(
        agent,
        "_call_llm",
        lambda prompt, *args, **kwargs: pytest.fail("full text save revision should not call LLM"),
    )
    monkeypatch.setattr(
        agent.summarizer,
        "summarize",
        lambda text, existing_tags=None: {"summary": "全文保存。", "tags": ["source-archive"]},
    )

    revised = agent.revise_draft(draft["id"], "全文原文入库，一字不改")

    assert revised["status"] == "draft_ready"
    assert revised["draft_text"] == draft["raw_text"]
    assert revised["capture_intent"]["material_intent"] == "verbatim_full_text"
    assert revised["capture_intent"]["output_shape"] == ["full_text"]


def test_capture_intent_classifies_four_material_contracts(agent: CaptureAgent):
    full_text = agent._build_capture_intent("这篇文章全文入库，一字不改保存").to_dict()
    refined = agent._build_capture_intent("稍加总结，整理成一篇精炼笔记").to_dict()
    focused = agent._build_capture_intent("总结全文核心内容，方法论那部分详细一些").to_dict()
    enhanced = agent._build_capture_intent("结合刚才讨论，整理成原文增强版笔记").to_dict()

    assert full_text["material_intent"] == "verbatim_full_text"
    assert full_text["output_shape"] == ["full_text"]
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


def test_capture_intent_defaults_summary_full_content_to_medium(agent: CaptureAgent):
    intent = agent._build_capture_intent("总结全文核心内容").to_dict()

    assert intent["detail_level"] == "medium"


def test_capture_intent_detects_brief_detail_level(agent: CaptureAgent):
    intent = agent._build_capture_intent("用几句话简短摘要一下").to_dict()

    assert intent["detail_level"] == "brief"


def test_capture_intent_detects_deep_detail_level(agent: CaptureAgent):
    intent = agent._build_capture_intent("深度总结，尽量保留细节").to_dict()

    assert intent["detail_level"] == "deep"


def test_capture_intent_detects_source_like_detail_and_low_compression(agent: CaptureAgent):
    intent = agent._build_capture_intent("稍加总结，尽可能地提取原文，保留关键表述和数据").to_dict()

    assert intent["detail_level"] == "source_like"
    assert intent["compression"] == "low"
    assert "original_wording" in intent["preserve"]
    assert "data" in intent["preserve"]


def test_capture_intent_detects_user_named_section_as_emphasis_not_selected(agent: CaptureAgent):
    intent = agent._build_capture_intent("总结全文核心内容，方法论那部分详细一些，案例和数据别删").to_dict()

    assert intent["coverage"] == "full_article"
    assert intent["detail_level"] == "deep"
    assert intent["emphasis"] == ["方法论"]
    assert "examples" in intent["preserve"]
    assert "data" in intent["preserve"]


def test_capture_intent_selected_section_when_user_says_only(agent: CaptureAgent):
    intent = agent._build_capture_intent("只看商业模式部分，其他不用整理").to_dict()

    assert intent["coverage"] == "selected_sections"
    assert intent["emphasis"] == ["商业模式"]
    assert intent["compression"] == "medium"


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
        "一、Codex 路线\n"
        + ("Codex 要求用户显式触发 subagent，避免奖励归因模糊。" * 30)
        + "\n\n二、Claude 路线\n"
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


def test_commit_full_text_save_writes_full_capture_without_outline(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(
        agent,
        "_call_llm",
        lambda prompt, *args, **kwargs: pytest.fail("full text save should not call LLM"),
    )
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
    assert body == TEST_CONTENT
    assert "outline" not in frontmatter
    assert "## 目录纲要" not in body


def test_commit_long_full_text_save_renders_directory_without_rewriting_source(
    agent: CaptureAgent,
    tmp_path: Path,
    monkeypatch,
):
    class FakeOutliner:
        def __init__(self, **_kwargs):
            pass

        def generate(self, _body: str, _length: int):
            return [
                {"level": 1, "title": "第一部分", "summary": "LLM 生成的目录摘要。"},
                {"level": 1, "title": "第二部分", "summary": "LLM 生成的目录摘要。"},
            ]

    class FakeEmbedder:
        def __init__(self, api_key: str):
            self.api_key = api_key

        def embed(self, text: str) -> bytes:
            if "章节：" in text:
                return b"section"
            return b"identity"

    long_body = (
        "# 长文标题\n\n"
        + ("第一部分原文内容，不应该被目录纲要或总结改写。" * 120)
        + "\n\n"
        + ("第二部分原文内容，也必须原样留在 capture 正文里。" * 120)
    )
    agent.config.qwen_api_key = "sk-test-qwen"
    monkeypatch.setattr("agents.capture_agent.Outliner", FakeOutliner, raising=False)
    monkeypatch.setattr("agents.capture_agent.Embedder", FakeEmbedder, raising=False)
    monkeypatch.setattr(
        agent,
        "_call_llm",
        lambda prompt, *args, **kwargs: pytest.fail("full text save should not call revision LLM"),
    )
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
    assert "LLM 生成的目录摘要" in body
    assert "第一部分原文内容，不应该被目录纲要或总结改写。" in body
    assert "第二部分原文内容，也必须原样留在 capture 正文里。" in body

    metadata = agent.repo.conn.execute(
        "SELECT outline_json, directory_rendered_at FROM capture_metadata WHERE capture_id = ?",
        (committed["capture_id"],),
    ).fetchone()
    assert metadata is not None
    assert "第一部分" in metadata["outline_json"]
    assert metadata["directory_rendered_at"]

    section_count = agent.repo.conn.execute(
        "SELECT COUNT(*) AS count FROM capture_embeddings WHERE capture_id = ? AND kind = 'section'",
        (committed["capture_id"],),
    ).fetchone()["count"]
    assert section_count >= 1


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

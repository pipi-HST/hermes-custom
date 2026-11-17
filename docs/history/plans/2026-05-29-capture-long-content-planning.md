---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Capture Agentic Long Content Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Capture Agent handle long content by making its own internal reading and drafting plan, while the code only enforces workflow safety and user-visible cleanliness.

**Architecture:** Keep raw extraction, draft storage, confirmation, and Obsidian writing unchanged. Add an internal-planning prompt path that asks the model to decide reading strategy, detail level, emphasis allocation, and draft shape before writing, but never expose that plan to the user. Avoid hard-coded content modes, fixed compression ratios, or rigid writing templates; keep only technical guards for truncation, timeout, raw preservation, and “no visible metadata blocks”.

**Tech Stack:** Python, SQLite draft repo, existing `CaptureAgent`, existing Hermes tool wrappers, pytest.

---

## Design Boundary

Hard-code workflow safety, not writing strategy:

- The system must save extracted raw material before any LLM drafting.
- The system must keep the existing timeout, continuation, and `draft_incomplete` behavior.
- The system must prevent incomplete drafts from being committed.
- The system may detect quoted or explicitly named emphasis terms, but must not turn action words like `展开` into content topics.
- The system must tell the LLM to plan internally, but the LLM decides how detailed, how structured, and how long the final draft should be.
- The final draft should not visibly contain the internal plan, task contract, tags block, summary block, outline block, or directory block.

Do not add a second LLM quality self-check pass. The user explicitly rejected that because it is too slow.

---

## File Structure

- Modify: `agents/capture_agent.py`
  - Add a context-budget helper instead of a fixed “long article” magic number.
  - Add an internal-planning prompt instruction for content near or over the safe single-pass budget.
  - Simplify intent metadata so it guides the model without forcing output structure.
  - Clean emphasis extraction so action words are not treated as topics.
  - Preserve existing timeout, continuation, and incomplete-draft behavior.
- Modify: `tests/test_capture_draft_workflow.py`
  - Add tests for internal planning instructions, clean emphasis extraction, and removal of visible metadata requirements.
  - Replace tests that expect “完整整理稿 / 必须保留全文主要脉络” with tests that assert the model is allowed to decide the writing strategy.
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/capture_tool.py`
  - Remove tool-description language that pushes a fixed “完整草稿 / 结构预览 / 标签摘要” behavior.
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/revise_capture_draft_tool.py`
  - Keep revision instructions aligned with the new Capture behavior.
- Modify if needed: `/Users/pipi/Downloads/hermes-agent-main/tools/refine_capture_tool.py`
  - Keep compatibility wording aligned.
- Test: `tests/test_e2e_hermes_tools.py`
  - Update stale assertions if they still expect old wording.

---

### Task 1: Replace Rigid Intent With Lightweight Guidance

**Files:**
- Modify: `agents/capture_agent.py:44-61`
- Modify: `agents/capture_agent.py:193-224`
- Test: `tests/test_capture_draft_workflow.py`

- [ ] **Step 1: Write failing tests for lightweight intent**

Add these tests near `test_about_two_parts_instruction_extracts_real_emphasis`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py::test_capture_intent_does_not_force_visible_output_sections tests/test_capture_draft_workflow.py::test_capture_intent_keeps_user_request_as_guidance_not_fixed_mode -q
```

Expected: fail because `draft_guidance` does not exist and `output_shape` still includes `outline/tags/summary`.

- [ ] **Step 3: Update `CaptureIntent`**

Change the dataclass to include a strategy hint without fixed modes:

```python
@dataclass
class CaptureIntent:
    user_request: str
    task_type: str
    coverage: str
    emphasis: list[str]
    must_preserve: list[str]
    output_shape: list[str]
    draft_guidance: str = "let_model_decide"

    def to_dict(self) -> dict:
        return {
            "user_request": self.user_request,
            "task_type": self.task_type,
            "coverage": self.coverage,
            "emphasis": self.emphasis,
            "must_preserve": self.must_preserve,
            "output_shape": self.output_shape,
            "draft_guidance": self.draft_guidance,
        }
```

Update `_build_capture_intent` so it keeps the old broad task type but removes fixed visible sections:

```python
must_preserve = [] if coverage == "selected_sections" else [
    "source_fidelity",
    "main_thread",
    "user_named_focus",
]
return CaptureIntent(
    user_request=user_request,
    task_type=task_type,
    coverage=coverage,
    emphasis=emphasis,
    must_preserve=must_preserve,
    output_shape=["natural_draft"],
)
```

Do not add `key_points`, `full_note`, fixed ratios, or a list of required visible sections.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py::test_capture_intent_does_not_force_visible_output_sections tests/test_capture_draft_workflow.py::test_capture_intent_keeps_user_request_as_guidance_not_fixed_mode -q
```

Expected: pass.

---

### Task 2: Clean Emphasis Extraction Without Over-Parsing User Intent

**Files:**
- Modify: `agents/capture_agent.py:226-256`
- Test: `tests/test_capture_draft_workflow.py`

- [ ] **Step 1: Add a failing test for the real problematic instruction**

Add this test below the existing two-part emphasis test:

```python
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
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py::test_emphasis_extraction_removes_quotes_and_action_words -q
```

Expected: fail because current extraction keeps quotes and can add `展开`.

- [ ] **Step 3: Implement minimal cleanup**

Keep the current lightweight parser, but make it less noisy. Update the generic set:

```python
generic_terms = {
    "内容", "重点内容", "重点", "核心观点", "要点",
    "展开", "阐述", "解释", "详细阐述", "详细解释",
    "着重展开", "这两部分", "这两个部分",
}
```

Update `_clean_emphasis_item`:

```python
def _clean_emphasis_item(self, item: str) -> str:
    item = (item or "").strip()
    item = item.strip("「」『』“”\"' ")
    item = re.sub(r"^(和|以及|：|:|\s)+", "", item).strip()
    item = re.sub(r"(这两部分|这两个部分|两个部分|两部分|这部分|部分)$", "", item).strip()
    item = item.strip("「」『』“”\"' ")
    return item
```

Keep append checks simple:

```python
if item and item not in generic_terms and item not in items:
    items.append(item)
```

Do not add a large intent-classification grammar. The model will still read the full user instruction and decide the strategy.

- [ ] **Step 4: Run emphasis tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py::test_about_two_parts_instruction_extracts_real_emphasis tests/test_capture_draft_workflow.py::test_emphasis_extraction_removes_quotes_and_action_words -q
```

Expected: pass.

---

### Task 3: Add Internal Planning Prompt Based On Context Budget

**Files:**
- Modify: `agents/capture_agent.py:26-31`
- Modify: `agents/capture_agent.py:258-289`
- Test: `tests/test_capture_draft_workflow.py`

- [ ] **Step 1: Add tests for agentic prompt behavior**

Replace `test_emphasis_instruction_preserves_other_key_points_in_prompt` with:

```python
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
    assert "先在内部判断用户真正想要的整理方式" in prompt
    assert "由你决定最终整理稿的详略和结构" in prompt
    assert "不要把内部计划输出给用户" in prompt
    assert "不要输出标签、摘要、提纲、目录或任务合同" in prompt
    assert "必须保留全文的主要脉络和其他关键要点" not in prompt
    assert "输出应是可入库的完整整理稿" not in prompt
    assert "30%" not in prompt
    assert "50%" not in prompt
    assert prompt.count(TEST_CONTENT) == 1
```

Add a context-budget prompt test:

```python
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
```

- [ ] **Step 2: Run prompt tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py::test_prompt_allows_model_to_choose_draft_strategy tests/test_capture_draft_workflow.py::test_near_budget_content_gets_internal_planning_instruction -q
```

Expected: fail because the current prompt still includes full-note pressure and no internal planning wording.

- [ ] **Step 3: Add context-budget helper**

Near existing constants, add a ratio rather than a fixed article-length category:

```python
CAPTURE_INTERNAL_PLAN_BUDGET_RATIO = 0.45
```

Add helper methods inside `CaptureAgent`:

```python
def _needs_internal_plan(self, text: str) -> bool:
    return len(text or "") >= int(CAPTURE_SOURCE_CHAR_LIMIT * CAPTURE_INTERNAL_PLAN_BUDGET_RATIO)

def _build_internal_plan_rule(self, source_text: str) -> str:
    if not self._needs_internal_plan(source_text):
        return (
            "- 先在内部判断用户真正想要的整理方式，再直接写最终稿。"
            "不要把内部计划输出给用户。\n"
        )
    return (
        "- 材料较长或接近上下文预算。先在内部制定阅读计划：判断主线、重点、支线、"
        "哪些部分要略读，哪些部分要精读。不要把内部计划输出给用户，只输出最终整理稿。\n"
    )
```

This uses the existing source budget as the reference point and avoids a standalone magic number like `6000`.

- [ ] **Step 4: Rewrite `_build_revision_prompt`**

Replace the current prompt body with:

```python
def _build_revision_prompt(
    self,
    *,
    instruction: str,
    capture_intent: dict,
    current_text: str,
    raw_text: str,
) -> str:
    """Build a revision prompt without duplicating the same source text."""
    current = (current_text or "").strip()
    raw = (raw_text or "").strip()
    source_text = (current or raw)[:CAPTURE_SOURCE_CHAR_LIMIT]
    if current and raw and current != raw:
        material = f"当前草稿：\n{current}\n\n原文：\n{raw[:CAPTURE_SOURCE_CHAR_LIMIT]}"
    else:
        material = f"待处理文本：\n{source_text}"

    emphasis = capture_intent.get("emphasis", [])
    emphasis_rule = ""
    if emphasis:
        emphasis_rule = (
            f"- 用户点名关注：{', '.join(emphasis)}。这些不是唯一内容，"
            "但你需要在内部计划里给它们更高权重。\n"
        )

    return (
        f"用户要求：{instruction}\n\n"
        f"任务信息：{json.dumps(capture_intent, ensure_ascii=False)}\n\n"
        "工作方式：\n"
        f"{self._build_internal_plan_rule(source_text)}"
        "- 由你决定最终整理稿的详略和结构：可以精炼，可以保留必要细节，也可以使用少量自然小标题。\n"
        "- 不要逐段搬运原文；不要为了格式而格式化；不要机械套用固定模板。\n"
        "- 不要输出标签、摘要、提纲、目录或任务合同；这些元信息由系统另行处理。\n"
        "- 如果用户明确说“只要/仅保留/删除其他内容”，才只处理指定部分。\n"
        f"{emphasis_rule}\n"
        f"{material}\n\n"
        "请直接输出最终整理稿。"
    )
```

The prompt gives agency to the model while still protecting the user-visible output from metadata blocks.

- [ ] **Step 5: Run prompt tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py::test_prompt_allows_model_to_choose_draft_strategy tests/test_capture_draft_workflow.py::test_near_budget_content_gets_internal_planning_instruction -q
```

Expected: pass.

---

### Task 4: Preserve Technical Guards Without Adding LLM Self-Check

**Files:**
- Modify only if tests reveal regressions: `agents/capture_agent.py:161-190`
- Test: `tests/test_capture_draft_workflow.py`

- [ ] **Step 1: Run existing incomplete-output tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py::test_capture_llm_continues_when_output_hits_length_limit tests/test_capture_draft_workflow.py::test_revise_draft_marks_incomplete_when_llm_still_truncated tests/test_capture_draft_workflow.py::test_commit_incomplete_draft_rejected -q
```

Expected: pass. If they fail, fix only the regression in incomplete handling.

- [ ] **Step 2: Confirm no new LLM quality self-check path exists**

Run:

```bash
rg -n "quality|self.?check|自检|重写一次|review|critique" agents/capture_agent.py
```

Expected: no new quality-review pipeline in `CaptureAgent`. Existing unrelated words are acceptable only if they are not an extra LLM pass.

---

### Task 5: Clean Hermes Tool Descriptions

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/capture_tool.py:17-33`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/revise_capture_draft_tool.py:17-25`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/refine_capture_tool.py:15-25`
- Test: `tests/test_e2e_hermes_tools.py`

- [ ] **Step 1: Update tool wording**

In all three tool descriptions, remove wording that presents a fixed preview structure such as “摘要、标签、草稿编号、关键要点/结构预览” or over-emphasizes “完整草稿”.

Use this wording:

```python
"工具会生成待确认整理稿并返回微信可读预览。重要：本工具不会正式入库，"
"用户确认'存库'后再调用 commit_capture_draft。"
"生成或修改草稿后，直接把工具返回的 USER_VISIBLE_REPLY 发给用户；"
"只有用户后续明确要求“展开草稿/查看草稿/看全文/继续看”时才调用 show_capture_draft。"
```

Keep this instruction guidance:

```python
"如果用户同时要求总结/提取/改写，把用户的原话尽量完整放入 instruction，"
"不要自行压缩、改写或把“着重阐述”改成“只保留”。"
```

- [ ] **Step 2: Run Hermes schema tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_e2e_hermes_tools.py::test_hermes_capture_schema_has_content tests/test_e2e_hermes_tools.py::test_hermes_draft_tools_registered -q
```

Expected: pass, or fail only on assertions expecting old description text.

- [ ] **Step 3: Update stale assertions if needed**

If tests fail because they assert old “完整草稿” description text, update those assertions to check:

```python
assert "待确认整理稿" in description
assert "USER_VISIBLE_REPLY" in description
```

Then rerun the same tests.

---

### Task 6: Focused And Full Verification

**Files:**
- No code files unless tests reveal regressions.

- [ ] **Step 1: Run focused Capture tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run Hermes bridge/tool tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_e2e_hermes_tools.py tests/test_e2e_full_flow.py -q
```

Expected: all tests pass or skip only environment-dependent tests already skipped before.

- [ ] **Step 3: Run full hermes-custom tests**

Run:

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: pass.

- [ ] **Step 4: Restart Hermes and clear session memory**

Use the existing Hermes restart process that clears session state before testing. Verify logs show one active gateway and `Gateway running`.

Check:

```bash
tail -n 80 ~/.hermes/logs/gateway.log
```

Expected: logs show the restarted gateway is running.

- [ ] **Step 5: Manual WeChat smoke test**

Send the same kind of instruction:

```text
这篇文章我不满意，重新提取全文重点，其中关于「41天发版的另一层是节奏狙击」和「Terminal-Bench那个故意没补的坑」这两部分要详细阐述和解释。
```

Expected:
- The preview is concise.
- The full draft does not contain visible internal plan, task contract, tags block, summary block, outline block, or directory block.
- The two named parts receive visibly more attention.
- The agent chooses the final draft shape naturally instead of following a rigid template.

---

## Self-Review

- Spec coverage: Covers internal planning, no LLM quality self-check, no rigid content-mode classification, clean emphasis extraction, Hermes wording cleanup, truncation guard preservation, and restart verification.
- Placeholder scan: No `TBD`, `TODO`, or open-ended implementation instructions remain.
- Type consistency: New `draft_guidance` appears in dataclass, `to_dict`, tests, and prompt builder.
- Scope check: This is one focused Capture Agent behavior change plus necessary Hermes tool wording alignment. It does not change DB schema, Obsidian writing, RAG, Theme, or Garden behavior.

---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Capture Medium Density Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make “总结全文 / 总结核心内容 / 提取重点” produce a medium-density Capture draft instead of a short abstract.

**Architecture:** Keep extraction, draft storage, preview, and Obsidian commit unchanged. Add lightweight `detail_level` intent metadata, route medium/deep long-ish articles through the existing chunked reading path, and update prompts so density is expressed as information coverage rather than hard word counts or rigid templates.

**Tech Stack:** Python, SQLite draft repository, existing `CaptureAgent`, pytest.

---

## File Structure

- Modify: `agents/capture_agent.py`
  - Add `CAPTURE_LONG_READING_CHAR_THRESHOLD`.
  - Add `detail_level` to `CaptureIntent`.
  - Add density detection and prompt rules.
  - Lower the chunked-reading trigger for medium/deep long-ish content.
- Modify: `tests/test_capture_draft_workflow.py`
  - Add tests for `brief / medium / deep`.
  - Add tests for medium prompt quality rules.
  - Add regression test that around-9,000-character input uses chunked reading.

No Hermes runtime tool files need to change. WeChat preview remains short and already reads from `chat_preview`.

---

### Task 1: Add Intent Density Tests

**Files:**
- Modify: `tests/test_capture_draft_workflow.py`

- [ ] **Step 1: Add failing tests for detail level detection**

Add tests near the existing Capture intent tests:

```python
def test_capture_intent_defaults_summary_full_content_to_medium(agent: CaptureAgent):
    intent = agent._build_capture_intent("总结全文核心内容").to_dict()

    assert intent["detail_level"] == "medium"


def test_capture_intent_detects_brief_detail_level(agent: CaptureAgent):
    intent = agent._build_capture_intent("用几句话简短摘要一下").to_dict()

    assert intent["detail_level"] == "brief"


def test_capture_intent_detects_deep_detail_level(agent: CaptureAgent):
    intent = agent._build_capture_intent("深度总结，尽量保留细节").to_dict()

    assert intent["detail_level"] == "deep"
```

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_capture_draft_workflow.py::test_capture_intent_defaults_summary_full_content_to_medium \
  tests/test_capture_draft_workflow.py::test_capture_intent_detects_brief_detail_level \
  tests/test_capture_draft_workflow.py::test_capture_intent_detects_deep_detail_level -q
```

Expected: fail with missing `detail_level`.

- [ ] **Step 3: Implement minimal intent density support**

In `agents/capture_agent.py`, add `detail_level: str = "medium"` to `CaptureIntent` and include it in `to_dict()`. Add `_detect_detail_level()`:

```python
def _detect_detail_level(self, user_request: str) -> str:
    if re.search(r"(一句话|几句话|简短|简要|短摘要|快速概括)", user_request):
        return "brief"
    if re.search(r"(详细整理|详细总结|深度总结|尽量保留细节|不要漏细节|完整整理成笔记)", user_request):
        return "deep"
    return "medium"
```

Pass `detail_level=self._detect_detail_level(user_request)` when building `CaptureIntent`.

- [ ] **Step 4: Verify intent tests pass**

Run the same three-test command. Expected: pass.

---

### Task 2: Add Medium-Density Prompt Rules

**Files:**
- Modify: `tests/test_capture_draft_workflow.py`
- Modify: `agents/capture_agent.py`

- [ ] **Step 1: Add a failing prompt test**

Add this test near `test_prompt_allows_model_to_choose_draft_strategy`:

```python
def test_medium_density_prompt_preserves_structure_without_hard_word_count(agent: CaptureAgent):
    intent = agent._build_capture_intent("总结全文核心内容").to_dict()

    prompt = agent._build_revision_prompt(
        instruction="总结全文核心内容",
        capture_intent=intent,
        current_text=TEST_CONTENT,
        raw_text=TEST_CONTENT,
    )

    assert "这不是几句话摘要" in prompt
    assert "主要论证层次" in prompt
    assert "代表性例子" in prompt
    assert "重要转折" in prompt
    assert "根据材料复杂度决定篇幅" in prompt
    assert "2000" not in prompt
    assert "8 个小标题" not in prompt
    assert "是什么 / 为什么 / 边界" not in prompt
```

- [ ] **Step 2: Verify the prompt test fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py::test_medium_density_prompt_preserves_structure_without_hard_word_count -q
```

Expected: fail because the new density language is absent.

- [ ] **Step 3: Add density prompt helper**

Add `_build_density_rule(capture_intent: dict) -> str` to `CaptureAgent`:

```python
def _build_density_rule(self, capture_intent: dict) -> str:
    detail_level = capture_intent.get("detail_level", "medium")
    if detail_level == "brief":
        return (
            "- 用户要求偏简短：可以高度压缩，只保留结论和最核心理由。\n"
        )
    if detail_level == "deep":
        return (
            "- 用户要求偏详细：请保留更多分支、背景、案例、数字和论证细节，"
            "但仍然不要逐段搬运原文。\n"
        )
    return (
        "- 这不是几句话摘要。请根据材料复杂度决定篇幅；默认保留文章的"
        "主要论证层次、关键概念、代表性例子、重要转折和最终判断。"
        "删掉重复铺垫和口语冗余，但不要删掉支撑主线的细节。\n"
    )
```

Insert this helper output into both `_build_revision_prompt()` and `_build_long_revision_prompt()` under `工作方式`.

- [ ] **Step 4: Verify the prompt test passes**

Run the prompt test command. Expected: pass.

---

### Task 3: Route Long-Ish Medium Articles Through Chunked Reading

**Files:**
- Modify: `tests/test_capture_draft_workflow.py`
- Modify: `agents/capture_agent.py`

- [ ] **Step 1: Add a failing regression test for around-9,000-character articles**

Add this test near the existing long-source tests:

```python
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
```

- [ ] **Step 2: Verify the regression test fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py::test_medium_density_longish_source_uses_chunked_reading -q
```

Expected: fail because the current chunked path starts only after `CAPTURE_SOURCE_CHAR_LIMIT`.

- [ ] **Step 3: Lower the chunked-reading trigger safely**

In `agents/capture_agent.py`, add:

```python
CAPTURE_LONG_READING_CHAR_THRESHOLD = 8000
```

Add:

```python
def _should_use_chunked_reading(self, source_text: str, capture_intent: dict) -> bool:
    text_len = len(source_text or "")
    if text_len > CAPTURE_SOURCE_CHAR_LIMIT:
        return True
    if text_len >= CAPTURE_LONG_READING_CHAR_THRESHOLD:
        return capture_intent.get("detail_level", "medium") in ("medium", "deep")
    return False
```

Update `_revise_with_llm()` to call `_should_use_chunked_reading(source_text, capture_intent)`.

- [ ] **Step 4: Verify the regression test passes**

Run the regression test command. Expected: pass.

---

### Task 4: Full Verification and Runtime Restart

**Files:**
- No new source files.

- [ ] **Step 1: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_draft_workflow.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run full hermes-custom tests**

Run:

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 3: Commit code changes**

Run:

```bash
git add agents/capture_agent.py tests/test_capture_draft_workflow.py
git commit -m "Improve capture medium density drafting"
```

- [ ] **Step 4: Restart Hermes and clear sessions**

Back up and clear sessions, then restart via Hermes service command:

```bash
backup_dir=/Users/pipi/.hermes/session_backups
sessions_dir=/Users/pipi/.hermes/sessions
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$backup_dir"
if [ -d "$sessions_dir" ]; then
  tar -czf "$backup_dir/sessions-$stamp.tar.gz" -C /Users/pipi/.hermes sessions
  find "$sessions_dir" -type f -delete
fi
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m hermes_cli.main gateway restart
```

- [ ] **Step 5: Verify Hermes is running**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m hermes_cli.main gateway status
cat /Users/pipi/.hermes/gateway_state.json
```

Expected: gateway service loaded/running, and `weixin` plus `feishu` connected or reconnecting without code errors.

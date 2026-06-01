# RAG and Capture File Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Hermes export only two user-requested long outputs as files: RAG long answers with complete citation reports, and Capture draft full text.

**Architecture:** Do not add a separate "export everything" tool. Add an opt-in file-output flag to the existing RAG/Capture bridges and Hermes runtime tool schemas. Default behavior stays chat-only; files are generated only when the user explicitly asks for a file, export, attachment, full report, or draft full text as a file.

**Tech Stack:** Python, SQLite, Hermes Agent Result Envelope, existing `send_message` `MEDIA:<local_path>` attachment capability, pytest.

---

## Scope

In scope:

- RAG: export "long answer + complete citation report" to a local Markdown file when explicitly requested.
- Capture: export "current draft full text" to a local Markdown file when explicitly requested.
- Return the file as a durable artifact so the Hermes supervisor can attach it via `send_message`.
- Keep WeChat message short: only a concise status/summary and file path/artifact.

Out of scope:

- No generic file-export wrapper for every tool.
- No automatic file export merely because an answer is long.
- No change to Capture commit/storage logic.
- No change to Obsidian note generation.
- No new database table unless tests prove existing `agent_runs` and `capture_drafts` cannot support it.

## File Structure

Modify in `hermes-custom`:

- `bridge/rag_bridge.py`
  - Add opt-in `export_file: bool = False`.
  - Write RAG answer/citation report when `export_file=True`.
  - Add `rag_answer_report` artifact.

- `bridge/rag_continue_bridge.py`
  - Add opt-in export of the latest full RAG answer/report.
  - Keep pagination behavior unchanged when export is not requested.

- `bridge/capture_bridge.py`
  - Add opt-in `as_file: bool = False` to `show_capture_draft`.
  - Write full `draft_text` to Markdown when `as_file=True`.
  - Add `capture_draft_fulltext` artifact.

- `bridge/file_artifacts.py`
  - New small helper for safe report filenames and Markdown writes.
  - Keep it boring and local: no delivery, no platform logic.

- `tests/test_rag_bridge.py` or new `tests/test_rag_file_output.py`
  - RAG export tests.

- `tests/test_rag_continue_bridge.py`
  - Latest RAG export tests.

- `tests/test_capture_bridge.py` or new `tests/test_capture_file_output.py`
  - Capture draft file export tests.

Modify in `hermes-agent-main`:

- `/Users/pipi/Downloads/hermes-agent-main/tools/search_notes_tool.py`
  - Add optional `export_file` schema field.
  - Description: set true only when user explicitly asks to export/send file/full report.

- `/Users/pipi/Downloads/hermes-agent-main/tools/continue_rag_answer_tool.py`
  - Add optional `export_file` schema field for exporting the latest full RAG answer/report.

- `/Users/pipi/Downloads/hermes-agent-main/tools/show_capture_draft_tool.py`
  - Add optional `as_file` schema field.
  - Description: set true only when user asks for draft full text as file.

- `/Users/pipi/Downloads/hermes-agent-main/agent/hermes_custom_envelope_adapter.py`
  - Teach model-facing instruction that artifact paths can be sent with `send_message` as `MEDIA:<path>` only when user requested a file.

- `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_search_notes_tool.py`
  - Tool schema/handler test.

- `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_show_capture_draft_tool.py`
  - Tool schema/handler test.

- `/Users/pipi/Downloads/hermes-agent-main/tests/agent/test_hermes_custom_direct_reply.py`
  - Adapter instruction test.

## Output Directories

Use local generated artifacts that are not committed:

```text
/Users/pipi/hermes-custom/data/exports/rag/
/Users/pipi/hermes-custom/data/exports/capture/
```

Add to `.gitignore` if not already covered:

```gitignore
data/exports/
```

## Artifact Shapes

RAG export artifact:

```json
{
  "type": "rag_answer_report",
  "id": "rag-answer-20260601T020000Z",
  "title": "RAG 完整回答与引用报告",
  "path": "/Users/pipi/hermes-custom/data/exports/rag/rag-answer-20260601T020000Z.md",
  "status": "created"
}
```

Capture export artifact:

```json
{
  "type": "capture_draft_fulltext",
  "id": "draft-id",
  "title": "Capture 草稿全文",
  "path": "/Users/pipi/hermes-custom/data/exports/capture/capture-draft-draft-id.md",
  "status": "created"
}
```

## Task 1: Add File Artifact Helper

**Files:**

- Create: `/Users/pipi/hermes-custom/bridge/file_artifacts.py`
- Test: `/Users/pipi/hermes-custom/tests/test_file_artifacts.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_file_artifacts.py`:

```python
from pathlib import Path

from bridge.file_artifacts import safe_export_stem, write_markdown_export


def test_safe_export_stem_removes_unsafe_filename_chars():
    assert safe_export_stem('A/B: C? "D"') == "A-B-C-D"


def test_write_markdown_export_creates_parent_and_returns_absolute_path(tmp_path):
    path = write_markdown_export(
        base_dir=tmp_path / "exports",
        stem="测试报告",
        content="# 标题\n\n正文",
    )

    assert Path(path).is_absolute()
    assert Path(path).exists()
    assert Path(path).read_text(encoding="utf-8") == "# 标题\n\n正文\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_file_artifacts.py -q
```

Expected: fail because `bridge.file_artifacts` does not exist.

- [ ] **Step 3: Implement helper**

Create `bridge/file_artifacts.py`:

```python
"""Small helpers for writing user-requested Hermes file exports."""
from __future__ import annotations

import re
from pathlib import Path


def safe_export_stem(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", value or "").strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "hermes-export"


def write_markdown_export(*, base_dir: Path, stem: str, content: str) -> str:
    base_dir = base_dir.expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)
    path = (base_dir / f"{safe_export_stem(stem)}.md").resolve()
    text = (content or "").rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    return str(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_file_artifacts.py -q
```

Expected: pass.

## Task 2: RAG Search Export

**Files:**

- Modify: `/Users/pipi/hermes-custom/bridge/rag_bridge.py`
- Test: `/Users/pipi/hermes-custom/tests/test_rag_file_output.py`

- [ ] **Step 1: Write failing bridge tests**

Create `tests/test_rag_file_output.py`:

```python
from pathlib import Path


def test_search_knowledge_base_does_not_export_by_default(monkeypatch, tmp_path):
    from bridge import rag_bridge

    captured = {}

    def fake_operation(query, config, repo):
        captured["export_file"] = False
        return {
            "status": "success",
            "agent": "rag",
            "intent": "search_knowledge_base",
            "chat_preview": "答案 [1]",
            "durable_artifacts": [],
            "source_map": [],
            "next_actions": [],
            "warnings": [],
            "errors": [],
            "meta": {},
        }

    monkeypatch.setattr(rag_bridge, "_search_knowledge_base", fake_operation)
    envelope = rag_bridge.search_knowledge_base("问题")

    assert envelope["durable_artifacts"] == []


def test_rag_export_file_writes_answer_and_full_citations(monkeypatch, tmp_path):
    from bridge import rag_bridge

    export_dir = tmp_path / "exports" / "rag"
    results = [
        {
            "id": "cap1",
            "title": "材料一",
            "file_path": "captures/one.md",
            "section_title": "章节一",
        },
        {
            "id": "cap2",
            "title": "材料二",
            "file_path": "captures/two.md",
            "section_title": "",
        },
    ]
    answer = "这是完整回答 [1] [2]"
    sources = [
        {"title": "材料一", "path": "captures/one.md", "section": "章节一"},
        {"title": "材料二", "path": "captures/two.md", "section": ""},
    ]

    artifact = rag_bridge._rag_report_artifact(
        query="测试问题",
        answer=answer,
        sources=sources,
        results=results,
        export_dir=export_dir,
    )

    path = Path(artifact["path"])
    assert artifact["type"] == "rag_answer_report"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# RAG 完整回答与引用报告" in text
    assert "## 问题" in text
    assert "测试问题" in text
    assert "## 完整回答" in text
    assert "这是完整回答 [1] [2]" in text
    assert "[1] 材料一 / 章节一" in text
    assert "captures/one.md" in text
    assert "[2] 材料二" in text
    assert "captures/two.md" in text
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_rag_file_output.py -q
```

Expected: fail because `_rag_report_artifact` and export plumbing do not exist.

- [ ] **Step 3: Implement RAG report writer**

In `bridge/rag_bridge.py`, import helper and `artifact_ref`:

```python
from bridge.file_artifacts import write_markdown_export
from bridge.result_envelope import artifact_ref, source_ref, success_envelope
```

Add function:

```python
def _rag_report_artifact(
    *,
    query: str,
    answer: str,
    sources: list[dict],
    results: list[dict],
    export_dir: Path | None = None,
) -> dict:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_dir = export_dir or (Path(__file__).parent.parent / "data" / "exports" / "rag")
    lines = [
        "# RAG 完整回答与引用报告",
        "",
        "## 问题",
        "",
        query.strip(),
        "",
        "## 完整回答",
        "",
        answer.strip(),
        "",
        "## 完整引用",
        "",
    ]
    for index, source in enumerate(sources or [], 1):
        title = str(source.get("title") or "").strip()
        section = str(source.get("section") or source.get("section_title") or "").strip()
        path = str(source.get("path") or source.get("file_path") or "").strip()
        label = f"[{index}] {title or path or '未命名'}"
        if section:
            label += f" / {section}"
        lines.extend([f"- {label}", f"  - 路径：{path or '无'}"])
    file_path = write_markdown_export(
        base_dir=base_dir,
        stem=f"rag-answer-{timestamp}",
        content="\n".join(lines),
    )
    return artifact_ref(
        type="rag_answer_report",
        id=f"rag-answer-{timestamp}",
        title="RAG 完整回答与引用报告",
        path=file_path,
        status="created",
    )
```

- [ ] **Step 4: Add opt-in parameter**

Change signatures:

```python
def search_knowledge_base(query: str, export_file: bool = False) -> dict:
```

and:

```python
def _search_knowledge_base(query: str, config, repo: CaptureRepo, *, export_file: bool = False) -> dict:
```

Pass `export_file` through `run_with_ledger` input and operation.

When returning success, set artifacts only when requested:

```python
artifacts = []
if export_file:
    artifacts.append(
        _rag_report_artifact(
            query=query,
            answer=resp["answer"],
            sources=resp["sources"],
            results=results,
        )
    )
```

Use `durable_artifacts=artifacts` in the success envelope.

- [ ] **Step 5: Run focused RAG tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_rag_file_output.py tests/test_rag_continue_bridge.py -q
```

Expected: pass.

## Task 3: Capture Draft Full Text Export

**Files:**

- Modify: `/Users/pipi/hermes-custom/bridge/capture_bridge.py`
- Test: `/Users/pipi/hermes-custom/tests/test_capture_file_output.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_capture_file_output.py`:

```python
from pathlib import Path


def test_capture_draft_file_artifact_contains_full_draft(tmp_path):
    from bridge.capture_bridge import _capture_draft_file_artifact

    draft = {
        "id": "draft123",
        "title": "测试草稿",
        "draft_text": "第一段\n\n第二段",
        "raw_text": "原文不应优先",
    }

    artifact = _capture_draft_file_artifact(draft, export_dir=tmp_path)

    path = Path(artifact["path"])
    assert artifact["type"] == "capture_draft_fulltext"
    assert artifact["id"] == "draft123"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# 测试草稿" in text
    assert "第一段" in text
    assert "第二段" in text


def test_show_capture_draft_default_remains_chat_only(monkeypatch):
    from bridge import capture_bridge

    class DraftRepo:
        def get_latest_open(self):
            return {"id": "d1", "title": "草稿", "draft_text": "正文", "raw_text": ""}

    class Agent:
        draft_repo = DraftRepo()

    monkeypatch.setattr(capture_bridge, "_init", lambda: (None, Agent()))

    envelope = capture_bridge.show_capture_draft()

    assert envelope["durable_artifacts"][0]["type"] == "capture_draft"
    assert all(item["type"] != "capture_draft_fulltext" for item in envelope["durable_artifacts"])


def test_show_capture_draft_exports_file_only_when_requested(monkeypatch, tmp_path):
    from bridge import capture_bridge

    class DraftRepo:
        def get_by_id(self, draft_id):
            return {
                "id": draft_id,
                "title": "草稿",
                "draft_text": "完整正文" * 500,
                "raw_text": "",
                "status": "draft_ready",
            }

    class Agent:
        draft_repo = DraftRepo()

    monkeypatch.setattr(capture_bridge, "_init", lambda: (None, Agent()))
    monkeypatch.setattr(
        capture_bridge,
        "_capture_draft_file_artifact",
        lambda draft: {
            "type": "capture_draft_fulltext",
            "id": draft["id"],
            "title": "Capture 草稿全文",
            "path": str(tmp_path / "draft.md"),
            "status": "created",
        },
    )

    envelope = capture_bridge.show_capture_draft("d1", as_file=True)

    assert any(item["type"] == "capture_draft_fulltext" for item in envelope["durable_artifacts"])
    assert "已生成草稿全文文件" in envelope["chat_preview"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_file_output.py -q
```

Expected: fail because `_capture_draft_file_artifact` and `as_file` do not exist.

- [ ] **Step 3: Implement Capture draft file writer**

In `bridge/capture_bridge.py`, import helper:

```python
from bridge.file_artifacts import write_markdown_export
```

Add function:

```python
def _capture_draft_file_artifact(draft: dict, export_dir: Path | None = None) -> dict:
    draft_id = str(draft.get("id") or "")
    title = str(draft.get("title") or "未命名")
    body = str(draft.get("draft_text") or draft.get("raw_text") or "").strip()
    base_dir = export_dir or (Path(__file__).parent.parent / "data" / "exports" / "capture")
    path = write_markdown_export(
        base_dir=base_dir,
        stem=f"capture-draft-{draft_id or title}",
        content=f"# {title}\n\n{body}",
    )
    return artifact_ref(
        type="capture_draft_fulltext",
        id=draft_id,
        title="Capture 草稿全文",
        path=path,
        status="created",
    )
```

- [ ] **Step 4: Add `as_file` to `show_capture_draft`**

Change signature:

```python
def show_capture_draft(draft_id: str = "", page: int = 1, as_file: bool = False) -> dict:
```

If `as_file` is true, keep chat short and attach full-text artifact:

```python
artifacts = [
    artifact_ref(
        type="capture_draft",
        id=draft.get("id", ""),
        title=draft.get("title", ""),
        status=draft.get("status", ""),
    )
]
if as_file:
    artifacts.append(_capture_draft_file_artifact(draft))
    message = f"已生成草稿全文文件：《{title}》。我会把文件发给你。"
```

Default behavior remains paginated chat preview with only `capture_draft`.

- [ ] **Step 5: Run focused Capture tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_capture_file_output.py tests/test_e2e_full_flow.py -q
```

Expected: pass.

## Task 4: Hermes Runtime Tool Schemas

**Files:**

- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/search_notes_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/continue_rag_answer_tool.py`
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/show_capture_draft_tool.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_search_notes_tool.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_show_capture_draft_tool.py`

- [ ] **Step 1: Add schema tests**

In search tool tests, assert:

```python
def test_search_my_notes_export_file_is_explicit_opt_in():
    import tools.search_notes_tool as tool

    prop = tool.SEARCH_SCHEMA["parameters"]["properties"]["export_file"]
    assert prop["type"] == "boolean"
    assert "只有用户明确要求" in prop["description"]
```

In show draft tool tests, assert:

```python
def test_show_capture_draft_as_file_is_explicit_opt_in():
    import tools.show_capture_draft_tool as tool

    prop = tool.SHOW_CAPTURE_DRAFT_SCHEMA["parameters"]["properties"]["as_file"]
    assert prop["type"] == "boolean"
    assert "只有用户明确要求" in prop["description"]
```

- [ ] **Step 2: Run runtime tool tests to verify failure**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/tools/test_search_notes_tool.py tests/tools/test_show_capture_draft_tool.py -q
```

Expected: fail because schema fields do not exist.

- [ ] **Step 3: Update `search_notes_tool.py`**

Add parameter:

```python
"export_file": {
    "type": "boolean",
    "description": "只有用户明确要求发文件、导出、完整报告、完整引用报告时才设为 true；普通知识库问答必须保持 false。",
}
```

Handler:

```python
result = do_search(args.get("query", ""), export_file=bool(args.get("export_file", False)))
```

- [ ] **Step 4: Update `continue_rag_answer_tool.py`**

Add parameter:

```python
"export_file": {
    "type": "boolean",
    "description": "只有用户明确要求把上一条完整 RAG 回答/引用报告发成文件时才设为 true；普通继续展开保持 false。",
}
```

Handler:

```python
result = continue_latest_rag_answer(export_file=bool(args.get("export_file", False)))
```

- [ ] **Step 5: Update `show_capture_draft_tool.py`**

Add parameter:

```python
"as_file": {
    "type": "boolean",
    "description": "只有用户明确要求把 Capture 草稿全文发成文件/附件时才设为 true；普通展开草稿保持 false。",
}
```

Handler:

```python
result = show_capture_draft(
    args.get("draft_id", ""),
    args.get("page", 1),
    as_file=bool(args.get("as_file", False)),
)
```

- [ ] **Step 6: Run runtime tool tests**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/tools/test_search_notes_tool.py tests/tools/test_show_capture_draft_tool.py tests/tools/test_generate_canvas_tool.py -q
```

Expected: pass.

## Task 5: Adapter Instruction for Attachments

**Files:**

- Modify: `/Users/pipi/Downloads/hermes-agent-main/agent/hermes_custom_envelope_adapter.py`
- Test: `/Users/pipi/Downloads/hermes-agent-main/tests/agent/test_hermes_custom_direct_reply.py`

- [ ] **Step 1: Write failing adapter test**

Add:

```python
def test_envelope_adapter_mentions_media_attachment_for_user_requested_artifacts():
    from agent.hermes_custom_envelope_adapter import agent_result_envelope_content_for_model
    from bridge.result_envelope import artifact_ref, envelope_json, success_envelope

    content = agent_result_envelope_content_for_model(
        envelope_json(
            success_envelope(
                agent="rag",
                intent="search_knowledge_base",
                chat_preview="已生成完整报告。",
                durable_artifacts=[
                    artifact_ref(
                        type="rag_answer_report",
                        id="r1",
                        title="完整报告",
                        path="/tmp/rag.md",
                        status="created",
                    )
                ],
            )
        )
    )

    assert "MEDIA:/tmp/rag.md" in content
    assert "only if the user explicitly requested a file" in content
```

- [ ] **Step 2: Run adapter test to verify failure**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/agent/test_hermes_custom_direct_reply.py -q
```

Expected: fail because instruction does not mention attachment behavior.

- [ ] **Step 3: Update adapter instruction**

In model-facing content for Hermes envelopes, add a concise rule:

```text
If durable_artifacts contains an absolute local file path and the user explicitly requested a file/export/attachment, send a short status message and call send_message with MEDIA:<path>. Do not attach files automatically for normal answers.
```

- [ ] **Step 4: Run adapter test**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/agent/test_hermes_custom_direct_reply.py -q
```

Expected: pass.

## Task 6: Gitignore and Focused Regression

**Files:**

- Modify: `/Users/pipi/hermes-custom/.gitignore`

- [ ] **Step 1: Add generated export directory**

Add:

```gitignore
data/exports/
```

- [ ] **Step 2: Run hermes-custom tests**

Run:

```bash
cd /Users/pipi/hermes-custom
.venv/bin/python -m pytest tests/test_file_artifacts.py tests/test_rag_file_output.py tests/test_capture_file_output.py tests/test_rag_continue_bridge.py tests/test_e2e_hermes_tools.py -q
```

Expected: pass.

- [ ] **Step 3: Run hermes-agent-main tests**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest tests/tools/test_search_notes_tool.py tests/tools/test_show_capture_draft_tool.py tests/tools/test_generate_canvas_tool.py tests/agent/test_hermes_custom_direct_reply.py -q
```

Expected: pass.

## Task 7: Manual Smoke Test

**Files:** no code changes.

- [ ] **Step 1: Clear Hermes sessions**

Run:

```bash
mkdir -p ~/.hermes/session_backups
if [ -d ~/.hermes/sessions ] && [ "$(find ~/.hermes/sessions -mindepth 1 -maxdepth 1 | wc -l)" -gt 0 ]; then
  mv ~/.hermes/sessions ~/.hermes/session_backups/sessions-$(date -u +%Y%m%dT%H%M%SZ)
fi
mkdir -p ~/.hermes/sessions
```

- [ ] **Step 2: Restart Hermes**

Run:

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m hermes_cli.main gateway restart
cat ~/.hermes/gateway_state.json
```

Expected: `weixin` and `feishu` are `connected`.

- [ ] **Step 3: RAG default behavior**

Ask in WeChat:

```text
基于我的知识库，解释一下 AI Agent 工程化的关键点
```

Expected:

- Normal chat answer.
- Has references.
- Does not send a file.

- [ ] **Step 4: RAG file behavior**

Ask in WeChat:

```text
把刚才这个回答和完整引用报告发成文件
```

Expected:

- Short chat status.
- A Markdown file attachment is sent.
- File contains full answer and all citations.

- [ ] **Step 5: Capture default behavior**

Ask in WeChat:

```text
总结这篇文章的核心内容 <url>
```

Expected:

- Normal Capture preview.
- Does not send full draft file.

- [ ] **Step 6: Capture file behavior**

Ask in WeChat:

```text
把这个草稿全文发成文件
```

Expected:

- Short chat status.
- A Markdown file attachment is sent.
- File contains full draft text, not only current page.

## Self-Review

Spec coverage:

- RAG long answer and complete citation report can be exported as file: Task 2, Task 4, Task 5, Task 7.
- Capture long draft full text can be exported as file: Task 3, Task 4, Task 5, Task 7.
- Export only when user explicitly requests: Task 2 default test, Task 3 default test, Task 4 schema descriptions, Task 5 adapter instruction, Task 7 smoke tests.
- Not a generic wrapper tool: Architecture and scope keep export inside existing RAG/Capture tools only.

Placeholder scan:

- No `TBD`, `TODO`, or "implement later" items remain.

Type consistency:

- RAG uses `export_file`.
- Capture uses `as_file`.
- Artifact paths are absolute for `MEDIA:<path>` delivery.

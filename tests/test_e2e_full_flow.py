"""E2E 测试：Hermes tool handler → bridge → agent → local Markdown 完整链路。

运行方式:
  /opt/homebrew/bin/python3.11 -m pytest tests/test_e2e_full_flow.py -v --tb=long
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from tests.conftest import requires_runtime, requires_symlink  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(os.environ.get(
    "HERMES_RUNTIME_ROOT", str(PROJECT_ROOT.parent / "hermes-agent-main")
)).expanduser()
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(RUNTIME_ROOT))


def _tool_envelope(result: str) -> dict:
    from bridge.result_envelope import ENVELOPE_FIELDS

    envelope = json.loads(result)
    assert list(envelope.keys()) == list(ENVELOPE_FIELDS)
    return envelope


# ═══════════════════════════════════════════════════════
# Layer 1: Hermes tool handler → bridge → agent (no HTTP)
# ═══════════════════════════════════════════════════════

class TestToolHandlerToAgent:
    """测试 Hermes tool handler 调用链路"""

    @pytest.fixture(autouse=True)
    def isolate_bridge(self, monkeypatch):
        monkeypatch.delenv("HERMES_KNOWLEDGE_URL", raising=False)
        monkeypatch.delenv("HERMES_KNOWLEDGE_TOKEN", raising=False)
        monkeypatch.setenv("HERMES_KNOWLEDGE_LOCAL_ROOT", str(PROJECT_ROOT))
        with tempfile.TemporaryDirectory() as tmp:
            from agents.capture_agent import CaptureAgent, CaptureConfig
            from agents.extractors.text_extractor import TextExtractor
            import bridge.capture_bridge as capture_bridge

            config = CaptureConfig(
                llm_api_key="sk-test",
                llm_provider="openai",
                llm_model="gpt-4o",
                llm_base_url="https://api.deepseek.com",
                vault_path=tmp,
                db_path=str(Path(tmp) / "test.db"),
            )
            agent = CaptureAgent(config)
            agent.extractors = [TextExtractor()]
            monkeypatch.setattr(capture_bridge, "_config", config)
            monkeypatch.setattr(capture_bridge, "_agent", agent)
            yield
            agent.conn.close()

    def test_capture_tool_handler_returns_success(self):
        """tool handler 调用 bridge 应该返回成功消息"""
        from tools.capture_tool import _handler
        result = _handler({
            "content": (
                "人工智能是计算机科学的一个分支，旨在创建能够模拟人类智能的系统。"
                "现代AI技术包括机器学习、深度学习和自然语言处理等多个子领域。"
                "这些技术已经在医疗诊断、自动驾驶、金融风控等领域取得了显著成果。"
                "补充：人工智能技术正在快速发展，深度学习模型取得了显著成果。"
                "从图像识别到自然语言处理，AI应用越来越广泛地改变着我们的世界。"
                "这足以让中文字符超过五十个以确保能通过内容质量检测。"
            ),
        })
        assert "已提取" in result, f"Unexpected result: {result}"
        assert "想怎么整理" in result

    def test_capture_tool_handler_with_instruction_returns_draft(self, monkeypatch):
        """链接/文本同轮要求总结时，只返回草稿，不直接保存。"""
        from tools.capture_tool import _handler
        monkeypatch.setattr(
            "agents.capture_agent.CaptureAgent._call_llm",
            lambda self, prompt: "核心观点：AI 的瓶颈在电力基础设施。",
        )
        monkeypatch.setattr(
            "agents.capture_agent.Summarizer.summarize",
            lambda self, text, existing_tags=None: {
                "title": "AI电力",
                "summary": "AI发展受电力制约。",
                "tags": ["ai-energy"],
            },
        )
        result = _handler({
            "content": (
                "AI时代的电力协议讨论了算力中心、电网扩容和中国变压器供应链之间的关系。"
                "文章指出AI发展的瓶颈不仅是芯片和模型，也包括稳定电力基础设施。"
                "其中关于中国变压器供应链的部分尤其重要，因为电力设备交付周期长，"
                "会直接影响数据中心扩张速度。补充文字确保内容长度足够通过质量检测。"
            ),
            "instruction": "总结全文核心观点",
        })
        envelope = _tool_envelope(result)
        assert envelope["status"] == "success"
        assert "原文已提取，已生成待确认初稿" in envelope["chat_preview"]
        assert "标题：" in envelope["chat_preview"]
        assert "重点回应：" not in envelope["chat_preview"]
        assert "总结：" not in envelope["chat_preview"]
        assert "草稿预览" not in envelope["chat_preview"]
        assert "核心观点" not in envelope["chat_preview"]
        intents = [action["intent"] for action in envelope["next_actions"]]
        labels = [action["label"] for action in envelope["next_actions"]]
        assert "show_capture_draft" in intents
        assert "commit_capture_draft" in intents
        assert "展开原文" in labels
        assert "按当前方向整理保存" in labels

    def test_capture_tool_handler_with_url(self):
        """tool handler 处理 URL 输入"""
        from tools.capture_tool import _handler
        result = _handler({
            "content": "https://mp.weixin.qq.com/s/Esk0AFhP-apyWY8eWruv-Q",
        })
        envelope = _tool_envelope(result)
        assert envelope["status"] in {"success", "error"}
        assert len(envelope["chat_preview"]) > 0

    def test_capture_tool_handler_empty_content(self):
        """tool handler 处理空 content"""
        from tools.capture_tool import _handler
        result = _handler({"content": ""})
        envelope = _tool_envelope(result)
        assert envelope["status"] == "error"
        assert len(envelope["errors"]) > 0

    def test_capture_tool_handler_no_content_key(self):
        """tool handler 处理缺少 content key 的参数"""
        from tools.capture_tool import _handler
        result = _handler({})
        envelope = _tool_envelope(result)
        assert envelope["status"] == "error"
        assert len(envelope["errors"]) > 0


# ═══════════════════════════════════════════════════════
# Layer 2: Bridge → Agent → Markdown → File Read Back
# ═══════════════════════════════════════════════════════

TEST_CONTENT = (
    "知识管理有三个核心层次：第一层是信息捕获，需要建立高效的信息采集渠道；"
    "第二层是知识整理，将碎片化信息转化为结构化的知识体系；"
    "第三层是知识内化，通过实践和复习将知识转化为能力和直觉。"
    "这三层形成了一个完整的学习闭环，缺一不可。"
    "补充文本让中文字符数量超过一百个以确保能够通过内容质量检测。"
    "人工智能和深度学习是当前最热门的技术方向之一。"
)


class TestBridgeToMarkdownE2E:
    """Bridge → Agent → Markdown 写入 → 文件回读验证"""

    @pytest.fixture(autouse=True)
    def setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.vault_path = Path(tmp)
            self.db_path = str(self.vault_path / "test.db")

            from agents.capture_agent import CaptureAgent, CaptureConfig
            self.config = CaptureConfig(
                llm_api_key="sk-test", llm_provider="openai", llm_model="gpt-4o",
                llm_base_url="https://api.deepseek.com",
                vault_path=str(self.vault_path),
                db_path=self.db_path,
            )
            self.agent = CaptureAgent(self.config)
            # Use only TextExtractor to avoid network calls
            from agents.extractors.text_extractor import TextExtractor
            self.agent.extractors = [TextExtractor()]
            yield
            self.agent.conn.close()

    @pytest.fixture
    def mock_summarize(self, monkeypatch):
        def _mock(*args, **kwargs):
            return {
                "title": "知识管理三层次",
                "summary": "捕获、整理、内化是知识管理的三个核心层次。",
                "tags": ["km", "learning"],
            }
        monkeypatch.setattr(self.agent.summarizer, "summarize", _mock)

    def test_capture_creates_draft_without_markdown_file(self, mock_summarize):
        result = self.agent.create_draft(TEST_CONTENT)
        assert "error" not in result, f"Process failed: {result}"
        assert result["status"] == "raw_extracted"
        assert result["raw_text"]
        assert not (self.vault_path / "raw").exists()
        assert not (self.vault_path / "captures").exists()

    def test_capture_draft_not_in_capture_db_until_commit(self, mock_summarize):
        result = self.agent.create_draft(TEST_CONTENT)
        draft_id = result["id"]

        draft = self.agent.draft_repo.get_by_id(draft_id)
        assert draft, f"Draft {draft_id} not found"
        assert draft["raw_text"]
        assert self.agent.repo.list_all() == []

    def test_capture_bridge_returns_draft_id(self, mock_summarize, monkeypatch):
        import bridge.capture_bridge as capture_bridge
        from bridge.capture_bridge import capture_content

        monkeypatch.setattr(capture_bridge, "_config", self.config)
        monkeypatch.setattr(capture_bridge, "_agent", self.agent)

        result = capture_content(TEST_CONTENT)

        assert result["status"] == "success"
        artifacts = result["durable_artifacts"]
        assert artifacts and artifacts[0]["type"] == "capture_draft"
        assert artifacts[0]["id"], "No draft id from bridge.capture_content"
        assert artifacts[0]["status"] == "raw_extracted"

    def test_reprocess_updates_draft_and_commit_writes_markdown(self, mock_summarize, monkeypatch):
        # Mock LLM call to avoid real API call
        monkeypatch.setattr(self.agent, "_call_llm",
                           lambda prompt: (
                               "## 目录纲要\n\n- [知识处理流程](#知识处理流程)\n\n"
                               "## 知识处理流程\n\n"
                               "1. **信息捕获**是第一步\n"
                               "2. 知识整理是第二步\n"
                               "3. 知识内化是第三步"
                           ))

        # First capture
        result = self.agent.create_draft(TEST_CONTENT)
        draft_id = result["id"]

        # Reprocess/revise updates only the draft.
        reprocessed = self.agent.reprocess(draft_id, "提取三个核心观点")
        assert reprocessed is not None
        assert "1. **信息捕获**" in reprocessed["draft_text"]
        assert not (self.vault_path / "captures").exists()

        committed = self.agent.commit_draft(draft_id)
        updated = (self.vault_path / committed["file_path"]).read_text(encoding="utf-8")
        assert "1. **信息捕获**" in updated

    def test_reprocess_nonexistent_id(self):
        result = self.agent.reprocess("nonexistent", "test")
        assert result is None

    def test_empty_content_returns_error(self):
        result = self.agent.process("")
        assert "error" in result, f"Should return error for empty content: {result}"

pytestmark = requires_runtime

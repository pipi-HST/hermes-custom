---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes Knowledge 联网研究工具接入可执行方案

日期：2026-06-20

状态：背景参考，已被主执行方案吸收。

主执行方案为：

```text
/Users/pipi/hermes-custom/docs/superpowers/plans/2026-06-20-hermes-web-research-pipeline-upgrade-plan.md
```

后续实现、验收和回归测试以主执行方案为准。本文保留为工具接入背景，避免丢失当时对现有 `web_search` / `web_extract` / toolset 的调查记录。

本文档定义 Hermes Knowledge 如何新增“联网搜索 + 网页抽取 + 本地知识库混合研究”能力，并明确要求该能力同时覆盖两个 AI 入口：

- `/knowledge-chat` 工作台 AI 对话。
- `/knowledge-library` 知识库浏览模式右侧 AI 对话。

目标不是给模型随意打开一个 `web_search` 工具，而是把联网能力做成可控、可引用、可保存、可验证的研究流程。Hermes Knowledge 的产品定位仍然是：

> AI-native 的个人知识工作台：人负责捕捉、阅读、判断、修改和归类，AI 负责整理、关联、检索和复用。

联网搜索应服务于这个知识工作流，而不是把 Hermes 变成普通搜索聊天机器人。

## 1. 背景和当前事实

### 1.1 现有能力

当前 Hermes 主仓库 `/Users/pipi/Downloads/hermes-agent-main` 已存在通用联网工具：

```text
tools/web_tools.py
├─ web_search
├─ web_extract
└─ web_crawl
```

`web_tools.py` 已支持多种后端：

- Firecrawl
- Parallel
- Tavily
- Exa
- SearXNG
- Brave Search
- ddgs

`toolsets.py` 中也已有基础 toolset：

```python
"web": {
    "description": "Web research and content extraction tools",
    "tools": ["web_search", "web_extract"],
}

"search": {
    "description": "Web search only (no content extraction/scraping)",
    "tools": ["web_search"],
}
```

但当前知识工作台前端固定使用：

```ts
const KNOWLEDGE_TOOLSETS = ["personal-assistant"];
```

而 `personal-assistant` 目前主要包含本地知识、Capture、Reading、Garden、Review、发送消息等工具，没有把 `web_search` 和 `web_extract` 暴露给知识工作台。

### 1.2 现有本地知识能力

当前 Hermes Knowledge 已有：

```text
search_my_notes
continue_rag_answer
save_answer_to_knowledge
synthesize_garden
start_reading_session
discuss_reading
summarize_reading_session
commit_reading_artifact
```

本地知识库搜索通过：

```text
/Users/pipi/hermes-custom/bridge/rag_bridge.py
/Users/pipi/Downloads/hermes-agent-main/tools/search_notes_tool.py
```

搜索结果已经有 `source_map` 和编号引用，但当前引用体系主要服务本地知识库。联网搜索加入后，必须避免把本地来源和网页来源混成同一种 `[1]`。

### 1.3 两个必须覆盖的 AI 入口

第一类入口是工作台：

```text
/knowledge-chat
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx
```

它是通用 AI 协作窗口，适合开放问题、长答案、工具调用、保存笔记和生成卡片。

第二类入口是知识库右侧 AI：

```text
/knowledge-library
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx
/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py
POST /api/knowledge/library/items/{kind}/{item_id}/selection-chat
```

它围绕当前文件、当前选区、前后文和整篇文章回答问题。这个入口不能只做普通问答，也必须获得同一套联网研究能力。

## 2. 目标

### 2.1 产品目标

完成后，Hermes Knowledge 应支持三种研究模式：

```text
仅知识库
知识库 + 联网
仅联网
```

两个 AI 入口都应支持这三种模式。

工作台 AI 可以回答：

- “最近市场会涨还是会跌？”
- “结合我的知识库和最新外部信息判断一下。”
- “这个模型最近有什么新进展？”
- “把这个联网研究回答存成学习笔记。”

知识库右侧 AI 可以回答：

- “我选中这段是什么意思？”
- “这篇文章的判断现在还成立吗？”
- “这段观点和我知识库里之前的观点有什么关系？”
- “结合最新公开信息，更新一下这篇文章里的判断。”

### 2.2 工程目标

实现一个共享研究层，不在两个前端入口各写一套逻辑。

推荐新增：

```text
/Users/pipi/hermes-custom/bridge/research_bridge.py
/Users/pipi/Downloads/hermes-agent-main/tools/research_with_web_tool.py
```

并在主仓库新增或扩展：

```text
/Users/pipi/Downloads/hermes-agent-main/toolsets.py
/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/lib/api.ts
```

## 3. 设计原则

### 3.1 不把联网搜索当默认万能答案

联网搜索只在需要时使用：

- 用户明确说“联网搜一下”“查一下最新”。
- 问题包含强时间敏感性，例如“最新、最近、今天、现在、今年、版本、价格、政策、新闻、市场”。
- 本地知识库没有足够答案。
- 用户要求和外部公开资料对比。
- 当前文章内容明显需要验证是否过时。

如果用户问的是“我的笔记里怎么说”“我之前保存过什么”，默认不联网。

### 3.2 本地知识和联网资料必须分层

回答中必须区分：

```text
本地知识库依据
联网资料依据
AI 综合判断
```

本地知识库来源使用：

```text
[K1] [K2]
```

联网来源使用：

```text
[W1] [W2]
```

不要把网页来源写成 `[1]`，否则会和本地 RAG 引用混淆。

### 3.3 搜索结果不能直接等同事实

`web_search` 返回的是候选结果，不是充分证据。

规则：

- 简单事实可以引用搜索结果摘要，但必须标注来源。
- 关键结论、争议判断、金融/医疗/法律/政策类内容，应进一步调用 `web_extract` 抽取网页正文。
- 如果搜索结果互相冲突，回答必须标注冲突。
- 如果来源质量不足，回答必须说“不足以支撑强结论”。

### 3.4 人确认后才沉淀

联网资料不能自动入库。

可以做：

- 回答后让用户保存成学习笔记。
- 保存时保留联网来源。
- 用户确认后生成 Garden Card。

不做：

- 搜到网页就自动写入 Obsidian。
- 自动创建大量外部来源笔记。
- 自动把外部搜索结果当作用户知识库内容。

## 4. 用户体验设计

### 4.1 工作台 AI 对话

工作台输入区增加一个检索模式控件：

```text
检索模式：
[仅知识库] [知识库 + 联网] [仅联网]
```

默认建议：

```text
知识库 + 联网
```

但“知识库 + 联网”不等于每次都联网。模型或桥接层应先判断是否需要外部实时资料。

回答卡片展示：

```md
结论
...

依据

本地知识库
- [K1] 《材料标题》
- [K2] 《材料标题》

联网资料
- [W1] 网页标题 - example.com
- [W2] 网页标题 - another.com

综合判断
...
```

工作台仍保留：

- 复制回答。
- 存为笔记。
- 做成卡片。
- 继续展开。
- 导出完整引用报告。

### 4.2 知识库右侧 AI 对话

右侧 AI 面板也增加同样的检索模式控件：

```text
检索模式：
[当前文章] [文章 + 知识库] [文章 + 知识库 + 联网] [仅联网]
```

为了和工作台保持统一，内部可以映射为：

```text
current_article_only
local_only
local_web
web_only
```

右侧 AI 请求必须注入：

```json
{
  "question": "用户问题",
  "mode": "local_web",
  "article": {
    "id": "...",
    "kind": "capture 或 capture_draft",
    "title": "...",
    "folder_path": "...",
    "body": "当前文章全文"
  },
  "selection": {
    "selected_text": "...",
    "surrounding_before": "...",
    "surrounding_after": "...",
    "heading_path": ["..."],
    "selection_confidence": "offset 或 text_only 或 none"
  }
}
```

右侧 AI 的回答应根据问题自动决定：

- 只是解释选区：优先当前文章。
- 问“我之前有没有类似观点”：查本地知识库。
- 问“现在是否还成立”：查联网。
- 问“结合我的资料和最新资料”：本地知识库 + 联网。

### 4.3 保存为学习笔记

两个入口的回答都可以保存为学习笔记。

保存时正文底部必须包含：

```md
## 参考来源

### 知识库
- [K1] 《材料标题》

### 联网资料
- [W1] 网页标题，https://example.com，访问时间：2026-06-20
```

frontmatter 建议增加：

```yaml
source_type: mixed_research
research_mode: local_web
knowledge_sources:
  - capture_id: ...
    title: ...
    path: ...
web_sources:
  - title: ...
    url: ...
    domain: ...
    accessed_at: ...
```

如果暂时不改数据库结构，可以先把这些放到 `reading_drafts.meta_json.sources`，commit 时写入 Markdown frontmatter 和正文参考来源。

## 5. 后端架构

### 5.1 新增共享桥接层

新增：

```text
/Users/pipi/hermes-custom/bridge/research_bridge.py
```

核心函数：

```python
def research_answer(
    *,
    query: str,
    mode: str = "local_web",
    article_context: dict | None = None,
    selection_context: dict | None = None,
    export_file: bool = False,
) -> dict:
    ...
```

返回仍使用 Hermes envelope：

```python
success_envelope(
    agent="research",
    intent="research_answer",
    chat_preview="...",
    source_map=[...],
    meta={
        "query": "...",
        "mode": "local_web",
        "knowledge_sources": [...],
        "web_sources": [...],
        "used_local": True,
        "used_web": True,
    },
)
```

### 5.2 research_bridge 内部流程

推荐流程：

```text
1. normalize mode
2. build query pack
3. if mode includes local:
     call rag_bridge.search_knowledge_base(...)
4. if mode includes web and should_search_web(...):
     call web_search adapter
5. if web result needs extraction:
     call web_extract adapter
6. merge contexts
7. call synthesis LLM
8. validate citations
9. return envelope
```

### 5.3 是否需要联网的判断

`should_search_web` 不应只靠关键词，但第一版可以做混合策略：

强制联网：

```text
用户明确要求联网、搜索、外部资料、最新、今天、现在、最近
```

建议联网：

```text
市场、价格、政策、法律、模型版本、公司动态、新闻、产品更新、开源项目状态
```

不联网：

```text
“我的笔记里”
“我之前保存”
“这篇文章什么意思”
“解释选区”
```

右侧 AI 中，如果问题是：

```text
“这段现在还成立吗”
“有没有最新变化”
“外部资料怎么看”
```

则联网。

### 5.4 工具接入方式

不要在 `hermes-custom` 里重新实现搜索后端。复用主仓库：

```text
/Users/pipi/Downloads/hermes-agent-main/tools/web_tools.py
```

可选实现方式：

第一版简单方案：

```python
from tools.web_tools import web_search_tool, web_extract_tool
```

如果 import 路径不稳定，则在主仓库新建工具：

```text
tools/research_with_web_tool.py
```

由主仓库工具调用 hermes-custom bridge：

```python
HERMES_CUSTOM = os.environ.get("HERMES_CUSTOM_ROOT", "/Users/pipi/hermes-custom")
sys.path.insert(0, HERMES_CUSTOM)
from bridge.research_bridge import research_answer
```

### 5.5 新增工具 schema

新增：

```text
research_with_web
```

参数：

```json
{
  "query": {
    "type": "string",
    "description": "用户问题"
  },
  "mode": {
    "type": "string",
    "enum": ["local_only", "local_web", "web_only"],
    "description": "检索模式"
  },
  "article_context": {
    "type": "object",
    "description": "可选，当前文章上下文"
  },
  "selection_context": {
    "type": "object",
    "description": "可选，用户选中的文本及前后文"
  },
  "export_file": {
    "type": "boolean"
  }
}
```

工具说明必须强调：

- 用户问本地知识时优先本地。
- 用户问最新事实时必须联网。
- 回答必须保留 `[K]` 和 `[W]` 引用。
- 不能把搜索结果当已确认事实。

## 6. Toolset 设计

### 6.1 新增 knowledge-research

在：

```text
/Users/pipi/Downloads/hermes-agent-main/toolsets.py
```

新增：

```python
"knowledge-research": {
    "description": "Hermes Knowledge workbench with local RAG and controlled web research",
    "tools": [
        "capture_content",
        "revise_capture_draft",
        "commit_capture_draft",
        "show_capture_draft",
        "refine_capture",
        "search_my_notes",
        "continue_rag_answer",
        "research_with_web",
        "web_search",
        "web_extract",
        "start_reading_session",
        "discuss_reading",
        "show_reading_session",
        "summarize_reading_session",
        "commit_reading_artifact",
        "save_answer_to_knowledge",
        "graph_report",
        "synthesize_garden",
        "revise_garden_draft",
        "commit_garden_draft",
        "daily_review",
        "review_feedback",
        "review_followup",
        "audit_library",
        "agent_run_status",
        "send_message",
    ],
    "includes": [],
}
```

第一版可以保留 `web_search` 和 `web_extract` 作为底层工具，但系统提示应鼓励优先使用 `research_with_web`。

### 6.2 工作台接入

修改：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx
```

从：

```ts
const KNOWLEDGE_TOOLSETS = ["personal-assistant"];
```

改为：

```ts
const KNOWLEDGE_TOOLSETS = ["knowledge-research"];
```

后续如果要让用户开关联网能力，可以动态传：

```ts
const toolsets = webEnabled ? ["knowledge-research"] : ["personal-assistant"];
```

## 7. 知识库右侧 AI 接入

### 7.1 当前接口

当前右侧 AI 走：

```text
POST /api/knowledge/library/items/{kind}/{item_id}/selection-chat
```

所在文件：

```text
/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py
```

前端调用：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/lib/api.ts
```

### 7.2 API 请求体扩展

扩展请求体：

```python
class _KnowledgeLibrarySelectionChatBody(BaseModel):
    question: str
    selected_text: str = ""
    surrounding_before: str = ""
    surrounding_after: str = ""
    heading_path: list[str] = []
    mode: str = "local_web"
```

### 7.3 后端流程

当前 selection-chat 不应再只调用普通 LLM，而应改为：

```python
from bridge.research_bridge import research_answer

result = research_answer(
    query=body.question,
    mode=body.mode,
    article_context={
        "kind": kind,
        "id": item_id,
        "title": item["title"],
        "body": item["body"],
        "folder_path": item["folder_path"],
    },
    selection_context={
        "selected_text": body.selected_text,
        "surrounding_before": body.surrounding_before,
        "surrounding_after": body.surrounding_after,
        "heading_path": body.heading_path,
    },
)
```

返回给前端：

```json
{
  "answer": "...",
  "sources": {
    "knowledge": [...],
    "web": [...]
  },
  "mode": "local_web"
}
```

### 7.4 右侧 AI 保存回答

当前右侧 AI 已经有复制回答能力，但还应补：

- 存为笔记。
- 做成卡片。

第一版如果 UI 不想加太多按钮，可以至少让 `save_answer_to_knowledge` 接收右侧 AI 的回答、当前文章和 sources。

## 8. 引用和来源模型

### 8.1 source_map 结构

建议统一 source map：

```python
source_ref(
    role="evidence",
    type="knowledge",
    id="capture_id",
    title="...",
    path="...",
    section="..."
)
```

联网来源用：

```python
{
    "role": "evidence",
    "type": "web",
    "id": "W1",
    "title": "...",
    "url": "...",
    "domain": "...",
    "accessed_at": "2026-06-20T..."
}
```

### 8.2 引用编号规则

本地来源编号：

```text
[K1] [K2] [K3]
```

联网来源编号：

```text
[W1] [W2] [W3]
```

不再用单纯 `[1]` 混排。

### 8.3 回答底部来源

统一显示：

```md
## 来源

### 知识库
- [K1] 《标题》 / 章节

### 联网资料
- [W1] 标题 - domain.com
  URL: https://...
  访问时间：2026-06-20 20:30
```

## 9. 保存和沉淀

### 9.1 保存为学习笔记

`save_answer_to_knowledge` 需要识别 sources 中的 web sources。

保存时：

- `story` 由 AI 基于完整回答生成摘要。
- `tags` 由 AI 基于完整回答生成。
- 正文保留回答。
- 底部保留参考来源。

### 9.2 生成 Garden Card

Garden Card Agent 接收联网研究回答时，需要知道：

- 哪些观点来自用户知识库。
- 哪些事实来自网页。
- 哪些是 AI 综合判断。

建议在 card suggestion 中加入：

```json
{
  "evidence_policy": "knowledge_and_web_sources_must_be_preserved",
  "source_ids": ["K1", "W1"]
}
```

第一版可以先只保证学习笔记保存来源，Garden Card 后续再增强。

## 10. 前端改动清单

### 10.1 KnowledgeChatPage

文件：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx
```

改动：

- `KNOWLEDGE_TOOLSETS` 改为 `["knowledge-research"]`。
- 输入区增加检索模式状态。
- 提交消息时把模式写入 prompt 或 gateway request metadata。
- 回答卡片中保留来源渲染。
- 保存为笔记时把 sources 一起传递。

### 10.2 GatewayChat

文件：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/knowledge-chat/GatewayChat.tsx
```

改动：

- 在输入区附近渲染检索模式 segmented control。
- 保持复制按钮。
- 后续可加来源折叠区。

### 10.3 KnowledgeLibraryPage

文件：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx
```

改动：

- 右侧 AI 面板增加检索模式控件。
- `askArticleAi()` 请求体增加 `mode`。
- 回答消息结构增加 sources。
- 回答卡片显示复制按钮和来源。
- 后续增加“存为笔记/做成卡片”。

### 10.4 api.ts

文件：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/lib/api.ts
```

改动：

```ts
export interface KnowledgeLibrarySelectionChatRequest {
  question: string;
  selected_text?: string;
  surrounding_before?: string;
  surrounding_after?: string;
  heading_path?: string[];
  mode?: "current_article_only" | "local_only" | "local_web" | "web_only";
}
```

响应增加：

```ts
sources?: {
  knowledge?: ResearchSource[];
  web?: ResearchSource[];
}
```

## 11. 后端改动清单

### 11.1 hermes-custom

新增：

```text
bridge/research_bridge.py
tests/test_research_bridge.py
```

修改：

```text
bridge/answer_bridge.py
bridge/reading_bridge.py
agents/run_evidence.py
```

### 11.2 hermes-agent-main

新增：

```text
tools/research_with_web_tool.py
tests/tools/test_research_with_web_tool.py
```

修改：

```text
toolsets.py
hermes_cli/web_server.py
web/src/pages/KnowledgeChatPage.tsx
web/src/pages/knowledge-chat/GatewayChat.tsx
web/src/pages/KnowledgeLibraryPage.tsx
web/src/lib/api.ts
```

## 12. 配置和降级

### 12.1 web backend 配置

沿用 Hermes 现有 web tools 配置。

可用后端优先级由 `web_tools.py` 处理：

- `web.search_backend`
- `web.extract_backend`
- `web.backend`
- 环境变量自动探测

第一版不新增配置中心，只在 UI 上增加模式选择。

### 12.2 无联网配置时

如果用户选择联网，但后端不可用：

回答应返回：

```text
当前没有可用的联网搜索后端。已基于本地知识库和当前文章回答；如需联网，请先配置 web backend。
```

不能静默失败。

### 12.3 超时和失败

建议：

- `web_search` 超时：15-30 秒。
- `web_extract` 单页超时：30 秒。
- 最多抽取 3-5 个网页。
- 搜索失败时保留本地答案。
- 抽取失败时只使用搜索摘要，并标注“未能抽取全文”。

## 13. 验收测试

### 13.1 单元测试

新增：

```text
tests/test_research_bridge.py
tests/tools/test_research_with_web_tool.py
```

覆盖：

- `local_only` 不调用 web。
- `web_only` 不调用本地 RAG。
- `local_web` 同时合并本地和网页来源。
- 搜索失败时 graceful fallback。
- 网页来源编号为 `[W1]`。
- 本地来源编号为 `[K1]`。
- 来源保存到 envelope meta。

### 13.2 API 测试

覆盖：

- `/api/knowledge/library/items/{kind}/{item_id}/selection-chat` 传 `mode=local_web`。
- 选区文本进入 research bridge。
- 当前文章全文进入 research bridge。
- 响应包含 `sources.web` 和 `sources.knowledge`。

### 13.3 前端构建

必须跑：

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm run build
```

### 13.4 手工 smoke

工作台：

```text
问题：结合我的知识库和最新公开信息，最近市场会涨还是会跌？
预期：调用本地知识库 + 联网搜索，回答底部有 K/W 来源。
```

知识库右侧 AI：

```text
打开一篇已入库文章，选中一段话。
问题：这段观点现在还成立吗？请结合最新资料判断。
预期：回答解释选区，并补充联网资料来源。
```

保存笔记：

```text
把刚才这个回答存成学习笔记。
预期：Obsidian 笔记底部包含知识库来源和联网来源。
```

## 14. 实施顺序

### 阶段一：最小可用联网研究

1. 新增 `knowledge-research` toolset。
2. 新增 `research_with_web` 工具。
3. 工作台启用 `knowledge-research`。
4. `research_with_web` 能返回本地/网页分组来源。
5. 跑工具测试和工作台 smoke。

### 阶段二：知识库右侧 AI 接入

1. 扩展 selection-chat request/response。
2. 后端 selection-chat 调用 `research_bridge`。
3. 前端右侧 AI 增加模式选择。
4. 回答卡片显示来源。
5. 跑 API 测试和浏览器 smoke。

### 阶段三：保存和沉淀

1. `save_answer_to_knowledge` 保存 web sources。
2. 学习笔记正文底部写入参考来源。
3. frontmatter 写入 `source_type/research_mode/web_sources/knowledge_sources`。
4. Garden Card 生成时带来源上下文。

### 阶段四：质量增强

1. 根据问题自动判断是否联网。
2. 对高风险领域加来源质量和免责声明。
3. 支持“刷新联网资料”。
4. 支持“只看我的知识库，不联网”作为明确隐私模式。

## 15. 风险和边界

### 15.1 成本和延迟

联网搜索 + 网页抽取 + LLM 综合会增加延迟。第一版应限制：

- 搜索结果最多 5 条。
- 抽取网页最多 3 条。
- 每次回答最多一轮网页抽取。

### 15.2 来源可靠性

不能假设搜索排名最高就是最可靠。对于市场、政策、法律、医疗等问题，应优先选择：

- 官方网站。
- 主流媒体。
- 原始公告。
- 研究报告原文。

### 15.3 隐私

当前文章全文如果包含私人信息，直接作为搜索 query 会有风险。

规则：

- 搜索 query 应由问题和必要关键词生成，不把整篇文章发给搜索引擎。
- 当前文章全文可以给 LLM 综合，但不应原样作为搜索关键词。
- 用户选择“仅知识库”时绝不联网。

### 15.4 与 Obsidian 的边界

联网资料默认不创建独立 Obsidian 文件。只有用户明确保存研究回答时，才作为学习笔记进入 vault。

## 16. 最终验收标准

完成后应满足：

1. 工作台 AI 支持本地/联网/混合研究。
2. 知识库右侧 AI 支持当前文章/本地/联网/混合研究。
3. 两个入口都能显示并复制回答。
4. 两个入口都能保留来源。
5. 保存成学习笔记时保留本地和联网来源。
6. 无联网配置时不崩溃，并明确告知用户。
7. 本地知识引用和联网引用不混淆。
8. 相关 Python 测试、前端 build、9119 smoke 全部通过。

---
status: historical
execution_status: done
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Hermes Knowledge Library 浏览/编辑双模式改版可执行方案

日期：2026-06-20

本文档把知识库从“文件管理页”升级为“阅读 + 编辑 + 选区问 AI”的知识工作区。它承接 `2026-06-19-hermes-knowledge-workspace-redesign-brief.md` 的产品定位：

> Hermes Knowledge 是 AI-native 的个人知识工作台：人负责捕捉、阅读、判断、修改和归类，AI 负责整理、关联、检索和复用。

本方案是后续实施和验收的执行文档。它不把知识库做成新的聊天页，也不替代原有满血对话工作台；它只负责把知识库页面本身变成可阅读、可管理、可编辑、可基于选区问 AI 的文档工作区。

## 1. 当前事实和实施边界

### 1.1 当前运行事实

当前浏览器访问的知识库页面来自 Hermes 原生 Dashboard，而不是 `/Users/pipi/hermes-custom/dashboard` 下已废弃的自定义 Dashboard。

已观察到的运行方式：

```text
127.0.0.1:9119
└─ /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python
   └─ from hermes_cli.web_server import start_server
      └─ HERMES_CUSTOM_ROOT=/Users/pipi/hermes-custom
```

因此实施时必须同时区分两类代码：

```text
/Users/pipi/Downloads/hermes-agent-main
├─ web/                         # 9119 Dashboard 前端源码
│  ├─ src/App.tsx
│  ├─ src/pages/KnowledgeLibraryPage.tsx
│  ├─ src/components/Markdown.tsx
│  └─ src/lib/api.ts
└─ hermes_cli/web_server.py      # 9119 后端 API 路由

/Users/pipi/hermes-custom
├─ agents/
├─ bridge/
├─ api/routes/
├─ tests/
└─ docs/superpowers/plans/       # 本文档所在定制根目录
```

实施前不能只改 `/Users/pipi/hermes-custom/dashboard`，因为该目录的 `package.json` 明确标注自定义 Dashboard 已废弃。

### 1.2 当前已观察到的相关实现

当前 `/Users/pipi/Downloads/hermes-agent-main` 中已经存在一部分本方案相关能力：

- `/web/src/App.tsx` 已注册 `/knowledge-library`、`/knowledge-chat`、`/cards-board` 三个知识工作区入口。
- `/web/src/pages/KnowledgeLibraryPage.tsx` 已包含知识库页面主体。
- `/web/src/components/Markdown.tsx` 提供轻量 Markdown 渲染，浏览模式不再使用富文本编辑器。
- `/web/src/lib/api.ts` 已存在 knowledge library folders/items/update/delete/dependencies/selection-chat 相关 API client。
- `/hermes_cli/web_server.py` 已存在 `/api/knowledge/library/items/{kind}/{item_id}/selection-chat` 路由雏形。

这意味着后续实施不是“从零新建页面”，而是按本文档做一次事实核查、缺口修复、体验收敛和端到端验收。

### 1.3 成功定义

第一版完成后必须满足：

1. 知识库左侧栏可以完成文件夹、搜索、筛选、文件列表和文件打开。
2. 点击已有文件默认进入浏览模式；创建、粘贴、导入类入口默认进入编辑模式。
3. 浏览模式只负责 Markdown 渲染和选区捕获，不提供正文编辑。
4. 编辑模式负责标题、正文和文件夹修改；保存正文仍然保存为 Markdown 字符串，不引入私有文档格式。
5. 右侧 AI 面板只保留自由提问，不设置 AI 快捷按钮。
6. 用户选中正文片段后，AI 能基于选区、前后文、标题路径和当前文章回答。
7. 用户不选中文本时，AI 也能基于当前文章整体回答。
8. 草稿和已入库文件都支持浏览、编辑、保存、删除。
9. 保存正文不自动触发 AI 整理、摘要/标签重算或 Garden Card 生成。
10. 原粘贴整理、草稿、入库、Garden Card、文件夹、搜索、筛选、删除依赖检查流程不回归。
11. 前端 build、后端相关测试、浏览器 smoke 均通过。

## 2. 产品定位

### 2.1 知识库和工作台的区别

知识库页面的职责：

- 阅读知识。
- 管理知识。
- 整理知识。
- 修改标题、正文、摘要、标签、文件夹。
- 判断是否入库。
- 判断是否生成 Garden Card。
- 基于当前文件向 AI 提问。

工作台页面的职责：

- 和 AI 协作。
- 使用原满血对话工作台。
- 查看长答案、工具调用、引用来源、草稿审阅和对话上下文。
- 继续承接复杂 AI 协作流程。

本次不把工作台能力塞进知识库，也不把知识库做成第二个聊天页。知识库只在右侧提供“围绕当前文件的 AI 阅读助手”。

### 2.2 人和 AI 的边界

人负责：

- 内容是否保存。
- 标题是否正确。
- 正文是否需要改。
- 文件放到哪个文件夹。
- 是否入库。
- 是否删除。
- 是否生成 Garden Card。

AI 负责：

- 生成标题、摘要、标签等元信息。
- 建议文件夹。
- 建议可以生成哪些 Garden Card。
- 回答当前文章或选区相关问题。
- 辅助理解概念、上下文和关联。

AI 不应该：

- 自动重写正文。
- 自动保存正文。
- 自动入库。
- 自动生成 Garden Card。
- 自动创建大量文件夹。
- 把用户未确认的建议当成事实写入库。

## 3. 信息架构

目标页面保持三栏结构，但每栏职责必须清晰。

```text
知识库页面
├─ 左侧栏
│  ├─ 新建/导入入口
│  ├─ 搜索文件
│  ├─ 状态筛选
│  ├─ 一级文件夹
│  └─ 当前文件夹或搜索结果下的文件列表
│
├─ 主工作区
│  ├─ 文件标题、状态、文件夹、保存状态
│  ├─ 模式切换：浏览模式 / 编辑模式
│  ├─ 浏览模式：Markdown 只读渲染、选区捕获
│  └─ 编辑模式：标题、正文、摘要、标签、文件夹、入库、删除、Garden Card
│
└─ 右侧 AI 面板
   ├─ 当前文件
   ├─ 当前选区状态
   ├─ 对话历史
   └─ 自由提问输入框
```

左侧栏回答“我有哪些文件，它们在哪里”。主工作区回答“我正在读或改哪一篇”。右侧 AI 面板回答“我围绕这篇文章想问什么”。

## 4. 左侧栏设计

### 4.1 文件夹模型

当前阶段只支持一级文件夹。

推荐默认文件夹：

```text
Inbox
AI 产品
数据 Agent
待读
临时
```

规则：

- 用户没有选择文件夹时，默认进入 `Inbox`。
- 文件夹是人的位置感，不是标签，也不是 AI 智能视图。
- 智能视图不能伪装成文件夹。
- Obsidian 物理目录不直接决定前台文件夹结构，前台以 `folder_path` 作为逻辑分类。

### 4.2 文件夹操作

必须支持：

- 新建文件夹。
- 重命名文件夹。
- 点击文件夹后在左侧栏展示该文件夹下文件。
- 重命名后同步该文件夹下文件的 `folder_path`。
- 刷新 folders/items，不依赖前端本地猜测。

不做：

- 多级文件夹。
- 拖拽排序。
- 物理目录镜像。
- Obsidian 双向移动同步。

### 4.3 文件列表条目

每个文件条目至少展示：

- 标题。
- 状态：草稿 / 已入库。
- 所属文件夹。
- 更新时间。
- 摘要或正文预览。
- 是否可检索。
- 是否已有 Garden Card 或 Garden Card 草稿。

点击文件后，主工作区打开详情。文件夹本身不打开详情页。

### 4.4 搜索和筛选

搜索和筛选应位于左侧栏，避免额外中间列表占据阅读空间。

建议筛选：

- 全部。
- 草稿。
- 已入库。
- 可检索。
- 有卡片。

搜索结果仍使用同一文件条目样式，避免让用户在“文件夹列表”和“搜索结果列表”之间切换心智。

## 5. 主工作区设计

### 5.1 顶部区域

主工作区顶部固定展示：

- 文件标题。
- 状态：草稿 / 已入库。
- 所属文件夹。
- 可检索状态。
- Garden Card 状态。
- 保存状态。
- 模式切换：浏览模式 / 编辑模式。

模式切换应使用 segmented control，不要做成两个散落按钮。

### 5.2 模式状态

前端状态建议：

```ts
type LibraryWorkbenchMode = "browse" | "edit";
const [workbenchMode, setWorkbenchMode] = useState<LibraryWorkbenchMode>("browse");
```

默认规则：

- 点击已有文件：默认进入 `browse`。
- 点击粘贴整理、新建空白文件、导入 Markdown/Text：进入 `edit`。
- 用户手动切换后，在当前文件内保持用户选择。
- 切换文件后回到默认规则。

### 5.3 正文状态源

正文必须只有一个逻辑 source of truth。

建议状态：

```ts
selectedItemKey: string;
detail: KnowledgeLibraryItem | null;
workbenchMode: "browse" | "edit";
body: string;
dirty: boolean;
saving: boolean;
notice: string;
error: string;
```

规则：

- 打开文件时，用 `detail.body` 初始化 `body`。
- 浏览模式不修改正文，只读取同一个 `body` 并渲染 Markdown。
- 编辑模式修改正文，更新同一个 `body`。
- 任一模式保存成功后，用后端返回值刷新 `detail` 和 `body`。
- 不允许浏览模式和编辑模式各自持有互相不同步的正文副本。

### 5.4 未保存变更

需要统一处理 `dirty`：

- 修改标题、正文、摘要、标签、文件夹后标记 `dirty`。
- 切换文件、刷新列表、关闭页面前如果 `dirty=true`，需要提醒。
- 第一版可以使用浏览器 `confirm`。
- 后续再做自定义 modal。

## 6. 编辑模式

编辑模式是管理型页面，承接现有知识库能力。

必须保留：

- 标题编辑。
- 正文编辑。
- 摘要编辑。
- 标签编辑。
- 文件夹选择。
- 保存草稿。
- 保存已入库文件。
- 保存入库。
- 删除草稿。
- 删除已入库文件。
- 删除前依赖检查。
- Garden Card 生成入口。
- Garden Card 建议展示和修改。
- 文件夹建议展示和修改。

编辑模式不做选区问 AI。它的职责是“管理、整理、入库、删除、生成卡片”。

## 7. 浏览模式

浏览模式是阅读型页面，目标是让知识库像文档工作区，而不是表单管理器。

### 7.1 Markdown 渲染能力

第一版必须支持：

- 标题。
- 段落。
- 有序列表。
- 无序列表。
- 引用。
- 代码块。
- 行内代码。
- 加粗。
- 链接。

第一版不要求：

- 浏览模式富文本编辑。
- 图片拖拽。
- 多人协同。
- 块级数据库。
- Notion slash command。

### 7.2 渲染依赖

浏览模式使用现有轻量 `Markdown` 组件渲染 Markdown 字符串，不引入 `@mdxeditor/editor` 这类富文本编辑器依赖。

实施和验收必须关注：

- Vite build 是否通过。
- 浏览/编辑模式切换是否无明显卡顿。
- 浏览模式是否没有 `textarea` 或 `contenteditable`。
- Markdown 保存后是否仍是字符串。
- `npm audit --omit=dev` 是否引入新增高危问题。

依赖治理原则：

- 不为了修 audit 一次性大规模升级 React Router、Vite 或整套前端依赖。
- 如果漏洞来自新增依赖的 transitive dependency，优先评估锁定、override、补丁版本或替代方案。
- 如果风险只影响开发时构建链，需要在文档和 PR 中说明影响范围，不做盲目 `audit fix --force`。

### 7.3 保存规则

浏览模式不提供保存入口。编辑模式保存仍复用已有 update API。

已入库文件：

```ts
api.updateKnowledgeLibraryItem("capture", id, {
  title,
  body: markdown,
  folder_path,
});
```

草稿文件：

```ts
api.updateKnowledgeLibraryItem("capture_draft", id, {
  title,
  body: markdown,
  folder_path,
  summary,
  tags,
  folder_suggestion,
  card_suggestion,
});
```

规则：

- 保存正文不触发 AI 整理。
- 保存正文不重算摘要。
- 保存正文不重算标签。
- 保存正文不自动生成 Garden Card。
- 保存成功后刷新 detail。

## 8. 右侧 AI 面板

### 8.1 面板定位

右侧 AI 面板是当前文章的阅读助手，不是完整工作台对话。

展示结构：

```text
当前文件：《标题》
状态：草稿 / 已入库
选区：已选中 N 字 / 未选择文本

对话历史

输入框：问 AI...
发送按钮
```

要求：

- 不放 AI 快捷按钮。
- 不放“解释这段”“总结全文”“生成卡片”等预设动作。
- 用户可以选中文字后自由提问。
- 用户也可以不选文字直接问。
- AI 回复不直接修改正文。

### 8.2 收起规则

- 浏览模式默认展开右侧 AI 面板。
- 编辑模式可以默认收起或隐藏，但保留打开入口。
- 右侧 AI 面板必须可收起，避免遮挡阅读区。
- Hermes 左侧主导航也必须可收起，让工作区可以接近全屏。

## 9. 选区上下文设计

### 9.1 前端选区数据

前端需要形成结构化上下文：

```ts
interface ArticleSelectionContext {
  selectedText: string;
  surroundingBefore: string;
  surroundingAfter: string;
  headingPath: string[];
  selectionStart?: number;
  selectionEnd?: number;
  selectionConfidence: "offset" | "text_only" | "none";
}
```

### 9.2 选区提取策略

优先级：

1. 使用 `window.getSelection()` 从浏览模式只读 Markdown DOM 获取选中文本。
2. 用选中文本在当前 Markdown 字符串中匹配，推算前后文。
3. 能稳定匹配时传 `selectionConfidence: "offset"`。
4. 如果文本重复或渲染文本与 Markdown 源不完全一致，传 `selectionConfidence: "text_only"`。

### 9.3 前后文规则

第一版建议：

- 选区前 `1200` 字。
- 选区后 `1200` 字。
- 标题路径从选区前 Markdown 中向上查找最近的 `# / ## / ###`。
- 没有选区时，`selectedText=""`，进入全文问答模式。

### 9.4 长文策略

选区问答不应该默认把全文直接塞给模型。

有选区时优先传：

1. 文件标题。
2. 摘要。
3. 标签。
4. 文件夹。
5. 标题路径。
6. 选中文本。
7. 选区前后文。
8. 文章目录结构。

无选区时优先传：

1. 文件标题。
2. 摘要。
3. 标签。
4. 文章目录结构。
5. 与问题相关的正文片段。
6. 必要时补充正文开头和结尾。

后续如果要支持超长全文理解，应单独做“当前文章内检索”或“长上下文模型调用”策略，不把它和第一版选区问答混在一起。

## 10. 后端/API 方案

### 10.1 专用接口

新增或保留专用接口：

```http
POST /api/knowledge/library/items/{kind}/{item_id}/selection-chat
```

参数：

- `kind`: `capture` 或 `capture_draft`。
- `item_id`: 当前文件 ID。

不复用普通聊天接口，原因是选区问答需要明确当前文章、选区、前后文、标题路径和文件状态。

### 10.2 请求体

```json
{
  "question": "这段是什么意思？",
  "selected_text": "用户选中的文本",
  "surrounding_before": "选区前文",
  "surrounding_after": "选区后文",
  "heading_path": ["章节", "小节"],
  "selection_start": 120,
  "selection_end": 260,
  "selection_confidence": "offset"
}
```

规则：

- `question` 必填。
- `selected_text` 可为空。
- `surrounding_before` 和 `surrounding_after` 可为空，但有选区时应尽量提供。
- `heading_path` 可为空。
- offset 可选，第一版不能强依赖 offset。

### 10.3 返回体

```json
{
  "ok": true,
  "answer": "AI 回复内容",
  "used_selection": true,
  "citations": [
    {
      "label": "当前选区",
      "excerpt": "引用片段"
    }
  ]
}
```

错误规则：

- 文件不存在：`404`。
- 文件已归档：`409` 或按现有风格返回不可访问。
- 问题为空：`400`。
- LLM 调用失败：`502` 或现有错误包装。

### 10.4 后端职责

后端负责：

- 校验 token。
- 校验 `kind`。
- 读取 `capture` 或 `capture_draft`。
- 拒绝 archived 文件。
- 补充标题、正文、摘要、标签、文件夹、状态。
- 组装 prompt。
- 调用现有 LLM client。
- 返回结构化响应。
- 打日志，便于排查模型失败和超时。

后端不负责：

- 修改正文。
- 保存 AI 回复。
- 自动创建 Garden Card。
- 自动修改摘要或标签。
- 自动移动文件夹。

## 11. Prompt 设计

Prompt 由后端统一组装，前端不拼 prompt。

### 11.1 有选区

```text
你是 Hermes Knowledge 的文章阅读助手。
请基于当前文章、当前章节、用户选中文本及其前后文回答问题。

规则：
- 优先解释用户选中文本。
- 必须结合前后文理解，不要孤立解释。
- 不要编造文章没有的信息。
- 如果用户问题超出文章内容，明确说明。
- 不要修改正文。
- 不要主动生成摘要、标签或 Garden Card。
```

### 11.2 无选区

```text
你是 Hermes Knowledge 的文章阅读助手。
用户没有选择具体文本，请基于当前文章整体回答问题。

规则：
- 回答必须基于当前文章内容。
- 不要自动总结全文，除非用户明确要求。
- 如果文章没有相关信息，直接说明。
- 不要修改正文。
- 不要主动生成摘要、标签或 Garden Card。
```

## 12. 与现有流程兼容

### 12.1 创建入口

只保留必要入口：

- 粘贴整理。
- 新建空白文件。
- 导入 Markdown/Text 文件。

说明：

- 粘贴整理：适合用户已有一段材料，希望 AI 生成标题、摘要、标签、文件夹建议和 Garden Card 建议。
- 新建空白文件：适合用户从零写笔记。
- 导入 Markdown/Text 文件：适合把外部文档作为文件导入。

不要再保留“导入 Markdown 文本”这种和粘贴整理/导入文件重复的入口。

### 12.2 AI 元信息生成

整理成草稿时，AI 只生成元信息和建议：

- 标题。
- 摘要。
- 标签。
- 文件夹建议。
- Garden Card 建议。

不再让 AI 返回“整理后的正文”。正文由用户自己编辑。这样避免长文输出不完整、格式被模型改坏、用户无法判断 AI 改了什么的问题。

### 12.3 保存入库

保存入库继续复用 Capture -> LibraryAgent -> RAG 链路。

入库规则：

- 用户选择现有文件夹时，使用用户最终选择的 `folder_path`。
- AI 建议新建文件夹且用户采纳时，先创建文件夹，再保存入库。
- 用户修改为另一个现有文件夹时，尊重用户最终选择。
- 入库后刷新文件夹、文件列表和当前详情。

### 12.4 已入库文件保存

已入库文件保存必须验证：

- 点击保存有 loading。
- 保存成功有 notice。
- 保存失败有 error。
- 标题、正文、`folder_path` 能写回。
- 刷新页面后内容保留。
- 不触发重新整理或 Garden Card 生成。

### 12.5 删除

草稿和已入库文件都支持删除。

草稿删除：

- `draft_ready` 草稿可删除或标记 abandoned。
- 已入库草稿不能被当作普通草稿删除，应引导用户处理对应已入库文件。

已入库文件删除前必须检查依赖：

- Garden Card。
- Garden Draft。
- committed draft。
- RAG/索引数据。
- Obsidian 文件。
- 其他文件引用。

删除策略必须在 UI 中说清楚：

- 是物理删除。
- 是归档隐藏。
- 是阻止删除。
- 还是同步清理索引和关联。

不能静默留下不可解释的孤儿数据。

### 12.6 Garden Card

本次不自动生成 Garden Card，但保留入口。

Garden Card 建议应从“是否生成卡片”升级为“建议生成哪些卡”：

```ts
interface CardSuggestion {
  title: string;
  card_kind?: string;
  reason: string;
  source_excerpt?: string;
}
```

后续调用 Garden Card Agent 时，可以把这些建议作为输入，辅助它判断生成哪些卡。

## 13. 前端改动方案

### 13.1 文件位置

实际前端源码：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/components/Markdown.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/lib/api.ts
/Users/pipi/Downloads/hermes-agent-main/web/src/App.tsx
```

### 13.2 建议组件拆分

`KnowledgeLibraryPage.tsx` 已经较大，后续不应继续无限堆逻辑。建议逐步抽取：

```text
web/src/pages/knowledge-library/
├─ LibrarySidebar.tsx
├─ EditModePane.tsx
├─ BrowseModePane.tsx
├─ ArticleAIPanel.tsx
├─ selectionContext.ts
└─ types.ts
```

职责：

- `LibrarySidebar.tsx`: 文件夹、搜索、筛选、文件列表。
- `EditModePane.tsx`: 现有编辑、入库、删除、Garden Card。
- `BrowseModePane.tsx`: Markdown 只读渲染、选区捕获。
- `ArticleAIPanel.tsx`: 右侧 AI 自由问答。
- `selectionContext.ts`: 从 Markdown 和选区中提取上下文。
- `types.ts`: 共享类型。

实施原则：

- 如果当前页面已经实现某些能力，先审查缺口，再局部修复。
- 不为了“拆组件”一次性重写所有状态。
- 每抽一个组件都要保持行为不变并跑 build。

### 13.3 API client

`web/src/lib/api.ts` 需要保留或补齐：

```ts
export interface KnowledgeLibrarySelectionChatRequest {
  question: string;
  selected_text?: string;
  surrounding_before?: string;
  surrounding_after?: string;
  heading_path?: string[];
  selection_start?: number;
  selection_end?: number;
  selection_confidence?: "offset" | "text_only" | "none";
}

export interface KnowledgeLibrarySelectionChatResponse {
  ok: boolean;
  answer: string;
  used_selection: boolean;
  citations?: Array<{ label: string; excerpt: string }>;
}
```

以及：

```ts
selectionChatKnowledgeLibraryItem(
  kind: KnowledgeLibraryItemKind,
  itemId: string,
  body: KnowledgeLibrarySelectionChatRequest,
): Promise<KnowledgeLibrarySelectionChatResponse>
```

## 14. 后端改动方案

### 14.1 文件位置

实际后端路由目前在：

```text
/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py
```

定制根目录通过：

```text
HERMES_CUSTOM_ROOT=/Users/pipi/hermes-custom
```

连接到：

```text
/Users/pipi/hermes-custom/data/captures.db
/Users/pipi/hermes-custom/agents/
/Users/pipi/hermes-custom/bridge/
```

### 14.2 后端实现原则

优先复用现有 Capture、Library、Garden、RAG 能力，不新建平行知识库。

需要补齐或验证：

- `capture` 详情能读到完整正文。
- `capture_draft` 详情能读到完整正文。
- `update` 能写回标题、正文、文件夹。
- `delete` 能走依赖检查。
- `selection-chat` 能根据 `kind` 读取正确对象。
- archived 文件不会进入正常浏览和问答。
- LLM 调用失败时返回前端可展示错误。

## 15. 数据契约

### 15.1 文件类型

```ts
type KnowledgeLibraryItemKind = "capture" | "capture_draft";
```

前端文案：

- `capture_draft`: 草稿。
- `capture`: 已入库。
- archived: 默认不展示。

### 15.2 文件字段

列表和详情至少需要：

```ts
interface KnowledgeLibraryItem {
  id: string;
  kind: "capture" | "capture_draft";
  title: string;
  summary?: string;
  preview?: string;
  body?: string;
  folder_path?: string;
  tags?: string[];
  source?: string;
  source_type?: string;
  status?: string;
  file_path?: string;
  raw_path?: string;
  word_count?: number;
  created_at?: string;
  updated_at?: string;
  indexed?: boolean;
  folder_suggestion?: unknown;
  card_suggestion?: unknown;
}
```

### 15.3 文件夹字段

```ts
interface KnowledgeLibraryFolder {
  name: string;
  count: number;
}
```

文件夹名是逻辑 `folder_path`，不是 Obsidian 物理路径。

## 16. 分阶段实施计划

### Phase 0：事实核查和基线保护

目标：确认要改的是正在运行的 UI 和 API，避免再次改错目录。

工作：

- 确认 9119 进程路径。
- 确认 `/knowledge-library` 使用的前端文件。
- 确认 web build 命令。
- 确认后端 route 所在文件。
- 确认当前 Markdown 渲染、selection-chat、folder、delete、update 是否已存在。
- 记录当前 git diff，避免覆盖用户已有改动。

验证：

- `lsof -nP -iTCP:9119 -sTCP:LISTEN` 能定位进程。
- `/Users/pipi/Downloads/hermes-agent-main/web` 能 `npm run build`。
- 浏览器能打开 `/knowledge-library`。

### Phase 1：左侧栏和模式职责收敛

目标：把知识库页面的心智稳定下来。

工作：

- 左侧栏承载文件夹、搜索、筛选、文件列表。
- 文件夹点击后展示该文件夹下文件。
- 主工作区只展示当前文件详情。
- 浏览/编辑模式切换明确。
- AI 面板可收起。
- Hermes 左侧主导航可收起。

验证：

- 点击文件夹能看到文件。
- 搜索结果能点击打开。
- 切换文件不丢详情。
- 收起侧栏后主工作区可全屏使用。

### Phase 2：浏览模式 Markdown 只读渲染

目标：让浏览模式可作为主要阅读入口。

工作：

- 校验轻量 `Markdown` 渲染组件对标题、列表、引用、代码块等常见语法的展示。
- 修正样式和高度，避免和页面其他元素重叠。
- 统一正文 source of truth。
- 确认浏览模式不出现正文编辑器、保存按钮或 `contenteditable`。

验证：

- Markdown 标题/列表/引用/代码块渲染正确。
- 浏览/编辑切换没有明显卡顿。
- 编辑模式修改正文后保存，刷新页面内容保留。
- 保存后没有触发摘要/标签/Garden Card 重算。

### Phase 3：选区上下文和 AI 面板

目标：用户选中一段文字后，右侧能明确知道选区。

工作：

- 抽出或完善 `selectionContext`。
- 显示选中字数。
- 取消选区后显示未选择。
- AI 面板只保留自由输入。
- 对话历史按当前文件隔离，第一版切换文件可清空。

验证：

- 选中正文后右侧显示 `已选中 N 字`。
- 取消选区后恢复 `未选择文本`。
- 不选区也能输入问题。

### Phase 4：selection-chat 后端和前端串联

目标：打通选区问 AI。

工作：

- 校验 API client 请求字段。
- 校验后端 route 参数和错误处理。
- 后端补充文章上下文并组装 prompt。
- 前端显示 loading、answer、error。

验证：

- 有选区请求包含 `selected_text`、前后文、标题路径。
- 无选区请求 `used_selection=false`。
- AI 回复显示在右侧。
- LLM 失败时 UI 不假装成功。

### Phase 5：原流程回归

目标：保证新增浏览模式不破坏旧流程。

工作：

- 跑粘贴整理。
- 跑新建空白文件。
- 跑导入 Markdown/Text。
- 跑保存草稿。
- 跑保存入库。
- 跑已入库文件保存。
- 跑草稿删除。
- 跑已入库文件删除依赖检查。
- 跑 Garden Card 生成入口。

验证：

- 每条流程有 UI 反馈。
- 后端返回结果和前端状态一致。
- 刷新后状态持久。

## 17. 测试计划

### 17.1 前端构建

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm run build
```

验证：

- TypeScript 通过。
- Vite build 通过。
- 浏览模式不再生成独立富文本编辑器 chunk。

### 17.2 依赖审计

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm audit --omit=dev
```

处理原则：

- 记录新增依赖带来的风险。
- 不做破坏性 `audit fix --force`。
- 如需 override，单独说明原因和影响。

### 17.3 后端测试

建议覆盖：

- `selection-chat` 空问题返回 400。
- `selection-chat` 找不到 capture 返回 404。
- `selection-chat` 找不到 capture_draft 返回 404。
- archived capture 不可问答。
- 有选区时 prompt 包含 selected text 和 surrounding context。
- 无选区时 prompt 走全文模式。
- 草稿 update 保存正文。
- 已入库 update 保存正文。
- 删除依赖检查覆盖 Garden/RAG/Obsidian 关联。

可优先跑：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
pytest tests/hermes_cli/test_web_server.py -q
```

如涉及 `/Users/pipi/hermes-custom` 后端 agent/bridge 变更，再补跑：

```bash
cd /Users/pipi/hermes-custom
pytest tests/test_api_routes.py tests/test_capture_draft_workflow.py tests/test_garden_bridge.py -q
```

### 17.4 浏览器 smoke

必须用浏览器或 Playwright 模拟真人路径：

1. 打开 `http://127.0.0.1:9119/knowledge-library`。
2. 收起 Hermes 左侧主导航。
3. 在知识库左侧搜索文件。
4. 点击一个文件夹，确认文件列表出现。
5. 点击一个草稿文件，默认进入浏览模式。
6. 切到编辑模式，修改标题或文件夹并保存。
7. 切回浏览模式，确认 Markdown 只读渲染且没有 textarea/contenteditable/保存按钮。
8. 再回编辑模式修改 Markdown 正文并保存，刷新页面确认内容保留。
9. 选中一段文字，右侧显示选中字数。
10. 输入“这段是什么意思？”，确认 AI 返回。
11. 取消选区，输入“这篇文章的核心判断是什么？”，确认 AI 返回。
12. 删除草稿文件，确认列表刷新。
13. 对已入库文件触发删除，确认依赖检查出现。
14. 触发 Garden Card 生成入口，确认旧链路可用。

## 18. 发布和回滚

### 18.1 发布前门禁

必须满足：

- 前端 build 通过。
- 后端相关测试通过。
- 浏览器 smoke 通过。
- 没有新增 critical runtime 依赖风险。
- 文案统一为“已入库”，不出现“已发布”。
- 未保存变更不会被静默丢弃。
- AI 面板和主导航都可收起。

### 18.2 回滚策略

如果浏览模式编辑器出现严重问题：

- 保留编辑模式旧流程。
- 暂时把浏览模式降级为只读 Markdown 预览。
- 暂时隐藏右侧 AI 面板。
- 不删除已经写入的 folder、draft、capture 数据。

如果 selection-chat 出现问题：

- 保留浏览/编辑功能。
- 右侧 AI 面板显示“暂不可用”。
- 不影响保存、入库、删除、Garden Card。

如果依赖风险不可接受：

- 移除新增编辑器依赖。
- 使用 textarea + preview 作为临时兜底。
- 保持 Markdown 字符串数据格式不变。

## 19. 明确非目标

本次不做：

- AI 快捷按钮。
- 自动改写正文。
- 自动生成摘要/标签。
- 自动生成 Garden Card。
- 多文件联合问答。
- Obsidian 双向实时同步。
- 物理目录镜像。
- 多级文件夹。
- 协同编辑。
- 块级数据库。
- 替换原满血对话工作台。
- 重建一套新的知识系统。

## 20. 执行 checklist

实施前：

- [x] 确认 9119 实际运行源码路径。
- [x] 确认当前 git diff，避免覆盖用户改动。
- [x] 确认 `web/package.json` 依赖状态。
- [x] 确认 `KnowledgeLibraryPage.tsx` 当前功能缺口。
- [x] 确认 `web_server.py` 当前 route 缺口。
- [x] 确认 captures.db 和 Obsidian vault 路径。

实施中：

- [x] 每次组件抽取后跑 build。
- [x] 每次 API 修改后跑对应后端测试。
- [x] 不改无关页面。
- [x] 不删除旧工作台能力。
- [x] 不新增重复入口。

实施后：

- [x] 前端 build 通过。
- [x] 后端知识库相关测试通过。
- [x] 浏览器 smoke 通过。
- [x] 依赖审计有记录。
- [x] 删除依赖行为可解释。
- [x] Garden Card 真实生成链路通过临时 capture 实跑；知识库详情页 `生成卡片` 按钮也已实跑，并已清理临时 card、draft、capture、ledger、write job 和 Obsidian 文件。
- [x] 文档更新实际完成状态。

## 21. 推荐执行结论

建议按“事实核查 -> 缺口修复 -> 端到端验收”的方式推进，而不是再次重写整个知识库页面。

当前最安全路径是：

1. 以 `/Users/pipi/Downloads/hermes-agent-main/web` 和 `/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py` 为真实 UI/API 改造对象。
2. 以 `/Users/pipi/hermes-custom` 作为数据、Agent、bridge、测试和文档定制根目录。
3. 保留原工作台和 Garden Card 旧能力。
4. 把知识库收敛成左侧文件定位、中间文档阅读/编辑、右侧当前文章 AI 的三栏工作区。
5. 每阶段都用 build、测试和浏览器 smoke 证明没有回归。

这样可以避免再次出现“新 UI 看起来更乱、旧功能迁移不完整、实际 pipeline 没跑通”的问题。

## 22. 当前实施状态与验收记录

更新时间：2026-06-20

### 22.1 已实施内容

当前实现已经落在真实运行的 Hermes Dashboard：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/components/Markdown.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/lib/api.ts
/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py
```

已完成：

- 知识库页面收敛为左侧文件夹/文件列表、中间文档浏览/编辑、右侧文章 AI 面板。
- 左侧支持搜索、状态筛选、一级文件夹、文件夹展开后显示文件。
- 新建入口只保留 3 个：粘贴整理、新建空白文件、导入 Text / Markdown 文件。
- 文件夹支持新建和重命名；重命名会同步草稿和已入库文件的 `folder_path`。
- 已入库状态统一展示为“已入库”，不再使用“已发布”文案。
- 点击已有文件默认进入浏览模式；新建、粘贴、导入进入编辑/整理界面。
- 浏览模式使用轻量 Markdown 渲染，只读展示正文并捕获选区，不提供正文编辑。
- 编辑模式保留标题、正文、文件夹、保存草稿、入库、删除、生成卡片入口，保存仍写回 Markdown 字符串。
- 右侧 AI 面板支持收起；Hermes 主侧栏支持收起。
- AI 面板只保留自由提问，不提供 AI 快捷按钮。
- 选区问 AI 会向后端传 `selected_text`、前后文、标题路径和 selection confidence。
- 无选区时仍可基于当前文章提问。
- 草稿删除走现有 `discard_capture_draft`，语义是标记为 `abandoned`。
- 已入库文件删除前展示依赖说明，实际行为是归档 capture，保留 Obsidian 文件、数据库行、卡片、引用和历史记录。
- 文件详情和粘贴/导入编辑态已经接入未保存变更保护；切换文件、切换文件夹、进入新建/导入或离开页面前会提示确认。

### 22.2 浏览器 smoke 结果

真实浏览器地址：

```text
http://127.0.0.1:9119/knowledge-library
```

已通过：

- 页面渲染：Hermes nav、知识库三栏、文件夹树、文件列表、详情区均可见。
- Hermes 主侧栏收起/展开：宽度从展开态缩小到折叠态，主工作区随之扩展。
- 右侧 AI 面板收起/展开：收起后 `AI 对话` 面板消失，点击 `展开 AI 面板` 后恢复。
- 新建菜单：只显示 `粘贴整理`、`新建空白文件`、`导入 Text / Markdown 文件` 三个入口。
- 导入 Markdown：上传 `codex-smoke-import.md` 后进入编辑界面，标题和正文自动填入。
- 文件夹新建：通过 UI 新建 `codex-smoke-folder-140859` 成功。
- 文件夹重命名：通过 UI 重命名为 `codex-smoke-folder-renamed-140859` 成功。
- 临时草稿打开：通过左侧搜索打开 `Codex Smoke 临时草稿 140859`。
- 浏览/编辑切换：同一文件可在浏览模式和编辑模式间切换。
- 保存草稿：修改标题为 `Codex Smoke 已保存 140859`，修改正文后点击保存成功。
- 刷新持久化：刷新页面后再次搜索打开，正文仍包含 `保存后项目`。
- 选区问 AI：选中 `选区问 AI 验证文本` 后右侧显示已选中，发送问题后 mock API 返回 `mock: 已基于选区回答`。
- 删除草稿：点击删除并确认后，前端显示 `已删除草稿。`，搜索列表不再出现该草稿。
- 数据库删除语义：临时草稿被置为 `abandoned`，符合现有删除草稿语义。
- 未保存提醒：导入临时 Markdown 后点击 `所有文件` 会弹出 `当前文件有未保存更改，确定离开吗？`；取消后仍停留在编辑内容，确认后可离开。
- Garden Card 真实链路：创建临时 capture `codex-garden-smoke-20260620142235` 后，通过真实 Dashboard token 调用 `/api/knowledge/garden-drafts`，返回 `commit_garden_draft` success，生成 card_id `57cf93daddc7`、draft_id `6fad55b955ce` 和 Obsidian 文件 `garden/Codex Garden Smoke 临时卡片-57cf93daddc7.md`。
- Garden Card 前端按钮：创建临时 capture `codex-garden-ui-smoke-20260620142428` 后，在知识库页面搜索打开并点击 `生成卡片`，页面返回成功提示 `已存入知识卡片：《前端按钮触发卡片生成与清理流程》`，生成 card_id `5310d7db8693`、draft_id `a880ebfdd717` 和 Obsidian 文件 `garden/前端按钮触发卡片生成与清理流程-5310d7db8693.md`。

测试产生的 `codex-smoke-*`、`codex-garden-smoke-*` 临时文件夹配置、临时草稿行、临时 capture、临时 Garden Card/Draft、ledger/write job 记录和 Obsidian 文件已清理。

### 22.3 自动化验证结果

前端构建：

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm run build
```

结果：通过。

知识库相关后端测试：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest -o addopts='' tests/hermes_cli/test_web_server.py -q -k 'knowledge_library or selection_chat or capture_draft'
```

结果：`6 passed, 152 deselected, 1 warning`。

全量 `test_web_server.py`：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
.venv/bin/python -m pytest -o addopts='' tests/hermes_cli/test_web_server.py -q
```

结果：`155 passed, 3 failed, 1 warning`。

失败项均为 PTY WebSocket 测试：

- `TestPtyWebSocket.test_streams_child_stdout_to_client`
- `TestPtyWebSocket.test_client_input_reaches_child_stdin`
- `TestPtyWebSocket.test_resize_escape_is_forwarded`

这 3 项和知识库页面、selection-chat、文件夹、草稿、入库 API 无直接关系。

依赖审计：

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm audit --omit=dev
```

结果：仍有 1 个 low severity：

- `esbuild 0.27.3 - 0.28.0`
- 来源：Vite 开发服务器链路
- 影响：Windows dev server arbitrary file read advisory
- 当前处理：记录为残留风险，不执行破坏性的依赖大升级。

### 22.4 当前残留风险

- `dashboard-plugins/example/dist/index.js` 在浏览器控制台报 404，这是 example 插件资源缺失，当前不影响知识库主流程。
- Garden Card 真实生成已用临时 capture 验收；该接口当前会自动从 Garden Draft commit 到正式 Garden Card，后续如需“只生成草稿、不入正式卡片”，应单独调整 `/api/knowledge/garden-drafts` 的后端契约。
- 浏览模式富文本编辑已移除，`@mdxeditor/editor` 依赖已卸载；浏览/编辑切换验证约 18ms/23ms。
- 未保存提醒目前使用浏览器原生 confirm，后续可替换为 Hermes 风格 modal。

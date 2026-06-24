# Hermes 知识库 UI 精简、侧栏拖拽与检索过程改版方案

日期：2026-06-22

## 1. 背景

当前 Hermes 知识库和满血对话工作台已经具备核心功能：文件夹、文件列表、知识库搜索、右侧 AI 对话、联网检索过程、来源展示、草稿审阅、对话历史和 Inspector。

问题不在于功能缺失，而在于信息架构和交互密度过高：

- 左侧知识库栏同时展示“文件夹展开文件”和“当前范围文件列表”，形成重复。
- 右侧 AI 面板的“检索过程”把模式、计划、来源、意图、查询词、采用依据、候选来源、搜索状态、warning 全部堆在一个调试式卡片里。
- 满血对话工作台顶栏像产品介绍页，占用大量垂直空间，但高频操作只需要连接状态、草稿入口和详情开关。
- `去知识库整理` 入口和当前知识库页面能力重复，应删除。
- 页面大量使用 `backdrop-blur`、复杂 shadow、多个滚动容器和 streaming 状态重渲染，容易造成拖拽和流式输出时卡顿。

本方案目标是在保全所有功能的前提下，做最大幅度的 UI 精简和动效/流畅度优化。

## 2. 真实代码落点

实际运行页面不在 `/Users/pipi/hermes-custom/dashboard`。该目录的 Next dashboard 已经标记为 deprecated。

当前 9119 dashboard 进程来自：

```text
/Users/pipi/Downloads/hermes-agent-main/web
```

主要前端改动文件：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeChatPage.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/knowledge-chat/WorkbenchSidebar.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/knowledge-chat/Inspector.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/pages/knowledge-chat/GatewayChat.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/components/ToolCall.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/index.css
```

可能新增的共享组件：

```text
/Users/pipi/Downloads/hermes-agent-main/web/src/components/ResizableRail.tsx
/Users/pipi/Downloads/hermes-agent-main/web/src/components/ResearchTracePanel.tsx
```

后端和数据合同相关位置：

```text
/Users/pipi/hermes-custom/bridge/research_bridge.py
/Users/pipi/Downloads/hermes-agent-main/web/src/lib/api.ts
```

原则：前端主改 `hermes-agent-main/web`；只有当 trace 字段不足时，才同步调整 `hermes-custom` 的 research trace 输出。

## 3. 设计原则

### 3.1 人主导，AI 辅助

Hermes 不是把用户推入 AI 黑箱，而是一个人主导的知识工作台：

- 人负责捕捉、命名、分类、审阅和确认。
- AI 在右侧辅助整理、关联、检索和复用。
- 文件夹、标题、状态、来源和入库过程必须可见。

### 3.2 功能保全，入口减法

删除重复入口，不删除能力：

- 删除 `去知识库整理` 卡片入口，但保留 `/knowledge-library?mode=paste` 能力。
- 删除重复文件栏，但保留文件搜索、筛选、打开文件和文件夹展开。
- 精简顶栏文案，但保留连接状态、草稿入口、Inspector 开关。

### 3.3 展示用户需要的过程，不展示调试噪音

联网检索过程要实时、可信、可展开，但默认不应像 debug dump：

- 默认展示：当前阶段、采用来源数、独立来源数、证据状态、采用依据。
- 折叠展示：查询词、候选网页、线索来源、未采用来源、provider status、warnings。

### 3.4 动画只服务操作反馈

避免大面积装饰动画。动效原则：

- 拖拽时必须流畅。
- streaming 时内容逐步更新但不抖动。
- hover/active 只动 `opacity`、`transform`、`background-color`。
- 不用动画改变布局尺寸。
- 尊重 `prefers-reduced-motion`。

### 3.5 先统一工作区壳层，再优化局部组件

当前知识工作区存在多层 chrome：

- 全局 App 左侧导航。
- 全局 `PageHeaderProvider` 顶栏。
- `KnowledgeChatPage` 内部的大标题说明卡片。
- `KnowledgeLibraryPage` 内部自己的知识库侧栏和右侧 AI 顶部。

这会让页面看起来像多个应用叠在一起，而不是一个稳定工作台。后续改版优先级应是：

1. 统一 `/knowledge-library`、`/knowledge-chat`、`/cards-board` 的知识工作区 chrome。
2. 压缩全局 header 和页面内部 header 的重复表达。
3. 再处理左栏、右栏、检索过程等局部组件。

原则：工作区页面不做 landing-style 大标题，不写长说明；只保留当前任务需要的工具条、状态和操作。

### 3.6 用分割线和密度组织信息，减少浮动卡片

当前页面大量使用 `rounded-xl`、`shadow`、`backdrop-blur`、半透明背景和嵌套 card。视觉上每个区域都在争层级，也会增加 GPU 合成成本。

新版视觉方向：

- 外层保留一个工作区框架。
- 内部区域用分割线、标题密度和间距建立层级。
- 只有可重复列表项、对话气泡、弹窗和真正需要框定的工具面板使用卡片。
- 不在卡片里再套大卡片。
- 滚动区域避免 `backdrop-blur`。

## 4. 视觉与交互目标

### 4.0 知识工作区全局壳层

适用路由：

```text
/knowledge-library
/knowledge-chat
/capture-review
/cards-board
```

目标：

- 全局左侧导航默认更窄，减少对知识工作区的侵占。
- 全局 `PageHeaderProvider` 保持 48-56px 的工具条高度。
- 页面内部不再出现大标题说明卡片。
- 页面标题、连接状态、待审草稿、详情/Inspector 开关整合在同一行。
- 路由内部主区域使用稳定三栏或两栏布局，不再用多个浮动大卡片拼接。

满血对话页当前的大 header 卡片应被替换为紧凑工具条：

```text
满血对话        已连接    待审草稿 21    详情
```

知识库页当前不需要再额外写“知识库”大介绍。它应该直接进入文件工作区：

```text
知识库          新建    导入    刷新
```

### 4.1 知识库左侧栏

当前问题：

- 顶部有文件夹树，下面又有 `INBOX/当前范围` 文件列表。
- 同一批文件在两个位置出现。
- 搜索文件和文件夹视图不是同一个浏览模型。
- 左侧栏实际承担了导航、文件浏览、搜索结果三种职责，但现在是把三者竖向堆叠。

目标状态：

- 左侧只有一个主列表区域。
- 左侧栏使用明确状态模型，而不是同时展示所有内容：
  - `browse`：处理队列 + 文件夹树 + 文件夹展开文件。
  - `search`：搜索结果。
  - `filter`：智能视图或状态筛选结果。
- 保留状态筛选：全部、草稿、已入库、可检索、有卡片。
- 保留刷新、新建文件夹、重命名文件夹、打开文件。

推荐布局：

```text
知识库                         刷新
搜索文件...
全部 草稿 已入库 可检索 有卡片

处理队列
  所有文件
  草稿箱
  最近更新

文件夹
  Inbox
    文件 A
    文件 B
  AI 产品
    文件 C
```

搜索态：

```text
知识库                         刷新
搜索文件: coding agent
全部 草稿 已入库 可检索 有卡片

搜索结果 7
  文件 A
  文件 B
  文件 C
```

删除项：

- 当前左栏下半段永久存在的 `selectionTitle + visibleItems` 文件列表。
- 重复的第二个刷新按钮。

搜索、筛选和浏览的关系：

- 搜索框有值时进入 `search`，主列表只显示搜索结果。
- 点击 `草稿箱`、`最近更新` 或状态筛选时进入 `filter`，主列表只显示对应结果。
- 清空搜索或点击文件夹时回到 `browse`。
- 搜索结果项必须显示所属 folder path，避免用户丢失位置感。

### 4.2 右侧 AI 面板

当前问题：

- `检索过程` 面板过度暴露内部字段。
- 查询词以多个大 pill 堆叠，视觉重量太高。
- provider status 和 warnings 混在主视图中。
- 用户难以一眼理解“现在查到哪一步、采用了哪些来源、证据够不够”。
- AI 面板长期占据与文件内容相近的视觉重量，但在知识库浏览场景里，主角应该是当前文件。

目标状态：

右侧 AI 面板支持三态，而不是只有开关和拖拽宽度：

- `collapsed`：窄条，只显示 AI 图标、当前状态和未读/运行提示。
- `assist`：默认状态，显示上下文、聊天、检索过程和输入框。
- `focus`：临时放大，用于长问答、长答案审阅或来源核查。

三态关系：

```text
collapsed <-> assist <-> focus
```

拖拽宽度只服务 `assist` 和 `focus`，不替代这三个明确状态。

右侧 AI 回答中的检索过程拆成三个信息模型：

1. `ResearchProgress`：pending 状态使用，实时展示阶段。
2. `EvidenceSummary`：完成状态使用，展示采用来源、独立来源和证据状态。
3. `ResearchAuditDetails`：折叠审计详情，展示 query、candidate、clue、rejected、provider status、warnings。

默认展示：

```text
检索过程
规划 -> 本地 -> 搜网页 -> 筛来源 -> 读网页 -> 生成

采用 2 · 独立来源 2 · 线索 3 · 未采用 1 · 证据充分

采用依据
[W1] Clarivate Web of Science...
[W2] Cambridge University Press...
```

折叠详情：

```text
查询词
- Journal of Economic History impact factor JCI ranking
- JEH economic history journal reputation review

候选网页
...

线索来源
...

未采用
...

搜索状态
search_backend: dashscope
extract_backend: local
search_elapsed_ms: 15485

Warnings
...
```

字段映射：

- `research_trace.source_counts.adopted`
- `research_trace.source_counts.adopted_independent_domains`
- `research_trace.source_counts.candidate`
- `research_trace.source_counts.clue`
- `research_trace.source_counts.rejected`
- `research_trace.evidence_status`
- `research_trace.evidence_sources`
- `research_trace.candidate_sources`
- `research_trace.clue_sources`
- `research_trace.rejected_sources`
- `research_trace.provider_status`
- `message.warnings`

如果后端没有 `adopted_independent_domains`，前端可临时按 `source.domain || host(url)` 去重，但最终应以后端 trace 为准。

等待中和完成后的展示不同：

- 等待中：突出当前阶段和刚发现的来源，给用户“正在推进”的反馈。
- 完成后：弱化过程，突出可引用依据和证据质量。
- 失败或证据不足：明确显示失败发生在哪一步，不把失败包装成正常回答。

### 4.3 满血对话工作台侧栏

当前问题：

- `当前对话` 是大卡片。
- `去知识库整理` 卡片重复。
- `对话历史`、草稿筛选、搜索和知识条目列表挤在一起。

目标状态：

- 删除 `去知识库整理` 卡片。
- `当前对话` 压缩成紧凑 session row。
- `新对话` 改成顶部按钮。
- `对话历史` 保留，但默认更紧凑。
- 草稿/知识条目列表压缩卡片高度。
- 保留：
  - 当前对话
  - 新对话
  - 对话历史
  - 草稿筛选
  - 搜索知识条目
  - 审阅草稿/回答草稿

推荐布局：

```text
当前对话                 新建
已连接 · Session 75f842fd

对话历史
  最近会话 1
  最近会话 2

待处理 / 全部 / 已入库
搜索知识条目...

知识条目
  草稿 A
  回答 B
```

### 4.4 满血对话顶栏

当前问题：

- `Hermes Knowledge`、大标题、长说明和多个 pill 占据太多空间。
- 顶栏更像 landing page，而不是工作台工具栏。

目标状态：

高度控制在 52-64px。

保留：

- 标题：`满血对话`
- 连接状态
- 待审草稿入口
- 详情/Inspector 开关

收起或删除：

- `Hermes Knowledge` eyebrow
- 长说明文案
- Session 长 pill 默认隐藏，移到 tooltip 或 Inspector
- 页面内部大 header card

推荐布局：

```text
满血对话        已连接    待审草稿 21    详情
```

注意：这里不只是压缩 `KnowledgeChatPage` 自己的 header，还要避免和全局 `PageHeaderProvider` 形成双顶栏。最终应只有一个工作区工具条。

### 4.5 侧栏拖拽

需要支持：

- 知识库左栏拖拽宽度。
- 知识库右侧 AI 面板拖拽宽度。
- 满血对话工作台左侧栏拖拽宽度。
- 满血对话 Inspector 拖拽宽度。

实现约束：

- 使用 Pointer Events。
- 使用 `setPointerCapture`。
- 移动过程中通过 `requestAnimationFrame` 节流。
- 尽量写 CSS custom property，而不是每个 pointermove 都触发大 React tree 重渲染。
- 宽度写入 `localStorage`。
- 双击拖拽条恢复默认值。
- 移动端关闭拖拽，保留收起/展开。

建议宽度：

```text
knowledgeLibrary.leftWidth: default 360, min 260, max 460
knowledgeLibrary.aiWidth: default 380, min 320, max 560
knowledgeChat.sidebarWidth: default 280, min 240, max 380
knowledgeChat.inspectorWidth: default 320, min 280, max 520
```

拖拽条视觉：

- 宽度 6-8px。
- 默认透明。
- hover 显示淡绿色线。
- active 显示更强主色。
- 不使用明显大把手，避免界面变重。

拖拽与 AI 三态的关系：

- `collapsed` 态宽度固定，不参与拖拽。
- `assist` 态使用用户保存的默认宽度。
- `focus` 态使用更大宽度，也可拖拽。
- 双击拖拽条恢复当前态的默认宽度。

## 5. 性能优化方案

### 5.0 性能目标

本次 UI 动画优化的目标不是“更多动画”，而是让高频操作稳定：

- 拖拽侧栏时跟手，无明显掉帧。
- streaming 检索过程时页面不跳、不抖、不整体重绘。
- 文件列表滚动顺滑。
- AI 回答变长时输入框和主布局不被挤压。
- 多个 loading/spinner/pulse 不同时抢视觉注意力。

性能验收以用户感知为主，Chrome Performance 录制为辅。

### 5.1 减少高成本样式

现状里多个主容器使用：

- `backdrop-blur`
- 大 shadow
- 半透明背景叠层

优化：

- 主工作区只保留 1 层背景和边框。
- 左右栏去掉重 shadow。
- 大面积滚动容器避免 `backdrop-blur`。
- 保留 Hermes Paper 质感，但减少叠层。

### 5.2 降低 streaming 重渲染成本

当前右侧 selection chat streaming 每个 progress event 都：

```text
setChatMessages(messages => messages.map(...))
```

短期优化：

- `ArticleResearchProcess` 使用 `React.memo`。
- `ResearchSourceGroup` 使用 `React.memo`。
- `FileListItem`、`FolderNode` 使用 `React.memo`。
- `applySelectionChatStreamEvent` 只更新必要字段，避免每次重建过大的对象。
- trace progress 事件合并渲染，建议 120-200ms flush 一次。
- Markdown 答案渲染和检索 trace 渲染分离，trace 更新不触发已生成 Markdown 重渲染。
- 来源列表只有在 sources 数组变化时重算，不随 stage 文案变化重算。

中期优化：

- 将 pending assistant message 的 trace 状态拆成独立 state map：
  - `messageBodies`
  - `messageResearchTraces`
- 或者在组件内部用 reducer 更新当前 pending message，减少整条消息列表重绘。

### 5.2.1 拖拽渲染合同

拖拽侧栏是最高频交互，必须避免每个 pointermove 都触发 React 大树更新。

实现合同：

- pointermove 中只更新 CSS custom property，例如 `--knowledge-left-width`。
- 使用 `requestAnimationFrame` 合并写入。
- pointerup 时再把最终宽度写入 React state 和 `localStorage`。
- 拖拽过程中关闭 transition，避免“滞后跟手”。
- 拖拽条只改变相邻 grid track，不重算列表内容。

禁止：

- 在 pointermove 中 `setState` 导致整个 `KnowledgeLibraryPage` 重渲染。
- 拖拽时动态 mount/unmount 左右栏内容。
- 用动画过渡每一帧宽度。

### 5.3 工具调用计时器优化

当前 `ToolCall` 对 running tool 每 500ms `setNow`。

优化：

- 只有 tool 可见且展开时才更新 elapsed。
- 或把 elapsed 改成 CSS/静态阶段，不需要精确到每 500ms。
- running 状态可保留轻微 pulse，但减少多个 tool 同时 interval。

### 5.4 固定布局，避免跳动

- 右侧检索过程各阶段使用固定高度或最小高度。
- 来源列表截断到 3-5 条，更多放 details。
- query 列表默认折叠，避免长 query 撑开气泡。
- AI 输入框高度固定，避免消息区高度跳动。
- `collapsed/assist/focus` 切换只改变 panel 宽度和内部显示密度，不改变主内容滚动位置。
- pending 状态的阶段条保留固定区域，避免每来一个事件就把回答内容往下推。

### 5.5 Reduced Motion

新增全局 CSS：

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
```

注意：如果项目已有类似规则，应合并，不重复。

### 5.6 第一轮组件拆分边界

`KnowledgeLibraryPage.tsx` 当前超过 3000 行，但第一轮不应重写业务逻辑。推荐只做 JSX 层和纯展示组件抽离：

- `LibraryNavigationPane`
- `LibraryContentPane`
- `ArticleAIPane`
- `ResearchProgress`
- `EvidenceSummary`
- `ResearchAuditDetails`
- `ResizablePane`

第一轮状态仍可留在父组件，避免同时重构数据流和视觉层。等 UI 稳定后，再考虑 reducer 或 store 化。

### 5.7 视觉降噪清单

改版时逐项检查：

- 主工作区内部不再出现多个 `shadow-[0_18px...]`。
- 滚动容器不使用 `backdrop-blur`。
- 同一级区域 radius 统一，避免 `rounded-xl`、`rounded-lg`、`rounded-md` 混用无规则。
- 顶栏不使用长说明文案。
- 状态 pill 数量受控，同一行最多 3-4 个。
- 工程字段默认不外露，例如 `provider_status`、`candidate_sources`、`plan_status` 原值。

## 6. 实施步骤

### Phase 0：工作区壳层统一

改动：

- 为知识工作区路由定义紧凑 chrome 目标。
- 避免全局 `PageHeaderProvider` 和页面内部大 header 双重出现。
- 压缩满血对话顶栏。
- 去掉 landing-style 说明文案。

验证：

- `/knowledge-chat` 首屏不再出现大标题说明卡片。
- `/knowledge-library` 首屏直接进入文件工作区。
- 顶栏高度控制在 48-64px。

### Phase 1：左栏与入口减法

改动：

- 删除知识库左栏重复文件列表。
- 左栏改为 `browse/search/filter` 状态模型。
- 删除 `去知识库整理` 卡片。
- 压缩满血对话工作台侧栏。

验证：

- 知识库文件夹展开仍可打开文件。
- 搜索文件仍可过滤结果。
- 状态筛选仍生效。
- `/knowledge-library?mode=paste` 仍可直接访问。
- 工作台草稿审阅入口仍可用。

### Phase 2：右侧 AI 三态和可拖拽侧栏

改动：

- 新增 `ResizableRail`。
- 右侧 AI 面板接入 `collapsed/assist/focus` 三态。
- 接入 KnowledgeLibraryPage 左栏。
- 接入 KnowledgeLibraryPage 右侧 AI 面板。
- 接入 KnowledgeChatPage 左侧栏和 Inspector。
- 宽度持久化。

验证：

- 拖拽不卡顿。
- 刷新后宽度保留。
- 双击恢复默认宽度。
- 小屏布局不破。
- 右侧 AI 收起后主内容区域变宽，展开后恢复。

### Phase 3：检索过程改版

改动：

- 抽离 `ResearchProgress`、`EvidenceSummary`、`ResearchAuditDetails`。
- pending 状态用阶段条替代 pill dump。
- 完成状态默认展示采用依据和证据摘要。
- 查询词、候选、线索、未采用、provider status、warnings 放入 details。
- 同步 `researchSourceReasonLabel`，支持：
  - `evidence_critic_clue`
  - `evidence_critic_rejected`
  - `summary_only_disallowed`
  - `stale_for_freshness_sensitive_claim`
  - `wrong_subject`
  - `weak_secondary`

验证：

- streaming 时阶段实时变化。
- 最终答案仍展示参考来源。
- warnings 没丢。
- 采用/线索/未采用分层清晰。

### Phase 4：动画、渲染和视觉降噪

改动：

- memo 化高频列表项和 trace 组件。
- 降低 blur/shadow。
- 优化 ToolCall interval。
- 加 reduced motion。
- 拖拽 pointermove 只写 CSS variable。
- streaming progress 节流。
- 统一内部 radius、边框和状态 pill 密度。

验证：

- Chrome Performance 简单录制拖拽过程。
- streaming 期间页面不跳动。
- 滚动顺滑。

## 7. 验收清单

### 功能验收

- [ ] 知识库左侧没有重复文件栏。
- [ ] 文件夹展开后仍能直接打开文件。
- [ ] 搜索文件功能仍可用。
- [ ] 状态筛选仍可用。
- [ ] 新建文件夹、重命名文件夹仍可用。
- [ ] `去知识库整理` 入口已删除。
- [ ] `/knowledge-library?mode=paste` 仍可访问。
- [ ] 右侧 AI 面板仍支持四种模式：
  - 当前文章
  - 仅知识库
  - 知识库+联网搜索
  - 联网搜索
- [ ] 重新联网检索仍可用。
- [ ] 满血对话工作台可新建对话、恢复历史、审阅草稿。
- [ ] Inspector 仍可开关。

### UI 验收

- [ ] 顶栏高度明显降低。
- [ ] 右侧检索过程默认不再像 debug 面板。
- [ ] 查询词和 provider status 默认折叠。
- [ ] 采用来源、线索来源、未采用来源分层清晰。
- [ ] 来源数量和独立来源数量分开展示。
- [ ] 视觉上更像工作台，不像 landing page。

### 性能验收

- [ ] 左栏拖拽流畅。
- [ ] 右栏拖拽流畅。
- [ ] streaming 时不明显卡顿。
- [ ] streaming 时消息区域不大幅跳动。
- [ ] 大列表滚动顺滑。
- [ ] Chrome Performance 中无持续 layout thrash。

### 构建验收

```bash
cd /Users/pipi/Downloads/hermes-agent-main/web
npm run build
```

如涉及后端 trace 字段：

```bash
cd /Users/pipi/hermes-custom
./.venv/bin/python -m pytest tests/test_research_bridge.py tests/test_api_routes.py tests/test_research_quality_eval.py -q
./.venv/bin/python scripts/eval_research_quality.py
```

### 运行验收

当前 dashboard 运行方式参考：

```bash
cd /Users/pipi/Downloads/hermes-agent-main
HERMES_CUSTOM_ROOT=/Users/pipi/hermes-custom .venv/bin/python -m hermes_cli.main dashboard --host 127.0.0.1 --port 9119 --no-open --skip-build
```

验收路由：

```text
http://127.0.0.1:9119/knowledge-library
http://127.0.0.1:9119/knowledge-library?mode=paste
http://127.0.0.1:9119/knowledge-chat
```

## 8. 风险与取舍

### 8.1 不建议一次性重写页面

`KnowledgeLibraryPage.tsx` 当前文件较大，但功能已经跑通。第一轮应做结构性减法和组件抽取，不做全量重写。

原因：

- 右侧 AI、文件编辑、入库、卡片生成、删除、草稿等功能耦合较多。
- 大重构容易引入行为回归。
- 用户当前核心诉求是页面整洁和流畅，不是重建架构。
- 第一轮应优先抽展示组件和布局组件，业务状态、API 调用和保存/入库逻辑尽量不动。

### 8.2 搜索结果和文件夹展开的关系

搜索态建议临时覆盖文件夹树，而不是额外增加新面板。

取舍：

- 优点：减少重复，最清晰。
- 缺点：搜索时看不到完整文件夹结构。
- 解决：清空搜索即可回到文件夹视图；搜索结果项仍显示 folder path。

### 8.3 检索过程默认折叠多少

建议默认展示采用依据，不默认展示候选和 query。

取舍：

- 用户需要知道 AI 不是黑箱，所以阶段和采用来源必须可见。
- 但 query/provider/warnings 默认全展开会形成调试噪音。
- 详情折叠可满足追溯需要。

### 8.4 侧栏拖拽与响应式

桌面拖拽，移动端抽屉。

不建议移动端也保留拖拽，因为触摸滚动和拖拽冲突明显。

### 8.5 右侧 AI 三态的取舍

右侧 AI 面板如果只做拖拽，会变成“用户自己管理复杂度”。三态模型能让产品默认更清晰：

- `collapsed` 适合阅读文件。
- `assist` 适合边读边问。
- `focus` 适合核查长答案和来源。

取舍：

- 优点：比单纯拖拽更符合真实工作流。
- 缺点：状态更多，需要避免按钮和入口变复杂。
- 解决：只暴露一个主切换按钮，拖拽作为高级补充，不让用户先理解布局系统。

### 8.6 检索过程组件不要做成万能大组件

原方案里的 `ResearchTracePanel` 容易再次变成“什么都塞进去”的组件。更好的边界是：

- `ResearchProgress` 只管实时阶段。
- `EvidenceSummary` 只管最终证据摘要。
- `ResearchAuditDetails` 只管折叠审计详情。

这三个组件可以共享工具函数和类型，但不要在一个组件里用大量分支兼容所有状态。

### 8.7 全局壳层改动的风险

统一知识工作区 chrome 会触碰 `App.tsx` 和 `PageHeaderProvider`，比改页面内部风险更高。建议做成知识工作区路由的条件分支，不影响普通系统管理页：

```text
Config / Logs / Models / Plugins 等系统页：保留原有 dashboard chrome。
Knowledge Library / Knowledge Chat / Cards Board：使用紧凑 workspace chrome。
```

这样既能解决知识工作区的冗余顶栏，又不破坏其它 dashboard 页面。

## 9. 推荐最终界面骨架

### 知识库浏览

```text
┌──────────────────────────────────────────────────────────────┐
│ 知识库                                  新建  导入  刷新  AI   │
├───────────────┬──────────────────────────────┬───────────────┤
│ 搜索文件...    │ 当前文件标题                  │ AI Assist      │
│ 全部 草稿...   │ 标签 / 状态 / 文件夹           │ 当前文件/选区   │
│ browse/search  │                              │ 模式切换        │
│ 处理队列       │ 正文 / 编辑器                  │ 对话            │
│ 文件夹         │                              │ ResearchProgress│
│  Inbox         │                              │ EvidenceSummary │
│    文件 A      │                              │ 输入框          │
│    文件 B      │                              │ collapsed/focus │
└───────────────┴──────────────────────────────┴───────────────┘
```

### 满血对话工作台

```text
┌──────────────────────────────────────────────────────────────┐
│ 满血对话                              已连接  待审草稿  详情  │
├───────────────┬──────────────────────────────┬───────────────┤
│ 当前对话/新建  │ 对话主区域                    │ Inspector      │
│ 对话历史       │                              │ 来源/工具/对象  │
│ 筛选+搜索      │ Tool/Evidence summary         │               │
│ 草稿列表       │ 输入框                        │ collapsed/open │
└───────────────┴──────────────────────────────┴───────────────┘
```

## 10. 第一轮完成标准

第一轮不追求所有代码架构完美，追求用户可感知的明确改善：

- 页面更清爽。
- 知识工作区没有全局 header 和页面大 header 双重占位。
- 左栏不重复。
- 左栏具备 `browse/search/filter` 三种清晰状态。
- 右侧 AI 面板具备 `collapsed/assist/focus` 三态。
- 右侧检索过程更像产品能力，不像日志面板。
- 检索过程区分 pending progress、final evidence 和 audit details。
- 顶栏不再占据大块空间。
- 侧栏可拖拽。
- streaming 和拖拽明显更顺，拖拽过程不触发页面大面积重绘。
- 主工作区内部少用重 shadow 和 backdrop blur。
- 所有原功能仍在。

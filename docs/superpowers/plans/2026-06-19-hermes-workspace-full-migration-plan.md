# Hermes Workspace 完整迁移方案

日期：2026-06-19

## 0. 结论

推荐方案：**在现有 Hermes UI 和现有业务逻辑上做收敛式迁移，而不是从零新建一套 UI。**

更准确地说，不是继续把旧页面嵌到新页面里，也不是另起一套空壳重写，而是：

1. 以现有 `Knowledge Workbench`、`Knowledge Cards / Garden Card` 为功能源。
2. 把它们拆成可复用 controller、panel、list、detail、sidecar 组件。
3. 用一个新的 `Workspace` 作为最终主入口。
4. 旧入口先保留为兼容 wrapper，功能验收完成后再从导航中移除。

这样做的原因很直接：用户现在最在意的是“不漏功能、旧流程还能跑通”。纯新建 UI 视觉上更快，但最容易漏掉旧工作台里已经跑通的草稿审阅、捕获审阅、SSE 对话、提交入库、RAG 索引等细节。

## 1. 当前问题判断

当前 `/workspace` 的状态只是一个演示壳：

- 左侧有新的资料库 / 工作台 / Garden Cards 入口。
- 中间把旧 `knowledge-chat` 页面嵌入进来。
- 中间把旧 `garden` 页面嵌入进来。
- 右侧做了一个新的 AI 建议栏。

这个做法能演示方向，但不能作为最终迁移形态，因为它会导致：

- 左侧 Hermes 全局导航和 Workspace 内部导航重复。
- `知识工作台`、`草稿审阅`、`知识卡片` 这些旧页面概念和新 `资料库`、`工作台`、`Garden` 并列，用户不知道哪个才是主入口。
- 旧 `knowledge-chat` 自己还有左栏、右栏、模式 tab，嵌入 Workspace 后形成多层导航。
- AI 建议栏和旧工作台右侧 `来源与工具` 重复。
- 新资料库里的文件夹和新建文件目前只是前端演示数据，未真正接入 Capture / Markdown / SQLite / RAG 链路。

所以最终迁移必须先做功能拆分，再做 UI 收敛。

## 2. 迁移原则

### 2.1 人的工作区优先

Hermes Knowledge 的主界面应该是一个个人知识工作区，不是一个聊天页，也不是数据库后台。

用户先看到：

- 文件夹
- 文件
- 草稿
- 卡片
- 当前工作内容

AI 是右侧辅助能力，不是主导航，也不是保存前置条件。

### 2.2 旧功能完整迁移

任何旧功能在新 Workspace 没有对应入口之前，不删除旧页面、不删除 API、不改掉旧数据结构。

迁移完成的定义不是“页面能打开”，而是以下流程都能跑通：

- 对话工作台能发消息、流式返回、保持 session。
- 读书 / 知识草稿能加载、选择、修改、提交。
- 粘贴整理能新建草稿、保存整理、确认入库。
- 捕获草稿能加载、选择、修改、提交。
- Garden Card 能加载、筛选、展示状态。
- Capture 文件能在资料库里按文件夹展示、打开、编辑、保存，并继续触发 LibraryAgent / RAG 后处理。

### 2.3 去重导航

最终主导航只保留一套用户心智：

- `资料库`
- `工作台`
- `Garden`

旧概念的去向：

- `Knowledge Workbench` 变成 `工作台` 内部能力。
- `Draft Review` 变成 `工作台` 里的一个模式 / 队列。
- `Knowledge Cards` 变成 `Garden`。
- `AI 建议` 变成右侧 sidecar，不再作为独立页面。

### 2.4 文件夹是逻辑组织，不先改物理目录

短期不移动 Obsidian 物理文件路径。

继续保留已有系统目录：

- `captures/`
- `_archive/raw/`
- `Garden Card/`

新 Workspace 的一级文件夹先作为逻辑字段保存：

- Markdown frontmatter: `folder_path`
- API hydrated item: `folder_path`
- 必要时 SQLite 增加轻量字段或继续从 frontmatter hydrate

这样不会破坏现有 `file_path`、raw 归档、repair、watcher、RAG 索引、Garden 关联。

## 3. 两种方案对比

| 方案 | 做法 | 优点 | 风险 | 结论 |
| --- | --- | --- | --- | --- |
| A. 纯新建 UI | 从 `/workspace` 新写所有界面和状态 | 视觉自由，演示快 | 最容易漏旧功能；需要重接所有 API；容易出现两套工作台 | 不推荐作为主线 |
| B. 直接改旧 UI | 在现有 `knowledge-chat`、`garden`、`_app` 上删除旧导航并加新功能 | 最不容易断旧逻辑 | 旧页面结构复杂，容易越改越乱 | 可作为过渡，但不是最终结构 |
| C. 收敛式迁移 | 旧功能组件化抽出，新 Workspace 作为唯一主入口，旧页面保留 wrapper | 旧功能不丢，最终信息架构清楚，风险可控 | 需要先做组件边界和验收清单 | 推荐 |

推荐 C。实际落地时，C 会包含一部分 B 的动作：保留旧逻辑、删除重复导航、把旧页面变成 wrapper。但它不是在旧页面里无限加东西，而是把旧页面拆干净后迁移到统一 Workspace。

## 4. 目标信息架构

```text
Hermes Workspace
├── 左侧可折叠导航
│   ├── 资料库
│   │   ├── Inbox
│   │   ├── AI 产品
│   │   ├── 数据 Agent
│   │   ├── 待读
│   │   └── 临时
│   ├── 工作台
│   └── Garden
├── 中间主工作区
│   ├── 文件列表 / 文件详情 / 编辑器
│   ├── 满血对话工作台
│   └── Garden Card board
└── 右侧可折叠 AI sidecar
    ├── 当前文件建议
    ├── 当前草稿上下文
    ├── 当前 Garden Card 关联
    └── 来源、工具、索引链路、提交状态
```

一级导航只保留 `资料库 / 工作台 / Garden`。其他审阅、草稿、捕获、来源工具都进入中间或右侧上下文，不再和主入口并列。

## 5. Knowledge Workbench 功能迁移清单

现有来源：`dashboard/src/pages/knowledge-chat.tsx`

| 旧功能 | 当前位置 | 新 Workspace 位置 | 迁移要求 |
| --- | --- | --- | --- |
| `chat` 模式 | `KnowledgeChatPage` mode | `工作台 > 对话` | 保留消息列表、输入框、Enter 发送、Shift+Enter 换行 |
| Gateway SSE | `sendMessageToGateway` | `useWorkbenchChat` | 保留 `X-Hermes-Session-Id`、delta stream、complete、error |
| 本地 session id | `crypto.randomUUID()` | workbench controller | 保持一次页面会话内连续 |
| 读书草稿列表 | `fetchReadingDrafts(20)` | 工作台左侧队列 | 保留加载、空状态、选择 |
| 知识草稿列表 | `fetchKnowledgeDrafts(20)` | 工作台左侧队列 | 和 reading drafts 去重合并 |
| 草稿审阅 | `DraftReviewPanel` | 工作台中心 detail | 不重写，先原样复用 |
| 草稿提交后刷新 | `handleDraftCommitted` | controller callback | 保留返回 chat / 刷新列表 |
| 粘贴整理 | `editable-capture` | 工作台中心 editor | 保留 source、title、body、保存整理、确认入库、新建 |
| editable draft 创建 | `createEditableCaptureDraft` | controller action | 保留 create / reload draft / status |
| editable draft 更新 | `updateEditableCaptureDraft` | controller action | 保留 title/body 更新 |
| editable commit | `commitCaptureDraft` | controller action | 保留先 update 再 commit 的链路 |
| 捕获草稿列表 | `fetchCaptureDrafts(50)` | 工作台左侧队列 | 保留加载、空状态、选择 |
| 捕获草稿详情 | `selectedCapture` detail | 工作台中心 detail | 保留标题、来源、类型、字数、状态、标签、摘要、草稿、原文 |
| 捕获草稿修改 | `reviseCaptureDraft` | detail action | 保留指令输入、Enter 修改、刷新 |
| 捕获草稿提交 | `commitCaptureDraft` | detail action | 保留提交后刷新 |
| 来源与工具右栏 | old right panel | Workspace 右侧 AI sidecar | 内容保留，但归入统一右栏 |
| 状态文案 | `editableStatusLabel` | shared labels | 不丢状态：empty/editing/saving/ready/committing/committed/error |

## 6. Garden Card 功能迁移清单

现有来源：`dashboard/src/pages/garden.tsx`、`api/routes/knowledge.py`

| 旧功能 | 当前位置 | 新 Workspace 位置 | 迁移要求 |
| --- | --- | --- | --- |
| 加载卡片 | `fetchGardenCards(100)` | `Garden` 主视图 | 保留 limit、loading、失败降级 |
| 类型筛选 | `viewpoint/fact/method/risk` | Garden 顶部筛选 | 保留全部 / 单类型筛选 |
| 状态展示 | seed/accepted/archived | Card badge | 保留状态映射 |
| 卡片字段 | title/status/kind/claim/file_path | Card board | 保留展示，不隐藏 file_path |
| 空状态 | 暂无知识卡片 | Garden empty state | 保留但改成 Workspace 风格 |
| API | `/knowledge/garden-cards` | 不变 | 第一阶段不改接口 |

注意：当前 Garden 页面本身比较薄。如果后续要做卡片详情、接受/归档、关联 Capture、生成 Obsidian Canvas，必须先确认后端已有能力，不能在前端先做假按钮。

## 7. 资料库 / 文件夹新增能力

这是新 UI 真正新增的部分，也是不能只做前端 demo 的部分。

### 7.1 左侧文件夹

必须支持：

- 左栏折叠 / 展开。
- 一级文件夹展开 / 收起。
- 点击文件夹后显示该文件夹下文件。
- 新建文件。
- 文件点击后打开详情。

第一阶段只做一级文件夹：

- Inbox
- AI 产品
- 数据 Agent
- 待读
- 临时

### 7.2 文件数据来源

文件列表使用 `Capture` 作为第一阶段主数据源。

`Capture` 需要补齐：

- `body`
- `folder_path`
- `title`
- `summary`
- `tags`
- `source_type`
- `status`
- `updated_at`

当前 `api/routes/captures.py` 已经 hydrate Markdown frontmatter，但还没有把 `folder_path` 明确返回，也没有让 `folder` 过滤命中 `folder_path`。这必须补。

### 7.3 新建文件

不能继续只创建本地前端对象。

推荐链路：

1. 用户在左栏某文件夹点击新建。
2. 中间打开空编辑器。
3. 用户输入标题 / 正文。
4. 保存时调用 editable capture draft 或新增 capture create API。
5. 后端写入 Markdown frontmatter：

```yaml
entry_mode: manual_file
folder_path: Inbox
title_source: user
```

6. 确认入库后继续走 `Capture -> LibraryAgent -> RAG`。

### 7.4 编辑文件

现有 `PATCH /captures/{capture_id}` 支持 title/body 更新，并会调用 `LibraryAgent(...).maintain_capture()`。新文件详情应该直接复用它。

需要补：

- 前端 `Capture` type 增加 `body`、`folder_path`。
- 编辑器保存调用 `updateCapture`。
- 保存后刷新当前 item。
- 保存失败在当前面板内展示，不用 alert。

### 7.5 移动文件夹

MVP 可以先做“修改逻辑文件夹”，不做物理移动。

需要后端支持：

- `PATCH /captures/{id}` 接收 `folder_path`，或新增 `PATCH /captures/{id}/folder`。
- 写回 Markdown frontmatter。
- API list 支持 `folder=AI 产品` 过滤。

不做：

- 物理移动 Markdown 文件。
- 自动创建多级目录。
- 直接改 `file_path`。

## 8. API / 后端依赖清单

| 依赖 | 当前情况 | 必须修改 |
| --- | --- | --- |
| `GET /captures` | 支持 `folder` 参数，但 `_matches_filters` 查 `folder` | hydrate 返回 `folder_path`，filter 同时看 `folder_path` |
| `PATCH /captures/{id}` | 只支持 `body/title` | 支持 `folder_path` 或新增 move endpoint |
| `Capture` frontend type | 没有 `body/folder_path` | 增加字段 |
| `createEditableCaptureDraft` | 当前前端未传 folder | 增加 `folder_path` 参数 |
| capture draft schema | 当前无 folder 字段 | 可加 `folder_path`，或放入 `capture_intent`，推荐显式字段 |
| commit capture draft | 写入 capture note | commit 时写入 frontmatter `folder_path` |
| Garden cards API | 当前只 list seed/accepted | 第一阶段不改，后续再加 detail/action |
| Workspace demo data | 当前有 fallback demo | 最终生产入口不应混入假数据，可改为明确 empty state |

## 9. 前端组件拆分方案

目标是避免继续维护“大页面里塞所有逻辑”。

建议结构：

```text
dashboard/src/features/workspace/
├── WorkspaceShell.tsx
├── WorkspaceSidebar.tsx
├── WorkspaceMain.tsx
├── AISidecar.tsx
├── library/
│   ├── LibraryFolderTree.tsx
│   ├── LibraryFileList.tsx
│   ├── LibraryFileDetail.tsx
│   └── useLibraryController.ts
├── workbench/
│   ├── WorkbenchSurface.tsx
│   ├── WorkbenchModeTabs.tsx
│   ├── WorkbenchDraftQueue.tsx
│   ├── WorkbenchChatPanel.tsx
│   ├── EditableCapturePanel.tsx
│   ├── CaptureReviewPanel.tsx
│   └── useKnowledgeWorkbenchController.ts
└── garden/
    ├── GardenBoard.tsx
    ├── GardenFilters.tsx
    ├── GardenCardItem.tsx
    └── useGardenCards.ts
```

旧页面保留为 wrapper：

```text
dashboard/src/pages/knowledge-chat.tsx -> <WorkbenchSurface standalone />
dashboard/src/pages/garden.tsx -> <GardenBoard standalone />
dashboard/src/pages/workspace.tsx -> <WorkspaceShell />
```

这样旧路由还能测试，新 Workspace 也不会靠嵌套旧页面来实现。

## 10. 右侧 AI sidecar 规则

右侧栏只做上下文辅助，不再重复主流程。

它根据当前 active surface 切换内容：

| 当前页面 | 右侧内容 |
| --- | --- |
| 资料库文件列表 | 文件夹统计、最近更新、未整理建议 |
| 资料库文件详情 | 标题/摘要/标签建议、相关 Capture、RAG 索引状态 |
| 工作台对话 | 来源、工具进度、可保存为草稿的回答 |
| 粘贴整理 | editable draft 状态、标题/摘要/标签、检索链路 |
| 草稿审阅 | session id、output type、target capture |
| 捕获审阅 | source、source_type、instruction、committed id |
| Garden | card kind 统计、相关 capture、下一步建议 |

右栏必须支持折叠。折叠后只保留一个窄栏按钮和必要计数，不占用主要编辑空间。

## 11. 导航清理方案

阶段内不立刻删除旧页面文件，但要清理用户可见导航。

最终 `_app.tsx` 导航应收敛为：

- `Workspace`
- 可选：`Capture Timeline`，如果它仍是独立诊断入口

不再并列展示：

- `知识工作台`
- `草稿审阅`
- `知识卡片`

这些作为 Workspace 内部视图存在。

## 12. 实施阶段

### P0：冻结范围和补齐验收表

目标：不再继续临时堆 UI。

动作：

- 保留当前 `/workspace` 作为临时演示，不继续加新业务。
- 按本文档建立功能 parity checklist。
- 为 `knowledge-chat` 和 `garden` 现有行为补 smoke tests。

验收：

- 每个旧功能都有新位置。
- 每个旧 API 都标明是否复用、扩展或不动。

### P1：抽出 Workbench 功能，不改变行为

目标：把旧 `KnowledgeChatPage` 拆成可复用模块。

动作：

- 抽 `useKnowledgeWorkbenchController`。
- 抽 `WorkbenchDraftQueue`。
- 抽 `WorkbenchChatPanel`。
- 抽 `EditableCapturePanel`。
- 抽 `CaptureReviewPanel`。
- 旧页面改为 wrapper，确保视觉可以暂时不变。

验收：

- `/knowledge-chat` 原流程全部可用。
- `/workspace > 工作台` 使用同一套组件，不再嵌旧页面。
- SSE 对话、草稿提交、捕获提交都可跑通。

### P2：抽出 Garden 功能，不改变行为

目标：Garden 不再作为嵌入页面。

动作：

- 抽 `useGardenCards`。
- 抽 `GardenBoard`、`GardenFilters`、`GardenCardItem`。
- 旧 `/garden` 改为 wrapper。
- `/workspace > Garden` 使用同一套组件。

验收：

- `/garden` 和 `/workspace > Garden` 展示一致。
- kind filter、loading、empty state 不丢。

### P3：资料库接入真实 Capture

目标：文件夹和文件不再是 demo。

动作：

- `GET /captures` hydrate `folder_path`。
- `folder` filter 改为按 `folder_path`。
- 前端 `Capture` type 增加 `body/folder_path`。
- 资料库文件列表使用真实 captures。
- 文件详情支持编辑和保存。

验收：

- 点击 Inbox / AI 产品 / 数据 Agent 能看到真实文件。
- 打开文件能看到 body。
- 保存 title/body 后 Markdown 和 API 返回同步。
- 保存后 LibraryAgent / RAG 后处理仍能跑。

### P4：新建文件和移动文件夹

目标：左侧文件夹成为真实工作入口。

动作：

- 新建文件接入 editable capture 或 capture create flow。
- 保存时写入 `folder_path`。
- 支持修改文件 folder。
- commit 时保留 existing Capture -> LibraryAgent -> RAG。

验收：

- 在任意一级文件夹新建文件后，刷新页面仍在该文件夹下。
- 入库后能被 RAG 检索。
- Obsidian 物理路径不被破坏。

### P5：导航收敛和视觉整理

目标：用户只看到一套工作台。

动作：

- `_app.tsx` 删除旧主导航项。
- `/workspace` 成为默认入口。
- 旧 `/knowledge-chat`、`/drafts`、`/garden` 保留兼容，但不在主导航出现。
- 清理 demo data fallback，改为空状态。
- 统一 loading / empty / error 状态。

验收：

- 用户从入口进入后不会看到重复的 Knowledge Workbench / Draft Review / Knowledge Cards。
- 左右栏折叠可用。
- 中间主区域没有嵌套侧栏造成的混乱。

## 13. 验证清单

### 前端构建

- `npm run build`

### API 单元测试

- capture draft workflow
- capture file output
- API routes
- reading bridge
- garden bridge
- schema migration

建议命令：

```bash
.venv/bin/python -m pytest tests/test_api_routes.py tests/test_capture_draft_workflow.py tests/test_capture_file_output.py tests/test_reading_bridge.py tests/test_garden_bridge.py tests/test_schema.py -q
```

### 手工 smoke

- 打开 `/workspace`。
- 折叠 / 展开左栏。
- 点击每个一级文件夹。
- 新建文件、保存、刷新确认仍存在。
- 打开已有 capture，修改标题和正文，保存。
- 进入 `工作台 > 对话`，发送消息，确认 SSE 返回。
- 进入 `工作台 > 粘贴整理`，保存整理，确认入库。
- 进入 `工作台 > 草稿审阅`，选择草稿，修改，提交。
- 进入 `工作台 > 捕获审阅`，选择捕获草稿，修改，提交。
- 进入 `Garden`，筛选 viewpoint/fact/method/risk。
- 折叠 / 展开右侧 AI sidecar。

### Runtime 验证

如涉及真实 Hermes gateway / RAG 行为，需要：

- 重启 dashboard API。
- 重启 dashboard UI。
- 如改动 gateway chat，需要重启 gateway。
- 检查日志中没有 API 500、SSE 断流、LibraryAgent postprocess failure。

## 14. 回滚策略

- P1/P2 阶段旧页面只是 wrapper，若 Workspace 出问题，旧 `/knowledge-chat` 和 `/garden` 仍可直接访问。
- P3/P4 不移动物理 Markdown 路径，所以不会破坏 Obsidian vault。
- folder 先写 frontmatter，若前端显示异常，Capture/RAG 主链路仍按原 `file_path` 工作。
- 新导航删除旧入口前，必须完成 smoke checklist。

## 15. 下一步建议

下一步不要继续在当前 `workspace.tsx` 上加功能。

应该先做 P1：

1. 抽出 `Knowledge Workbench` controller 和组件。
2. 让 `/knowledge-chat` 仍然正常。
3. 让 `/workspace > 工作台` 使用同一套组件。
4. 跑构建和工作台 smoke。

这样可以先解决用户最关心的“满血工作台完整迁移”，再迁 Garden，最后再补资料库真实新建 / 文件夹能力。

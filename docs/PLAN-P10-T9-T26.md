# 技术债收口方案：P10 / T9 / T26

日期：2026-11-17

本方案只处理三项明确遗留：P10（`web_only` UI 不可达）、T9（筛选全量 hydration）、T26（页面 helper 拆分）。
目标不是“看起来更整齐”，而是给出可验证的行为契约、减少实际 I/O，并保持现有 API/历史会话兼容。

---

## 1. P10：`web_only` 的 UI 不可达

### 1.1 现状（证据）

- `web/src/lib/api.ts`：`KnowledgeResearchMode` 同时包含 `web_only` 和四种可达模式。
- `web/src/pages/knowledge-chat/GatewayChat.tsx`：主聊天只有一个联网开关，点击只在 `local_only` / `local_web` 之间切换；`web_only` 永远不会由 UI 产生。
- `hermes-custom/bridge/research_bridge.py`、`web_context_answer.py`、`bridge/research/*`：`web_only` 是真实存在的后端/评测模式，不是死代码。

### 1.2 决策

**把“可选择模式”和“可持久化/响应模式”拆开**，而不是删除后端能力：

| 层 | 类型 | 是否含 `web_only` | 说明 |
| --- | --- | --- | --- |
| API 请求/响应、历史会话 | `KnowledgeResearchMode` | 是 | `web_only` 是合法 API/评测模式，后端继续支持 |
| UI 可选择模式 | `KnowledgeSelectableResearchMode` | 否 | 用户只能选 `current_article_only` / `local_only` / `local_web` / `theme_scan` |
| 主聊天状态 | `ChatResearchMode` | 否 | `local_only` / `local_web` / `theme_scan` |
| 伴读面板状态 | `ArticleResearchMode` | 否 | `current_article_only` / `local_web` |

### 1.3 实施步骤

1. `web/src/lib/api.ts` 新增 `KnowledgeSelectableResearchMode`，保留 `KnowledgeResearchMode`。
2. 主聊天：
   - `KNOWLEDGE_TOOLSETS_BY_MODE` 改为 `Record<ChatResearchMode, string[]>`，删除 `web_only` 键。
   - `researchMode` state、`withResearchModeInstruction()`、`GatewayChat` props 全部收窄到 `ChatResearchMode`。
   - `knowledgeToolsetsForMode(mode: KnowledgeResearchMode)` 保留宽入参，遇到历史 `web_only` 映射到 `local_web` 的 toolsets。
3. 伴读面板：
   - `articleResearchMode`、`ResearchModeControl`、`askArticleAi` 收窄到 `ArticleResearchMode`。
   - 历史消息 `mode=web_only` 仍可渲染；重跑时映射为 `local_web + force_web=true`，不丢失“必须联网”的语义。
   - 展示层（`researchModeLabel`、`researchStatusTitle`、进度文案）保留宽类型分支，因为它们在读历史/API 数据，不是可选控件。
4. 新增前端契约脚本 `web/scripts/check-knowledge-research-mode-contract.mjs`：
   - 断言 API 类型仍包含 `web_only`；
   - 断言 UI 可选择类型不包含 `web_only`；
   - 断言主聊天/伴读控件源码不再出现 `web_only` 作为可选值。
   - 接入 `npm run check:knowledge-research-modes` 和 `test:knowledge:complete`。
5. 后端补一个契约测试：`web_context_answer._normalize_mode("web_only") == "web_only"` 且 `_mode_needs_web("web_only", ...) is True`，锁住 API 兼容性。
6. 文档：`docs/CODE-REVIEW-V3.md` P10 行改为“UI 可选择类型已收窄；`web_only` 仅 API/评测/历史兼容，有契约脚本与后端测试”。

### 1.4 验收

- `npm run build` 通过（TypeScript 穷尽检查会强制所有 UI 模式都有定义）。
- `npm run check:knowledge-research-modes` 通过。
- `pytest tests/test_web_context_answer.py -q` 通过。
- 历史 `web_only` 答案仍能显示；重跑映射为 `local_web + force_web=true`。

### 1.5 风险与回滚

- 风险：伴读面板历史重跑语义变化。缓解：映射时保留 `force_web=true`，并加测试。
- 回滚：只回退前端类型/控件和契约脚本，后端无改动。

---

## 2. T9：筛选下推

### 2.1 现状

`bridge/library_view.py::list_library_items()`：

- 无筛选：已走 `_list_unfiltered()`，SQL 排序 + limit 后才读文件。
- 有 folder/status/search：先 `SELECT * FROM captures` + 打开草稿，逐条 `capture_library_item()` 读 Markdown，再内存过滤、计数、分页。

### 2.2 下推矩阵

| 维度 | captures | capture_drafts | 备注 |
| --- | --- | --- | --- |
| kind | SQL 跳过另一类 | 同左 | counts 仍在 kind 过滤前计算 |
| folder | `capture_metadata.folder_path`（有索引） | `json_extract(capture_intent,'$.folder_path')`（表达式索引） | metadata 覆盖不完整时回退读文件 |
| status | 不强行下推（frontmatter `status` 可覆盖 `review_state`） | `status = ?`（有索引） | capture 状态在 hydration 后校验 |
| search | `capture_embeddings.index_text LIKE ?`（索引覆盖完整时） | `title/summary/draft_text/raw_text LIKE ?` | 非 ASCII 大小写场景回退全量 |
| counts | SQL COUNT 或 hydration 后计数 | 同左 | 语义保持“kind 过滤前” |
| pagination | 每类取 top `limit+offset` 后归并 | 同左 | 避免全量 hydration |

“覆盖完整”判定：

- folder：active captures 都有 `capture_metadata` 行；
- search：active captures 都有 `capture_embeddings` 行；
- 不完整时该维度回退到现有全量路径，保证正确性；文档提示运行 library audit / repair 后自动进入快路径。

### 2.3 实施步骤

1. 抽 `_list_filtered()`：SQL 预筛 + 必要时 hydration + 统一 Python 校验（`_library_search_haystack` 保持原语义）。
2. 新增 `_like_pattern()`，对 `%`、`_`、`\` 转义，保证搜索仍是“字面子串”。
3. 新增索引：
   - `idx_capture_drafts_folder`（表达式索引，`TRIM(COALESCE(json_extract(...),'Inbox'))`）；
   - `idx_capture_drafts_status_updated`（如现有单列索引不够）。
4. 测试：
   - folder 命中时只读取目标 note（monkeypatch `read_capture_note` 计数）；
   - metadata 缺失时仍能按 frontmatter folder 命中（回退路径）；
   - search 命中时只 hydration 命中的 indexed capture；
   - embeddings 缺失时仍能按正文命中（回退路径）；
   - folder/status/search 的 counts、分页、kind 语义与现有测试一致。
5. 文档：`docs/CODE-REVIEW-V3.md` T9 行更新为“folder/draft-status/search 已下推；capture status 因 frontmatter 优先级保留 hydration 后校验”。

### 2.4 验收

- `pytest tests/test_library_read_api.py tests/test_library_write_api.py -q` 通过。
- 新增“只读目标 note”的计数断言通过。
- 全量 `pytest tests` exit 0。

### 2.5 风险

- SQLite `LIKE` 对非 ASCII 大小写不敏感支持有限 → 检测到非 ASCII 大小写查询时回退 Python 全量。
- `capture_metadata` 覆盖不完整 → 回退读文件，不牺牲正确性。

---

## 3. T26：剩余 helper 拆分

### 3.1 现状

`web/src/pages/KnowledgeLibraryPage.tsx` 约 4.3k 行；Header/Sidebar/shared 已拆出，但纯 helper、阅读组件、FileWorkbench 仍在页面内。

### 3.2 目标模块

| 新文件 | 内容 | 预估行数 |
| --- | --- | --- |
| `knowledge-library/helpers.ts` | 纯函数：依赖计数/删除文案、卡片建议解析、folder/status 匹配、selection label、article chat 模式键 | ~350 |
| `knowledge-library/ReadingOverlay.tsx` | `ImmersiveReadingOverlay`、`ReadonlyMarkdownDocument` | ~80 |
| `knowledge-library/FileWorkbench.tsx` | `FileWorkbench` 组件 | ~260 |

页面保留：主组件、Article AI 面板与流式事件（强耦合页面状态），以及仅页面使用的常量。

### 3.3 实施步骤

1. 先移纯函数到 `helpers.ts`，页面只保留 import；`npm run build` 验证。
2. 再移阅读覆盖层；`npm run build` 验证。
3. 最后移 `FileWorkbench`；`npm run build` 验证。
4. 每步保持 props/行为不变，不做顺手重构。
5. 文档记录拆分后的行数与剩余耦合点。

### 3.4 验收

- `npm run build` 通过。
- `npm run lint` 通过（若仓库当前 lint 可运行）。
- 页面行数显著下降（目标 < 3.8k），主组件职责仍是“组合 + 页面状态”。

---

## 4. 总验收

| 项 | 命令 |
| --- | --- |
| 后端全量 | `hermes-custom: .venv\Scripts\python -m pytest tests -q` |
| 前端构建 | `hermes-agent-main/web: npm run build` |
| 前端契约 | `npm run check:knowledge-research-modes` |
| 服务健康 | `/api/health`、`/api/status` |

Windows 平台专属用例仍按既有口径单独记录，不计入本轮验收线。

---

## 5. 执行结果（2026-11-17）

| 项 | 结果 |
| --- | --- |
| P10 | API 模式 `KnowledgeResearchMode` 保留 `web_only`；UI 类型 `KnowledgeSelectableResearchMode` 收窄为四种可达模式；主聊天/伴读控件不再产生 `web_only`；历史重跑映射 `local_web + force_web`；契约脚本 `npm run check:knowledge-research-modes` 通过；后端 `test_web_only_remains_api_only_mode_contract` 通过 |
| T9 | `_list_filtered()` 落地：capture folder 走 `capture_metadata`，草稿 folder/status/search 走 SQL，capture search 走 `capture_embeddings.index_text`；覆盖不足自动回退。新增“只读目标 note”测试、metadata 缺失回退、embedding 缺失回退、草稿筛选索引测试 |
| T26 | 页面 4,868 → 3,783 行；`helpers.ts`（纯函数）、`selection.ts`（选区上下文）、`ReadingOverlay.tsx`（沉浸阅读/只读正文）、`CollapsedAIPanelRail.tsx`、`model.ts`（类型/常量）拆出；`shared.tsx` 只保留组件，lint 通过 |

验证命令与结果见本轮提交说明；全量 `pytest tests`、`npm run build`、`npm run lint`、`npm run check:knowledge-research-modes` 均通过。

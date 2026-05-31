# Hermes 项目当前状态与下一步

日期：2026-05-31

## 一句话结论

Hermes 知识库主链路已经基本跑通：微信入口可以触发 Capture、Library、Theme、Garden、RAG、Review 等工具；知识材料会进入 SQLite 与 Obsidian；RAG 已经开始使用 embedding + Garden/Theme/Capture 图扩展；最近一次运行记录也能通过 Agent Run Ledger 回看。

现在最该做的不是继续堆新 Agent，而是做一次产品级收口：把“回答质量、引用可读性、运行记录、重复知识卡片、主题页可读性、手动数据修复”这些已经暴露过的细节打磨稳定。

## 已经可用的能力

### Capture

- 可以从微信链接或文本生成草稿。
- 支持先审阅草稿，再确认入库。
- 支持“全文保存”与“整理总结”区分。
- 已经处理过长文输出截断、草稿预览、继续查看草稿、入库后 raw 归档等问题。

仍要注意：长文处理现在更像“让 Agent 先判断该怎么整理”，不是完整 map-reduce 深处理框架。对超长文章，如果要求既保留细节又写得好，还需要继续做分段阅读和合成策略。

### Library

- 负责知识库体检、DB 与 Obsidian 文件对齐。
- 可以补齐长文 outline、清理重复 open drafts、处理缺失 raw/capture 文件。
- 可以维护 capture embeddings，包括 identity 和 section 两类索引。
- raw 已经定位为归档区，不是日常阅读区。

下一步可继续补：Library 对“文章没有 Markdown 标题、挤成一坨”的自动结构化修复，需要更系统地覆盖真实材料。

### Theme

- 负责扫描最近或全库内容，发现可以沉淀为主题页/MOC 的关联。
- 新方案已经偏向“索引图谱优先”：先用 identity/section embeddings 与元数据找候选关系，再让 LLM解释关系。
- MOC 页面定位为主题导航和材料关系页，不要求反向给每篇 capture 写 wiki 链接。

仍要注意：Theme 候选组的质量要持续观察，不能只靠“看起来相关”的标题聚类。

### Garden

- 负责把跨材料关系沉淀为知识卡片草稿。
- 已经加入重复卡片防护，避免同一观点反复生成多张几乎一样的卡。
- 卡片类型由系统推断，不再把“方法/观点/事实/风险”等分类负担推给用户。

仍要注意：Garden Card 的展示要继续避免无意义 ID，关系应该尽量显示为 Obsidian 可读链接或材料标题。

### RAG

- 定位已经回到简单问题回答工具：用户问问题，它从知识库找材料回答。
- 检索已接入图扩展：先从命中的 capture/section 出发，再沿 Garden、Theme、Capture 网络找补充材料。
- 回答引用正在往“有材料名、有章节名、只列实际用到的来源”方向收口。
- Agent Run Ledger 可以让用户追问“刚刚 RAG 查了哪些材料”。

仍要注意：答案质量还需要用真实问题持续测。尤其是“命中 Garden 后沿网络扩展多少”“Theme 命中后如何补 Capture”“什么时候停止搜索”这些策略，还不是最终形态。

### Review

- 定位已经从“考你”调整为“每天讲一个点，帮你回顾，怕你忘记”。
- 支持 follow-up，用户追问时应该围绕上一次讲解内容继续展开。
- 支持记录看过/反馈状态的基础能力。

仍要注意：Review 抽选材料要继续防止老盯一篇文章；参考来源只应该列实际引用过的材料。

## 当前验证结果

### hermes-custom

- 聚焦验收通过：214 passed。
- 全量测试通过：527 passed, 2 skipped。
- 本轮发现并修复了一个 RAG 图检索 bug：当 seed hit 没有 `file_path` 时，不能把 vault 根目录当成 Markdown 文件读取。

### hermes-agent-main

- 工具相关聚焦测试通过：36 passed。
- 全量测试没有作为通过依据：当前环境缺 `acp`、`fastapi`、`mcp` 等依赖，并有历史 `mcp_tool` 导入错误，测试收集阶段中断。这不是本轮知识库工具链失败，但需要后续单独整理 runtime 仓测试环境。

### 运行态

- 当前 gateway 只有一个主进程在跑：
  `/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m hermes_cli.main gateway run --replace`
- gateway 日志显示微信和飞书均已连接。
- 最近微信问题已经触发 RAG，并生成了 1246 字回答；随后“刚刚 RAG 查了哪些材料？”触发了运行记录查询，返回 419 字。
- `data/rag.log` 已记录图检索轨迹，例如 seed、garden、theme、expanded capture、final evidence 数量。
- SQLite 里已有 `agent_runs` 与 `agent_run_events`，最近记录包含 rag、theme、review、librarian 等运行。

## 当前工作区风险

两个仓库现在都不是干净状态，里面混有多条开发线。

### hermes-custom

主要未提交类别：

- Life Assistant 并行开发：`agents/life_agent.py`、`db/life_repo.py`、life API、dashboard life 页面、大量 life tests。
- Theme/Garden 并行开发：`agents/theme_agent.py`、`bridge/theme_bridge.py`、`agents/theme_graph.py`、Theme/Garden tests。
- 本轮核心修复：`agents/rag_graph.py`、`tests/test_rag_graph.py`、`tests/test_e2e_hermes_tools.py` 中的 `agent_run_status` 预期。
- 本地产物：`data/`、`reports/`、`dashboard/tsconfig.tsbuildinfo`。

提交时必须精确分拣，不能把 Life 或本地数据误提交。

### hermes-agent-main

主要未提交类别：

- Life 工具描述调整：`tools/record_life_event_tool.py`。
- Theme 候选/扫描工具调整：`tools/theme_scan_tool.py`、`tools/theme_candidates_tool.py`、`toolsets.py`、相关 tests。
- 上一轮 `agent_run_status` 已经提交并推送，本轮没有改 runtime 仓代码。

## 建议下一步

1. 做一次微信手测：
   - 问一个知识库问题。
   - 追问“刚刚 RAG 查了哪些材料？”
   - 看回复是否保留材料名和章节名。
2. 然后进入 RAG 质量打磨：
   - 把 evidence 从“能找出来”升级到“回答时真的用对”。
   - 优化图扩展停止条件，避免材料太少或太散。
   - 让引用始终显示人能看懂的文章名/章节名。
3. 同步收口 Theme/Garden：
   - Theme 候选关系要更像“这一组为什么值得整理”，少给内部 ID。
   - Garden 卡片重复检测继续观察真实使用效果。
4. 最后再处理 Life Assistant 这一大块并行改动，不要和知识库主链路混在同一个提交里。

## 手动验收清单

- 微信发送：`AI Agent 工程化到底应该怎么理解？结合我的知识库回答`
- 微信追问：`刚刚 RAG 查了哪些材料？`
- 期望：
  - 第一条回答引用材料不要只显示 `[1]`，要能看到材料名或章节名。
  - 第二条回复要列出刚才实际检索/使用的材料，不要用“同上”。
  - 如果 RAG 命中 Garden 或 Theme，回复里应该能看出它沿着关联材料补证据，而不是只拿一张卡片硬答。

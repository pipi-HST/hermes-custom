# Hermes Knowledge Map and Theme Agent Design

更新日期：2026-06-27

## 1. 背景

Hermes Knowledge 已经从早期 Obsidian 主账本模型调整为 Knowledge File + Web/DB 主模型。根据 ADR 0003，Hermes 只保留 Capture Notes 和 Raw Evidence 这类持久 Markdown Knowledge Files；Knowledge Cards、Theme Views、graph relationships、review state、drafts、source maps 等长期结构对象以 Web/DB 为主。

因此，本设计不再把“地图”实现成 Obsidian MOC 或默认 Markdown 文件。知识地图是 Web/DB 结构化对象，Markdown 只作为可选导出。

这次要解决的问题不是“画一个看起来像图谱的页面”，而是让 Hermes 能在用户触发时回答：

- 当前 Hermes Knowledge 现在有什么？
- 当前 Hermes Knowledge 应该分成哪些 Knowledge Areas？
- 每个 Knowledge Area 应该按哪些维度组织？
- 每个主题、人物、分类、物种、冲突或场景下面有哪些具体材料？
- 哪些地图更新建议需要用户确认？

节点图谱仍然保留，但只是局部关系浏览器，不是主体验。

## 2. 产品定位

v1 定位为 **Knowledge Map + Theme Agent Map Proposal**。

核心用户价值：

- 用户能看清 Hermes Knowledge 的结构；
- Theme Agent 可以提出 Knowledge Areas、地图维度和归类建议；
- 用户确认后，地图成为长期导航层；
- 用户可以从地图条目直接看到具体 Knowledge File、Knowledge Card 和 Theme View；
- 局部图谱只辅助解释“谁和谁有关，为什么有关”。
- Theme Agent 仍保留手动“运行体检”能力，但它是二级动作，不主动触发，也不自动改地图。

## 3. 关键决策

### 3.1 当前 Hermes Knowledge 只有一个地图

v1 不引入多个 Knowledge Base，也不提供多地图管理。当前 Hermes Knowledge 只有一个 Knowledge Map。不同“库感”或不同用途不通过多个 map 解决，而是通过同一张地图里的顶层 Knowledge Areas 解决。每个 Knowledge Area 再拥有自己的索引维度。

例如：

- 高质量人类谈话材料是一个 Knowledge Area，下面有人物索引、主题索引、观点冲突索引；
- 动物百科材料是一个 Knowledge Area，下面有动物分类、栖息地、行为习性、保护状态；
- AI 产品材料是一个 Knowledge Area，下面有能力模块、业务场景、指标体系、案例、风险边界。

只有当未来出现独立存储、重置、权限或检索边界时，才考虑引入真正的 Knowledge Base 概念。

### 3.2 维度由 LLM 提议，用户确认后稳定

Theme Agent 可以根据 Hermes Knowledge 内容和用户目标提出 Knowledge Areas 与各 Area 内的地图维度，但确认后的 Area 名和维度名是长期配置。后续生成建议时必须沿用已确认结构，不得每次重新发明分类体系。

Theme Agent 想新增、合并、重命名 Knowledge Area 或维度时，只能生成 proposal，用户确认后才修改正式地图。

用户也可以手动创建空 Knowledge Area。空 Area 可以先进入正式地图，随后由 Theme Agent 为它生成维度建议。

### 3.3 材料允许多归属

同一条材料可以自由出现在多个 Knowledge Areas、多个维度、多个条目下。Knowledge Area 不是文件夹，不要求唯一主归属。

Map Dimension 也不是互斥分类树，而是一个 Knowledge Area 内的索引视角。用户可以从不同维度找到同一条材料。

例如同一条卡帕西笔记可以同时属于：

- 人物索引 > 卡帕西；
- 主题索引 > 知识库索引方法；
- 观点冲突索引 > 手工整理 vs 自动整理。

确定性约束：同一条材料在同一个地图条目下只能出现一次。

正式材料归类的最小落点是 Map Entry。Map Proposal 可以建议 Area 和 Dimension，但确认后的材料不能只挂在 Knowledge Area 根部。

### 3.4 体检是手动二级动作

“运行体检”仍属于 Theme Agent 能力，但不是 Knowledge Map 页的主动作，也不主动触发。

体检只能读取当前地图结构和已确认材料，输出 Health Report，并可附带 Map Proposal。用户确认 proposal 后，正式地图才更新。

v1 不做定时体检、主动提醒或自动应用体检建议。

### 3.5 v1 不默认影响 RAG

Knowledge Map v1 不默认参与 RAG 检索或回答排序。

低风险入口是显式使用：用户在地图条目里点击“基于这个条目提问”时，系统可以把该条目下的 confirmed items 作为检索范围或上下文。

这避免地图尚未稳定时影响现有问答链路。

### 3.6 地图结构可以硬删除

Knowledge Map 是索引结构，不是材料本体。用户删除 Knowledge Area、Map Dimension、Map Entry 或 Map Entry Item 时，可以从地图结构中彻底删除。

删除地图结构不得删除底层 Capture、Knowledge Card 或 Theme View。

## 4. 核心对象

### 4.1 Knowledge Map

Hermes Knowledge 的唯一地图。保存地图配置、Knowledge Areas、版本和更新时间。

字段方向：

- id 或固定 singleton key；
- title；
- status；
- version；
- created_at；
- updated_at；
- last_proposal_at。

### 4.2 Knowledge Area

Knowledge Map 里的顶层类知识库区域，例如“高质量人类谈话”“动物百科”“AI 产品资料”。

字段方向：

- id；
- name；
- description；
- order_index；
- status；
- created_by；
- updated_at。

Knowledge Area 不是单独的存储、权限、重置或检索边界；它只是地图里的顶层组织区域。
用户可以手动创建暂时没有维度的 Knowledge Area。

### 4.3 Map Dimension

某个 Knowledge Area 内的地图维度，例如“人物索引”“主题索引”“观点冲突索引”“栖息地索引”。

字段方向：

- id；
- area_id；
- name；
- description；
- order_index；
- status；
- created_by；
- updated_at。

维度名来自 LLM proposal 或用户编辑，但正式维度必须用户确认。

### 4.4 Map Entry

某个维度下的条目，例如“卡帕西”“内容变现”“长期主义 vs 短期增长”。

字段方向：

- id；
- dimension_id；
- title；
- summary；
- status；
- confidence；
- evidence_json；
- created_at；
- updated_at。

### 4.5 Map Entry Item

条目下的具体材料。

允许类型：

- capture：已确认 Capture / Knowledge File；
- garden_card：Knowledge Card；
- theme_view：Theme View。

字段方向：

- id；
- entry_id；
- item_kind；
- item_id；
- reason；
- evidence_json；
- status；
- created_at；
- updated_at。

正式地图只展示 confirmed items。Map Proposal 也只能基于 confirmed material；未确认材料不进入地图建议。

Map Entry Item 是材料进入正式地图的最小单位。Area 可以为空，但正式材料不直接挂在 Area 根部。

### 4.6 Map Proposal

Theme Agent 生成的地图更新建议。Map Proposal 是 reviewable state object，不是 Durable Artifact。

Proposal 来源：

- map_generation：用户触发“生成地图建议”；
- health_check：用户手动“运行体检”时附带的地图建议。

包含：

- 建议新增、修改、删除的 Knowledge Area；
- 建议新增、修改、删除的维度；
- 建议新增、修改、删除的条目；
- 建议归类的材料；
- 不确定项；
- 证据和统计。

状态：

- pending；
- accepted；
- rejected；
- deferred；
- partially_accepted。

### 4.7 Health Report

Theme Agent 手动体检的可读输出。

包含：

- Hermes Knowledge 或某个 Knowledge Area 的覆盖情况；
- 未入图材料；
- 可能的结构问题；
- 可选 Map Proposal 引用。

Health Report 不是 Knowledge Map 的一部分，也不是长期地图维度。
Health Report 是 Durable Artifact，可以回看；它不默认参与 RAG 检索。
如果 Health Report 附带 Map Proposal，该 proposal 只能基于 confirmed material。

## 5. 入图和入地图范围

允许进入地图的正式材料：

- 已确认且非 archived 的 capture；
- 非 archived 的 garden_card；
- 已确认的 Theme View。

不进入正式地图：

- capture_drafts；
- garden_drafts；
- reading_drafts；
- 临时 answer draft；
- agent run 日志；
- 失败抽取；
- 测试占位内容；
- archived capture / card；
- 无法定位的材料。

材料有效但没有合适位置时，不强行归类，计为“未入图材料”。

## 6. Theme Agent 工作流

### 6.1 首次建地图

用户手动触发“生成知识地图”。

代码收集确定性输入：

- 已确认 captures；
- Knowledge Cards；
- Theme Views；
- 现有 graph edges；
- 现有 theme candidates。

LLM 输出 Map Proposal：

- 建议 Knowledge Areas；
- 建议维度；
- 每个维度的说明；
- 初始条目；
- 条目下建议材料；
- 归类理由；
- 不确定项。

正式地图不自动更新。

如果用户已经手动创建空 Knowledge Area，也可以只针对该 Area 生成维度和条目建议。

### 6.2 用户审阅

前端展示 proposal diff：

- 按 Knowledge Area 和维度分组；
- 展示新增 Area、维度、条目、材料归类；
- 支持批量接受一组建议；
- 支持逐条排除错误项；
- 支持改 Area 名、维度名和条目名；
- 不做复杂逐字段编辑器。

用户确认后，代码把 accepted 变更写入正式地图版本。

“生成地图建议”和“运行体检”产生的 Map Proposal 共用同一套审阅界面和确认合同。区别只在 proposal source。

### 6.3 手动运行体检

用户可以手动触发 Theme Agent 运行体检。体检不是默认自动任务，也不会主动弹出。

体检输出：

- Health Report；
- 可选 Map Proposal；
- 未入图材料统计；

体检只报告结构状态和建议，不直接修改正式 Knowledge Map。
如果体检产生 Map Proposal，该 proposal 进入同一套审阅/确认流程。

## 7. 前端体验

入口放在知识卡片页内，作为“知识卡片”的并列视图：

- 知识卡片；
- 知识地图。

建议 URL：

```text
/cards-board?view=map
```

页面结构：

- 左侧：Hermes Knowledge 概况、Knowledge Area 列表、生成地图建议按钮；
- 中间上：当前 Area 下的维度，以及当前维度下的地图条目；
- 中间下：选中条目下的具体材料列表；
- 右侧：当前条目详情、一跳关系图、证据说明；
- 顶部或摘要卡片：待审阅 Map Proposal 入口。

“运行体检”可作为二级操作放在更多菜单或侧栏辅助区，不作为第一主按钮。

具体材料列表必须展示：

- 标题；
- 类型：Knowledge File / Knowledge Card / Theme View；
- 摘要；
- 归类原因；
- 状态；
- 打开 Hermes。

如果 capture 有 Knowledge File，可提供“打开 Knowledge File”。这不是 Obsidian 入口，也不是主账本入口。

## 8. 知识图谱关系

Knowledge Graph 仍然保留，但定位为辅助关系浏览器。

它回答：

- 当前地图条目和哪些材料有关；
- 当前 Knowledge Card 使用了哪些 Capture；
- 当前 Capture 与哪些 Capture 语义相似；
- 当前 Theme View 聚合了哪些材料。

它不负责：

- 决定 Knowledge Area 或地图维度；
- 替代地图条目列表；
- 自动影响 RAG。

v1 图谱使用一跳 ego graph，避免复杂多跳关系和毛线团。

## 9. 确定性合同

LLM 负责：

- 建议 Knowledge Areas 和地图维度；
- 解释归类理由；
- 提出 Map Proposal。
- 生成 Health Report 的自然语言摘要。

代码负责：

- 正式入图范围；
- schema；
- proposal 状态机；
- 用户确认；
- 版本；
- 计数；
- 体检统计；
- 稳定展示顺序；
- 去重；
- 打开链接；
- 引用和证据字段；
- 不让草稿、失败抽取、测试材料进入正式地图。

四层防劣化保护：

1. 稳定配置：确认后的 Knowledge Area 和维度长期沿用；
2. 变更建议：结构变更只能进入 proposal；
3. 证据约束：每个建议都要带材料、计数或行为证据；
4. 用户确认：正式地图只接受用户确认后的变更。

## 10. v1 不做

- 不做多个 Knowledge Map；
- 不让 LLM 自动写入正式地图；
- 不把地图默认写成 Markdown 主账本；
- 不恢复 Obsidian 主存储模型；
- 不引入 Neo4j；
- 不做多跳推理；
- 不让图谱默认参与 RAG；
- 不硬编码某类材料的分类体系；
- 不追踪鼠标停留、点击热度等细粒度行为；
- 不读外部浏览器历史。
- 不做智能排序、代表材料、推荐置顶或先读材料。
- 不做定时体检、主动体检提醒或自动应用体检建议。
- 不因为删除地图索引而删除底层材料。
- 不让 Theme Agent Health Check 统计或引用未确认材料。

## 11. 验收标准

产品验收：

- 用户能打开知识地图视图；
- 用户能看到 Hermes Knowledge 的 Knowledge Areas；
- 用户能点击 Area，再看到该 Area 内的长期地图维度；
- 用户能点击维度并看到条目；
- 用户能点击条目并看到具体材料；
- 每条材料有类型、摘要、归类原因和 Hermes 打开入口；
- 用户能生成地图更新建议；
- 用户能手动运行体检并看到 Health Report；
- 用户能接受或拒绝建议；
- 正式地图只显示已确认变更。

技术验收：

- 草稿、失败抽取、测试材料、archived 资产不进入正式地图；
- 同一材料可属于多个条目，但同一条目下不重复；
- 删除 Area、Dimension、Entry 或 Entry Item 只删除地图索引，不删除底层材料；
- confirmed map 不因重新运行 Theme Agent 自动漂移；
- 体检不自动修改 confirmed map；
- v1 不影响现有 RAG 默认检索；
- 现有 Knowledge Card、Theme View、Knowledge Graph 功能不退化。

## 12. 后续演进

后续可以考虑：

- 用户主动从地图条目发起限定范围问答；
- 后续重新设计周期体检和订阅；
- 支持导出地图 Markdown；
- 让 LLM 解释已有 graph edges，但不决定 edge 入库；
- 在地图稳定后，评估 RAG 是否显式读取 confirmed map context。

# Hermes Knowledge Map and Theme Agent Design

更新日期：2026-06-27

## 1. 背景

Hermes Knowledge 已经从早期 Obsidian 主账本模型调整为 Knowledge File + Web/DB 主模型。根据 ADR 0003，Hermes 只保留 Capture Notes 和 Raw Evidence 这类持久 Markdown Knowledge Files；Knowledge Cards、Theme Views、graph relationships、review state、drafts、source maps 等长期结构对象以 Web/DB 为主。

因此，本设计不再把“地图”实现成 Obsidian MOC 或默认 Markdown 文件。知识地图是 Web/DB 结构化对象，Markdown 只作为可选导出。

这次要解决的问题不是“画一个看起来像图谱的页面”，而是让 Hermes 能定期回答：

- 这个知识库现在有什么？
- 当前知识库应该按哪些维度组织？
- 每个主题、人物、分类或冲突下面有哪些具体材料？
- 哪些方向是用户近期关心但材料偏薄的“待补强方向”？
- 哪些地图更新建议需要用户确认？

节点图谱仍然保留，但只是局部关系浏览器，不是主体验。

## 2. 产品定位

v1 定位为 **Knowledge Map + Theme Agent Health Check**。

核心用户价值：

- 用户每周花几分钟就能看清知识库结构；
- Theme Agent 可以提出地图维度、归类建议和待补强方向；
- 用户确认后，地图成为长期导航层；
- 用户可以从地图条目直接看到具体 Knowledge File、Knowledge Card 和 Theme View；
- 局部图谱只辅助解释“谁和谁有关，为什么有关”。

## 3. 关键决策

### 3.1 一个知识库只有一个地图

每个知识库只有一个 Knowledge Map。不同用途不通过多个 map 解决，而是通过同一个地图里的多个索引维度解决。

例如：

- 高质量人类谈话库：人物索引、主题索引、观点冲突索引；
- 动物百科库：动物分类、栖息地、行为习性、保护状态；
- AI 产品库：能力模块、业务场景、指标体系、案例、风险边界。

v1 不提供多地图管理，不引入“主地图/副地图”的复杂度。

### 3.2 维度由 LLM 提议，用户确认后稳定

Theme Agent 可以根据知识库内容和用户目标提出地图维度，但确认后的维度名是长期配置。后续体检必须沿用已确认维度，不得每次重新发明分类体系。

Theme Agent 想新增、合并、重命名维度时，只能生成 proposal，用户确认后才修改正式地图。

### 3.3 材料允许多归属

同一条材料可以出现在多个维度、多个条目下。

例如同一条卡帕西笔记可以同时属于：

- 人物索引 > 卡帕西；
- 主题索引 > 知识库索引方法；
- 观点冲突索引 > 手工整理 vs 自动整理。

确定性约束：同一条材料在同一个地图条目下只能出现一次。

### 3.4 七天动态不是正式维度

“七天动态”默认属于 Health Report 的临时视图，不作为长期地图维度。

Health Report 可以展示：

- 最近 7 天新增材料；
- 最近 7 天高频问题；
- 最近 7 天保存成笔记或卡片的内容；
- 最近 7 天待确认归类。

如果某个短期动态值得长期沉淀，Theme Agent 生成 proposal，例如“是否把内容变现加入主题索引”。

### 3.5 v1 不默认影响 RAG

Knowledge Map v1 不默认参与 RAG 检索或回答排序。

低风险入口是显式使用：用户在地图条目里点击“基于这个条目提问”时，系统可以把该条目下的 confirmed items 作为检索范围或上下文。

这避免地图尚未稳定时影响现有问答链路。

## 4. 核心对象

### 4.1 Knowledge Map

知识库的唯一地图。保存地图配置、正式维度、版本和更新时间。

字段方向：

- id 或固定 singleton key；
- title；
- status；
- version；
- created_at；
- updated_at；
- last_health_check_at。

### 4.2 Map Dimension

地图维度，例如“人物索引”“主题索引”“观点冲突索引”。

字段方向：

- id；
- name；
- description；
- order_index；
- status；
- created_by；
- updated_at。

维度名来自 LLM proposal 或用户编辑，但正式维度必须用户确认。

### 4.3 Map Entry

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

### 4.4 Map Entry Item

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
- order_index；
- created_at；
- updated_at。

正式地图只展示 confirmed items。Theme Agent 认为可能相关但尚未确认的内容进入 proposal，不直接进入正式地图。

### 4.5 Map Proposal

Theme Agent 生成的地图更新建议。

包含：

- 建议新增、修改、删除的维度；
- 建议新增、修改、删除的条目；
- 建议归类的材料；
- 不确定项；
- 待补强方向；
- 证据和统计。

状态：

- pending；
- accepted；
- rejected；
- deferred；
- partially_accepted。

### 4.6 Health Report

体检报告。保存一次体检的结构化结果和可读摘要。

包含：

- 总材料数；
- Knowledge Card 数；
- Theme View 数；
- 各维度覆盖；
- 未入图材料数；
- 待确认归类数；
- 最近 7 天动态；
- 待补强方向；
- 建议动作。

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
- 最近用户问题；
- 最近保存行为；
- 现有 graph edges；
- 现有 theme candidates。

LLM 输出 Map Proposal：

- 建议维度；
- 每个维度的说明；
- 初始条目；
- 条目下建议材料；
- 归类理由；
- 不确定项。

正式地图不自动更新。

### 6.2 用户审阅

前端展示 proposal diff：

- 按维度分组；
- 展示新增维度、条目、材料归类；
- 支持批量接受一组建议；
- 支持逐条排除错误项；
- 支持改维度名和条目名；
- 不做复杂逐字段编辑器。

用户确认后，代码把 accepted 变更写入正式地图版本。

### 6.3 定期体检

v1 支持手动运行，后续可选每周定时运行。定时运行只生成报告和建议，不自动应用。

体检输出：

- Health Report；
- Map Proposal；
- 待补强方向；
- 未入图材料；
- 待确认归类；
- 可选收集建议。

### 6.4 待补强方向

产品文案使用“待补强方向”，不使用“内容空白”。

Theme Agent 判断待补强方向时必须基于证据：

- 该条目或方向当前材料数偏少；
- 最近用户多次提问或保存相关内容；
- 同一维度下相邻条目材料更多，形成明显不平衡。

示例：

```text
内容变现只有 2 条正式材料，但最近 7 天被问到 5 次；同维度下内容创作方法已有 8 条，因此建议补采集内容变现案例。
```

LLM 负责判断是否值得提醒；代码负责提供计数、近期问题统计和同维度分布。

## 7. 前端体验

入口放在知识卡片页内，作为“知识卡片”的并列视图：

- 知识卡片；
- 知识地图。

建议 URL：

```text
/cards-board?view=map
```

页面结构：

- 左侧：知识库概况、地图维度列表、运行体检按钮；
- 中间上：当前维度下的地图条目；
- 中间下：选中条目下的具体材料列表；
- 右侧：当前条目详情、一跳关系图、证据说明；
- 顶部或摘要卡片：本周体检结论、待补强方向、地图更新建议入口。

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

- 决定地图维度；
- 替代地图条目列表；
- 执行健康检查；
- 自动影响 RAG。

v1 图谱使用一跳 ego graph，避免复杂多跳关系和毛线团。

## 9. 确定性合同

LLM 负责：

- 建议地图维度；
- 解释归类理由；
- 判断待补强方向；
- 生成 Health Report 的自然语言摘要；
- 提出 Map Proposal。

代码负责：

- 正式入图范围；
- schema；
- proposal 状态机；
- 用户确认；
- 版本；
- 计数；
- 排序；
- 去重；
- 打开链接；
- 引用和证据字段；
- 不让草稿、失败抽取、测试材料进入正式地图。

四层防劣化保护：

1. 稳定配置：确认后的维度长期沿用；
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
- 不硬编码某个知识库的分类体系；
- 不追踪鼠标停留、点击热度等细粒度行为；
- 不读外部浏览器历史。

## 11. 验收标准

产品验收：

- 用户能打开知识地图视图；
- 用户能看到一个知识库的长期地图维度；
- 用户能点击维度并看到条目；
- 用户能点击条目并看到具体材料；
- 每条材料有类型、摘要、归类原因和 Hermes 打开入口；
- 用户能手动运行体检；
- 体检能输出待补强方向和地图更新建议；
- 用户能接受或拒绝建议；
- 正式地图只显示已确认变更。

技术验收：

- 草稿、失败抽取、测试材料、archived 资产不进入正式地图；
- 同一材料可属于多个条目，但同一条目下不重复；
- 七天动态只存在于 Health Report，不默认成为正式维度；
- confirmed map 不因重新运行 Theme Agent 自动漂移；
- v1 不影响现有 RAG 默认检索；
- 现有 Knowledge Card、Theme View、Knowledge Graph 功能不退化。

## 12. 后续演进

后续可以考虑：

- 用户主动从地图条目发起限定范围问答；
- 对 Health Report 增加周期订阅；
- 支持导出地图 Markdown；
- 让 LLM 解释已有 graph edges，但不决定 edge 入库；
- 在地图稳定后，评估 RAG 是否显式读取 confirmed map context。

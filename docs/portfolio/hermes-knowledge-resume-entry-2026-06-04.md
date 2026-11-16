# Hermes Knowledge 简历项目介绍

更新日期：2026-06-04（口径更新：2026-11）

## 推荐简历写法

**Hermes Knowledge｜AI-native 个人知识工作台｜个人项目**

基于 Hermes Agent Framework、SQLite 与自研 Web 前端，设计并实现一个面向个人深度学习的 AI-native 知识工作台。系统支持从微信、网页、文件、小红书图文和图片/OCR 等入口接收材料，先生成可审阅 Capture 草稿，用户确认后才写入知识库；再由 Library Agent 自动维护元数据、章节切分和 embedding；通过 evidence-first 检索、卡片按来源数与引用数加权、资料 → 卡片 → 专题的知识结构，以及自研卡片板与知识图谱，实现可追溯问答、卡片沉淀、专题组织和关系浏览。Obsidian 仅作为可选的 Markdown 导出/外部编辑兼容。

**项目亮点：**

- 设计“草稿预览 - 用户确认 - 入库 - Library 后处理”的知识入库闭环，避免 AI 自动写入错误内容污染长期知识库。
- 将知识库对象拆分为 raw archive、capture、topic/专题、Knowledge Card、review item、section embedding 等层次，支持长文定位、来源追溯和跨材料复用。
- 实现 Capture、Library、RAG、Topic、Cards、Review、Graph 等能力模块，覆盖材料进入、结构维护、问答、专题聚合、卡片生成、每日回顾和关系浏览。
- 针对真实使用问题迭代长文处理、小红书图文提取、微信字数限制、重复 Knowledge Card、防止引用错列、数据一致性等产品细节。
- 拆分 Knowledge 与 Life Assistant 为独立 capability projects，并通过同一个 Hermes runtime 统一接入微信/飞书，明确数据边界和工具职责。

## 更短版本

设计并实现 Hermes Knowledge，一个基于 Hermes Agent Framework + SQLite + 自研 Web 前端的 AI-native 个人知识工作台。系统支持从微信、网页、文件和图文材料生成可审阅草稿，经用户确认后入库；由 Library Agent 维护 metadata、章节和 embedding；通过 evidence-first RAG、资料 → 卡片 → 专题的知识结构、自研卡片板和知识图谱实现可追溯问答、卡片沉淀、专题组织和知识关系浏览。项目重点解决长文处理、入库确认、引用可信、知识库数据一致性和微信输出限制等真实 AI 产品问题。

## 30 秒口播

我做了一个 AI-native 个人知识工作台，叫 Hermes Knowledge。它把微信里的公众号、小红书、文件或图片接进 Web 工作台：Hermes 不会直接入库，而是先生成可审阅草稿；我可以要求全文保存、重点总结或某部分详细展开，满意后再回复“存库”。入库后 Library 会补目录、属性、章节和 embedding；之后我可以基于知识库问问题，RAG 会先找证据、再按来源和引用给卡片加权，回答带来源；多篇材料形成关系时，Cards 会沉淀成知识卡片，再用自研卡片板和知识图谱展示关系。这个项目的重点不是“AI 总结一篇文章”，而是把知识从输入、确认、维护、检索、沉淀到回顾串成一个可控闭环。

## 面试标题可选

- Hermes Knowledge：基于 Agent 的个人知识工作台
- AI-native PKM：从微信材料到 Web 知识资产
- 面向个人研究场景的可追溯 RAG、卡片板与知识图谱

## 能力标签

AI Product Management / Agent Workflow / RAG / Knowledge Management / Web Product / SQLite / Human-in-the-loop / Product Prototyping / Evaluation Thinking

## 面试时要强调

- 这不是“接一个大模型总结文章”，而是完整知识生命周期设计。
- 产品核心是“可信入库”和“可复用知识对象”，不是自动化越多越好。
- Web 工作台是主载体；Markdown 导出保留可迁移性，Obsidian 只是可选外部编辑器。
- LLM 负责理解、整理和判断；确定性代码负责状态流转、文件写入、数据一致性、引用约束和边界控制。

## 面试时不要说过头

稳妥口径：

- 这是个人可用的 v0.1 产品原型。
- 核心知识库 pipeline 已跑通。
- 多模态和极长文处理还需要更多真实样本继续验证。
- RAG 已支持 evidence-first 检索、图谱扩展和可读来源；已完成一次 28 条 query 的 DashScope 重跑（Recall@5 0.389→0.833，G3 1.0，负样本 9/10），答案质量仍需持续评测。
- 如果要给其他用户使用，还需要安装配置、权限隔离、错误恢复和运行观测产品化。

避免口径：

- 不说“成熟商业产品”。
- 不说“完全自动知识库”。
- 不说“所有多模态都稳定支持”。
- 不说“RAG 永远准确”。
- 不说“Cards 能自动沉淀所有有价值知识”。


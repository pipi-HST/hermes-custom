# Hermes Knowledge 简历项目介绍

更新日期：2026-06-04

## 推荐简历写法

**Hermes Knowledge｜AI-native 个人知识管理系统｜个人项目**

基于 Hermes Agent Framework、Obsidian、SQLite 与多 Agent 工具链，设计并实现一个面向个人深度学习的 AI-native 知识管理系统。系统支持从微信、网页、文件、小红书图文和图片/OCR 等入口接收材料，先生成可审阅 Capture 草稿，用户确认后入库到 Obsidian；再由 Library Agent 自动维护元数据、目录、章节切分和 embedding；通过 Graph-aware RAG、Theme/Garden 知识卡片与 Obsidian Canvas，实现可追溯问答、主题发现、知识卡片沉淀和关系可视化。

**项目亮点：**

- 设计“草稿预览 - 用户确认 - Obsidian 入库 - Library 后处理”的知识入库闭环，避免 AI 自动写入错误内容污染长期知识库。
- 将知识库对象拆分为 raw archive、capture、theme/MOC、Garden card、review item、section embedding 等层次，支持长文定位、来源追溯和跨材料复用。
- 实现 Capture、Library、RAG、Theme、Garden、Review、Canvas 等能力模块，覆盖材料进入、结构维护、问答、主题聚合、卡片生成、每日回顾和可视化展示。
- 针对真实使用问题迭代长文处理、小红书图文提取、微信字数限制、重复 Garden card、防止引用错列、DB/Obsidian 同步修复等产品细节。
- 拆分 Knowledge 与 Life Assistant 为独立 capability projects，并通过同一个 Hermes runtime 统一接入微信/飞书，明确数据边界和工具职责。

## 更短版本

设计并实现 Hermes Knowledge，一个基于 Hermes Agent Framework + Obsidian + SQLite 的 AI-native 个人知识管理系统。系统支持从微信、网页、文件和图文材料生成可审阅草稿，经用户确认后入库；由 Library Agent 维护 metadata、目录、章节和 embedding；通过 Graph-aware RAG、Theme/Garden 知识卡片和 Obsidian Canvas 实现可追溯问答、主题发现和知识关系可视化。项目重点解决长文处理、入库确认、引用可信、知识库数据一致性和微信输出限制等真实 AI 产品问题。

## 30 秒口播

我做了一个 AI-native 个人知识管理系统，叫 Hermes Knowledge。它接在微信和 Obsidian 之间：我把公众号、小红书、文件或图片发给 Hermes，它不会直接入库，而是先生成可审阅草稿；我可以要求全文保存、重点总结或某部分详细展开，满意后再回复“存库”。入库后 Library 会补目录、属性、章节和 embedding；之后我可以基于知识库问问题，RAG 会带来源回答；多篇材料形成关系时，Garden 会沉淀成知识卡片，并用 Obsidian Canvas 做关系可视化。这个项目的重点不是“AI 总结一篇文章”，而是把知识从输入、确认、维护、检索、沉淀到回顾串成一个可控闭环。

## 面试标题可选

- Hermes Knowledge：基于 Agent 的个人知识管理工作台
- AI-native PKM：从微信材料到 Obsidian 知识资产
- 面向个人研究场景的可追溯 RAG 与知识卡片系统

## 能力标签

AI Product Management / Agent Workflow / RAG / Knowledge Management / Obsidian / SQLite / Human-in-the-loop / Product Prototyping / Evaluation Thinking

## 面试时要强调

- 这不是“接一个大模型总结文章”，而是完整知识生命周期设计。
- 产品核心是“可信入库”和“可复用知识对象”，不是自动化越多越好。
- Obsidian 是长期知识容器，Hermes 是入口、加工、维护和问答编排层。
- LLM 负责理解、整理和判断；确定性代码负责状态流转、文件写入、数据一致性、引用约束和边界控制。

## 面试时不要说过头

稳妥口径：

- 这是个人可用的 v0.1 产品原型。
- 核心知识库 pipeline 已跑通。
- 多模态和极长文处理还需要更多真实样本继续验证。
- RAG 已支持图谱扩展和可读来源，但答案质量仍需要持续评测。
- 如果要给其他用户使用，还需要安装配置、权限隔离、错误恢复和运行观测产品化。

避免口径：

- 不说“成熟商业产品”。
- 不说“完全自动知识库”。
- 不说“所有多模态都稳定支持”。
- 不说“RAG 永远准确”。
- 不说“Garden 能自动沉淀所有有价值知识”。


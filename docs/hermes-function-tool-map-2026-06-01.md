# Hermes 功能与工具地图

更新日期：2026-06-01

这份文档用来回答两个问题：

1. 现在 Hermes 到底有哪些功能和工具。
2. 长报告、长总结、审计结果能不能用文件发给你，而不是塞进微信消息里。

## 1. 当前整体链路

```text
微信 / 飞书
  -> Hermes Gateway
     /Users/pipi/Downloads/hermes-agent-main
  -> personal-assistant toolset
  -> hermes-custom bridge / agents
     /Users/pipi/hermes-custom
  -> SQLite + Obsidian Vault
     /Users/pipi/hermes-custom/data/captures.db
     /Users/pipi/Obsidian/hermes-vault
```

人话版：

- `hermes-agent-main` 管网关、会话、微信/飞书收发、工具注册、主 Agent 调度。
- `hermes-custom` 管你的个人知识库 Agent：Capture、Library、Theme、Garden、Canvas、Review、RAG、Life 等。
- SQLite 是结构化状态和索引。
- Obsidian Vault 是给人看的知识库界面，包括 `captures/`、`themes/`、`garden/`、`canvas/` 等。

## 2. personal-assistant 当前工具

当前 `personal-assistant` 工具集共有 24 个工具：

```text
agent_run_status
audit_library
capture_content
commit_capture_draft
commit_garden_draft
continue_rag_answer
daily_review
generate_canvas
graph_report
life_tick
log_decision
record_life_event
refine_capture
review_feedback
review_followup
revise_capture_draft
revise_garden_draft
search_my_notes
send_message
show_capture_draft
synthesize_garden
theme_candidates
theme_scan
wellness_check
```

## 3. 按功能分组

### Capture：把外部材料变成可确认的草稿

- `capture_content`
  - 抓取链接或接收文本。
  - 生成 Capture 草稿。
  - 支持用户要求，比如“全文保存”“总结核心内容”“方法论部分详细一些”。
  - 默认不是直接入库，而是先给你确认。

- `show_capture_draft`
  - 展开最近的 Capture 草稿。
  - 用于“展开草稿 / 看全文 / 继续看”。
  - 只读草稿，不入库。

- `revise_capture_draft`
  - 按你的修改意见重写当前草稿。
  - 用于“这部分没写到位，重新提取重点”。

- `refine_capture`
  - 对已经入库的 Capture 做后处理或优化。

- `commit_capture_draft`
  - 你确认后，把草稿写入 Obsidian 和 DB。

### Library：维护知识库健康

- `audit_library`
  - 扫描 DB、Capture、Raw、Garden、Theme 等一致性。
  - 清理重复草稿。
  - 补长文目录纲要。
  - 同步你手动删除的文件。
  - 修复或补齐部分 frontmatter / 元数据。

当前定位：Library Agent 是知识库管家，不负责回答问题，也不负责生成观点。

### RAG：基于知识库回答问题

- `search_my_notes`
  - 从 Garden、Theme、Capture、章节索引、来源网络里找材料。
  - 返回答案和引用。
  - 重点是广泛找材料，但不能乱堆候选。

- `continue_rag_answer`
  - 当回答太长或需要继续展开时，沿同一个检索上下文继续回答。

当前定位：RAG Agent 是“问答助手”，不是知识库维护工具。

### Theme / MOC：整理主题页和关联

- `theme_scan`
  - 扫描最近或全库内容，发现可以整理成主题页或知识关联的问题。

- `theme_candidates`
  - 展示候选主题。
  - 让主 Agent 能沿 `question_id` 继续生成 Garden 或 Theme。

- `graph_report`
  - 输出知识库图谱/连接情况报告。

当前定位：Theme Agent 负责主题聚合和 MOC，不负责把观点写成 Garden Card。

### Garden：沉淀知识卡片

- `synthesize_garden`
  - 根据多篇材料生成 Garden Card 草稿。
  - 现在的卡片可以是观点、方法、事实、风险边界、开放问题等，类型由系统建议，不强迫你分类。
  - 生成前应检查是否已经有高度重复的卡片。

- `revise_garden_draft`
  - 按你的意见修改 Garden Card 草稿。

- `commit_garden_draft`
  - 你确认后，把 Garden Card 入库。

当前定位：Garden Agent 负责把多篇材料之间真正有价值的联系沉淀成卡片，而不是每篇文章都强行生成观点。

### Canvas：把 Garden Card 做成可视化结构

- `generate_canvas`
  - 生成 Obsidian Canvas。
  - 当前更适合展示一组 Garden Card 的结构关系，而不是简单 MOC 图。
  - LLM 负责判断这些卡片是否真的形成结构；确定值得画后，确定性 renderer 负责生成 Obsidian `.canvas` 文件。

当前定位：Canvas 是 Garden Card 的结构化聚类视图，不是替代 Obsidian，也不是普通看板。

### Review：每天讲一个知识点

- `daily_review`
  - 从知识库里选一个点，给你讲清楚。
  - 更像“怕你忘记的知识助手”，不是考试官。

- `review_feedback`
  - 记录你对本次回顾的反馈，比如有用、以后再讲、不用复习。

- `review_followup`
  - 针对刚讲过的 Review 内容继续追问。
  - 不应该误调用 Capture 草稿工具。

当前定位：Review Agent 负责回顾和讲解，不要求你背题。

### Life Assistant：健康和生活辅助

- `record_life_event`
  - 记录生活/健康事件。

- `life_tick`
  - 定时检查，比如久坐提醒。

- `wellness_check`
  - 查询或记录健康、运动、睡眠、心情相关内容。

当前定位：Life Assistant 是独立方向，后续可以和 Clarify Life Assistant 合并，但不应该污染知识库 Agent。

### Ops / 消息 / 可观测性

- `agent_run_status`
  - 查看 Agent 运行状态。

- `log_decision`
  - 记录重大决策。

- `send_message`
  - 主动发消息。
  - 支持文本，也支持通过 `MEDIA:<local_path>` 发送本地图片或文件。

## 4. 长输出能不能改成发文件？

可以，基础能力已经具备。

`send_message` 的工具描述里明确支持：

```text
MEDIA:<local_path>
```

也就是说，Hermes 可以先在本地生成一个 `.md`、`.txt`、`.canvas`、`.json` 或报告文件，再用：

```text
MEDIA:/Users/pipi/hermes-custom/data/reports/xxx.md
```

把它作为原生附件发给你。

## 5. 哪些内容适合发文件

适合发文件：

- Library 审计报告。
- RAG 的长答案和完整引用报告。
- Capture 的长草稿全文。
- Garden Card 批量扫描结果。
- Theme / Graph 全库关联报告。
- Canvas 说明、结构化预览和导出的 `.canvas`。
- Hermes 工具地图、项目路线图、测试报告。

适合直接发微信短消息：

- 成功/失败状态。
- 草稿预览。
- 需要你确认的选项。
- 一句话结论。
- 下一步操作提示。

## 6. 推荐的输出策略

以后长内容建议统一成“两段式”：

```text
微信短消息：
我已经生成完整报告，摘要如下：
1. ...
2. ...
3. ...

完整文件：
MEDIA:/path/to/report.md
```

这样有几个好处：

- 微信里不容易被截断。
- 主 Agent 不需要把长内容再压缩一遍。
- 你可以在电脑上直接打开完整文件看。
- 后续 RAG / Review / Library 都能引用这个报告文件。

## 7. 还需要补的一层

现在 `send_message` 已经能发文件，但各个业务工具还没有完全统一“长输出生成文件”的协议。

建议下一步做一个很薄的约定：

```json
{
  "chat_preview": "微信里看的短回复",
  "artifact": {
    "type": "markdown_report",
    "path": "/absolute/path/to/report.md",
    "title": "完整报告"
  }
}
```

主 Agent 看到 `artifact.path` 后，如果用户在微信里，就优先用 `send_message` 附件发送文件。

这不是重构 Agent，只是补一个跨工具输出约定。最应该先接入的是：

1. `audit_library`
2. `search_my_notes`
3. `capture_content` / `show_capture_draft`
4. `synthesize_garden`
5. `graph_report`

## 8. 当前判断

结论：可以用“发文件”解决长上下文/长输出问题，而且这条路比继续压微信消息更合理。

短期不需要大改网关，只需要让关键工具在长输出时生成本地文件，并让主 Agent 调 `send_message` 发 `MEDIA:/path/to/file`。

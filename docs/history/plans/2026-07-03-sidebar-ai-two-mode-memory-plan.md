---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# 侧边栏 AI 两模式、上下文记忆与过程展示改造计划

## 1. 目标

将知识库文章侧边栏 AI 从四个可见模式收敛为两个模式：

- `直出`
- `知识库+联网`

本次改造的核心目标：

- 减少模式数量，砍掉可见的 `知识库` 和 `联网` 独立 tab。
- 支持同一篇文章内的多轮上下文记忆。
- 避免旧选区在后续轮次里被自动当成本轮选区。
- `直出` 模式不走 Planner、不新检索、不联网。
- `知识库+联网` 模式保留 Planner 和完整检索链路。
- 展示远程 API 显式返回的完整可展示过程字段，并保留知识库+联网的完整执行链路。

## 2. 模式语义

### 2.1 直出

UI label 使用 `直出`。

wire value v1 可继续复用现有 `current_article_only`，避免扩大后端枚举和旧测试改动。

语义：

- 不走 Planner。
- 不新检索知识库。
- 不联网。
- 调用普通 LLM 基于当前文章、当前选区、短期历史、共享长期摘要直接回答。
- 可以参考同一篇文章内共享长期摘要里的既有外部结论。
- 不在答案正文里提示“使用了长期摘要”。
- 不显示 K/W 来源，因为本轮没有新检索。

### 2.2 知识库+联网

UI label 使用 `知识库+联网`。

wire value 使用现有 `local_web`。

语义：

- 保留 Planner。
- history 和 history_summary 参与 Planner 与最终回答。
- 默认由 Planner 决定是否联网。
- `联网补充` 使用 `mode=local_web + force_web=true`，不再隐藏调用 `web_only`。
- 展示完整执行链路：Planner、query、知识库命中、网页候选、采用/跳过原因、warnings、耗时。

### 2.3 旧模式兼容

- 新 UI 不再提供 `local_only` 和 `web_only` tab。
- 后端继续兼容旧 `local_only` / `web_only`。
- 旧历史消息仍可渲染旧 mode label。
- 旧消息的重试按钮隐藏，或映射到 `知识库+联网`，避免重新暴露旧 tab。

## 3. 文章、Tab 与生成状态

### 3.1 同一篇文章内

- 两个 tab 各自显示独立短期历史。
- 切 tab 不清空历史。
- 发送前切 tab 保留待发送选区。
- 生成中禁止切 tab。
- 禁切时显示提示：`请先停止或等待回答完成`。

### 3.2 切换文章

切换文章时：

- 停止正在生成的 AI 请求。
- 取消 queued send。
- 取消 fallback reasoning summary。
- 取消本轮 history summary 压缩。
- 清空两个 tab 的短期历史。
- 清空共享长期摘要。
- 清空选区和过程 metadata。

### 3.3 停止

用户点击停止时：

- abort 当前请求。
- 当前轮不进入成功历史。
- 当前轮不进入长期摘要。
- 取消该轮所有后处理。

## 4. 选区策略

### 4.1 本轮选区

当前代码已有本轮选区上限：

- 前端 `MAX_SELECTION_SEARCH_CHARS = 1000`
- 后端 `MAX_SELECTION_SEARCH_CHARS = 1000`

本次保持 1000 字上限。

发送后立即：

- 清空内部 `selectionContext`。
- 清空页面 DOM 高亮 selection。
- 本轮消息仍保存自己的 `requestContext`，用于展示、重试、联网补充。

### 4.2 历史选区

带选区的历史 user content 在发送给后端的 history payload 中加短前缀：

```text
[当轮选区: ...] 用户原问题
```

规则：

- UI 不显示这个前缀。
- 前缀从原先讨论的 160 字提高到 300 字。
- Planner 可看到 300 字历史选区摘要。
- 最终 web query 仍保持单条 120 字上限。

UI 的 `selectionPreview` 可以继续沿用现有 160 字，不必同步变为 300 字。

## 5. 短期 History

每个 tab 独立维护短期 history。

发送给后端时：

- 只发送最近 6 个完整成功问答对。
- 当前问题不重复塞进 history，而是单独放在 `question` 字段。
- history 只包含 `role + content`。
- 不包含 sources、research trace、reasoning_trace、warnings。
- 失败、停止、超时轮次不进入 history。

## 6. 共享长期摘要

### 6.1 作用域

共享长期摘要是同一篇文章内两个 tab 共享的长期记忆。

它不同于短期 history：

- 短期 history 按 tab 独立。
- 长期 `history_summary` 在同一篇文章内共享。
- 切文章清空。

两个模式都使用 `history_summary`。

### 6.2 内容

长期摘要是自然语言混合摘要。

保留：

- 讨论主题。
- 已确认结论。
- 重要实体/术语。
- 未解决问题。
- 联网补充成功回答中的有效结论。

不保留：

- 用户偏好。
- 完整用户原话。
- reasoning_trace。
- research trace。
- sources。
- warnings。

摘要不保留来源边界。产品取舍是答案体验优先于可审计性。

最大长度约 1000 中文字符，超过后再次压缩到 1000 字左右。

### 6.3 压缩时机

每次成功回答完成后：

- 后端异步统一扫描同一篇文章两个 tab 的超窗成功问答。
- 将所有超出最近 6 轮窗口的成功问答增量压进共享摘要。
- 压缩输入为 `old_summary + overflow_successful_messages`。

压缩由后端普通 LLM 完成：

- 不走 Planner。
- 不检索知识库。
- 不联网。

### 6.4 压缩失败

如果摘要压缩失败：

- 不阻断主聊天流程。
- 退化为旧 summary + 最近 6 个完整成功问答对。
- UI 轻提示：`长期记忆压缩失败，已使用最近 6 轮继续。`

## 7. Summary API 与竞态

### 7.1 字段合同

selection-chat 请求体新增：

```ts
history?: Array<{ role: "user" | "assistant"; content: string }>;
history_summary?: string;
history_summary_version?: number;
force_web?: boolean;
```

响应或 SSE final metadata 新增：

```ts
updated_history_summary?: string;
updated_history_summary_version?: number;
```

共享 summary 使用单调递增 `summary_version`。

前端只接受版本更大的 summary update，避免旧异步事件覆盖新摘要。

### 7.2 SSE

流式请求中：

- 答案正常流式返回。
- `complete.response.meta` 返回最终 answer metadata。
- 摘要压缩完成后追加 `summary_updated` SSE 事件。

### 7.3 非流式

普通 `/selection-chat` endpoint 必须支持同一套合同。

区别：

- 非流式不发 `summary_updated` 事件。
- 直接在最终 JSON `meta` 中返回 `updated_history_summary` 和 `updated_history_summary_version`。

### 7.4 等待摘要时发送下一轮

如果用户发送下一轮时 summary 仍在更新：

- 阻塞 queued send，直到 summary 成功或失败。
- 后端 summary 任务硬超时 30 秒。
- 30 秒失败后自动继续发送，使用旧 summary + 最近 6 轮。
- queued send 使用点击发送那一刻的 question 快照，不跟随后续输入框变化。

等待期间：

- 点停止：取消 queued send。
- 切文章：取消 queued send，清空历史和 summary。
- 切 tab：禁止切换，并显示提示。

## 8. UI History 保留与裁剪

语义发送窗口和 UI 回看窗口分离：

- 发送给后端：`history_summary + 当前 tab 最近 6 个完整成功问答对`。
- UI：显示全部可见历史。

为避免页面状态无限膨胀：

- 同一篇文章最多保留 100 条 UI 消息。
- 超过 100 条时，删除最旧且已压缩进 summary 的消息。
- 每条被压缩成功的消息标记：

```ts
compressedIntoSummaryVersion?: number;
```

如果消息还没有压缩成功，不裁剪。

## 9. Reasoning Trace / 过程展示

### 9.1 字段名

统一使用：

```ts
reasoning_trace
```

建议结构：

```ts
interface ReasoningTrace {
  api_reasoning?: string | Record<string, unknown> | unknown[];
  reasoning_summary?: string;
  fallback_reason?: string;
  source?: "api" | "fallback" | "missing";
}
```

### 9.2 直出模式

直出答案继续流式。

`reasoning_trace` 不新增单独 SSE event，放在 final `complete.response.meta.reasoning_trace`。

远程 API 如果显式返回可展示过程字段，则填入 `reasoning_trace.api_reasoning`。

候选字段按优先级读取：

- `reasoning`
- `reasoning_content`
- `reasoning_summary`
- `thoughts`
- `trace`

如果字段是对象或数组：

- 原样保留 JSON。
- UI 用 pretty JSON 展示。

如果 API 没有返回可展示过程字段：

- 保留答案流式。
- 后端额外轻量 LLM 调用生成 fallback `reasoning_summary`。
- UI 明确标注：`模型未返回可展示推理字段，以下为依据摘要。`
- 如果 fallback summary 失败，显示：`模型未返回可展示推理字段，依据摘要生成失败。`
- 不影响答案。

### 9.3 知识库+联网模式

展示完整执行链路：

- Planner 决策。
- force_web 状态。
- query。
- 本地知识库命中。
- 网页候选。
- 采用/跳过来源。
- warnings。
- stage timings。

展示方式：

- 阶段摘要默认可见。
- 详细链路折叠。

### 9.4 UI 容器

过程面板统一挂在 assistant 消息下。

直出模式：

- 默认折叠。
- 展开后 max-height: `40vh`。
- 内部滚动。
- `white-space: pre-wrap`。
- 提供复制按钮。

复制按钮：

- 复制完整 reasoning_trace。
- 字符串复制原字符串。
- 对象/数组复制 pretty JSON。

reasoning_trace 不进入：

- history。
- history_summary。
- 保存为笔记。
- 做卡片。

用户可以手动复制 reasoning_trace。

## 10. 保存/做卡片

保存为笔记或做卡片时：

- 保存用户问题。
- 保存 assistant 答案。
- 保存来源。
- 不保存 reasoning_trace。
- 不保存过程日志。

## 11. 前端主要改动点

主要文件：

- `/Users/pipi/Downloads/hermes-agent-main/web/src/pages/KnowledgeLibraryPage.tsx`
- `/Users/pipi/Downloads/hermes-agent-main/web/src/lib/api.ts`
- `/Users/pipi/Downloads/hermes-agent-main/web/src/components/KnowledgeAnswerView.tsx` 如过程面板需要复用来源渲染

改动：

- 可见 tab 改为 `直出` / `知识库+联网`。
- 新增每 tab 短期 history state。
- 新增同文章共享 `historySummary` 与 `historySummaryVersion`。
- 新增 summary pending / queued send 状态。
- 新增 `compressedIntoSummaryVersion`。
- 新增 `reasoning_trace` message metadata。
- 发送后清空 selectionContext 和 DOM selection。
- 生成中禁切 tab。
- 切文章取消请求并清空两个 tab history + summary。
- UI 最多保留 100 条消息，裁剪已压缩旧消息。

## 12. 后端主要改动点

Hermes Agent Main：

- `/Users/pipi/Downloads/hermes-agent-main/hermes_cli/web_server.py`
- `/Users/pipi/Downloads/hermes-agent-main/tests/hermes_cli/test_web_server.py`

改动：

- selection-chat body 增加 history、history_summary、history_summary_version、force_web。
- 普通和 stream endpoint 透传新增字段。
- SSE 支持 `summary_updated`。
- 非流式响应 meta 支持 updated summary。

Hermes Custom：

- `/Users/pipi/hermes-custom/bridge/web_context_answer.py`
- `/Users/pipi/hermes-custom/bridge/research/planner.py`
- `/Users/pipi/hermes-custom/tests/test_web_context_answer.py`
- `/Users/pipi/hermes-custom/tests/test_research_bridge.py`

改动：

- direct/直出分支跳过 Planner、本地检索、联网。
- direct 分支支持流式答案和 final reasoning_trace。
- local_web 支持 `force_web=true`。
- Planner payload 接收 history 与 history_summary。
- 普通 LLM summary helper 生成/更新共享长期摘要。
- query 继续保持 120 字上限。

## 13. 验收测试

### 前端

- 只显示两个 tab：`直出`、`知识库+联网`。
- 默认 tab 是 `直出`。
- 生成中 tab disabled，并显示提示。
- 切文章停止当前请求并清空两个 tab history + summary。
- 发送后清空选区和 DOM 高亮。
- 发送前切 tab 保留待发送选区。
- UI 可显示超过 6 轮历史。
- 发送 payload 只带当前 tab 最近 6 个完整成功问答对。
- 历史选区短前缀使用 300 字进入 payload，UI 不显示。
- summary_updated 只接受更高版本。
- reasoning_trace 默认折叠，展开滚动，复制完整内容。
- 保存笔记/做卡片不带 reasoning_trace。

### 后端 API

- 普通 selection-chat 支持新增字段并返回 updated summary。
- stream selection-chat 支持新增字段、complete reasoning_trace、summary_updated event。
- summary 压缩失败返回失败信息，不阻断后续发送。
- summary 任务 30 秒超时。
- force_web=true 强制 local_web 联网。
- current_article_only/direct 分支不调用 Planner/local/web。

### 后端语义

- 直出使用 article + selection + history + history_summary。
- 直出答案可流式。
- API reasoning 字段原样进入 reasoning_trace。
- API reasoning 为对象/数组时原样 JSON 保留。
- 无 API reasoning 时 fallback summary 成功或失败都不影响答案。
- reasoning_trace 不进入 history_summary。
- history_summary 增量压缩 old_summary + overflow successful messages。


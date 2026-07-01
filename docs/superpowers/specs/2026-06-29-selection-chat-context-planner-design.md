# Selection Chat Context Planner Design

## Summary

This design refines the Hermes Knowledge Library right-side AI panel. The goal is to make article-aware answers and web query planning use the same clear article context, without overbuilding a second reasoning pipeline.

The selected direction is:

- Short article: pass the full article.
- Long article: pass article summary plus the current section.
- If section lookup fails: pass article summary plus selected-text surrounding context.
- Use one shared article-context package for both query planning and final answer generation.
- Keep the planner small: it decides difficulty, search budget, whether local or web search is needed, query strings, and a human-readable reason.
- Do not let `question_type` drive code branches. It is a free-form trace label only.
- Keep side-panel turns independent by default. Do not automatically send previous user/assistant messages into the backend prompt.
- Use the selected mode as a hard product contract, not just a suggestion to the model.

## Current Problem

The current side-panel path passes article context through `bridge.web_context_answer`.

Today, `_article_context_prompt_block(...)` builds one generic block with title, folder, summary, tags, file path, and a mechanically clipped body excerpt. `_clip_text(...)` keeps the article head and tail when the article is long.

This has three problems:

1. Head-tail excerpts are often less relevant than the current section.
2. Query planning receives too much article text for a task that only needs search terms.
3. Selected-text surrounding context, section context, and body excerpt can overlap, causing repeated prompt content and less stable answers.

Recent UI checks also exposed two presentation problems in the side-panel answer flow:

4. The process UI can show web-search language in modes that should not use the web. In `KnowledgeLibraryPage.tsx`, stream events such as `queries_ready`, `web_search_start`, and `web_query_start` write web-search copy directly, and the answer footer always shows `重新联网检索`. On the backend, provider status is also shaped around web fields such as `search_backend`, `extract_backend`, `extracted_pages`, and `summary_only_pages`, even when the selected mode is current-article or knowledge-base only.
5. Citation links can disappear on later side-panel answers when the answer text contains `[K1]` or `[W1]` but the matching source is not present in `message.sources`. The current streaming source merge in `KnowledgeLibraryPage.tsx` caps merged sources at five before rendering, while `KnowledgeAnswerView` and `Markdown` only make a citation clickable when the final source map contains the exact rendered ref.

## Requirements

The side-panel AI should preserve the user's article context without making each question into a heavyweight research task.

Functional requirements:

- The four modes remain: current article, knowledge base, web, knowledge base plus web.
- Mode selection is a hard constraint on what the planner may request.
- Query planning and final answer generation consume the same article-context package.
- Short articles pass full text.
- Long articles pass summary plus current section.
- If the current section cannot be resolved, use selected-text surrounding context as fallback.
- `question_type` is a free-form label from the model, used only for trace/debug UI.
- Code must not branch on keyword-like `question_type` values.
- Process UI must be mode-aware. Current-article and knowledge-base-only modes must not show web-search copy, web backend fields, or a `重新联网检索` action.
- Citation rendering must be stable across turns. Each final assistant answer must receive sources covering every citation ref that remains in its answer text.
- Current-article mode does not need citation marks or a source list.
- Knowledge-base and web modes should preserve clickable source links when the answer contains citation refs.
- Retry and supplement actions must use the original message request context, not the current input box or current page selection.

Non-goals:

- No complex LLM-driven context planner.
- No semantic passage ranking in this change.
- No new UI mode for deep research in this change.
- No topic-specific hardcoding for words such as `skills` or `checkpoint`.
- No automatic follow-up history reasoning in this change.

## Article Context Rule

The backend should build a normalized article-context package before query planning and answer generation.

Always include:

- User question.
- Selected text.
- Heading path.
- Article title.

For article body context:

- If article body length is at most 4000 characters, include the full article body.
- If article body length is greater than 4000 characters, include article summary capped at 800 characters plus the current section body.
- If current section lookup fails, include article summary capped at 800 characters plus selected-text surrounding context: 500 characters before and 500 characters after.

If the current section exceeds 4000 characters:

- Clip the section to 4000 characters.
- Add a warning/trace note: current section is too long and was clipped; the article should be reorganized into smaller sections.
- Do not add complex second-stage section slicing.

File path and folder path should not be sent to the model as primary prompt context. They may stay in trace metadata.

## Current Section Resolution

The backend receives `heading_path` and full Markdown body.

Resolution strategy:

1. Strip the top-level article H1 if present.
2. Parse Markdown headings.
3. Match the deepest heading in `heading_path` against headings in the article.
4. Extract content from that heading until the next heading with the same or higher level.
5. If no heading matches, fallback to selected-text surrounding context.

The implementation uses a deterministic Markdown heading parser. It does not call an LLM to choose a section.

## Planner Contract

The planner returns a small JSON object:

```json
{
  "complexity": "simple",
  "question_type": "术语解释",
  "needs_web": false,
  "needs_local": true,
  "search_budget": 1,
  "queries": ["vibecoding skills CLAUDE.md AGENTS.md 记忆"],
  "reason": "用户在问选中术语的文章内含义，当前章节上下文已经足够，只需轻量补充"
}
```

Field rules:

- `complexity` is a controlled budget label: `simple`, `medium`, or `complex`.
- `question_type` is free-form text. The prompt may give examples, but the backend must not validate it against an enum and must not branch on it.
- `needs_web` and `needs_local` are boolean requests subject to mode constraints.
- `search_budget` is numeric and clamped by code.
- `queries` are search terms, clipped by `search_budget`.
- `reason` is for trace and debugging only.

Budget rules:

- `search_budget` is a maximum, not a target count. The backend must not invent extra queries just to fill the budget.
- If `needs_web=false`, planned queries must not execute. Keep them only as trace with a skipped/planned status.
- `simple`: normally 0-1 web query.
- `medium`: normally 2-3 web queries.
- `complex`: at most 3 web queries in web-only mode, and at most 6 web queries in knowledge-base-plus-web mode.
- The code hard limit is 6 queries.
- Existing single-query timeout and total stream timeout remain.

## Mode Behavior

Current article:

- Force `needs_web=false`.
- Force `needs_local=false`.
- Do not run web search.
- Do not run knowledge-base search.
- Use article-context package only.
- Do not emit `[K]` or `[W]` citation refs. If the model emits them anyway, remove them deterministically.
- Return no source list for the answer. The process details may still show whether the context came from full article, current section, or fallback.
- Natural wording such as "在这篇文章里" is allowed, but the answer must not claim that web or knowledge-base retrieval happened.

Knowledge base:

- Force `needs_web=false`.
- Force `needs_local=true`.
- Use article-context package plus local knowledge results.
- Preserve clickable knowledge-base citations when the answer contains `[K]` refs.
- If the answer has knowledge sources but no inline citations, show a source list as `使用来源`, not `引用来源`.

Web:

- Force `needs_local=false`.
- Force `needs_web=true`, because the user explicitly selected web.
- Use planner queries within `search_budget`.
- Run at least one light web query even for simple questions.
- Use article-context package as grounding context.
- Preserve clickable web citations when the answer contains `[W]` refs.

Knowledge base plus web:

- Force `needs_local=true`.
- Allow `needs_web` according to planner.
- For simple article-grounded questions, planner may return `needs_web=false`; the UI must show that web was intentionally skipped and offer `联网补充`.
- For medium or complex questions, planner may request web with a larger budget.
- If local retrieval returns no useful materials and planner skipped web, do not auto-upgrade to web in the first version. Show a readable trace such as "知识库未命中，可联网补充" and let the user choose.

## Mode-Aware Process UI

The side-panel process UI should describe the actual work performed for the selected mode.

Current article:

- Pending summary: `正在基于当前文章回答`.
- Details may show article context source, context clipping, and planner reason.
- Do not show web queries, web backend, extraction counts, candidate webpages, or `重新联网检索`.
- Do not show `引用来源` or `使用来源` for the current article itself.

Knowledge base:

- Pending summary: `正在检索知识库` while local retrieval is running, then `正在基于知识库回答`.
- Details may show local retrieval count and knowledge sources.
- Do not show web backend, extraction counts, candidate webpages, or `重新联网检索`.
- If a retry action is shown, label it as `重新检索知识库`; otherwise omit retry for this mode.

Web:

- Pending summary may use web-search language.
- Details may show web queries, per-query status, candidate webpages, provider status, warnings, and web retry.
- Footer action may be `重新联网检索`.

Knowledge base plus web:

- If the planner actually uses web, show both local and web stages.
- If the planner skips web for a simple article-grounded question, present it like a knowledge/article answer and show `跳过联网` as a trace fact, not as a failure.
- Footer retry should reflect the work that happened: web retry only when web was attempted, local retry only when only local was used.
- If web was skipped, show `联网补充`, not `重新联网检索`.

Implementation contract:

- Backend should emit explicit stage flags such as `used_article_context`, `used_local`, `web_search_attempted`, and `used_web`.
- Frontend should render web-specific rows only when `web_search_attempted=true` or web sources/query statuses are present.
- `providerStatusLabels` should not display web-only fields for current-article or knowledge-base-only responses.
- `applySelectionChatStreamEvent` should ignore or downlabel stale web events when the current mode forbids web.
- The answer footer must choose actions from mode and trace, not render `重新联网检索` unconditionally.
- The detail panel should show only stages that happened. Do not render empty placeholders for article, local, web, or generation stages.
- Planner `reason` should appear only in retrieval details, not in the main answer summary line.

Footer actions:

- Current article: no retrieval retry action.
- Knowledge base: `重新检索知识库`.
- Web: `重新联网检索`.
- Knowledge base plus web with web attempted: `重新联网检索`.
- Knowledge base plus web with web skipped: `联网补充`.
- `联网补充` creates a new assistant answer using the original question, original selection, original article context, and forced web. It must not edit the previous answer and must not include the previous answer in the backend prompt.
- All retry/supplement actions use the request context saved on the assistant message they belong to. They must not read the current input box or the current page selection.

## Prompt Shape

The query planner prompt should receive:

- User question.
- Selected text.
- Heading path.
- Article title.
- Normalized article-context package.
- Mode constraints.

It should not receive file path or folder path.

The final answer prompt should receive:

- User question.
- Selected text.
- Heading path.
- Article title.
- The same normalized article-context package.
- Local knowledge materials when used.
- Web materials when used.
- Mode constraints.

The answer prompt should explicitly say that short terms and English snippets must be explained in their current article context, not as generic dictionary definitions.

The side-panel backend should not automatically include previous side-panel turns in either prompt. If the user asks an underspecified follow-up such as "那它呢" and there is no clear current selection or article-grounded object, the answer should ask the user to select the target text or name the concept.

## Trace And UI

Trace should expose:

- `complexity`
- `question_type`
- `needs_web`
- `needs_local`
- `search_budget`
- `queries`
- `reason`
- Article context source: `full_article`, `current_section`, or `surrounding_fallback`
- Context clipped warning, if any
- `used_article_context`
- `used_local`
- `web_search_attempted`
- `used_web`
- skipped query status when queries were planned but not executed because `needs_web=false` or mode constraints forbade web.
- Mode-specific stage timings, with `total_elapsed_ms` reserved for end-to-end time and `stage_timings.search_ms` reserved for actual search time.

The UI does not need a new panel. Existing retrieval details should show the planner reason, search budget, and article context source when those fields are present.

The detail panel should be grouped by actual stage:

- Article context.
- Knowledge-base retrieval.
- Web search.
- Answer generation.

Groups with no activity should be hidden. This avoids showing empty web fields such as `搜索后端: dashscope`, `全文页: 0`, or `摘要页: 0` when the user is in current-article or knowledge-base mode.

## Citation Rendering Consistency

The citation contract is:

- If final answer text contains `[K1]`, `[K2]`, `[W1]`, or `[W2]`, the final response must include matching sources with those refs.
- Current-article mode should not contain citation refs. Remove any `[K]` or `[W]` refs before returning the response.
- The backend may remove invalid refs, but it must not leave an answer with refs that are missing from `source_map`, `meta.knowledge_sources`, or `meta.web_sources`.
- The frontend should build `message.sources` from the final response source payload, not from a truncated streaming trace.
- Streaming trace data may be capped only for display. The data used to resolve answer citations must not be capped to five sources before citation rendering.
- Source ref normalization must be per-answer and deterministic. A second side-panel answer should not inherit, drop, or renumber refs based on the first answer.
- Do not force-add inline citations when the model does not cite a knowledge source. Show the sources used by retrieval in the source list instead.

The UI source list can still display a small number of sources by default, but hidden extra sources must remain available to the Markdown citation map if the answer cites them. If the answer has inline citations, label the list `引用来源`. If the answer has sources but no inline citations, label it `使用来源`.

## Error Handling

If planner JSON is invalid:

- Fall back to a simple plan.
- Respect mode constraints.
- For web-only mode, use the user question as a single query.
- For knowledge-base-plus-web mode, default to article plus knowledge-base retrieval and offer `联网补充` instead of silently starting web.

If section lookup fails:

- Use summary plus selected-text surrounding context.
- Add a trace note.

If current section is too long:

- Clip to 4000 characters.
- Add a trace warning.

If all web queries fail:

- Keep existing graceful degradation behavior.
- Return answer from available article/local context when allowed by mode.
- Surface readable warning in the process UI rather than raw provider error.
- Do not put partial-query timeout warnings into the answer body. The body should answer the user; process details explain degraded retrieval.

If a forbidden web event appears in a non-web mode:

- Keep the answer generation alive.
- Do not switch the visible process UI into web-search mode.
- Add a debug trace warning so the stream contract problem can be diagnosed.

If a final answer contains a citation ref without a matching source:

- Prefer backend repair before returning the response.
- If the frontend still receives a missing source, render the text safely and show a user-readable diagnostic in retrieval details, such as `部分引用来源缺失，已以普通文本显示`. Do not use development-only wording in user-facing UI.

If the user asks an underspecified follow-up:

- If there is a current selection, answer based on that selection and the current article context.
- If there is no current selection but the article context clearly answers the question, answer from the article.
- If the referent is unclear, ask the user to select the target text or name the concept instead of guessing from previous turns.

## Testing

Backend tests should cover:

- Short article uses full body and omits summary duplication.
- Long article uses summary plus current section.
- Section lookup failure uses summary plus surrounding fallback.
- Overlong current section is clipped and emits trace warning.
- Planner free-form `question_type` is preserved but does not control behavior.
- Mode constraints override planner requests.
- Search budget is clamped to maximum 6.
- `search_budget` does not force the backend to generate or execute extra queries.
- `needs_web=false` prevents query execution even when query strings are present.
- Current-article mode emits no web query events and no web provider status rows.
- Current-article mode strips `[K]` and `[W]` refs and returns no sources.
- Knowledge-base mode emits local retrieval trace without web backend rows.
- Knowledge-base and knowledge-base-plus-web modes force local retrieval.
- Web-only mode forces at least one web query.
- Final answer refs are all represented in `source_map`, `meta.knowledge_sources`, or `meta.web_sources`.
- Answers with more than five candidate sources still preserve cited refs for rendering.
- Retry/supplement requests use the saved original request context.
- Default side-panel turns do not include prior chat history in the backend prompt.

Frontend/build verification:

- Existing TypeScript build should pass.
- Existing side-panel retrieval details continue to render.
- Current-article and knowledge-base modes do not show web-search copy or `重新联网检索`.
- Web and knowledge-plus-web modes still show per-query statuses and readable timeout warnings.
- Two consecutive side-panel answers both render clickable citation links when their final responses contain matching sources.
- Streaming source display caps do not remove sources needed by Markdown citations.
- Source list label is `引用来源` when inline citations exist and `使用来源` when sources exist without inline citations.
- `联网补充` produces a new answer from the original message context and does not mutate the previous answer.

Manual smoke:

- Open `/knowledge-library`.
- Select a term in a long article and ask "这是什么意思".
- Confirm the answer reflects current section context.
- Switch to knowledge base plus web and ask a complex question.
- Confirm trace shows query budget and query statuses without raw timeout strings.
- Switch to current-article mode and ask a simple article question.
- Confirm the process UI does not mention web search and the footer does not show `重新联网检索`.
- Ask two side-panel questions in a row where both answers include citations.
- Confirm both answers keep clickable citation links.
- In knowledge-base-plus-web mode, ask a simple article-grounded term question and confirm web can be skipped with an explicit `联网补充` action.
- Click `联网补充` after changing the input box and page selection, and confirm the new answer still uses the original message context.
- Ask an unclear follow-up such as "那它呢" without a current selection and confirm the UI asks for a clearer target instead of guessing from prior turns.

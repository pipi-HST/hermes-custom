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

## Current Problem

The current side-panel path passes article context through `bridge.web_context_answer`.

Today, `_article_context_prompt_block(...)` builds one generic block with title, folder, summary, tags, file path, and a mechanically clipped body excerpt. `_clip_text(...)` keeps the article head and tail when the article is long.

This has three problems:

1. Head-tail excerpts are often less relevant than the current section.
2. Query planning receives too much article text for a task that only needs search terms.
3. Selected-text surrounding context, section context, and body excerpt can overlap, causing repeated prompt content and less stable answers.

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

Non-goals:

- No complex LLM-driven context planner.
- No semantic passage ranking in this change.
- No new UI mode for deep research in this change.
- No topic-specific hardcoding for words such as `skills` or `checkpoint`.

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

- `simple`: normally 0-1 web query.
- `medium`: normally 2-3 web queries.
- `complex`: at most 6 web queries.
- The code hard limit is 6 queries.
- Existing single-query timeout and total stream timeout remain.

## Mode Behavior

Current article:

- Force `needs_web=false`.
- Force `needs_local=false`.
- Do not run web search.
- Use article-context package only.

Knowledge base:

- Force `needs_web=false`.
- Allow `needs_local` according to planner, defaulting to true if the model is uncertain.
- Use article-context package plus local knowledge results.

Web:

- Force `needs_local=false`.
- Force or strongly require `needs_web=true`, because the user explicitly selected web.
- Use planner queries within `search_budget`.
- Use article-context package as grounding context.

Knowledge base plus web:

- Allow both local and web.
- For simple article-grounded questions, planner may return `needs_web=false`.
- For medium or complex questions, planner may request web with a larger budget.

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

The UI does not need a new panel. Existing retrieval details should show the planner reason, search budget, and article context source when those fields are present.

## Error Handling

If planner JSON is invalid:

- Fall back to a simple plan.
- Respect mode constraints.
- For web-capable modes, use the user question as a single query.

If section lookup fails:

- Use summary plus selected-text surrounding context.
- Add a trace note.

If current section is too long:

- Clip to 4000 characters.
- Add a trace warning.

If all web queries fail:

- Keep existing graceful degradation behavior.
- Return answer from available article/local context when allowed by mode.
- Surface readable warning rather than raw provider error.

## Testing

Backend tests should cover:

- Short article uses full body and omits summary duplication.
- Long article uses summary plus current section.
- Section lookup failure uses summary plus surrounding fallback.
- Overlong current section is clipped and emits trace warning.
- Planner free-form `question_type` is preserved but does not control behavior.
- Mode constraints override planner requests.
- Search budget is clamped to maximum 6.

Frontend/build verification:

- Existing TypeScript build should pass.
- Existing side-panel retrieval details continue to render.

Manual smoke:

- Open `/knowledge-library`.
- Select a term in a long article and ask "这是什么意思".
- Confirm the answer reflects current section context.
- Switch to knowledge base plus web and ask a complex question.
- Confirm trace shows query budget and query statuses without raw timeout strings.

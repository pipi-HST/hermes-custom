---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-18
---

# Archived Implementation Plans

`status: historical` 表示文档已归档，只保留写作时的背景，不代表当前行为。
`execution_status` 表示计划在代码库里的落地程度：

- `done`：正文、提交或测试能证明已经落地。
- `partial`：只有一部分落地，或实现尚待收尾。
- `pending`：文档明确写了尚未执行。
- `cancelled`：计划被明确放弃，或明确由后继方案替代。
- `unknown`：没有足够证据，尚未核实。

`unknown` 不是失败，只表示这份归档计划还没有逐项核对。
此页由 `python scripts/check_plan_status.py --write-index` 生成。

## 状态统计

| execution_status | 数量 |
| --- | --- |
| `done` | 33 |
| `partial` | 10 |
| `pending` | 1 |
| `cancelled` | 1 |
| `unknown` | 5 |

## Plans

| plan | 日期 | execution_status | 证据 |
| --- | --- | --- | --- |
| `2026-05-26-two-step-capture-and-slim-db.md` | 2026-05-26 | `done` | 正文 L10「全部 7 Tasks 完成 ✅」、L138 `73 passed`；`agents/markdown_writer.py` 与 `tests/test_markdown_writer.py` 存在 |
| `2026-05-27-theme-agent-enhancement.md` | 2026-05-27 | `done` | 正文 L199-202 L1-L4 均标 ✅ 完成；`agents/theme_agent.py` 的 `build_moc`、`graph_report` 与 `agents/markdown_writer.py` 的 `append_links`、`write_moc`、`update_moc` 存在 |
| `2026-05-28-obsidian-agent-knowledge-management.md` | 2026-05-28 | `partial` | 路线图 71 项未勾选；Garden、Theme、Review、Result Envelope 代码与测试已落地，但 Phase 1 要求的 capture frontmatter（`type`、`linked_themes`、`garden_cards`）未出现在 `agents/markdown_writer.py` |
| `2026-05-29-capture-long-content-planning.md` | 2026-05-29 | `done` | 提交 5c9f903 同批加入计划与实现；`agents/capture_agent.py` 的 `draft_guidance`、`detail_level` 与「不要输出内部计划」prompt 存在 |
| `2026-05-29-capture-long-note-structure.md` | 2026-05-29 | `cancelled` | 正文 L13「Status: Superseded for Capture Agent」；outline 职责移交 Library Agent，现由 `agents/library_agent.py` 的 `maintain_outlines` 承担 |
| `2026-05-29-capture-medium-density-strategy.md` | 2026-05-29 | `done` | 提交 2ca7ca3「Improve capture medium density drafting」；`_detect_detail_level`、`CAPTURE_LONG_READING_CHAR_THRESHOLD`、`_build_long_revision_prompt` 存在 |
| `2026-05-30-capture-embedding-index-upgrade.md` | 2026-05-30 | `done` | 提交 3d77eb3 同批加入计划与实现；`db/embedding_repo.py`、`agents/capture_indexer.py`、`tests/test_capture_indexer.py` 存在 |
| `2026-05-30-capture-metadata-storage-boundary.md` | 2026-05-30 | `done` | 提交 3d77eb3 同批加入计划与实现；`capture_metadata`、`CaptureMetadataRepo`、`outline_json` 在 `db/` 与 `agents/library_agent.py` 中存在 |
| `2026-05-30-review-agent-explainer-mode.md` | 2026-05-30 | `done` | 提交 b17bbaf「Change review agent to explainer mode」；`agents/review_agent.py` 与 `tests/test_review_agent.py` 含 `review_explanation` |
| `2026-05-31-garden-agent-draft-lifecycle.md` | 2026-05-31 | `done` | 提交 20987ed「feat: add garden draft lifecycle」；`garden_drafts`、`create_garden_draft`、`commit_garden_draft` 与相关测试存在 |
| `2026-05-31-garden-canvas-obsidian-implementation.md` | 2026-05-31 | `done` | 提交 b4ae23a「feat: generate obsidian garden canvas」；`agents/canvas_agent.py`、`bridge/canvas_bridge.py` 与测试存在 |
| `2026-05-31-garden-duplicate-guard.md` | 2026-05-31 | `done` | 提交 ec7e18f「guard garden duplicates and graph rag retrieval」；`agents/garden_duplicates.py`、`tests/test_garden_duplicates.py` 存在 |
| `2026-05-31-garden-knowledge-card-implementation.md` | 2026-05-31 | `done` | 提交 c334640「support typed garden knowledge cards」；`card_kind` 与 `viewpoint`、`fact`、`method`、`caution` 在 `agents/garden_agent.py` 中 |
| `2026-05-31-graph-aware-rag-v1.md` | 2026-05-31 | `done` | 计划提交 63c25e7，同日 ec7e18f 落地；`agents/rag_graph.py`、`build_rag_context_pack`、`tests/test_rag_graph.py` 存在 |
| `2026-05-31-hermes-agent-run-ledger.md` | 2026-05-31 | `done` | 提交 ec7e18f 加入 `db/agent_run_repo.py`、`bridge/run_ledger.py`、`agents/run_evidence.py`；`tests/test_run_ledger.py` 存在 |
| `2026-05-31-rag-and-run-ledger-next.md` | 2026-05-31 | `done` | 提交 ec7e18f；`context_pack`、`graph_trace`、`agent_runs` 与 `tests/test_run_ledger.py` 存在 |
| `2026-05-31-rag-managed-section-filtering.md` | 2026-05-31 | `done` | 提交 2d98e6a「filter managed sections from rag evidence」；`agents/managed_sections.py` 与 `tests/test_capture_indexer.py` 存在 |
| `2026-05-31-theme-agent-index-graph-scan.md` | 2026-05-31 | `done` | 提交 cf7e8db；`agents/theme_graph.py`、`cluster_relationship_graph`、`tests/test_theme_graph.py` 存在 |
| `2026-05-31-theme-garden-candidate-flow.md` | 2026-05-31 | `done` | 提交 cf7e8db；`bridge/theme_bridge.py` 的 `list_theme_candidates`、`next_actions` 与 `tests/test_theme_bridge.py` 存在 |
| `2026-06-01-garden-card-board-canvas.md` | 2026-06-01 | `done` | 提交 64e1066「make garden canvas card-first」；`CanvasItem`、`_garden_items` 与 `tests/test_canvas_agent.py` 存在 |
| `2026-06-01-rag-capture-file-output.md` | 2026-06-01 | `done` | 提交 656bd7f；`bridge/file_artifacts.py`、`_rag_report_artifact`、`tests/test_rag_file_output.py`、`tests/test_capture_file_output.py` 存在 |
| `2026-06-01-structured-garden-canvas.md` | 2026-06-01 | `done` | 提交 656bd7f；`agents/garden_structure_agent.py`、`CanvasStructure`、`tests/test_garden_structure_agent.py` 存在 |
| `2026-06-02-garden-card-canvas-visualization.md` | 2026-06-02 | `done` | 提交 f9fc45d「add garden card cluster canvas」；`garden_card_ids`、`CanvasTheme` 在 `agents/canvas_agent.py` 中 |
| `2026-06-03-split-knowledge-and-life-projects.md` | 2026-06-03 | `done` | 提交 ebd7548「split Life Assistant into separate project」；`agents/life_*` 已移出本仓库，`docs/adr/0002` 保留 |
| `2026-06-14-hermes-knowledge-product-redesign-implementation.md` | 2026-06-14 | `done` | 正文「Execution Status」P1/P2/P4 已实现并记录 `510 passed`；提交 afa8e5f 勾选约 78 项，仅剩 manual smoke |
| `2026-06-14-hermes-web-full-chat.md` | 2026-06-14 | `partial` | 提交 afa8e5f 勾选大部分任务，但 Task 4「Build side panels」L117 仍未勾选；`KnowledgeChatPage` 侧栏组件在本仓库不可核 |
| `2026-06-15-save-answer-to-knowledge.md` | 2026-06-15 | `done` | 正文 28 项已勾选，仅剩重启与 4 项手工 smoke；`save_answer_to_knowledge`、`commit_reading_artifact` 与测试存在 |
| `2026-06-15-theme-garden-moc-executable-plan.md` | 2026-06-15 | `partial` | 正文 34 项已勾选；未勾选项含 L677「Add the right inspector」与 L802「Migrate existing Theme pages」；`build_theme_page`、`write_moc` 已存在 |
| `2026-06-19-editable-capture-workbench-plan.md` | 2026-06-19 | `done` | 正文 L11「Status: implemented in hermes-custom」；`KnowledgeWriteService`、`createEditableCaptureDraft` 与 `tests/test_knowledge_write_service.py` 存在 |
| `2026-06-19-hermes-knowledge-stabilization-roadmap.md` | 2026-06-19 | `done` | 正文「Execution Status」Phase 0-7 全部 complete，并记录 `581 passed` |
| `2026-06-19-hermes-knowledge-workspace-redesign-brief.md` | 2026-06-19 | `unknown` | 需求 brief，无执行状态与任务勾选；没有对应实现提交可核 |
| `2026-06-19-hermes-product-language-audit.md` | 2026-06-19 | `done` | 正文说明 Phase 2 已完成：`CONTEXT.md`、`CONTEXT-MAP.md` 已更新术语；stabilization roadmap 将其记为 complete |
| `2026-06-19-hermes-stabilization-baseline.md` | 2026-06-19 | `done` | 正文 L47「Phase 0 is complete」；baseline 记录存在 |
| `2026-06-19-hermes-workspace-full-migration-plan.md` | 2026-06-19 | `partial` | 提交 9ebb946 只迁移 workspace shell；`dashboard/src/features/` 仅有 workbench 与 garden 部分组件，dashboard 已在 6759480 标记 deprecated |
| `2026-06-20-hermes-hardcoded-nlu-audit.md` | 2026-06-20 | `unknown` | 审计文档，罗列 P0-P2 问题与迁移顺序，无执行状态；`WEB_INTENT_RE` 等硬编码仍存在，不能判定已按建议迁移 |
| `2026-06-20-hermes-knowledge-web-research-tool-plan.md` | 2026-06-20 | `unknown` | 正文 L11「状态：背景参考，已被主执行方案吸收」；无独立执行记录 |
| `2026-06-20-hermes-web-research-pipeline-upgrade-plan.md` | 2026-06-20 | `unknown` | 正文 L11「执行状态：主执行方案」，未声明完成度；无独立验收记录 |
| `2026-06-20-hermes-web-search-quality-recovery-plan.md` | 2026-06-20 | `done` | 正文 L11「已按本文档完成第一轮实现和验证」；`bridge/research_bridge.py` 的质量门与 `tests/test_research_bridge.py` 存在 |
| `2026-06-20-knowledge-library-browse-mode-implementation-plan.md` | 2026-06-20 | `done` | 正文 §22 记录已实施内容、浏览器 smoke 与 `6 passed`；仅记录残留风险 |
| `2026-06-20-knowledge-note-retrieval-audit.md` | 2026-06-20 | `done` | 正文 L91「已实施修复」含修改文件与验证；`exact_learning_note` 在 `bridge/rag_bridge.py` 中 |
| `2026-06-21-hermes-contract-lite-web-research-design.md` | 2026-06-21 | `done` | 正文 L11「已实现并完成核心验证」；`evidence_contract`、`answer_policy`、`allow_summary_only` 在 `bridge/research_bridge.py` 中 |
| `2026-06-22-hermes-knowledge-ui-motion-cleanup-plan.md` | 2026-06-22 | `unknown` | 目标文件在 hermes-agent-main；本仓库无可核实现，`PageHeaderProvider`、`EvidenceSummary` 等标识不存在 |
| `2026-06-23-hermes-research-diff-audit.md` | 2026-06-23 | `partial` | 正文 §5 第 1、3 项「已完成」，第 2 项「继续收口」；§9 一键验收与 runtime smoke 已完成，live canary 未跑 |
| `2026-06-23-hermes-search-quality-eval-action-plan.md` | 2026-06-23 | `done` | L0/L1 已落地：`scripts/eval_research_quality.py`、`tests/test_research_quality_eval.py`、36 条 `tests/fixtures/research_eval_cases.jsonl`；§4 属后续建设 |
| `2026-06-23-hermes-web-research-contract-and-eval-protocol.md` | 2026-06-23 | `pending` | 正文 L11「执行状态：待确认后执行」；L23 明确 `research_bridge.py` 与测试仍是半途改动 |
| `2026-06-25-hermes-answer-first-web-search-ux-plan.md` | 2026-06-25 | `partial` | 正文 L11「状态：实施中」、L510「已完成基础方向」；`web_assist_answer`、`summary_only_pages` 存在 |
| `2026-06-25-hermes-lightweight-web-qa-refactor-plan.md` | 2026-06-25 | `partial` | 正文 L11「阶段 0-3 已落地，阶段 4 已完成默认入口切换，待真实 canary」；`bridge/research/light_web_qa.py`、`SEARCH_ONLY_EXTRACT_BACKENDS` 存在 |
| `2026-06-27-hermes-knowledge-deobsidian-local-reset-plan.md` | 2026-06-27 | `partial` | ADR 0003/0004、`docs/local-data-reset-runbook.md`、`scripts/reset_hermes_local_data.py` 已落地；destructive apply 无执行记录 |
| `2026-06-29-selection-chat-ux-implementation.md` | 2026-06-29 | `partial` | 提交 4aa71bd 同批实现后端（`bridge/web_context_answer.py` 与 437 行测试）；前端 `KnowledgeAnswerView`、`mergeResearchSources` 在本仓库不可核 |
| `2026-07-03-sidebar-ai-two-mode-memory-plan.md` | 2026-07-03 | `partial` | `bridge/web_context_answer.py` 已有 `history_summary`、`force_web`、`reasoning_trace`，`api/routes/knowledge.py` 透传；前端两 tab 验收在本仓库不可核 |

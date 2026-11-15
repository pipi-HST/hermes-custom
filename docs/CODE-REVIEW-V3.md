# Hermes Knowledge V3.0 代码评审（红队视角）

评审对象：`repos/hermes-custom`（26 个改动文件 + 24 个新增文件）、`repos/hermes-agent-main`（12 个改动文件 + 1 个新增测试）
评审方式：逐文件读真实 diff（`--ignore-cr-at-eol`）→ 写两个可复现脚本证伪 → 重跑测试与真实库 → 交叉核对文档数字。
**本文件里每一个缺陷都附实测输出或源码行号；没有"看起来可能有问题"的条目。**

---

## 0. 总体结论

**能跑、但没到能演示的状态。** 三个原因：

1. **引用编号会串行错位**（P1-1）。这不是理论风险——真实库里 6/6 篇资料都有多 section，9/9 张卡片都挂了 capture，只要图谱扩图命中任一卡片，就会返回同一篇的多行；而 prompt 侧按 capture 去重、source_map 侧不去重。实测 4 个 ref 对 3 个 `<source>`，`[3]` 会指到另一篇资料。
2. **测试主链不绿**：全量跑 7 failed（不含 3 个浏览器文件，它们直接让进程崩掉）。"新测试 30 passed / 宽回归 EXIT=0"这句话在当前工作区已经不成立。
3. **实验结论没回写代码**：`MIN_VECTOR_SCORE` 仍是 `0.20`，而自己的标定报告说 0.20 让负样本 100% 漏过；benchmark 文档却写"生效阈值 0.46"。

已经把该做的做对了的部分也写清楚了（§6），不要只读缺陷。

---

## 1. 验证方法（可复现）

| 动作 | 结果 |
| --- | --- |
| `git diff --ignore-cr-at-eol --stat` | custom 真实改动 **1,535+ / 118−**（26 文件）；裸 `git diff --stat` 显示 12,800+ / 11,383−，**8.3 倍是 CRLF 噪声** |
| `scripts/repro_v3_review_findings.py` P1 | `3 sources in prompt vs 4 refs in source_map` |
| `scripts/repro_v3_review_findings.py` P2 | `final_hits: 3 rows, 1 distinct capture ids` |
| `pytest tests`（9 个新测试文件） | **50 passed** |
| `pytest tests`（全量，排除 3 个浏览器文件） | **7 failed**，`EXIT=1` |
| `pytest tests`（全量，含浏览器文件） | 进程级崩溃（`crashpad_client_win.cc:867 not connected`，exit `4294930435`） |
| 真实库 `data/captures.db` | 6 captures / 30 chunks / 9 cards / 4 citation_events / FTS 30 行；**6/6 篇有多 chunk，9/9 卡有 capture_ids** |
| `_extract_query_keywords` 现场跑 | 见 P1-2 |

---

## 2. P1 — 阻断演示（必须改，改了才有资格讲）

### P1-1 引用编号与来源列表错位，`[n]` 会静默指错资料

**位置**：`bridge/rag_bridge.py:258` 把未去重的 `results` 交给 `RagAgent.query`；`bridge/rag_bridge.py:276` 又用同一个 `results` 枚举出 `source_map`；而 prompt 侧 `agents/rag_agent.py:66 _dedupe_captures` 按 capture 去重。

**链路**：
```
agents/rag_graph.py:214 _select_final_hits      # 按 (capture_id, section_id) 建 key
agents/rag_graph.py:257 _prefer_sections_over_whole_note  # 只删 identity，不合并同篇多 section
  -> results 里出现两行同 id、不同 section
bridge/rag_bridge.py:276 source_map = [_source_entry(item, i) for i, item in enumerate(results, 1)]
agents/rag_agent.py:66  _dedupe_captures(captures)        # 同 id 合并成一条
  -> prompt 只有 3 个 <source id>，source_map 有 4 个 ref
```

**实测证据**（`.venv/Scripts/python.exe scripts/repro_v3_review_findings.py`）：
```
results          : ['cap_a', 'cap_b', 'cap_a', 'cap_c']
source_map refs  : [('1','cap_a','S1'), ('2','cap_b',''), ('3','cap_a','S3'), ('4','cap_c','')]
prompt source ids: ['1', '2', '3']
model writes [3] meaning 'cap_c' -> source_map ref 3 says 'cap_a'
```
上游可复现性（P2 段）：
```
embedding rows  : 4
  id=cap_a  matched_kind=section  section_id=section-3
  id=cap_a  matched_kind=section  section_id=section-1
  id=cap_a  matched_kind=section  section_id=section-2
final_hits      : 3 rows, 1 distinct capture ids
```

**为什么必然会触发**：真实库 6/6 篇有多 section、9/9 卡挂了 capture；只要图谱扩图命中任一卡片，`_select_final_hits` 就会把该篇**所有** section 塞进 `final_hits`。

**为什么现有测试没抓到**：`tests/test_lineage_and_chunking_v3.py:131` 构造了完全一样的重复 id 场景，但只断言 `prompt.count("<source") == 2`，**从不断言 prompt 与 source_map 的索引对齐**——测试把 bug 当成了预期行为。

**修法（确定性，无实验依赖）**：去重只做一次，且在两个消费方之前。
- 首选：在 `_search_knowledge_base` 里 `results = _dedupe_captures(results)`，之后再用同一个 list 生 prompt 和 source_map；
- 保底：`encode` 时给每个 item 写入 `ref`，prompt 用 item 的 `ref` 而不是 `enumerate` 序号；
- 加一条不变式测试：`len(re.findall(r'<source id=', prompt)) == len(source_map)`，且第 i 个 `<source>` 的标题 == `source_map[i]['title']`。

---

### P1-2 中文关键词抽取会把技术名词切碎，FTS 补召回与相关度闸门双双静默失效

**位置**：`bridge/rag_bridge.py:84 _extract_query_keywords`（先对整串 `replace` 填充词，再按非字词切分）

**实测**：
```
转介绍的目的和边界是什么      -> ['和边界是']
解释器模式有什么用           -> ['器模式有']
了解了 MCP 之后应该怎么用     -> ['mcp', '之后应该']
什么是传话筒               -> ['是传话筒']
避坑经验有哪些              -> ['避坑经验有']
```
`"和边界是"` / `"器模式有"` 这类 token 在索引里**永远不可能命中**（索引是 2-gram 短语 `"和边 边界 界是"`），于是：
- `_keyword_match_captures`（`rag_bridge.py:564`）返回空，**"literal keyword top-up" 这个 V3 卖点对最常见的问句形式不起作用**；
- `_has_literal_keyword`（`rag_bridge.py:~750`）恒 False，相关度闸门退化成纯阈值判断。

`docstring` 自称"exact technical terms must survive"，只在词周围恰好有空格时才成立（`"请问 MCP 的 130秒 是什么？"` → `['mcp','130秒']` 是因为空格挡着）。

**根因**：对整串做 `replace("的"," ")`、`replace("了"," ")`、`replace("吗"," ")`、`replace("呢"," ")`，会把**词内字符**删掉（`目的`→`目 `、`解释`→` `），再切分时残片又被拼回去。`boundary` 不是"去掉停用词"，是"字符串手术"。

**修法**：先按 `[^\w\u4e00-\u9fff]+` 切分为候选 token，再**整 token 相等**才丢弃停用词（`的/了/吗/呢/什么/如何…`），最后去掉长度 <2 且非 ASCII 的 token。同时把"停用词"表和"疑问词"表分开——`解释` 不该在停用词里。

---

### P1-3 阈值：代码 0.20，文档 0.46，而 0.20 是已被自己实验证伪的值

**位置**：`bridge/rag_bridge.py:55` → `MIN_VECTOR_SCORE = 0.20`
**文档**：`docs/BENCHMARK-RAG-V3-dashscope.md:4` → `- 生效阈值 MIN_VECTOR_SCORE = 0.46`；`docs/PRODUCT-REFACTOR-STORY.md:243` → `（MIN_VECTOR_SCORE = 0.20）`

三处互相矛盾，且**代码站在被证伪的一侧**：
- `docs/calibration-dashscope.json`：`positive_p10=0.4286`、`negative_p95=0.4887`、`separation=-0.0601`；
- `PRODUCT-REFACTOR-STORY.md:379-380`：0.20 → 负样本拦截 **0%**；0.46 → 拦截 **100%**，Recall 不变。

也就是说，当前**生产默认值就是实验里表现最差的那个**。benchmark 的 0.46 是 `--threshold` 覆盖进去的，跑完没回写常量。

**修法**：把常量改成 0.46（或抽到 config，默认 0.46），并在 `_indexed_search` 的 docstring 注明"该值来自 `docs/calibration-dashscope.json`，`separation<0` 说明单阈值不完全可分，生产需 rerank"。然后**重跑一次不带 `--threshold` 的 benchmark**，让 docs 里的"生效阈值"重新变成事实。

---

### P1-4 回归测试主链是红的（7 failed），"宽回归 EXIT=0"不成立

**实测**（`pytest tests`，排除 3 个浏览器文件）：

```
FAILED tests/test_capture_draft_workflow.py::test_commit_long_full_text_save_generates_clickable_sections_and_rag
FAILED tests/test_capture_draft_workflow.py::test_commit_long_capture_repairs_directory_to_cover_primary_headings
FAILED tests/test_research_bridge.py::test_low_risk_overview_critic_failure_limited_adopts_direct_source
FAILED tests/test_research_bridge.py::test_research_stage_timings_split_provider_trace
FAILED tests/test_research_bridge.py::test_light_extract_urls_skip_llm_extract_when_low_risk_summaries_are_enough
FAILED tests/test_research_bridge.py::test_light_extract_urls_skip_extract_for_single_summary_concept_explainer
FAILED tests/test_web_context_answer.py::test_web_context_answer_records_light_path_stage_timings
EXIT=1
```

三类根因，性质完全不同，不要混着说：

| 类 | 用例 | 断言 | 性质 |
| --- | --- | --- | --- |
| A 行为契约未同步 | `test_light_extract_urls_skip_extract_for_single_summary_concept_explainer`、`..._when_low_risk_summaries_are_enough` | `assert urls == []` 实际 `['https://example.com/rag']` | 本轮**有意**改成"摘要优先也抓 1 篇锚点"（`light_web_qa.py:~427`），但没更新这 2 个旧契约。测试该改，不是代码错——**但"全绿"的话不能再说** |
| B 毫秒计时被加速打到 0 | `initial_critic_ms > 0`、`search_ms > 0` 等 4 个 | `assert 0 > 0` | 阶段计时用整数毫秒，本轮提速后 fake adapter 的耗时四舍五入为 0。**测试脆弱**，应改 `>= 0` 或 float 毫秒 |
| C 内部命名泄漏到用户可见标题 | `test_commit_long_full_text_...` | `{'第一部分','第二部分（第 2 部分）'} == {'第一部分','第二部分'}` | **真缺陷**，见 P2-5 |

另外 **含浏览器用例的全量跑会直接崩进程**：`crashpad_client_win.cc:867 not connected`，exit `4294930435`。CI 里这等于"测试没跑完但没人知道"。

---

## 3. P2 — 功能与文档层面的实质问题

### P2-1 `search_backend` 上报的是"意图"，不是"实际服务方"

`bridge/research/adapters.py:118 _default_web_search`：DeepSeek 调用抛异常或返回空时，**静默降级**到旧 `web_search_tool`，返回值不带任何 backend 标记；而 `light_web_qa.py:337` 用 `_default_search_backend()`（只看凭证是否存在）写 `provider_status["search_backend"]`。

结果：**DeepSeek 挂了、用的是备用后端，前端仍然显示 deepseek。** 这段代码的注释写的正是"Silent degradation is the dangerous case"——它防住了"没标记"，没防住"标记说谎"。

**修法**：`_default_web_search` 返回时带 `backend`（deepseek / legacy / local），`_assemble_light_web_result` 用**实际返回值**的 backend，而不是重新问一遍配置。

### P2-2 前端降级提示是死代码

`web/src/components/KnowledgeAnswerView.tsx:27` 新增 `searchBackend` prop，`:40` 有 `degraded` 判定和 `:67` 的黄色提示。但两个调用点（`KnowledgeLibraryPage.tsx:3928`、`GatewayChat.tsx:594`）**都没传这个 prop**（grep 全仓只有定义和内部使用）。

后端已经在 `meta.provider_status.search_backend` 里给了值，前端能力也写好了，中间没接线。P2-1 修完这里就是最后一步。

### P2-3 `source_theme`：写列、读 payload

- 写：`db/schema.py` 新增 `garden_cards.source_theme` 列；`agents/garden_agent.py:511-526` 写入该列；
- 读：`api/routes/garden.py:149` → `str(raw_payload.get("source_theme") or "")`，`raw_payload` 是 `payload_json` 解析结果；`normalize_garden_payload` 也不处理 `source_theme`。

真实库里两者恰好一致（seed 脚本两处都写），所以现在看不出问题；**任何运行时新建的卡片**都只有列有值，API 返回空 → 卡片板的"所属专题"和列表 chip 空白。

**修法**：`"source_theme": str(data.get("source_theme") or raw_payload.get("source_theme") or "")`，并以列为准。同一条链上 `rag_bridge._search_garden_cards` 读的是 `SELECT *` 的列，已经是对的。

### P2-4 超长**单段**仍然被截断（"滑动窗口"没覆盖最常见的网页正文）

`agents/capture_indexer.py:120 _split_long_body` 只在 `\n\s*\n` 段落边界切分。一段 3000 字、内部无空行的正文（抓网页的典型形态）→ 1 个 chunk → `_clip(..., 1600)` 把后面 1400 字**直接丢掉**。

实测：
```
chunks from a single 3000-char paragraph: 2 [3000, 211]
  section_id=section-1 len(index_text)=1615   # 3000 字被裁到 1600
```
`tests/test_lineage_and_chunking_v3.py::test_long_section_is_split_and_tail_survives` 用的是 700 字/段 ×4，所以永远测不到。

**修法**：`if len(paragraph) > chunk_chars: 按句号/分号硬切`；或对 `_normalize_body(chunk)` 超长时做定长滑窗。再补一条"单段 3000 字，尾部结论必须在某个 chunk 里"的测试。

### P2-5 分块后缀 `（第 2 部分）` 泄漏到用户可见的 section 标题

`agents/capture_indexer.py:95`：`sub_title = clean_title if part == 1 else f"{clean_title}（第 {part} 部分）"`，这个 `sub_title` 同时是：
- `capture_embeddings.section_title`（检索/去重用，合理）；
- `_source_entry` 里 `section=` → **引用面板与 source_map 展示的章节名**。

于是用户看到的是"第二部分（第 2 部分）"。`test_capture_draft_workflow` 那 2 个红灯就是它（类 C）。

**修法**：索引标题带后缀，**展示标题不带**；给 `IndexItem` 增加 `display_title`，或让 `_source_entry` 用 `re.sub(r"（第 \d+ 部分）$", "", section)`。

### P2-6 卡片排序"对抗结论"缺可复核凭证

`docs/card-ordering-judge.json` 只有 `boundary_mentioned: bool` 和 `answer_len`，**没有 prompt、没有答案正文**。n=4、单次判官、关键词级判定 → 0/4 vs 1/4 差异的证据强度约等于"一条方向性轶事"。

文档 §8.14 已经老实写了"n=4 只能算定向信号"，这点值得肯定；但**artifact 本身无法复核**，评审者只能选择相信脚本。

**修法**：JSON 里存 `query / 上下文 source 顺序 / 两个答案全文 / 判官原始返回`。成本几乎为零。

### P2-7 评测 artifact 无逐条明细，且 G0 基线不是"旧版"

`scripts/eval_rag_benchmark.py:634` 只写汇总 `report`；`docs/benchmark-rag-v3-dashscope.json` 里没有任何一条 query 的命中列表。别人问"q07 为什么算命中"，答不上来。

更关键：G0 的 `_legacy_hits`（脚本 `:~355`）用的是**本轮的 FTS 表 + 本轮的 2-gram tokenizer**，只把 `kind` 限成 identity。而真正的"旧版"是 `_legacy_keyword_match_captures`（扫 500 篇、子串匹配）。所以"旧版 Recall 0.5 → 0.833"里的 0.5 **不是旧版成绩**，是"新组件被砍一半后的成绩"。这是拿新零件搭的稻草人。

**修法**：要么把 G0 实现成真正的旧路径（`git show HEAD~:bridge/rag_bridge.py` 的实现），要么把这一组改名成 `G0_new_ftS_identity_only` 并在文档里删掉"相比旧版提升"的表述。逐条 query 明细按 `{id, query, kind, gold, hits_by_group}` 落盘，体积可忽略。

---

## 4. P3 — 工程质量与卫生问题

| # | 位置 | 问题 |
| --- | --- | --- |
| 1 | 全仓 | 无 `.gitattributes`，工作区是 CRLF、索引是 LF → **每个改过的文件都整篇重写**。真实改动 1.5k 行，diff 显示 12.8k 行。演示"看我的 PR"会当场翻车。加 `.gitattributes: * text=auto` + 一次 `git add --renormalize .` |
| 2 | `db/embedding_repo.py:12 _delete_fts_row` | `DELETE ... WHERE capture_id=? AND kind=? AND section_id=?` 三列都是 `UNINDEXED`，FTS5 只能全表扫；`library_agent.py:369` 每次 `apply` 全量 rebuild → O(n²) 写入。30 行无所谓，1 万 chunk 时是分钟级。改用 `rowid` 维护一张映射表，或 rebuild 改成"仅在探测到不一致时"执行 |
| 3 | `agents/rag_graph.py:163 _pending_question_rows` | 用 `try: 三种 SQL 形状 except: continue` 猜 schema。真 schema 就在 `db/schema.py`，第一、三条是猜的。且会吞掉"库被锁/损坏"这类真错误 |
| 4 | `bridge/rag_bridge.py:92 record_card_citations` | 唯一调用点不传 `run_id`（真实库里 4 条事件全为空串）→ 重试会重复计数，且 append-only 日志无法按 run 去重；docstring 说"计数器随时可重算"，但**没有任何 recompute 函数**。两次 `commit()` 夹着 UPDATE，中途崩会漂 |
| 5 | `db/vector_search.py:37` | 返回的是 `[c for c in embedding_captures if id in top_ids]`，即 `created_at DESC` 顺序 —— **不是分数顺序**。top-k 选对了，排序是反的 |
| 6 | `bridge/deepseek_web_tools.py:130` | `"content": _text(item.get("content")) or summary`：把"没抓到正文"抹成"正文等于摘要"，下游无法区分证据强度。§8.11 实测"0 篇全抓"的根因之一就在这里。应改成 `content: ""` + `content_is_summary: True` |
| 7 | `bridge/deepseek_web_tools.py:179` | `strong = [rerank_score>=1.5]` 之后 `parsed = strong + weak`——原列表已按同一 key 降序排列，这一步是恒等变换，死代码 |
| 8 | `bridge/research/adapters.py:40 _web_tools_is_stubbed` | 生产代码靠 `sys.modules["tools.web_tools"]` 有没有 `__file__` 判断"是不是测试替身"。可读性极差且脆，应改成注入 |
| 9 | `bridge/research/light_web_qa.py:337` 等 3 处 | `_default_search_backend()` 每次都会走 `_deepseek_credentials()` → `load_config()` **读磁盘**。一次研究跑 3 次以上。加 `functools.lru_cache` 或进程内缓存 |
| 10 | `dashboard/src/pages/workspace.tsx:20` | 文件夹默认值在 `bridge/folder_store.py` 与前端各写一份，这次两边都改了才没炸。应下发 API |
| 11 | `agents/capture_agent.py:2495 _wants_ai_reading_guide` | 正则里 `导读` 已覆盖 `核心导读|观点导读|阅读导读` 三个分支（这 3 个是死代码）；且任何含"导读"的指令都会强制走"原文全量保留 + 顶部导读"分支 |
| 12 | `agents/capture_agent.py:2561 _paragraph_retention_ratio` | 只检查 ≥20 字的段落，**短 bullet 被整段删掉不会被发现**；且 `_body_compare_text` 抹掉所有标点空白，列表/表格结构丢失不可见 |
| 13 | `agents/library_agent.py:390 _maintain_card_embeddings` | 每张缺向量的卡片串行一次 embedding API；9 张没事，500 张就是 100 秒。应批处理（`text-embedding-v4` 支持 batch） |
| 14 | `web/src/components/KnowledgeAnswerView.tsx:82` | `split(/\n{2,}/)` 切开再逐块渲染 Markdown：**含空行的代码块/表格会被拦腰截断**，前后两块各自渲染 → 围栏不闭合。流式时标记未输完还会先按正文渲染再翻成黄框（跳版） |
| 15 | `web/src/pages/knowledge-chat/GatewayChat.tsx:55` | `TOPIC_INSPIRATIONS` 是**写死的 3 个选题**，UI 上却叫"跨篇灵感与输出选题提案"，视觉上等同"AI 扫出来的"。面试官问一句"这三个选题怎么生成的"就穿帮 |
| 16 | `GatewayChat.tsx:275` | 三态模式选择器被换成二态开关 → **`web_only` 从 Gateway 侧不可达**；而 `KnowledgeLibraryPage.tsx:3602` 仍保留三态。同一产品两套模式控件 |

---

## 5. 文档可信度：三处数字对不上

| # | 冲突 | 事实 |
| --- | --- | --- |
| D1 | `BENCHMARK-RAG-V3-dashscope.md:4` "生效阈值 0.46" vs `rag_bridge.py:55` "0.20" | 代码是 0.20，0.46 只活在 benchmark 的 `--threshold` 参数里 |
| D2 | `PRODUCT-REFACTOR-STORY.md:243` 把 `MIN_VECTOR_SCORE = 0.20` 当作设计机制正面陈述，而 §8.9 (:385) 论证 0.20 是错的 | 同一份文档自相矛盾；面试时被追问"那你到底用哪个"会卡住 |
| D3 | §8.5 表："G3 单次 RAG Prompt 平均字符数 **362**（上限 14000）" vs benchmark md："**754**" | 两个数，同一指标。必须删掉旧的那个 |
| D4 | `calibration-dashscope.json` 的 `note`："语义引擎实测分布，**可直接用于生产阈值**" vs 同文件 `separation = -0.0601` | P10(正)=0.429 < P95(负)=0.489，分布重叠时不存在能同时保 Recall 又全拦负样本的单阈值。这句 note 与自己的数据相反，应改成"仅作起点，生产需 rerank/分数融合" |
| D5 | benchmark md "已知限制" 第一条："离线 hash 引擎只衡量字面重合，真实语义指标需配置 key 后重跑" | 这份文档本来就是 dashscope 版，这句话是 hash 版残留 |

反过来说，§8.3（130 秒是超时预算不是 P95）、§8.12（`rejected≥1` 是契约误判）、§8.13/8.14（n=4 只能算定向信号）这三处**自查自纠写得很好**，是全篇最值钱的部分，不要为了好看把它们删掉。

---

## 6. 值得保留的实现（评审里也要说对的）

1. **FTS 派生数据 + 开机自愈**（`db/schema.py:699`, `library_agent.py:369`）：`needs_reindex` 会跳过未变 chunk，这个坑只有真跑过才知道。有自愈、有 rebuild 函数、有单测。
2. **2-gram + phrase 而不是 AND**（`db/fts_tokenizer.py`）：`_phrase_for` 的注释把"为什么不是 AND"讲清楚了，这是有判断力的选择。
3. **`_preserve_editable_body`**（`capture_agent.py:2577`）：先判"原文是否整体仍在"再判长度再判段落抽样，三级顺序是对的——结构改写放行、偷懒摘要拦截。`tests/test_capture_guard_pressure.py` 四个用例把边界都钉住了。
4. **引用事件 append-only + 只记正文里真实出现的 `[n]`**（`rag_bridge.py:92`）：把"命中≠使用"这个语义分开，方向完全正确（只差 `run_id` 和 recompute）。
5. **`refresh_graph_after_write` 吞异常**（`agents/knowledge_graph.py:29`）：记账失败不能让用户的写操作失败，这个取舍对，而且有 `card_uses_capture` 边单测。
6. **`folder_store.DEFAULT_FOLDERS` 换主题后老目录不丢**：`dashboard/.../workspace.tsx:94-95` 确实做了 `[...baseFolderNames, ...new Set(dynamicFolders)]` 合并，注释里"老 vault 仍会显示"是**真的**（我核过）。
7. **`backfill.py` 重写**：明确指出旧版嵌入的是已删除的 `captures.summary` 列、"根本跑不起来"，并收敛到一个 reindex 入口。诚实且正确。

---

## 7. 建议的修复顺序（一个 PR 一件事）

| 序 | PR | 内容 | 验收 |
| --- | --- | --- | --- |
| 1 | fix/citation-refs | P1-1：去重提前到 `_search_knowledge_base`；加 prompt/source_map 对齐不变式测试 | 新增断言 `len(sources) == len(source_map)`，且构造重复 capture 的用例通过 |
| 2 | fix/keyword-extract | P1-2：切分后再按整 token 去停用词 | 上表 5 个 query 的期望输出写进测试 |
| 3 | fix/threshold | P1-3：常量 → 0.46，跑一次不带 `--threshold` 的 benchmark 重写 docs | `docs/*.json` 的 `threshold` 与代码常量相等（加一条测试读常量比对） |
| 4 | fix/red-suite | P1-4：改 2 个契约测试、4 个计时断言、section 标题泄漏（P2-5） | `pytest tests` 全绿；浏览器用例隔离到独立 marker 并在 CI 单独跑 |
| 5 | fix/wiring | P2-1/2/3：real backend 上报、前端接线、source_theme 读列 | 手动触发一次降级（改坏 key）能看到黄条 |
| 6 | fix/chunking | P2-4：超长段落硬切 | 单段 3000 字用例，尾句必须可检索 |
| 7 | chore/repo-health | P3-1 CRLF、P3-2 FTS 删除、P3-4 run_id | `git diff --stat` 回到 1.5k 量级 |
| 8 | docs/reconcile | §5 五条数字冲突 | 文档内不存在第二个阈值/第二个 prompt 字符数 |

---

## 8. 面试口径风险清单（被问到会答不上来的点）

1. "你说做了引用血缘，那 prompt 里第 3 个来源和第 3 号引用是同一篇吗？" → **现在不是**（P1-1）。
2. "阈值 0.46 是怎么生效的？" → 代码里没有 0.46（P1-3）。
3. "旧版 Recall 0.5 是哪份代码跑出来的？" → 是本轮新组件砍一半，不是旧版（P2-7）。
4. "0/4 和 1/4 的判官结果，能给我看答案原文吗？" → artifact 里没有（P2-6）。
5. "测试都绿吗？" → 7 红 + 浏览器崩（P1-4）。
6. "卡片排序结论能推广吗？" → 不能，n=4；这条目前文档里已经老实写了，保持住。

---

## 9. 全量问题清单

> §2–§5 是深挖版；本节是**完整索引**，已含此前未展开的 11 个 P2 与 18 个 P3。
> 级别含义：**阻断** = 不修就会被问穿；**P2** = 功能或口径错、有确定修法；**P3** = 工程质量/卫生/性能。

### 9.1 计数

| 级别 | 数量 |
| --- | --- |
| 阻断 P0/P1 | 4 |
| P2 | 19（原有 7 + 新增 12） |
| P3 | 34（原有 16 + 新增 18） |
| 文档冲突 | 8（原有 5 + 新增 3） |
| **合计** | **65** |

### 9.2 阻断级（4）— 详见 §2

| ID | 位置 | 问题 |
| --- | --- | --- |
| P1-1 | `rag_bridge.py:258/276` + `rag_agent.py:66` | prompt 按 capture 去重、source_map 不去重 → `[n]` 指向另一篇资料（已复现） |
| P1-2 | `rag_bridge.py:84` | 关键词抽取对整串做 `replace`，把技术名词切碎 → FTS 补召回与相关度闸门静默失效 |
| P1-3 | `rag_bridge.py:55` | `MIN_VECTOR_SCORE=0.20`，与文档 0.46、与自己的标定结论相反 |
| P1-4 | `tests/` | 全量 7 failed（排除浏览器）/ 含浏览器则进程崩溃；"宽回归 EXIT=0"不成立 |

### 9.3 P2（18）

| ID | 位置 | 问题 |
| --- | --- | --- |
| P2-1 | `research/adapters.py:118`、`light_web_qa.py:337` | `search_backend` 上报"配置的"而非"实际服务的"；降级时仍显示 deepseek |
| P2-2 | `KnowledgeAnswerView.tsx:27` | 新增的 `searchBackend` prop 两个调用点都没传 → 降级提示是死代码 |
| P2-3 | `api/routes/garden.py:149` | `source_theme` 写列、读 `payload_json`；运行时新建卡片专题为空 |
| P2-4 | `capture_indexer.py:120` | 单段超 1600 字无法切分 → `_clip` 静默丢弃（实测 3000 字丢 1400） |
| P2-5 | `capture_indexer.py:95` | 分块后缀「（第 2 部分）」泄漏成用户可见章节名（2 个红灯的根因） |
| P2-6 | `docs/card-ordering-judge.json` | 无 prompt、无答案正文；n=4 的结果无法复核 |
| P2-7 | `eval_rag_benchmark.py` | G0 用新 FTS+新 tokenizer 冒充旧版；artifact 无逐条 query 明细 |
| P2-8 | `rag_bridge.py:564` | FTS 命中的 section 行 hydrate 后不带 `section_title`，却被标成 `capture_section` → 章节空、面板点不中 |
| P2-9 | `rag_bridge.py:~200` | `extend(keyword_hits)` 之后紧接 `results[:7]`：graph 返 6 条时补召回最多留 1 条，静默丢弃 |
| P2-10 | `rag_agent.py:RAG_SYSTEM_PROMPT` | 删掉「诚实说不知道」「不要编造」，只留 💡 标记；全仓无测试断言该标记真会出现 |
| P2-11 | `rag_bridge.py:_fts_keyword_rows` | 把"无 FTS5"与"MATCH 语法错"都当成 `None` → 后者静默回退 500 篇 hydrate 扫描 |
| P2-12 | `calibrate_embedding_threshold.py:154` | τ=(Q95neg+Q10pos)/2，在分布重叠时按定义就牺牲约 10% 正样本，docstring 未声明该假设 |
| P2-13 | `eval_rag_benchmark.py:188` | `_EMBED_CACHE` 让 G1 冷跑付费（520ms）、G2/G3 读缓存（1.6ms）→ 延迟表不可比，且与 docs 口径自相矛盾 |
| P2-14 | `rag_bridge.py:195` | 降级到 `db/vector_search.search` 时它内部又 `embed(query)` 一次 → 同一 query 嵌两次 |
| P2-15 | `api/routes/library.py:53` | `capture_bridge.create_item` 的 `organize` 默认是 **True**，路由现在显式传 `False` → 既有调用方静默失去标题/摘要/建档建议。改动有意（免 LLM 阻塞），但无版本、无 changelog |
| P2-16 | `rag_agent.py:~50` | 预算触顶 `break` 时直接丢来源，prompt 与 meta 都不告知"已省略 N 条" |
| P2-17 | `rag_bridge.py:_search_garden_cards` | `SELECT *`（含 payload_json）+ Python 打分 + 无 LIMIT，每次问答全表扫 |
| P2-18 | `rag_bridge.py:_garden_card_match_score` | `needle in title`（整句匹配标题）几乎不可能命中；卡片 `score` 写死 0.85 与向量分同池排序不可比 |

### 9.4 P3（34）

| ID | 位置 | 问题 |
| --- | --- | --- |
| P3-1 | 全仓 | 无 `.gitattributes`，CRLF 让真实 1.5k 行改动显示成 12.8k 行 |
| P3-2 | `embedding_repo.py:12` | FTS 删除走三列 UNINDEXED 的全表扫，叠加每次 apply 全量 rebuild → O(n²) |
| P3-3 | `rag_graph.py:163` | 用 `try 三种 SQL` 猜 schema，且吞掉"库被锁/损坏"这类真错误 |
| P3-4 | `rag_bridge.py:92` | `run_id` 恒空、两次 commit 夹 UPDATE、无 recompute 函数 |
| P3-5 | `db/vector_search.py:37` | 返回顺序是 `created_at DESC` 而非分数序 |
| P3-6 | `deepseek_web_tools.py:130` | `content or summary` 把"没抓到正文"抹成"正文=摘要"，下游无法判证据强度 |
| P3-7 | `deepseek_web_tools.py:179` | `strong+weak` 在已同 key 降序的列表上是恒等变换，死代码 |
| P3-8 | `research/adapters.py:40` | 生产代码靠 `sys.modules` 有没有 `__file__` 判断"是不是测试替身" |
| P3-9 | `light_web_qa.py:337` 等多处 | `_default_search_backend()` 每次都走 `load_config()` 读磁盘，单次研究被调用 ≥2 次 |
| P3-10 | `dashboard/.../workspace.tsx:20` | 文件夹默认值前后端各写一份 |
| P3-11 | `capture_agent.py:2495` | 正则里 3 个 `导读` 分支是死代码（`导读` 已覆盖） |
| P3-12 | `capture_agent.py:2561` | 只检查 ≥20 字段落；短 bullet 被整段删掉不可见 |
| P3-13 | `library_agent.py:390` | 每张缺向量的卡片串行一次 embedding API，未批处理 |
| P3-14 | `KnowledgeAnswerView.tsx:82` | `split(/\n{2,}/)` 逐块渲染 → 含空行的代码块/表格被拦腰截断 |
| P3-15 | `GatewayChat.tsx:55` | `TOPIC_INSPIRATIONS` 是写死的 3 个选题，UI 上等同"AI 扫出来的" |
| P3-16 | `GatewayChat.tsx:275` | 三态换成二态开关 → `web_only` 从 Gateway 不可达，而 Library 页仍保留三态 |
| P3-17 | 仓库状态 | 24 个新文件 + 7 份文档（含本评审）全部 `??` 未提交，切分支即丢 |
| P3-18 | `hermes-agent-main/.tmp/` | node 编译缓存未被 gitignore，`git status` 被 30+ 行噪声占满 |
| P3-19 | `db/schema.py:699` | 自愈只在 FTS **完全为空**时触发；半写状态永不修复，与 `library_agent.py:369` 的全量 rebuild 语义不同 |
| P3-20 | `embedding_repo.py:_coarse_filter` | identity 向量粗筛算一次 cosine，外层又算一次 |
| P3-21 | `embedding_repo.py:search` | `LEFT JOIN captures` 让"capture 已删、chunk 残留"的行照样通过过滤 |
| P3-22 | `db/schema.py:citation_events` | 无 run_id / cited_at 索引、无唯一约束；按 run 聚合要全表扫 |
| P3-23 | `rag_bridge.py:_bm25_rank_to_score` | bm25 映射成 0..1"显示分"，量纲任意，不可与 cosine 分比较 |
| P3-24 | `rag_bridge.py:_CITED_REF_RE` | `\[(\d+)\]` 会把代码块和 `[1](url)` 链接里的方括号计入引用 |
| P3-25 | `rag_bridge.py:_legacy_keyword_match_captures` | 500 篇 hydrate 的旧路径仍在，且 P2-11 会静默走到它 |
| P3-26 | `capture_agent.py:2495` | 任何含"导读"的指令都强制走"原文全保留+顶部导读"，只想写导读也会被塞原文 |
| P3-27 | `capture_indexer.py:150` | `_fallback_sections` 生成"正文片段 3"作为章节名，会出现在引用面板 |
| P3-28 | `garden_agent.py:511` | INSERT 后补一条 UPDATE；循环里逐 capture_id 查 `capture_metadata`（N 次查询） |
| P3-29 | `garden_agent.py:~745` | 写卡片路径里 `load_config()` 读磁盘，每次提交一次 |
| P3-30 | `knowledge_graph.py:29` | bare `except` 无日志无指标 → 图谱静默过期 |
| P3-31 | `library_agent.py:390` | 卡片缺口只在 `apply=True` 时报告，`--dry-run` 完全静默 |
| P3-32 | `toolsets.py` | 冻结 23-tool legacy 条目，两套 surface 会漂移；测试只锁数量与差集 |
| P3-33 | `tests/test_toolset_exposure.py` | `REQUIRED_TOOLS` 与 `toolsets.py` 字面量重复（两处真理） |
| P3-34 | `eval_rag_benchmark.py:_vector_hits` | 用 `limit*3`（产品 `limit*4`）且重实现门禁 → 与产品缓慢漂移 |

### 9.5 文档冲突（8）

| ID | 位置 | 问题 |
| --- | --- | --- |
| D1 | `BENCHMARK-RAG-V3-dashscope.md:4` | 写"生效阈值 0.46"，代码是 0.20 |
| D2 | `PRODUCT-REFACTOR-STORY.md:243` vs `:385` | 同一份文档一边把 0.20 当机制陈述，一边论证 0.20 是错的 |
| D3 | `STORY:335`（362 字符） vs `BENCHMARK:8`（754 字符） | 同一指标两个数 |
| D4 | `calibration-dashscope.json` note | 写"可直接用于生产阈值"，而同文件 `separation=-0.06` |
| D5 | `BENCHMARK-RAG-V3-dashscope.md` 已知限制 | 残留 hash 版文字（"需配置 key 后重跑"） |
| D6 | `STORY:63/74/196` vs `:318` | Story 仍以"P95 130s→20s"陈述，§8.3 却写"不要用这个说法" |
| D7 | `STORY:335` | "新增测试 30 passed"，实测 50 passed（数字过期） |
| D8 | `docs/` | hash 版与 dashscope 版 benchmark 并存，未标主口径；面试引用错版本即自相矛盾 |

### 9.6 与修复批次的对应

| PR | 覆盖条目 |
| --- | --- |
| fix/citation-refs | P1-1、P2-8、P3-24 |
| fix/keyword-extract | P1-2、P2-9、P2-11、P3-25 |
| fix/threshold | P1-3、P2-12、P2-13、D1–D4 |
| fix/red-suite | P1-4、P2-5、D7 |
| fix/wiring | P2-1、P2-2、P2-3、P2-15、P3-9、P3-10 |
| fix/chunking | P2-4、P3-27 |
| fix/research-observability | P2-10、P2-16、P2-17、P3-6、P3-30 |
| fix/cards | P2-6、P2-18、P3-28、P3-29、P3-31 |
| chore/repo-health | P3-1、P3-2、P3-4、P3-17、P3-18、P3-19、P3-21、P3-22 |
| chore/toolsets | P3-32、P3-33 |
| docs/reconcile | D5、D6、D8 |

### 9.7 补充发现（复核施工方案时新增，第 65 项）

| ID | 位置 | 问题 |
| --- | --- | --- |
| P2-19 | `scripts/seed_interview_demo.py:940-950` | **演示库的向量是合成的，不是语义向量**：`_concept_vector(name)` 用 `sha256(name) → rng.normal(0,1,DIM)` 生成随机单位向量，section 向量由 identity 混合而来。实测真实库 30 个 chunk：同篇 chunk 余弦均值 **0.9903**（三个 section 几乎无法区分）、跨篇均值 **0.3478**、最大 **0.9940**。后果：①查询用 DashScope 真实向量、索引是随机向量，余弦≈0，向量检索在 demo 上等于不工作，全靠字面关键词兜底；②`MIN_VECTOR_SCORE` 从 0.20 改到 0.46 是在**另一个嵌入空间 + 另一个语料**上标定的值，demo 行为会变但从未被测过；③"两阶段 identity→section 检索"在 demo 上不可能生效，因为同篇 chunk 天然几乎重合 |

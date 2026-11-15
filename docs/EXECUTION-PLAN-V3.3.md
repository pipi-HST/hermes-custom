# V3.3 执行方案（定稿版）

> 本文是**可直接照着改代码**的施工单，不是讨论稿。
> 行号在 2026-11-16 的工作区核准过；若后续有偏移，**按函数名定位，不要按行号硬改**。
> 基线：`repos/hermes-custom` 改动 26 个文件 + 24 个新增文件（真实 1,535+/118−），全量 `pytest tests` 当前 **7 failed**（排除 3 个浏览器文件，含则进程崩溃）。

## 0. 相对 V3.3 的 3 处定稿修正

| # | V3.3 原写法 | 定稿改为 | 原因 |
| :-- | :-- | :-- | :-- |
| 1 | Step D 停用词表无 `了解` | 停用词表加 `"了解"` | 实测 `了解了 MCP 之后应该怎么用` / `了解 MCP 怎么用` 仍得到 `['了解','mcp']`，13 条 Gate 里 2 条 FAIL |
| 2 | 62 处 `time.monotonic()` 全换 `perf_counter` | **不换钟，只改精度**：`int(...)` → `round(...,3)`，共 8 处；`planner.py` 不动 | CPython 上两者同一个时钟，换钟零收益；且 `tests/test_research_bridge.py:121` 断言 `isinstance(planner_elapsed_ms, int)`，换 float 会把它改红；`_RESEARCH_DEADLINE` 仍以 monotonic 设定，混钟在 macOS 上是隐患 |
| 3 | `_keyword_match_captures` 记作 `rag_bridge.py:590` | **实际 564**；`_search_garden_cards` 498；`_fts_keyword_rows` 617 | 行号漂移，按名字定位 |

**另外一条 V3.3 没覆盖的实现细节**：`bridge/research/timing.py` 的 int 转换有 **3 处**（不是 1 处），只改 `_add_stage_timing` 不够——
`_empty_stage_timings()` 返回 `dict[str,int]`、`_normalized_stage_timings()` 会 `int(...)` 再截一次、`_add_stage_timing()` 才是取差值的地方。三处必须同时放宽为 float。

---

## 1. 执行顺序（严格串行，前一步 Gate 不过不进下一步）

```
Step 0  仓库卫生与基线快照      →  快照/renorm 两个独立提交
Step 1  引用编号单点化          →  repro 脚本 EXIT=0
Step 2  关键词抽取 + FTS 补召回  →  13 条分词断言全绿
Step 3a 阈值 0.46 + demo 真向量  →  6/6 命中 + 账本一致
Step 4  7 个红灯 + 切分 + 计时精度 →  pytest EXIT=0
Step 3b chunking 定稿后重跑评测  →  代码/JSON/文档三方相等
Step 5  前后端接线 + 文档对齐    →  前端 8 check + tsc + eslint 全绿
```

每个 Step 对应一个分支，Gate 通过后合入；任何一步 Gate 不过，停在原地修，不允许"先往下做回头补"。

---

## Step 0 — 仓库卫生与基线快照（`chore/repo-health`）

**改动**

1. `hermes-agent-main/.gitignore` 追加一行 `.tmp/`；删除工作区 `hermes-agent-main/.tmp/`（node 编译缓存，不要提交）。
2. 双仓各打一个快照提交，把当前所有修改与未追踪文件原样入库：
   ```bash
   cd repos/hermes-custom && git add -A && git commit -m "chore: snapshot v3 working tree before review fixes"
   cd ../hermes-agent-main && git add -A && git commit -m "chore: snapshot v3 working tree before review fixes"
   ```
3. 双仓根目录新增 `.gitattributes`（内容仅一行 `* text=auto eol=lf`），然后**单独一个提交**做归一化：
   ```bash
   git add --renormalize . && git commit -m "chore: normalize line endings to LF"
   ```
4. 记录测试基线（用于 Step 4 前后对比）：
   ```bash
   .venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider \
     --ignore=tests/test_knowledge_product_e2e.py \
     --ignore=tests/test_reset_hermes_local_data.py \
     --ignore=tests/test_reset_smoke.py > ../../.review/baseline-after-step0.log 2>&1
   ```

**Gate 0**

- `git status --short` 为空（两个仓）；
- `git log --oneline -3` 能看到 `snapshot` 与 `normalize` 两个独立提交；
- 之后任意功能改动的 `git diff --stat` 不再出现整文件重写（对照：现在 `capture_agent.py` 显示 5250 行，真实只有 182 行）。

**回滚**：两个提交都可 `git reset --soft` 回到快照态；renorm 提交单独 revert 即可恢复 CRLF。

---

## Step 1 — 引用编号单点化（`fix/citation-refs`）

**文件**：`agents/rag_graph.py`、`agents/rag_agent.py`、`bridge/rag_bridge.py`、`scripts/repro_v3_review_findings.py`、`tests/test_lineage_and_chunking_v3.py`

### 1.1 图检索每篇只留 1 席

`agents/rag_graph.py:257` `_prefer_sections_over_whole_note` 重命名为 `_one_hit_per_capture`，末尾加兼容别名 `_prefer_sections_over_whole_note = _one_hit_per_capture`（无测试引用，纯粹防外部脚本）。

新的语义：按 `capture_id` 分组；**若该篇存在 `matched_kind == "section"` 的命中，只取其中分数最高的一条；否则取 `identity` 中最高分的一条**。输入 `ranked` 已按分数降序，实现上遍历一次即可。

### 1.2 拆 `select_prompt_sources` / `render_prompt`（`agents/rag_agent.py`）

现状：`build_rag_prompt`（:31-84）内部 `for i, c in enumerate(_dedupe_captures(captures), 1)`，`:48` 去重、`:57` 预算 `break`，两件事耦合在编号里。

改成三个函数，**头部拼接只写一份**，避免 select 与 render 的开销估算漂移：

```python
def _prompt_header(question: str, context_pack: dict | None) -> list[str]:
    """question + orientation 的前缀块，select 与 render 共用。"""

def select_prompt_sources(captures, *, question="", context_pack=None,
                          budget=MAX_TOTAL_PROMPT_CHARS) -> list[dict]:
    """去重 + 预算裁剪，返回真正会进 prompt 的 K 条（编号的唯一来源）。"""

def render_prompt(question, selected, *, context_pack=None) -> str:
    """只渲染，不再去重、不再 break。"""

def build_rag_prompt(question, captures, *, context_pack=None) -> str:
    return render_prompt(question,
                         select_prompt_sources(captures, question=question,
                                               context_pack=context_pack),
                         context_pack=context_pack)
```

细节要求：

- `select_prompt_sources` 复制字典，把被截断那条的正文写入 `item["_prompt_body"]`；`render_prompt` 优先读 `_prompt_body or body`。
- `RagAgent.query(self, question, captures, *, context_pack=None, preselected=False)`：`preselected=True` 时直接 `render_prompt`，否则先 `select_prompt_sources`。保留旧签名兼容。

### 1.3 `rag_bridge` 统一编号（`bridge/rag_bridge.py:253-280`）

```python
deduped_total = len(_dedupe_captures(results))
results = select_prompt_sources(results, question=query, context_pack=context_pack)
omitted_source_count = max(0, deduped_total - len(results))
resp = rag.query(query, results, context_pack=context_pack, preselected=True)
...
for item in results:            # 不要让内部字段流进 artifact / API
    item.pop("_prompt_body", None)
source_map = [_source_entry(item, index) for index, item in enumerate(results, 1)]
```

`meta` 增加 `"omitted_source_count": omitted_source_count`。

### 1.4 FTS 命中行补齐章节信息

`_fts_keyword_rows`（`rag_bridge.py:617`）SQL 增加：

```sql
JOIN capture_embeddings ce
  ON ce.capture_id = capture_search_fts.capture_id
 AND ce.kind       = capture_search_fts.kind
 AND ce.section_id = capture_search_fts.section_id
```
并选出 `ce.section_title AS section_title, ce.index_text AS index_text`。
**不要给 `capture_search_fts` 起别名**——`bm25(capture_search_fts, 0,0,0,3,1)` 依赖真表名。

`_keyword_match_captures`（`:564`）在调 `_hydrate_capture` 前把 `matched_kind / section_id / section_title / index_text` 连同 `id / file_path / raw_path / created_at` 一起放进入参字典，**再** hydrate（顺序反了 `_hydrate_capture` 里按 section 截正文的分支不触发）。

### 1.5 引用正则排除代码块与链接

`record_card_citations` 中先把 ` ```…``` ` 与 `` `…` `` 剥掉，再 `re.findall(r"\[(\d+)\](?!\()", cleaned)`。

### 1.6 更新复现脚本

`scripts/repro_v3_review_findings.py`：`p1()` 先 `selected = select_prompt_sources(results, question="问题")`，再用同一份 `selected` 生成 `source_map` 与 `prompt`；结尾加

```python
if len(prompt_ids) != len(source_map) or len(ids) != len(set(ids)):
    raise SystemExit(1)
```

**Gate 1**

- `python scripts/repro_v3_review_findings.py` → `EXIT=0`，输出 `3 sources in prompt vs 3 refs in source_map` 与 `final_hits: 1 rows, 1 distinct capture ids`；
- `pytest tests/test_lineage_and_chunking_v3.py -q` 全绿，且该文件断言收紧为 `len(prompt) <= MAX_TOTAL_PROMPT_CHARS + 200` + `<source>` 数与 `source_map` 长度、标题逐项相等。

---

## Step 2 — 关键词抽取与 FTS 补召回（`fix/keyword-extract`）

**文件**：`bridge/rag_bridge.py`、`db/fts_tokenizer.py`、`tests/test_fts_chinese_and_schema.py`

### 2.1 重写 `_extract_query_keywords`（`:84`）

- **Step A** 按 `[^\w\u4e00-\u9fff]+` 切分；
- **Step B** 剥词首引导语 `^(?:请问|帮我|麻烦)?(?:说说|讲讲|介绍一下|解释一下|告诉我)?(?:了解了|了解|读了|看了)?(?:什么是|何为|为什么|怎么理解|如何理解|怎么|如何)`；剥词尾疑问壳 `(?:之后应该怎么用|应该怎么用|是什么|是什么意思|有什么用|有哪些|怎么用|怎么设计|是多少|在哪里)+$`；再在 `len(token) >= 3` 时剥 `^(?:是|有)` 与 `(?:了|吗|呢|呀|吧)$`；
- **Step C** 不做词内切分（`目的 / 总和 / 和而不同` 全部保住）；
- **Step D** 停用词 `{"什么","怎么","如何","哪些","一下","帮我","请问","关于","之后","应该","了解"}`，`len < 2` 丢弃。

### 2.2 新增 `build_fts_or_query`（`db/fts_tokenizer.py`）

把关键词的中文 2-gram 与拉丁/数字 token 用 `OR` 连接；**仅当 2-gram 的两个字符都在 `frozenset("的了是在和与及或吗呢呀吧")` 中时才剔除**（这样 `目的 / 的确 / 在线` 会保留）。

### 2.3 `_fts_keyword_rows` 补足回退（`:617`）

先跑 `build_fts_match_query`；**若命中数 `< limit`**，再跑 `build_fts_or_query`，按 `(capture_id, kind, section_id)` 去重后追加、补足到 `limit`。
异常分型：`sqlite3.OperationalError` 中仅 `no such table` / `no such module` 返回 `None`（表示无 FTS5），其余记日志返回 `[]`。

### 2.4 相关度闸门对长复合词放宽

`_indexed_search` 的 `_has_literal_keyword`：长度 ≥5 的中文词，非虚字 2-gram 命中 ≥2 个即算字面命中。

### 2.5 关键词补召回保底 2 席

`_search_knowledge_base` 中，`keyword_hits` 非空时主结果最多保留 `max(0, 7 - min(2, len(keyword_hits)))` 条再接关键词，避免被 `[:7]` 截掉。

**Gate 2**（13 条精确分词 + 2 条召回）

```
解释器模式有什么用→['解释器模式']   什么是传话筒→['传话筒']      避坑经验有哪些→['避坑经验']
了解了 MCP 之后应该怎么用→['mcp']  了解 MCP 怎么用→['mcp']      目的地在哪里→['目的地']
总和是多少→['总和']               成本总和是多少→['成本总和']    君子和而不同→['君子和而不同']
和谐社会→['和谐社会']             和而不同→['和而不同']         的确是重点→['的确是重点']
了如指掌→['了如指掌']
```
外加：`转介绍的目的和边界是什么`、`真正的目的是什么` 在测试库上 `_fts_keyword_rows` 命中数 `> 0`。
`pytest tests/test_fts_chinese_and_schema.py -q` 全绿。

---

## Step 3a — 阈值 + demo 真向量 + 引用账本（`fix/threshold-prep`）

**文件**：`bridge/rag_bridge.py`、`scripts/seed_interview_demo.py`、`tests/test_seed_interview_demo.py`、`scripts/eval_rag_benchmark.py`、`scripts/calibrate_embedding_threshold.py`

1. **阈值**：`MIN_VECTOR_SCORE = 0.46`（`:55`）。要让 config 能覆盖，**不要**在 `_indexed_search` 内部读 config（它没有 config 参数）——给 `_indexed_search(..., threshold: float | None = None)` 加参数，`_search_knowledge_base` 传 `getattr(config, "min_vector_score", None)`。
2. **`recompute_card_citation_counts(conn)`**：一条 SQL 从 `citation_events` 按 `card_id` 聚合 `COUNT(*)` / `MAX(cited_at)` 回写 `garden_cards.citation_count` / `last_cited_at`。`record_card_citations` 补 `run_id`（默认 UUID）并改成单事务。
3. **seed**：`seed(conn, vault, *, use_real_embeddings: bool = False)`（`:1257`）默认 False，单测零网络；`main()`（`:1335`）在 `--offline` 未传且 `config.qwen_api_key` 非空时传 True；`seed()` 末尾调 `recompute_card_citation_counts(conn)`。
4. **评测脚本**：`_blob`（`:198`）前先预热全部 query；`_legacy_hits`（`:364`）换成真实旧版「标题/摘要子串匹配」基线并改名 `G0_title_summary_substring`；`_vector_hits`（`:341`）候选倍数改 `limit*4` 与产品对齐；报告加 `per_query_details`。
5. **calibration**：docstring 与 JSON `note` 明确 `separation<0` 时 τ 是折中工作点、牺牲约 10% 边缘正样本、不可替代 rerank。

**执行前**：`cp data/captures.db data/captures.db.bak-step3a`（seed 不可逆）。

**Gate 3a**

- `pytest tests/test_seed_interview_demo.py -q` 秒过（0 次网络）；
- `python scripts/seed_interview_demo.py` 后：`SELECT COUNT(*) FROM garden_cards WHERE embedding IS NOT NULL` = 9；citation_count 与 citation_events 对账相等；
- 6 条 demo 问句实测：打印每题 Top-1 余弦，要求 **6/6 命中目标资料**；若有题落在 `0.42~0.46`，写进 config 与文档，不硬抬阈值。

---

## Step 4 — 清零红灯与切分定稿（`fix/red-suite`、`fix/chunking`）

1. **章节去后缀**（`agents/capture_indexer.py:95`）：`IndexItem.section_title` 存干净 `clean_title`；`index_text` 里保留 `f"章节：{sub_title}"`。
2. **分块摘要**（`bridge/rag_bridge.py:799` `_hydrate_capture`）：`matched_kind == "section"` 且 `index_text` 含 `"\n内容："` 时，`item["body"] = index_text.split("\n内容：", 1)[1]`；否则回退 `_extract_section_body`。
3. **单段超长**（`capture_indexer.py:120`）：`len(paragraph) > chunk_chars` 时先按句末标点切句合并，无标点则定长滑窗（步长 `chunk_chars - overlap`）；`_fallback_sections`（`:150`）用首句前 18 字做章节名。
4. **两个契约测试**（`tests/test_research_bridge.py`）：`:5206` → `["https://example.com/rag-1"]`；`:5255` → `["https://example.com/rag"]`。
5. **计时精度（只改精度，不换钟）**：
   - `bridge/research/timing.py` 三处：`_empty_stage_timings` 返回 `dict[str, float]`、`_normalized_stage_timings` 的 `int(...)` → `float(...)`、`_add_stage_timing` 的 `max(int(...),0)` → `max(round((time.monotonic()-started)*1000.0, 3), 0.0)`；
   - `bridge/research/evidence.py:179` 同上；
   - `bridge/web_context_answer.py:333, 874, 882, 1522, 1558` 同上；
   - **`bridge/research/planner.py` 不动**（`_elapsed_ms` 保持 int，`tests/test_research_bridge.py:121` 的 `isinstance(..., int)` 继续绿）。
6. **浏览器隔离**（`tests/conftest.py` 末尾追加）：`pytest_addoption("--run-browser")` + `pytest_collection_modifyitems`，默认 skip `test_knowledge_product_e2e.py` / `test_reset_hermes_local_data.py` / `test_reset_smoke.py`。

**Gate 4**

- `pytest tests -q` → **0 failed，EXIT=0**，无 crashpad；
- `grep -rn "int((time\." bridge/` 只剩与阶段计时无关的位置（应为空）；
- `pytest tests/test_research_bridge.py -q` 中 `== 0` 的 6 条与 `isinstance(..., int)` 1 条仍绿；
- 新增单测：单段 3000 字无空行长文，第 2900 字处关键词可被 FTS 命中。

---

## Step 3b — 定稿后重跑评测（`chore/rerun-benchmarks`）

1. `python scripts/seed_interview_demo.py`（最新分块 + 真向量）；
2. `python scripts/eval_rag_benchmark.py`（不带 `--threshold`）；
3. `python scripts/calibrate_embedding_threshold.py`；
4. `python scripts/eval_card_ordering_judge.py`（`docs/card-ordering-judge.json` 落 `query / sources / 两个答案正文 / judge_raw_output`）。

**Gate 3b**

- 单测 `benchmark_json["threshold"] == MIN_VECTOR_SCORE == 0.46`；
- judge JSON 每条含非空 `card_first_answer` / `evidence_first_answer` / `judge_raw_output`；
- benchmark JSON 含 `per_query_details`。

---

## Step 5 — 接线与文档对齐（`fix/wiring-and-docs`）

| 文件 | 改动 |
| :-- | :-- |
| `bridge/research/adapters.py:118` | 回退分支先归一化 `web_search_tool` 返回值（可能是 str），再注入 `"backend": "fallback"` |
| `bridge/research/light_web_qa.py:337`、`bridge/web_context_answer.py:1531` | 用**实际返回值**里的 backend，而非 `_default_search_backend()` |
| `web/src/lib/knowledgeChatState.ts`、`GatewayChat.tsx:594`、`KnowledgeLibraryPage.tsx:3928` | 透传 `searchBackend` |
| `api/routes/garden.py:149` | `str(data.get("source_theme") or raw_payload.get("source_theme") or "")` |
| `agents/rag_agent.py:18` | `RAG_SYSTEM_PROMPT` 补回「资料未提及且无可靠通识时诚实说明」 |
| `db/vector_search.py:8,37` | 接受 `query_embedding`；按分数降序返回 |
| `bridge/rag_bridge.py:498` `_search_garden_cards` | 显式列字段 + `LIMIT 200`；去掉硬编码 `0.85`；不加 `score>=2` |
| `db/embedding_repo.py:232` | `LEFT JOIN` → `JOIN` |
| `bridge/deepseek_web_tools.py:130,179` | 区分 `content`/`summary`（加 `content_is_summary`）；删 `strong+weak` |
| `web/src/components/KnowledgeAnswerView.tsx:82` | fence-aware + 列表/表格安全切分 💡 段 |
| `web/src/pages/knowledge-chat/GatewayChat.tsx:55` | 初始选题标注「示例选题（点击右侧扫描生成实时跨篇提案）」 |
| `docs/*.md` | 数字全部从 Step 3b 的 JSON 回填；负样本写 `2/2(n=2)`；统一 130 秒超时预算口径；补「单篇单节取舍」「卡片向量已落盘未接检索」两段 |

**Gate 5**：`pytest tests` 全绿；前端 8 个 `npm run check:*` + `npx tsc -b` + `npx eslint .` 全绿；`git status` 干净。

---

## 2. 三条"绝不做"

1. **绝不为让断言通过而伪造测量**：不把 0ms 记成 1ms；不出 nan/占位分。
2. **绝不混用两个时钟做减法**：`_RESEARCH_DEADLINE` 与超时判断仍走 monotonic，本轮只改精度。
3. **绝不用"删测试"换绿灯**：被 `EDGE` 规则拆坏的 `和谐社会` 已加回 Gate 2，删测试的提交一律打回。

## 3. 风险登记

| 风险 | 触发点 | 处置 |
| :-- | :-- | :-- |
| seed 不可逆 | Step 3a/3b 各跑一次 | 先 `cp data/captures.db data/captures.db.bak-*` |
| `0.46` 高于 `P10(pos)=0.4286` | demo 上有边缘正样本被拦 | Gate 3a 的 6 条实测打印 Top-1，落在 `0.42~0.46` 就走 config 覆盖并写文档 |
| `_prompt_body` 泄漏到 artifact | Step 1.3 | 建 `source_map` 前 `pop` |
| `timing.py` 只改 1 处 | Step 4.5 | 三处同改，否则 `_normalized_stage_timings` 会重新截回 int |
| 浏览器用例仍可能崩溃 | Step 4.6 | 默认 skip；需要时 `--run-browser` 单独跑 |

## 4. 执行清单（可勾选）

- [ ] Step 0 快照提交 + renorm 提交 + 基线日志
- [ ] Step 1 图检索 1 席 / select_render 拆分 / rag_bridge 统一编号 / FTS 章节 / 引用正则 / repro 脚本 → Gate 1
- [ ] Step 2 关键词重写 + `build_fts_or_query` + 补足回退 + 2 席保底 + 闸门放宽 → Gate 2
- [ ] Step 3a 阈值 0.46（带 config 覆盖）+ recompute + seed 双模式 + 评测脚本去偏 → Gate 3a
- [ ] Step 4 章节去后缀 / 分块摘要 / 单段硬切 / 两个契约测试 / 计时精度 8 处 / 浏览器隔离 → Gate 4
- [ ] Step 3b seed + benchmark + calibration + judge 重跑 → Gate 3b
- [ ] Step 5 接线 6 处 + 前端 3 处 + 文档 D1–D8 → Gate 5

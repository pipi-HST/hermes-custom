---
status: historical
owner: knowledge-service
last_verified: 2026-11-17
verified_against: snapshot; kept for context, not a current runbook
---

# RAG 检索消融评测（V3.0）

> 2026-11-16 修复 G0 dispatcher，并把 benchmark 的 G3 卡片检索改为传入 query embedding（此前只走字面匹配）。本轮 28 条 query（18 正 + 10 负）：G3 Recall@5 1.0、MRR@5 0.769，负样本 9/10（q24 漏过 `cap_distractor_02`）。引用必须指向本次 `docs/benchmark-rag-v3-dashscope.json`。

- Embedding engine: `dashscope-text-embedding-v4`
- 生效阈值 MIN_VECTOR_SCORE = `0.46`（与 bridge/rag_bridge.py 一致）
- 语料：20 篇资料 / 86 chunk / 12 张卡片 = 98 个检索单元
- Query：28 条（18 正样本 + 10 负样本）
- G3 单次 RAG Prompt 平均字符数：579（上限 14000）

| 组 | Recall@3 | Recall@5 | MRR@5 | 负样本拦截 | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| G0_title_summary_substring | 0.389 | 0.389 | 0.389 | 10/10 | 0.06 | 35.03 |
| G1_vector_only | 0.833 | 0.833 | 0.806 | 9/10 | 1.22 | 19.48 |
| G2_vector_plus_fts | 0.833 | 0.833 | 0.806 | 9/10 | 1.7 | 2.63 |
| G3_full_v3 | 0.833 | 1.0 | 0.769 | 9/10 | 2.42 | 8.8 |

口径说明：
- 正样本命中按 capture/card 级判定（随机基线约 1/32）；负样本要求零召回。
- 计时前已预热 embedding 缓存，四组都只测检索层，不含 LLM 合成。
- 负样本 10 条：G0 拦 10/10，G1–G3 拦 9/10；q24 漏过 `cap_distractor_02`，不能写成 100%。
- 逐条命中见同目录 JSON 的 `per_query_details`。

已知限制（必须写进面试口径）：
- 语义 embedding 引擎实测：G3 Recall@5 1.000、MRR@5 0.769、负样本 9/10。Recall 提升来自卡片语义召回，MRR 略低于 G1/G2（0.806）；结论只对本语料成立，换语料必须重跑。
- 阈值按 DashScope v4 标定（docs/calibration-dashscope.json）；separation<0 说明正负分布重叠，单阈值只是首轮粗筛。
- G0 是真实的旧版字面基线（标题/摘要子串，不用 FTS）。若它在本语料上与 V3 打平，说明这套 query 的收益已被字面匹配吃掉，不能宣称语义检索带来提升。

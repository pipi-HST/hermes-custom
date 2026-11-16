# RAG 检索消融评测（V3.0，离线 hash post-fix）

> 这是 G0 dispatcher 修复后的离线回归口径，数据源为 `docs/benchmark-rag-v3-offline-postfix.json`。它只衡量字面重合，**不代表语义能力**；对外引用时必须同时说明离线引擎和 20 条 query 的语料限制。真实 DashScope 重跑已于 2026-11-16 完成，见 `docs/BENCHMARK-RAG-V3-dashscope.md` / `docs/benchmark-rag-v3-dashscope.json`（G0 0.389 vs G1–G3 0.833）。

- Embedding engine: `char-ngram-blake2b-hash-1024`
- 生效阈值 MIN_VECTOR_SCORE = `0.46`（与 `bridge/rag_bridge.py` 一致）
- 语料：20 篇资料 / 86 chunk / 12 张卡片 = 98 个检索单元
- Query：20 条（18 正样本 + 2 负样本）
- G3 单次 RAG Prompt 平均字符数：222（上限 14000；样本相关，不是固定指标）

| 组 | Recall@3 | Recall@5 | MRR@5 | 负样本拦截 | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| G0_title_summary_substring | 0.389 | 0.389 | 0.389 | 2/2 | 0.06 | 709.65 |
| G1_vector_only | 0.389 | 0.389 | 0.389 | 2/2 | 1.63 | 4.57 |
| G2_vector_plus_fts | 0.389 | 0.389 | 0.389 | 2/2 | 2.35 | 4.93 |
| G3_full_v3 | 0.389 | 0.389 | 0.361 | 2/2 | 2.02 | 4.67 |

口径说明：

- 正样本命中按 capture/card 级判定（随机基线约 1/32）；负样本要求零召回。
- 计时前已预热 embedding 缓存，四组都只测检索层，不含 LLM 合成。
- 负样本只有 2 条，拦截率只能写成 2/2，不能写成百分比。
- 逐条命中见同目录 JSON 的 `per_query_details`。

已知限制（必须写进面试口径）：

- 离线 hash 引擎只衡量字面重合：G3 Recall@5 0.389、MRR@5 0.361。真实语义指标看 `docs/BENCHMARK-RAG-V3-dashscope.md`，**阈值不可跨引擎迁移**。
- G0 是修复 dispatcher 后的真实旧版字面基线（标题/摘要子串，不用 FTS）。在本离线引擎下四组 Recall@5 全部 0.389，说明这套 query 的字面收益已被字面匹配吃掉；这不能否定真实语义引擎的结果（见 DashScope 重跑），也不能写“G0 < G1 < G2 < G3”。
- 阈值按 DashScope v4 标定（`docs/calibration-dashscope.json`）；separation<0 说明正负分布重叠，单阈值只是首轮粗筛。`0.46` 是当前代码值，`0.20` 是历史未标定值。

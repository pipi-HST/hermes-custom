# RAG 检索消融评测（V3.0，离线 hash post-fix）

> 这是 G0 dispatcher 修复后的离线回归口径，数据源为 `docs/benchmark-rag-v3-offline-postfix.json`。它只衡量字面重合，**不代表语义能力**；本轮负样本 10 条（10/10 拦截），正样本 18 条。真实语义结果见 `docs/BENCHMARK-RAG-V3-dashscope.md`。

- Embedding engine: `char-ngram-blake2b-hash-1024`
- 生效阈值 MIN_VECTOR_SCORE = `0.46`（与 bridge/rag_bridge.py 一致）
- 语料：20 篇资料 / 86 chunk / 12 张卡片 = 98 个检索单元
- Query：28 条（18 正样本 + 10 负样本）
- G3 单次 RAG Prompt 平均字符数：222（上限 14000）

| 组 | Recall@3 | Recall@5 | MRR@5 | 负样本拦截 | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| G0_title_summary_substring | 0.389 | 0.389 | 0.389 | 10/10 | 0.07 | 516.07 |
| G1_vector_only | 0.389 | 0.389 | 0.389 | 10/10 | 1.07 | 2.57 |
| G2_vector_plus_fts | 0.389 | 0.389 | 0.389 | 10/10 | 0.83 | 1.76 |
| G3_full_v3 | 0.389 | 0.389 | 0.361 | 10/10 | 1.49 | 7.1 |

口径说明：
- 正样本命中按 capture/card 级判定（随机基线约 1/32）；负样本要求零召回。
- 计时前已预热 embedding 缓存，四组都只测检索层，不含 LLM 合成。
- 负样本 10 条，本轮四组均为 10/10；不能写成百分比或外推到其他语料。
- 逐条命中见同目录 JSON 的 `per_query_details`。

已知限制（必须写进面试口径）：
- 离线 hash 引擎只衡量字面重合：G3 Recall@5 0.389、MRR@5 0.361。真实语义指标需配置 embedding key 后重跑，**阈值不可跨引擎迁移**。
- 阈值按 DashScope v4 标定（docs/calibration-dashscope.json）；separation<0 说明正负分布重叠，单阈值只是首轮粗筛。
- G0 是真实的旧版字面基线（标题/摘要子串，不用 FTS）。若它在本语料上与 V3 打平，说明这套 query 的收益已被字面匹配吃掉，不能宣称语义检索带来提升。

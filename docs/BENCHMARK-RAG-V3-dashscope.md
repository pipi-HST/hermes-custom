# RAG 检索消融评测（V3.0）

> 2026-11-16 使用修复后的 G0 dispatcher 重跑。本报告替换此前因 G0 误走 G3 而作废的版本；引用时必须带日期并指向本次 `docs/benchmark-rag-v3-dashscope.json`。旧版报告里的四组 0.833 仍然作废（旧 G0 实际跑了 G3）。

- Embedding engine: `dashscope-text-embedding-v4`
- 生效阈值 MIN_VECTOR_SCORE = `0.46`（与 bridge/rag_bridge.py 一致）
- 语料：20 篇资料 / 86 chunk / 12 张卡片 = 98 个检索单元
- Query：20 条（18 正样本 + 2 负样本）
- G3 单次 RAG Prompt 平均字符数：671（上限 14000）

| 组 | Recall@3 | Recall@5 | MRR@5 | 负样本拦截 | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| G0_title_summary_substring | 0.389 | 0.389 | 0.389 | 2/2 | 0.06 | 38.26 |
| G1_vector_only | 0.833 | 0.833 | 0.806 | 2/2 | 0.85 | 1.69 |
| G2_vector_plus_fts | 0.833 | 0.833 | 0.806 | 2/2 | 1.1 | 2.88 |
| G3_full_v3 | 0.833 | 0.833 | 0.778 | 2/2 | 1.74 | 4.3 |

口径说明：
- 正样本命中按 capture/card 级判定（随机基线约 1/32）；负样本要求零召回。
- 计时前已预热 embedding 缓存，四组都只测检索层，不含 LLM 合成。
- 负样本只有 2 条，拦截率只能写成 2/2，不能写成百分比。
- 逐条命中见同目录 JSON 的 `per_query_details`。

已知限制（必须写进面试口径）：
- 语义 embedding 引擎实测：G3 Recall@5 0.833、MRR@5 0.778、负样本 2 条全拦。结论只对本语料成立，换语料必须重跑。
- 卡片专属 query（q14/q15/q16）在 G3 仍未召回目标卡片；卡片向量接入尚未转化为 card-only recall，需继续排查 `CARD_MIN_VECTOR_SCORE` 与卡片索引。
- 阈值按 DashScope v4 标定（docs/calibration-dashscope.json）；separation<0 说明正负分布重叠，单阈值只是首轮粗筛。
- G0 是真实的旧版字面基线（标题/摘要子串，不用 FTS）。本次 G0 为 0.389、V3 为 0.833，说明这套 query 上语义召回优于字面基线；若换语料后 G0 与 V3 打平，则不能宣称语义收益。

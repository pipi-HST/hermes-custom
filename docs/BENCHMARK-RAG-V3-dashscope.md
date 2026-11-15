# RAG 检索消融评测（V3.0）

- Embedding engine: `dashscope-text-embedding-v4`
- 生效阈值 MIN_VECTOR_SCORE = `0.46`
- 语料：20 篇资料 / 86 chunk / 12 张卡片 = 98 个检索单元
- Query：20 条（18 正样本 + 2 负样本）
- G3 单次 RAG Prompt 平均字符数：754（上限 14000）

| 组 | Recall@3 | Recall@5 | MRR@5 | 负样本拦截 | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| G0_legacy_identity_keyword | 0.5 | 0.5 | 0.5 | 1.0 | 0.11 | 38.13 |
| G1_vector_only | 0.833 | 0.833 | 0.806 | 1.0 | 520.93 | 767.47 |
| G2_vector_plus_fts | 0.833 | 0.833 | 0.806 | 1.0 | 1.58 | 2.08 |
| G3_full_v3 | 0.833 | 0.833 | 0.778 | 1.0 | 2.15 | 4.96 |

口径说明：
- 正样本命中按 capture/card 级判定（随机基线约 1/32）；负样本要求零召回。
- 离线 hash 引擎只衡量字面重合，真实语义指标需配置 embedding key 后重跑。
- 延迟只含检索层，不含 LLM 合成与 embedding API 往返。

已知限制（必须写进面试口径）：
- 语义 embedding 引擎实测，阈值与召回可在生产复用。
- 注意：负样本拦截率随引擎与阈值剧烈变化，0.20 是历史硬编码值，不是标定值。

# RAG 检索消融评测（V3.0，DashScope 历史报告）

> **⚠️ 历史报告，全部数字作废，等待真实重跑。** 本报告生成时 `G0` 的 dispatcher 条件与 `GROUPS` 名称不一致，G0 实际走了 G3 分支，因此四组指标打平。这些数字不能用于证明语义检索收益，也不能引用为“0.5 → 0.833”或“G0 < G1 < G2 < G3”。当前可复现的离线证据见 `docs/BENCHMARK-RAG-V3-offline-postfix.md`；DashScope 真实重跑因当前环境没有可用 API key，状态为 **待重跑**。代码已修复（`scripts/eval_rag_benchmark.py`），配好 key 后必须重跑并替换本文件。

- 数据源：`docs/benchmark-rag-v3-dashscope.json`（历史快照，不可引用）
- Embedding engine（本次运行配置）: `dashscope-text-embedding-v4`
- 生效阈值 MIN_VECTOR_SCORE = `0.46`（与 `bridge/rag_bridge.py` 一致；`0.20` 是历史未标定值）
- 语料：20 篇资料 / 86 chunk / 12 张卡片 = 98 个检索单元
- Query：20 条（18 正样本 + 2 负样本）
- G3 单次 RAG Prompt 平均字符数：671（上限 14000；样本相关，不是固定指标）

## 历史数据（G0 误走 G3，全部不可引用）

| 组 | Recall@3 | Recall@5 | MRR@5 | 负样本拦截 | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| G0_title_summary_substring | 0.833 | 0.833 | 0.778 | 2/2 | 1.2 | 34.86 |
| G1_vector_only | 0.833 | 0.833 | 0.806 | 2/2 | 0.77 | 1.9 |
| G2_vector_plus_fts | 0.833 | 0.833 | 0.806 | 2/2 | 0.91 | 2.15 |
| G3_full_v3 | 0.833 | 0.833 | 0.778 | 2/2 | 1.18 | 2.41 |

口径说明：

- 上表只用于记录“调度 bug 会让四组看起来一样”这个教训；G0 实际跑的是 G3，整张表不能当作四组独立实现的对照。
- 正样本命中按 capture/card 级判定（随机基线约 1/32）；负样本只有 2 条，拦截率只能写成 2/2，不能写成百分比。
- 计时只含检索层，不含 LLM 合成；逐条命中见同目录 JSON 的 `per_query_details`。

## 为什么不能引用

- G0 实际执行了 G3 逻辑，所以“G0 与 V3 打平”是 bug 产物，不是旧版基线真实成绩。
- “旧版 0.5 → 新版 0.833”里的 0.5 来自更早的、不完整的 G0 实现；0.833 来自本次 G0 误走 G3 的运行。两端都不可比，这条叙事作废。
- “G0 < G1 < G2 < G3”在本报告中从未成立，也不能作为产品结论。

## 仍然有效的独立证据

阈值标定（`docs/calibration-dashscope.json`）直接统计 embedding 分数，不经过 G0 dispatcher：

- 30 组正样本 P10 = 0.4286，168 组负样本 P95 = 0.4887，`separation = -0.0601`，正负分布重叠。
- 建议工作点与当前代码值均为 `0.46`；`0.20` 是历史未标定值。
- 单阈值只能做首轮粗筛，生产上仍需 rerank 或分数融合。

## 重跑清单

1. 配置可用的 DashScope `text-embedding-v4` key。
2. 运行 `python scripts/eval_rag_benchmark.py`，确认 G0/G1/G2/G3 的 dispatcher 分支正确。
3. 用新输出替换 `docs/benchmark-rag-v3-dashscope.json` 与本文件。
4. 在新结果出来前，对外只引用 `docs/BENCHMARK-RAG-V3-offline-postfix.md` 的离线口径，并明确“不代表语义能力”。

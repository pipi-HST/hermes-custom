> ⚠️ **本文件是 G0 dispatcher 修复前的离线 hash 冒烟记录，数字已过期，不要对外引用。**
> 当前离线口径请看 `docs/BENCHMARK-RAG-V3-offline-postfix.md` / `docs/benchmark-rag-v3-offline-postfix.json`。
> DashScope 真实重跑已于 2026-11-16 完成，语义结果看 `docs/BENCHMARK-RAG-V3-dashscope.md`（G0 0.389 vs G1–G3 0.833）；本文件的 0.5/0.667 仍是历史记录，不要引用。
> hash 引擎只衡量字面重合，其 Recall 与负样本拦截率都不可迁移到真实语义库。

# RAG 检索消融评测（V3.0，历史记录）

- 数据源：`docs/benchmark-rag-v3.json`（历史快照，不可引用）
- Embedding engine: `char-ngram-blake2b-hash-1024`（离线字符 n-gram，**不代表语义能力**）
- 生效阈值 MIN_VECTOR_SCORE = `0.2`（历史未标定值；当前代码为 `0.46`）
- 语料：20 篇资料 / 86 chunk / 12 张卡片 = 98 个检索单元
- Query：20 条（18 正样本 + 2 负样本）
- G3 单次 RAG Prompt 平均字符数：362（上限 14000；样本相关，不是固定指标）

以下数字为 G0 修复前记录，不能与 post-fix 数据混用（离线 hash post-fix 四组均为 0.389；真实 DashScope post-fix 为 G0 0.389 vs G1–G3 0.833）。

| 组 | Recall@3 | Recall@5 | MRR@5 | 负样本拦截 | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| G0_legacy_identity_keyword | 0.5 | 0.5 | 0.5 | 1.0 | 0.1 | 436.16 |
| G1_vector_only | 0.667 | 0.667 | 0.667 | 1.0 | 1.0 | 1.64 |
| G2_vector_plus_fts | 0.667 | 0.667 | 0.667 | 1.0 | 1.38 | 3.8 |
| G3_full_v3 | 0.667 | 0.667 | 0.611 | 1.0 | 1.65 | 3.54 |

口径说明：
- 正样本命中按 capture/card 级判定（随机基线约 1/32）；负样本要求零召回。
- 离线 hash 引擎只衡量字面重合，真实语义指标见 `docs/BENCHMARK-RAG-V3-dashscope.md` 的 2026-11-16 重跑。
- 延迟只含检索层，不含 LLM 合成与 embedding API 往返。

已知限制（必须写进面试口径）：
- 历史记录：离线 hash 引擎在阈值 0.20 下负样本拦截 100%、Recall@5 0.667；换成校准值 0.09 时 Recall@5 升到 0.722 但负样本全部漏过——这是清晰的 precision/recall 权衡，也说明**阈值不可跨引擎迁移**。当前代码阈值是 0.46，不是 0.20。
- 因此 G1~G3 的 Recall 衡量的是字面重合下的检索排序，不是语义能力。

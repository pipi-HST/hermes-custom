"""Seed the Golden Demo dataset: AI 产品经理求职与认知内化知识库.

Idempotently writes:

- six rich captures into SQLite (``captures``, ``capture_metadata``,
  ``capture_embeddings``) and the Markdown vault (``captures/`` and
  ``_archive/raw/``);
- nine accepted knowledge cards into ``garden_cards``;
- topic groupings into ``pending_questions`` plus the rebuilt
  ``knowledge_edges`` graph;
- the learner-centric folder taxonomy into
  ``vault/.hermes/knowledge_folders.json``.

Embeddings are deterministic 1024-dim float32 vectors built from topic and
keyword seeds, so offline semantic search / graph edges work without any API
key.  Re-running the script replaces the demo rows instead of duplicating them.

Usage::

    python scripts/seed_interview_demo.py
    python scripts/seed_interview_demo.py --db data/captures.db --vault data/vault
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.capture_agent import load_config  # noqa: E402
from agents.garden_card_payload import normalize_garden_payload, project_legacy_fields  # noqa: E402
from agents.knowledge_graph import KnowledgeGraphService  # noqa: E402
from bridge.atomic_io import atomic_write_text  # noqa: E402
from bridge.folder_store import STORE_RELATIVE_PATH, write_folder_store  # noqa: E402
from db.schema import init_db  # noqa: E402

DIM = 1024

# Set by ``seed(use_real_embeddings=True)``.  When present every vector is a
# real DashScope embedding; when absent the deterministic topic vectors are
# used so the demo and the unit tests stay fully offline.
_REAL_EMBEDDER = None


def _vector_for(index_text: str, fallback: np.ndarray) -> np.ndarray:
    """Return the real embedding for ``index_text``, or the offline vector."""
    if _REAL_EMBEDDER is None:
        return fallback
    return _REAL_EMBEDDER(index_text)


DEMO_FOLDERS = ["Inbox", "AI 产品面试", "Agent 与大模型", "行业与产品拆解", "读书与随笔"]

CAPTURE_1_BODY = """## 💡 AI 核心导读

> 面试官问「怎么判断真需求」，真正想听的不是需求模板，而是你能否把一句模糊诉求拆成可验证、可度量、可放弃的假设。真需求的四个信号是：有明确触发场景、有可量化的现状损失、有用户已经在用的替代方案、有不做的机会成本。伪需求则往往由技术、KPI 或 Demo 驱动，听起来先进，却找不到愿意持续使用的用户。

## 目录纲要

- 一、面试官真正在考什么
- 二、真需求的四个信号
- 三、伪需求的三种伪装
- 四、面试回答模板：把伪需求翻转成可验证假设

## 一、面试官真正在考什么

很多候选人一上来就背「用户画像、痛点、场景、解决方案」四件套，面试官听完却没有任何记忆点。原因不是框架错了，而是框架被当成了答案。面试官真正想看的是：面对一个信息不完整、利益相关方互相拉扯的需求，你会先问什么问题、用什么证据做判断、在什么条件下敢于说「不做」。

真需求判断本质上是一个降低不确定性的过程。产品经理不是需求的搬运工，而是假设的管理者。你要能说清楚：现在相信什么、这个判断有多脆弱、下一步用多低成本去验证它。能把「我觉得用户需要」翻译成「如果这个判断成立，用户应该已经在用某种土办法解决，并且愿意为更好的方案付出时间或金钱」，才算真正入门。

因此，回答这类问题时不要急着给结论。先复述业务目标，再指出当前信息缺口，最后给出验证路径。面试官会顺着你的验证路径继续追问，这恰恰说明你的回答有可讨论的深度。

## 二、真需求的四个信号

第一个信号是明确触发场景。用户不是随时随地都需要一个 AI 助手，而是在某个具体时刻被某个具体事件卡住，例如面试前两小时要快速梳理项目经历。场景越具体，需求越可信。

第二个信号是可量化的现状损失。用户现在因为这个问题损失了多少时间、金钱、机会或情绪成本？如果损失可以被估算，需求就有被付费或持续使用的可能；如果只能说「体验更好」，那大概率是锦上添花。

第三个信号是已有的替代方案。真正痛的题，用户往往已经在用 Excel、微信群、收藏夹或人工助理凑合解决。替代方案越笨拙、越普遍，越说明需求真实存在。反过来，如果用户从来没有为这件事付出过任何行动，你就要警惕「口头需求」。

第四个信号是不做的机会成本。需求优先级不是由「想做多少功能」决定，而是由「不做会失去什么」决定。能讲清楚机会成本，才能解释为什么现在做、为什么先做这一块。

## 三、伪需求的三种伪装

第一种是技术驱动：因为团队刚接入了大模型，所以要给每个页面加一个对话框。判断方法是问「如果去掉 AI，这个功能还成立吗？」如果答案是否定的，说明用户要的是结果，不是 AI 本身，应该先验证结果价值。

第二种是 KPI 驱动：为了提升某个使用时长或调用次数，设计出用户并不需要的提醒、生成和推荐。这类需求在数据上可能短期好看，但会透支信任。面试中要敢于指出指标与用户价值之间的偏差，并给出更健康的替代指标。

第三种是 Demo 驱动：为了让发布会或汇报好看，优先做演示效果强、真实使用弱的场景。Demo 不是原罪，但必须明确它是探索预算，不能伪装成长期产品路线。成熟的候选人会主动区分「验证性投入」和「规模化投入」。

## 四、面试回答模板：把伪需求翻转成可验证假设

可以这样组织回答：第一，复述需求背后的业务目标，确认双方讨论的是同一件事；第二，指出当前需求描述里缺失的关键证据，例如没有真实使用频次、没有替代方案、没有成本数据；第三，提出一个两周内能完成的最小验证，例如访谈五位高频用户、做一次人工代跑、上线一个不接模型的假按钮观察点击；第四，预先定义放弃条件，说明什么结果出现时就停止投入。

这个模板的杀伤力在于，它同时展示了产品判断、实验设计和资源克制。面试官会看到你不是「会背需求分析」的人，而是能在真实组织中帮团队少走弯路的人。最后补一句：真需求不是被说服出来的，而是被证据一点点逼出来的。"""

CAPTURE_1_RAW = """原始访谈笔记（2026-10-28）

- 候选人 A：面试被问「怎么判断伪需求」，回答了用户画像、痛点、场景、方案，面试官追问「如果用户说想要但不用怎么办」，答不上来。
- 候选人 B：先问业务目标，再说现在缺少使用频次和替代方案证据，提出两周内做人工代跑验证，面试官明显有兴趣。
- 面试官反馈：不要背框架。要看候选人能不能把模糊诉求拆成假设，能不能定义放弃条件。
- 我补充：真需求的信号是场景、损失、替代方案、机会成本；伪需求常见伪装是技术驱动、KPI 驱动、Demo 驱动。
- 待整理：把回答模板写成四步，补一个面试现场可复述的例子。"""

CAPTURE_2_BODY = """## 💡 AI 核心导读

> 多级 Agent 最大的风险不是模型不够聪明，而是信息在 Agent 之间被逐层转述后失真。每一次「总结后再交给下一个 Agent」都会丢掉限定条件、数字、反例和不确定性，最终让下游 Agent 基于一份看似合理、实则残缺的二手材料做决策。破局的关键不是换更强模型，而是让原始证据可旁路访问，并把交接从自然语言摘要改成结构化契约。

## 目录纲要

- 一、什么是大模型传话筒效应
- 二、三类高发链路
- 三、为什么它比幻觉更危险
- 四、破局：共享证据层、结构化交接与原始材料旁路

## 一、什么是大模型传话筒效应

想象一个十人传话游戏：每个人都会不自觉地删掉自己认为不重要的细节，再补上一句更顺耳的概括。多级 Agent 架构天然就是这个游戏。检索 Agent 把十篇材料压成一段摘要，分析 Agent 再压成三条结论，写作 Agent 最后只看到三条结论，却要输出一份看起来有依据的报告。

问题在于，每一层压缩都不是中性的。模型会优先保留符合叙事主线的信息，丢掉限定条件、失败案例和相互矛盾的证据。更隐蔽的是，模型会用流畅的语言填补空缺，让下游很难判断哪一句来自原文、哪一句是合理推测。等最终答案出错时，链路里没有任何一个节点觉得自己撒了谎。

所以传话筒效应的本质是「证据所有权」丢失：下游 Agent 不再拥有原始材料，只能信任上游的二手转述。它表现得不像故障，更像一种组织化的信息衰减。

## 二、三类高发链路

第一类是检索到写作的长链路。检索 Agent 返回摘要而不是原文片段，写作 Agent 再基于摘要发挥，最终引用看似完整，实际早已偏离来源。第二类是规划到执行的工具链路。规划 Agent 把用户需求改写成任务描述，执行 Agent 看不到原始约束，容易把「只看最近三个月」理解成「最近三篇」。第三类是评审到修订的反思链路。评审 Agent 用概括性意见替代具体问题，修订 Agent 只能猜哪里需要改，越改越偏。

这三类链路的共同点是：交接发生在自然语言层，且下游没有回查原始材料的权限。链路越长，失真越严重；任务越依赖数字、边界和反例，损失越致命。

## 三、为什么它比幻觉更危险

单点幻觉通常可以被引用和事实核查发现，而传话筒效应会让整条链路共同制造一个「自洽的错误」。每个 Agent 的输出单看都合理，甚至语气专业、结构完整，但没有任何一段能追溯到原文。用户看到的是高度确定的结论，实际上证据已经被稀释成印象。

更麻烦的是，它难以归因。你很难判断错误来自检索遗漏、摘要压缩、规划误解还是写作发挥。团队往往会误以为是模型能力问题，于是不断升级模型、增加反思轮次，结果延迟更高、成本更大，失真却没有减少。

对产品经理来说，这意味着不能只用「最终答案看起来对不对」来验收多级 Agent。必须检查证据链：结论有没有来源、来源是不是原文、原文有没有被完整传递。

## 四、破局：共享证据层、结构化交接与原始材料旁路

第一，建立共享证据层。所有 Agent 读写同一份带 ID 的原始材料，交接时传引用而不是转述。下游需要细节时可以直接回查，而不是要求上游再总结一遍。

第二，把交接改成结构化契约。明确字段：任务目标、必须保留的约束、可用证据 ID、允许的推断范围、失败时如何回退。结构化不是为了限制模型，而是为了把「不能丢的东西」变成机器可检查的字段。

第三，保留原始材料旁路。无论中间总结多漂亮，最终写作和评审都必须能访问原文。可以用「摘要 + 原文引用」双通道，让模型先读摘要定位，再读原文取证。

最后，把「证据保真率」纳入评测：随机抽取结论，检查其来源片段是否真的支持该结论。只有把保真当成一等指标，多级 Agent 才不是一场昂贵的传话游戏。"""

CAPTURE_2_RAW = """架构复盘会速记（2026-09-16）

- 现象：research 链路输出看起来很完整，但抽查三条结论，两条找不到原文依据。
- 拆链路：retriever 先 summarise 成 800 字，planner 再压成 5 条任务，writer 只拿到任务和摘要。
- 关键 bug：planner 把「只看最近三个月」改写成「最近三篇」，后面全部跑偏。
- 讨论：不是模型笨，是证据在传递中被洗掉了。每一层都在做有损压缩。
- 结论：共享证据层 + 结构化交接 + 原文旁路；评测加证据保真率，不只看答案像不像。
- 待补：三类高发链路的具体案例，以及和幻觉的区别。"""

CAPTURE_3_BODY = """## 💡 AI 核心导读

> 重型 Agent Research 管线从 130 秒降到 20 秒，靠的不是换更快的模型，而是砍掉低收益的反思环、把串行步骤并行化、为高频子任务建立缓存，并用早停策略替代「必须跑满所有 Agent」的执念。延迟优化的真正 ROI 不在省下的 Token，而在用户愿不愿意把第二次问题交给系统。

## 目录纲要

- 一、问题：反思环越多，用户越早离开
- 二、延迟拆解：130 秒花在哪里
- 三、重构方案：并行化、分层缓存与早停
- 四、ROI 复盘：省下的不是 Token，而是信任

## 一、问题：反思环越多，用户越早离开

最初的 Research 管线有七个阶段：意图理解、查询改写、多路检索、证据筛选、初步写作、自我反思、二次修订。设计时的假设是「多一轮反思，质量一定更好」。上线后却发现，用户在前 15 秒没有看到任何进展就会开始怀疑系统卡死，30 秒后大量用户直接刷新或放弃。

更尴尬的是，离线评测显示反思环只把人工评分从 3.9 提升到 4.0，却让 P95 延迟从 45 秒涨到 130 秒。也就是说，我们用了接近三倍的时间，换来一个用户几乎感知不到的质量提升。这不是优化，而是用工程复杂度购买心理安慰。

延迟问题因此不只是性能问题，而是产品留存问题。用户不会因为我们内部有七个 Agent 就更有耐心，他们只会判断「这个工具是否值得再试一次」。

## 二、延迟拆解：130 秒花在哪里

把链路按阶段打点后，时间分布非常集中：多路检索 38 秒，证据筛选 22 秒，初步写作 25 秒，自我反思 28 秒，二次修订 17 秒。真正与用户问题强相关的只有检索、筛选和初稿，反思与修订占了 45 秒，却经常只是把已经正确的段落改写一遍。

进一步看，检索阶段存在明显的串行等待：五个来源依次请求，最慢的源决定整体耗时；证据筛选又把所有材料重新塞给大模型做一次全量判断；写作阶段则因为上下文过长而变慢。换句话说，延迟不是某一个模型慢，而是链路结构把可以并行的东西串起来了，把可以复用的东西重复算了。

这也解释了为什么单纯换更快的模型收益有限。模型只占一部分时间，真正的浪费在编排方式。

## 三、重构方案：并行化、分层缓存与早停

第一步是并行化。把互不依赖的来源检索并发执行，设置统一超时和降级策略；证据筛选改成先规则去重、再小模型粗筛、最后大模型精排，避免每次都用最贵的方式处理全量材料。

第二步是分层缓存。对高频查询改写、网页摘要、实体归一化结果建立带 TTL 的缓存；同一会话内复用已检索证据，用户追问时不再从头跑一遍。缓存的关键不是命中率越高越好，而是只缓存稳定的中间产物，避免把过期结论当成事实。

第三步是早停。为每个阶段定义「足够好」的阈值：证据覆盖达到要求就停止扩充来源；初稿通过结构检查就跳过二次修订；置信度高的问题直接走轻量链路。把「是否继续反思」从固定流程改成基于收益预估的决策。

重构后，P50 从 62 秒降到 11 秒，P95 从 130 秒降到 20 秒，人工评分保持在 4.0 左右。少数复杂问题会自动进入深链路，但不再拖累大多数普通请求。

## 四、ROI 复盘：省下的不是 Token，而是信任

成本账面上，Token 消耗下降了约 46%，但更大的收益来自行为变化：重试率下降、二次提问率上升、用户愿意把更复杂的问题交给系统。延迟每降低 10 秒，用户完成第二次交互的概率就明显提高，这才是产品真正的复利。

复盘时我们犯过一个错误：一开始只盯着平均延迟，忽略了 P95。平均值好看但尾部很长的产品，用户感知依然是「有时很慢」。后来把 P95 和超时率作为一级指标，才逼着团队处理慢来源和异常重试。

如果重来一次，我会更早问三个问题：哪些步骤对最终质量没有可测贡献？哪些步骤可以并行？哪些步骤可以在证据不足时安全跳过？延迟优化的终点不是最快，而是让用户觉得「值得等」，并且大多数时候不需要等。"""

CAPTURE_3_RAW = """Research 管线性能复盘（2026-08-30）

- 旧链路 P95 130s：检索 38s、筛选 22s、初稿 25s、反思 28s、修订 17s。
- 离线评分：反思环只把 3.9 提到 4.0，用户却大量在 30s 内放弃。
- 改造：五路检索并发；规则去重 + 小模型粗筛 + 大模型精排；查询改写/网页摘要加 TTL 缓存；按证据覆盖率和置信度早停。
- 结果：P50 62s -> 11s，P95 130s -> 20s，Token -46%，重试率下降。
- 教训：不要只看均值，P95 和超时率才是用户感知；不要为了流程完整而跑满所有 Agent。"""

CAPTURE_4_BODY = """## 💡 AI 核心导读

> Notion AI、NotebookLM、腾讯 IMA 与 Readwise 看似都在做「个人知识库」，其实定义了四种不同的知识：Notion AI 把知识当成工作台里的可检索内容，NotebookLM 把知识当成有来源约束的理解材料，IMA 把知识当成组织内的协作资产，Readwise 把知识当成需要复习和内化的认知单元。Hermes 的机会不在再做一次收藏夹，而在打通「资料 → 卡片 → 专题」的内化闭环。

## 目录纲要

- 一、竞品地图：四种不同的「知识」定义
- 二、Notion AI 与腾讯 IMA：工作台内的检索增强
- 三、NotebookLM：来源约束式理解
- 四、Readwise 与 Hermes 的机会：从收藏到内化

## 一、竞品地图：四种不同的「知识」定义

分析 PKM 产品时，最容易犯的错误是比较功能清单：谁支持多少格式、谁有移动端、谁能导出。但真正决定产品形态的是它对「知识」的假设。Notion AI 假设知识存在于工作台，价值在于随时调用；NotebookLM 假设知识来自一组可信来源，价值在于围绕来源问答；IMA 假设知识是组织协作资产，价值在于共享与权限；Readwise 假设知识需要被反复触达，价值在于复习与连接。

这四个假设对应四种不同的用户时刻：工作时查、研究时读、协作时找、学习时记。产品如果同时想服务四个时刻，往往会变成功能很多但没有一个时刻不可替代。对新入场者来说，先选一个时刻做深，比做全更重要。

Hermes 的定位更接近第四类，但增加了 AI 主动整理与卡片沉淀。这意味着竞争点不是「能不能存」，而是「存完之后有没有让用户变聪明」。

## 二、Notion AI 与腾讯 IMA：工作台内的检索增强

Notion AI 的优势是上下文天然完整。文档、数据库、任务和讨论都在同一个工作台里，AI 可以直接基于权限内的内容回答问题、生成初稿、补全表格。它不需要用户先「导入知识库」，因为知识本来就在工作流里产生。这种嵌入式体验的迁移成本很低，但缺点是知识边界等于工作台边界，个人阅读和外部材料很难形成长期积累。

腾讯 IMA 更像组织知识助手：强调公众号、文档、网页等内容的收藏与共享，再通过搜索和问答把组织沉淀变成可调用资产。它的优势是中文内容生态和协作场景，挑战则在于个人认知内化：收藏一篇好文不等于理解它，组织共享也不等于个人形成判断。

两者共同说明了一件事：检索增强只能解决「找得到」，解决不了「想明白」。如果产品只停留在问答层，用户得到的是一次性答案，而不是可复用的认知结构。

## 三、NotebookLM：来源约束式理解

NotebookLM 最值得学习的是「来源约束」。用户先上传一组文档，系统只基于这些文档回答，并给出引用。这个限制看似降低了能力，实际上极大提升了信任：用户知道答案的边界在哪里，也知道该去核对哪一段原文。

它把交互从「搜索」变成「围绕材料的对话」。用户可以要求生成摘要、提纲、学习指南，甚至把多篇材料合成为一份 briefing。对学习者来说，这比一个无边界的聊天机器人更接近真实的学习过程：先有材料，再有理解，再有输出。

但 NotebookLM 的弱点也明显：它擅长理解一组材料，却不擅长长期管理一个人的知识资产。项目结束、来源删除后，认知很难继续沉淀。它解决的是「这一组材料怎么读懂」，不是「我如何持续变强」。

## 四、Readwise 与 Hermes 的机会：从收藏到内化

Readwise 的核心不是收藏，而是「让高亮反复出现」。它把阅读时划出的句子同步到每日回顾、卡片和外部工具，通过间隔重复把别人的句子变成自己的记忆。这种设计承认了一个事实：知识不会因为被保存而属于你，只会因为被使用而属于你。

Hermes 可以在此基础上再向前一步：不仅复习原文高亮，还把多次阅读与讨论提炼成结构化卡片，再把卡片组织成专题。资料是证据，卡片是判断，专题是长期问题意识。三者打通后，用户获得的不是更大的收藏夹，而是一条可见的认知成长轨迹。

因此，面对竞品时不要问「我们少哪个功能」，而要问「用户在哪个时刻会因为我们而变聪明」。如果答案是「当他想把读过的内容变成自己的观点时」，那么产品就应该把提炼、复习和专题连接做深，而不是继续堆收藏入口。"""

CAPTURE_4_RAW = """PKM 竞品研究笔记（2026-09-05）

- Notion AI：知识在工作台里，优势是上下文完整、权限天然，弱点是外部阅读难沉淀。
- 腾讯 IMA：公众号/文档收藏 + 组织共享，中文生态强，但个人内化弱。
- NotebookLM：来源约束式问答，引用清楚，信任度高；但不做长期个人知识管理。
- Readwise：高亮 + 每日回顾 + 间隔重复，核心是让内容反复出现。
- 我的判断：竞争点不是收藏功能，而是「资料 -> 卡片 -> 专题」的内化闭环。
- 待写：四类产品对「知识」的不同假设，以及 Hermes 应该选哪个时刻。"""

CAPTURE_5_BODY = """## 💡 AI 核心导读

> MCP 的价值不是让 Agent 多接几个工具，而是把「工具如何被发现、描述、调用和授权」变成可复用的协议层。但协议标准化只解决了接口问题，解决不了工具选择、上下文膨胀和权限边界。真正成熟的 Agent 需要三层收敛：按任务暴露最小工具集、用结构化描述约束调用、用评测持续淘汰低价值工具。

## 目录纲要

- 一、MCP 解决的真正问题：工具接口标准化
- 二、MCP 不解决什么：选择、上下文与权限
- 三、工具收敛的三层策略
- 四、落地检查清单

## 一、MCP 解决的真正问题：工具接口标准化

在没有 MCP 之前，每接一个外部系统，团队都要为它写一套私有适配：参数格式、鉴权方式、错误码、分页逻辑各不相同。Agent 看到的是几十个风格不一致的函数，模型很容易在相似工具之间选错，工程团队则被迫维护大量胶水代码。

MCP 把这件事抽象成客户端与服务端：服务端声明自己能提供哪些资源、工具和提示模板，客户端负责发现、连接和调用。对 Agent 来说，工具终于有了统一的描述方式；对平台来说，新增能力不必修改核心代码。这种解耦是协议层真正的价值。

但要注意，标准化降低的是集成成本，不是理解成本。工具变多之后，模型需要在更大的候选集中做选择，上下文也会被工具描述迅速填满。协议让「接得上」变容易了，却没有让「选得对」变容易。

## 二、MCP 不解决什么：选择、上下文与权限

第一，工具选择。两个工具名字都叫 search，一个查网页、一个查内部文档，如果描述含糊，模型只能靠猜。第二，上下文治理。几十个工具的参数说明会占用大量 token，挤掉真正重要的任务信息。第三，权限边界。协议可以声明能力，但不能替产品决定某个用户在当前场景下是否应该拥有这个能力。

还有一个容易被忽略的问题：副作用。读操作失败可以重试，写操作失败可能已经产生真实影响。MCP 本身不提供事务语义，产品必须自己设计确认、幂等和回滚策略。

因此，接入 MCP 不等于拥有可靠的 Agent 工具层。协议是地基，产品决策才是房子。

## 三、工具收敛的三层策略

第一层是任务级最小工具集。不要把所有工具一次性暴露给模型，而是根据当前任务动态装载。例如伴读场景只暴露检索、引用和讨论工具，写入工具在用户明确确认后再出现。候选集越小，选择越准，上下文也越干净。

第二层是结构化描述。工具名要动词化，描述要写清楚适用场景、不适用场景、输入约束和返回结构。参数尽量使用枚举和必填校验，避免让模型自由发挥。对于高风险写操作，增加预览和确认步骤，而不是依赖模型自觉。

第三层是评测驱动的淘汰。记录每个工具的调用成功率、误用率、平均延迟和用户采纳率，定期下线低价值工具，合并语义重叠的工具。工具数量不是能力证明，能稳定完成任务的少量工具才是。

## 四、落地检查清单

接入一个新工具前，先回答：它解决的是哪类任务？没有它时用户怎么做？它和现有工具是否语义重叠？失败时是否可重试、是否幂等？返回结果是否足够结构化，能让下游直接使用？权限是否按用户和场景收敛？

上线后持续观察：模型是否在相似工具间摇摆？工具描述是否挤占关键上下文？写操作是否经过确认？错误是否被正确暴露给用户？如果答案不理想，优先改描述和编排，而不是继续加工具。

还要为工具层设置预算：单轮最多调用几次、单次返回最大长度、失败重试上限、超时后的降级动作。没有预算约束的工具层会在长任务中不断膨胀，最终拖垮延迟和成本；而清晰的预算会让模型更早收敛，也让用户对系统行为有稳定预期。

最后记住：MCP 让 Agent 的世界更开放，但产品经理的职责是让这个开放世界保持可理解、可控制、可评估。协议负责连接，收敛负责可靠。"""

CAPTURE_5_RAW = """MCP 学习与实践记录（2026-10-12）

- 过去每个外部系统一套私有适配，参数/鉴权/错误码全不一样，模型经常选错工具。
- MCP 统一了资源、工具、提示模板的发现与调用，集成成本明显下降。
- 但工具多了之后：描述占上下文、相似工具难选、写操作缺少事务和确认。
- 我们的收敛做法：按任务动态装载最小工具集；动词化命名 + 适用/不适用场景 + 枚举参数；记录调用成功率/误用率，定期合并或下线。
- 结论：协议解决连接，产品解决选择、上下文和权限。"""

CAPTURE_6_BODY = """## 💡 AI 核心导读

> 所有请求都调用最强模型，是一种隐蔽的产品事故：成本随使用量线性上升，延迟却未必换来对应体验。合理的 Model Routing 不是简单按价格分流，而是根据任务类型、上下文长度、置信度、用户价值和失败代价，把请求分配给不同层级的模型，并用灰度与评测持续校准。目标不是最便宜，而是在可接受质量下让单位成本可持续。

## 目录纲要

- 一、为什么全量使用最强模型是产品事故
- 二、路由信号：任务类型、置信度、上下文与用户价值
- 三、分层架构：小模型先行、大模型兜底、缓存复用
- 四、评估与灰度：不要只看向均成本

## 一、为什么全量使用最强模型是产品事故

最强模型通常意味着更高单价、更长首字延迟和更敏感的限流。把客服分类、标签生成、格式转换这类高并发低难度任务也交给它，会让成本随用户增长迅速失控，同时把延迟压力集中在一个最脆弱的依赖上。

更隐蔽的损失是产品决策被成本绑架。因为每次调用都很贵，团队不敢做主动整理、批量归纳和多次反思，最终产品能力被预算锁死。反过来，如果低价值请求能被便宜模型稳定处理，团队才有空间把强模型留给真正需要推理的环节。

所以路由不是省钱技巧，而是产品架构能力：它决定了哪些体验可以规模化，哪些只能作为高价值服务。

## 二、路由信号：任务类型、置信度、上下文与用户价值

第一类信号是任务类型。分类、抽取、改写、格式校验适合小模型；多步推理、跨文档综合、复杂规划适合大模型。任务类型最好在入口就明确，而不是让模型自己猜。

第二类信号是置信度。可以先用小模型生成答案和置信评分，低置信度再升级到大模型。置信度不能只看输出概率，还要结合规则校验、引用覆盖和结构完整性。

第三类信号是上下文长度。长上下文不仅更贵，也更容易让模型迷失。对于长材料，可以先检索、分段、摘要，再决定是否需要强模型综合，而不是把整篇材料直接塞给最贵的模型。

第四类信号是用户价值与失败代价。高价值用户的复杂问题值得用强模型；低风险的通知、草稿和内部辅助可以接受稍弱的结果。但涉及医疗、金融、法律等高风险场景，不能只按成本路由，必须保留人工确认和升级路径。

## 三、分层架构：小模型先行、大模型兜底、缓存复用

一个可落地的架构通常有三层：轻量层负责分类、抽取、改写和格式整理；标准层负责大多数问答和摘要；推理层负责复杂规划、跨文档综合和高风险决策。请求先走轻量层，根据置信度和规则决定是否升级。

缓存是第四层隐形能力。稳定的查询改写、实体归一化、网页摘要和常见问答可以缓存；但缓存必须带版本和 TTL，避免模型升级或事实变化后继续返回旧结论。缓存的目标是复用稳定中间产物，不是复用所有答案。

兜底策略同样重要。大模型超时或限流时，系统应该有可解释的降级路径：返回已有证据摘要、提示稍后重试，或转人工，而不是静默失败。用户能接受的不是完美，而是确定性。

## 四、评估与灰度：不要只看向均成本

评估路由策略时，至少同时看四个指标：质量（人工评分、引用准确率）、成本（每完成任务的综合成本）、延迟（P50/P95/首字时间）、稳定性（超时率、降级率）。只看单位 Token 价格会鼓励团队把任务推给便宜模型，却忽略返工和用户流失的代价。

灰度时按用户群或任务类型逐步放量，保留可回滚开关。先验证低风险任务，再扩展到核心问答；先观察一周，再决定是否扩大。对质量下降的样本做归因，判断是路由错误、提示词不适配，还是模型能力边界。

最终，路由策略应该像产品本身一样持续迭代：定期重跑评测集，更新阈值，淘汰表现变差的模型组合。合理的 Model Routing 不是一次性配置，而是一套让成本、质量和体验长期平衡的运营机制。"""

CAPTURE_6_RAW = """Model Routing 方案讨论（2026-10-20）

- 背景：所有请求都走最强模型，月度成本随 DAU 线性上涨，P95 首字延迟也高。
- 任务盘点：分类/标签/格式转换占 60%，普通问答占 30%，复杂推理占 10%。
- 路由信号：任务类型、置信度、上下文长度、用户价值、失败代价。
- 方案：轻量层先跑，低置信度升级；标准层覆盖大多数问答；推理层只留给复杂任务；缓存查询改写和网页摘要。
- 评估：质量、综合成本、P50/P95、超时/降级率一起看，按任务类型灰度。
- 结论：目标不是最便宜，而是单位成本可持续、体验可预期。"""

CAPTURES: list[dict[str, Any]] = [
    {
        "id": "cap_pm_interview_01",
        "slug": "ai-pm-interview-real-vs-fake-demand",
        "title": "AI 产品经理面试避坑指南：如何区分真需求与“为了 AI 而 AI”的伪需求",
        "folder": "AI 产品面试",
        "theme": "ai-product-interview",
        "date": "2026-11-02",
        "keywords": ["需求判断", "伪需求", "面试", "AI 产品"],
        "tags": ["ai-product", "interview", "需求判断", "product-thinking"],
        "summary": "用触发场景、现状损失、替代方案和机会成本四个信号判断真需求，并给出面试中把伪需求翻转成可验证假设的回答模板。",
        "sections": [
            ("一、面试官真正在考什么", "需求判断题考的是假设管理与证据意识，而不是背诵框架。"),
            ("二、真需求的四个信号", "场景、损失、替代方案、机会成本共同构成真需求证据。"),
            ("三、伪需求的三种伪装", "技术驱动、KPI 驱动和 Demo 驱动是最常见的伪需求来源。"),
            ("四、面试回答模板：把伪需求翻转成可验证假设", "用目标复述、证据缺口、最小验证和放弃条件组织回答。"),
        ],
        "body": CAPTURE_1_BODY,
        "raw": CAPTURE_1_RAW,
    },
    {
        "id": "cap_agent_arch_01",
        "slug": "multi-agent-telephone-game",
        "title": "多级 Agent 架构的隐形杀手：大模型传话筒效应（LLM Telephone Game）与破局之道",
        "folder": "Agent 与大模型",
        "theme": "agent-engineering",
        "date": "2026-10-26",
        "keywords": ["多级 Agent", "传话筒", "证据保真", "上下文"],
        "tags": ["ai-agent", "agent-architecture", "context-engineering", "evidence-fidelity"],
        "summary": "多级 Agent 在自然语言转述中逐层丢失限定条件与证据，导致自洽但不可追溯的错误；破局依赖共享证据层、结构化交接和原始材料旁路。",
        "sections": [
            ("一、什么是大模型传话筒效应", "每次自然语言摘要都会丢失限定条件、反例和不确定性。"),
            ("二、三类高发链路", "检索到写作、规划到执行、评审到修订是失真最严重的链路。"),
            ("三、为什么它比幻觉更危险", "整条链路共同制造自洽错误，难以归因且会被误判为模型能力问题。"),
            ("四、破局：共享证据层、结构化交接与原始材料旁路", "用证据 ID、结构化契约和原文回查取代层层转述。"),
        ],
        "body": CAPTURE_2_BODY,
        "raw": CAPTURE_2_RAW,
    },
    {
        "id": "cap_agent_arch_02",
        "slug": "agent-research-latency-roi",
        "title": "从 130 秒到 20 秒：重构重型 Agent Research 管线的延迟与 ROI 复盘",
        "folder": "Agent 与大模型",
        "theme": "agent-engineering",
        "date": "2026-10-18",
        "keywords": ["延迟优化", "Research 管线", "ROI", "早停"],
        "tags": ["ai-agent", "latency", "roi", "pipeline-optimization"],
        "summary": "通过并行化、分层缓存和早停策略，把 Research 管线 P95 从 130 秒降到 20 秒，并复盘延迟优化对留存与信任的真实 ROI。",
        "sections": [
            ("一、问题：反思环越多，用户越早离开", "固定反思环只带来微小质量提升，却显著拉高延迟和流失。"),
            ("二、延迟拆解：130 秒花在哪里", "串行检索、全量筛选和过长上下文是主要耗时来源。"),
            ("三、重构方案：并行化、分层缓存与早停", "并发来源、分级筛选、稳定中间产物缓存和收益驱动早停。"),
            ("四、ROI 复盘：省下的不是 Token，而是信任", "P95、超时率和二次交互率比平均延迟更能反映用户价值。"),
        ],
        "body": CAPTURE_3_BODY,
        "raw": CAPTURE_3_RAW,
    },
    {
        "id": "cap_pkm_teardown_01",
        "slug": "pkm-notion-notebooklm-ima-readwise",
        "title": "个人知识库（PKM）产品竞品拆解：Notion AI、NotebookLM、腾讯 IMA 与 Readwise",
        "folder": "行业与产品拆解",
        "theme": "pkm-product",
        "date": "2026-09-22",
        "keywords": ["PKM", "NotebookLM", "Readwise", "竞品拆解"],
        "tags": ["product-teardown", "pkm", "notebooklm", "readwise"],
        "summary": "四类 PKM 产品分别把知识定义为工作台内容、来源约束材料、组织协作资产和需复习的认知单元；Hermes 的机会在资料到卡片再到专题的内化闭环。",
        "sections": [
            ("一、竞品地图：四种不同的「知识」定义", "不同产品假设决定不同用户时刻，功能清单比较会掩盖定位差异。"),
            ("二、Notion AI 与腾讯 IMA：工作台内的检索增强", "嵌入式上下文和组织共享很强，但难以形成个人长期内化。"),
            ("三、NotebookLM：来源约束式理解", "来源约束提升信任，适合围绕一组材料深度理解。"),
            ("四、Readwise 与 Hermes 的机会：从收藏到内化", "复习让内容反复出现，卡片与专题让认知持续沉淀。"),
        ],
        "body": CAPTURE_4_BODY,
        "raw": CAPTURE_4_RAW,
    },
    {
        "id": "cap_agent_mcp_01",
        "slug": "mcp-tool-calling-convergence",
        "title": "一文搞懂 MCP（Model Context Protocol）与 Agent 工具调用收敛策略",
        "folder": "Agent 与大模型",
        "theme": "agent-engineering",
        "date": "2026-10-09",
        "keywords": ["MCP", "工具调用", "工具收敛", "权限"],
        "tags": ["ai-agent", "mcp", "tool-calling", "protocol"],
        "summary": "MCP 统一工具发现与调用契约，但不解决工具选择、上下文膨胀和权限边界；可靠 Agent 需要任务级最小工具集、结构化描述和评测驱动淘汰。",
        "sections": [
            ("一、MCP 解决的真正问题：工具接口标准化", "统一资源、工具和提示模板的发现与调用，降低集成成本。"),
            ("二、MCP 不解决什么：选择、上下文与权限", "协议不负责候选集治理、描述膨胀、副作用和授权边界。"),
            ("三、工具收敛的三层策略", "任务级最小工具集、结构化描述、评测驱动淘汰。"),
            ("四、落地检查清单", "从任务价值、语义重叠、幂等、权限和可观测性逐项检查。"),
        ],
        "body": CAPTURE_5_BODY,
        "raw": CAPTURE_5_RAW,
    },
    {
        "id": "cap_model_routing_01",
        "slug": "model-routing-cost-latency",
        "title": "大模型成本与延迟权衡：如何设计合理的 Model Routing（大小模型协同）策略",
        "folder": "AI 产品面试",
        "theme": "ai-product-interview",
        "date": "2026-10-31",
        "keywords": ["Model Routing", "成本延迟", "大小模型", "评估"],
        "tags": ["ai-product", "model-routing", "cost", "latency"],
        "summary": "按任务类型、置信度、上下文长度、用户价值和失败代价路由请求，用三层模型架构、缓存和灰度评测实现质量、成本与延迟的长期平衡。",
        "sections": [
            ("一、为什么全量使用最强模型是产品事故", "成本随规模失控，延迟集中，产品能力被昂贵调用锁死。"),
            ("二、路由信号：任务类型、置信度、上下文与用户价值", "路由应基于任务与风险，而不是单一价格维度。"),
            ("三、分层架构：小模型先行、大模型兜底、缓存复用", "轻量层、标准层、推理层加缓存和可解释降级。"),
            ("四、评估与灰度：不要只看向均成本", "质量、综合成本、P95 延迟和稳定性必须一起看。"),
        ],
        "body": CAPTURE_6_BODY,
        "raw": CAPTURE_6_RAW,
    },
]

CARD_DEFINITIONS: list[dict[str, Any]] = [
    {
        "id": "card_agent_telephone_fact",
        "title": "什么是多级 Agent 的“大模型传话筒效应”",
        "kind": "fact",
        "theme": "Agent 与大模型",
        "capture_ids": ["cap_agent_arch_01"],
        "related": ["card_agent_mcp_fact", "card_agent_latency_view"],
        "claim": "多级 Agent 在自然语言转述中逐层丢失限定条件、反例和不确定性，最终让下游基于残缺的二手证据做出自洽但不可追溯的判断。",
        "confidence": "high",
        "when_to_use": [
            "设计多级 Agent / 多 Agent 协作链路时",
            "排查「每个节点都合理、整体却不可追溯」的错误时",
        ],
        "judgments": [
            {
                "statement": "每一层自然语言摘要都是有损压缩，会优先保留符合叙事主线的信息。",
                "reasoning": "模型会删掉自认为不重要的限定条件，并用流畅语言填补空缺。",
                "support": [
                    {
                        "summary": "检索 Agent 把材料压成摘要，分析 Agent 再压成结论，写作 Agent 最终只看到二手转述。",
                        "source_ids": ["cap_agent_arch_01"],
                    }
                ],
            },
            {
                "statement": "传话筒效应比单点幻觉更难发现，因为它让整条链路共同制造自洽错误。",
                "reasoning": "每个节点单看都合理，错误难以归因，团队容易误判为模型能力问题。",
                "support": [
                    {
                        "summary": "抽查三条结论有两条找不到原文依据，但链路中没有任何节点觉得自己撒了谎。",
                        "source_ids": ["cap_agent_arch_01"],
                    }
                ],
            },
        ],
        "boundaries": [
            "不适用于单 Agent 直接处理短材料的简单链路。",
            "结构化交接本身不能保证正确，仍需原始证据可回查。",
        ],
        "action_items": [
            "所有 Agent 共享带 ID 的原始证据层，交接传引用而不是转述。",
            "把必须保留的约束、证据 ID 和回退策略写进结构化契约。",
            "在评测中加入证据保真率抽查。",
        ],
    },
    {
        "id": "card_mcp_fact",
        "title": "MCP（模型上下文协议）的本质与工具收敛原则",
        "kind": "fact",
        "theme": "Agent 与大模型",
        "capture_ids": ["cap_agent_mcp_01"],
        "related": ["card_agent_telephone_fact"],
        "claim": "MCP 统一了工具发现、描述与调用契约，降低集成成本，但不解决工具选择、上下文膨胀和权限边界，必须配合任务级工具收敛。",
        "confidence": "high",
        "when_to_use": [
            "评估是否接入 MCP 或设计 Agent 工具层时",
            "工具数量增长、模型开始选错工具时",
        ],
        "judgments": [
            {
                "statement": "MCP 的核心价值是把私有适配变成统一的客户端/服务端协议。",
                "reasoning": "资源、工具和提示模板有了统一发现与调用方式，新增能力不必修改核心代码。",
                "support": [
                    {
                        "summary": "过去每个外部系统一套适配，参数、鉴权、错误码各不相同；MCP 统一了资源、工具和提示模板。",
                        "source_ids": ["cap_agent_mcp_01"],
                    }
                ],
            },
            {
                "statement": "协议标准化降低集成成本，却提高了选择成本和上下文成本。",
                "reasoning": "候选工具变多后，模型需要在更大集合中做选择，工具描述也会挤占上下文。",
                "support": [
                    {
                        "summary": "几十个工具的参数说明会占用大量 token，挤掉真正重要的任务信息。",
                        "source_ids": ["cap_agent_mcp_01"],
                    }
                ],
            },
        ],
        "boundaries": [
            "MCP 不提供事务语义，写操作仍需确认、幂等和回滚设计。",
            "协议不能替产品决定用户在当前场景下是否应拥有某个能力。",
        ],
        "action_items": [
            "按任务动态装载最小工具集，不要一次性暴露全部工具。",
            "工具名动词化，写清适用/不适用场景和参数约束。",
            "记录调用成功率、误用率和延迟，定期合并或下线低价值工具。",
        ],
    },
    {
        "id": "card_episodic_memory_fact",
        "title": "Episodic Memory（情景记忆）与传统向量 RAG 的核心差异",
        "kind": "fact",
        "theme": "Agent 与大模型",
        "capture_ids": ["cap_pkm_teardown_01"],
        "related": ["card_pkm_soul_view", "card_learning_method"],
        "claim": "向量 RAG 检索的是相似文本片段，情景记忆保存的是「何时、为何、在什么上下文中」形成的经验；前者解决找得到，后者解决下次怎么做更好。",
        "confidence": "medium",
        "when_to_use": [
            "设计个人知识库或 Agent 长期记忆时",
            "发现检索结果很多但无法指导行动时",
        ],
        "judgments": [
            {
                "statement": "传统 RAG 以文本相似度为中心，容易返回相关但不解决问题的片段。",
                "reasoning": "相似不等于有用，片段脱离原始任务上下文后很难直接指导决策。",
                "support": [
                    {
                        "summary": "NotebookLM 的来源约束问答提升信任，但项目结束后认知很难继续沉淀。",
                        "source_ids": ["cap_pkm_teardown_01"],
                    }
                ],
            },
            {
                "statement": "情景记忆需要记录任务、决策、结果与反思，而不只是文档内容。",
                "reasoning": "只有把「当时为什么这样判断」保存下来，下一次才能复用或修正。",
                "support": [
                    {
                        "summary": "Readwise 通过复习让高亮反复出现，但缺少对判断过程的记录。",
                        "source_ids": ["cap_pkm_teardown_01"],
                    }
                ],
            },
        ],
        "boundaries": [
            "情景记忆的存储和检索成本更高，不适合所有一次性问答。",
            "隐私与遗忘机制必须同步设计，否则长期记忆会变成负担。",
        ],
        "action_items": [
            "在资料之外单独记录决策场景、备选方案和结果。",
            "检索时同时考虑文本相似度与任务/情境相似度。",
            "为记忆设计过期、纠错和删除机制。",
        ],
    },
    {
        "id": "card_real_demand_method",
        "title": "AI 产品真伪需求判断三步法",
        "kind": "method",
        "theme": "AI 产品面试",
        "capture_ids": ["cap_pm_interview_01"],
        "related": ["card_approval_risk"],
        "claim": "先用场景、损失、替代方案和机会成本收集证据，再把需求改写成可验证假设，最后预设放弃条件；三步都通过才值得投入。",
        "confidence": "high",
        "when_to_use": [
            "面试中被要求判断真伪需求时",
            "团队因为技术或 KPI 冲动要做 AI 功能时",
        ],
        "judgments": [
            {
                "statement": "真需求必须同时有具体触发场景和可量化的现状损失。",
                "reasoning": "没有场景就无法定位用户时刻，没有损失就无法证明优先级和付费意愿。",
                "support": [
                    {
                        "summary": "用户不是随时随地需要 AI 助手，而是在具体事件卡住时产生需求。",
                        "source_ids": ["cap_pm_interview_01"],
                    }
                ],
            },
            {
                "statement": "已有笨拙替代方案是需求真实性的强证据。",
                "reasoning": "用户已经为问题付出行动，说明痛点足够强，而不是口头说说。",
                "support": [
                    {
                        "summary": "真正痛的题，用户往往已经在用 Excel、微信群或收藏夹凑合解决。",
                        "source_ids": ["cap_pm_interview_01"],
                    }
                ],
            },
            {
                "statement": "投入前必须定义可放弃条件，避免沉没成本绑架。",
                "reasoning": "预先写下什么结果出现就停止，能让团队在证据不利时体面退出。",
                "support": [
                    {
                        "summary": "面试回答模板要求提出两周内最小验证，并预先定义放弃条件。",
                        "source_ids": ["cap_pm_interview_01"],
                    }
                ],
            },
        ],
        "boundaries": [
            "不适用于监管强制或安全底线类需求，这类需求不能只用 ROI 判断。",
            "机会成本难以量化时，应先用定性访谈补足证据。",
        ],
        "action_items": [
            "把「用户需要」改写成「如果成立，用户应该已经在做什么」。",
            "为每个需求写一条最小验证和一条放弃条件。",
            "面试中用目标复述、证据缺口、验证路径、放弃条件四步作答。",
        ],
    },
    {
        "id": "card_model_routing_method",
        "title": "大小模型协同（Model Routing）分层设计框架",
        "kind": "method",
        "theme": "AI 产品面试",
        "capture_ids": ["cap_model_routing_01"],
        "related": ["card_agent_latency_view", "card_real_demand_method"],
        "claim": "用任务类型、置信度、上下文长度、用户价值和失败代价做路由信号，构建轻量层/标准层/推理层加缓存的架构，并以质量、成本、P95 和稳定性共同评估。",
        "confidence": "high",
        "when_to_use": [
            "大模型成本随 DAU 线性上涨时",
            "需要同时优化延迟、质量和单位成本时",
        ],
        "judgments": [
            {
                "statement": "全量使用最强模型会让成本失控并锁死产品能力。",
                "reasoning": "高并发低难度任务被贵模型处理，团队不敢做主动整理和多次反思。",
                "support": [
                    {
                        "summary": "分类、标签、格式转换占 60% 请求，却和复杂推理共用最贵模型。",
                        "source_ids": ["cap_model_routing_01"],
                    }
                ],
            },
            {
                "statement": "路由必须纳入失败代价，不能只按价格分流。",
                "reasoning": "高风险场景的弱模型错误成本远高于节省的 Token。",
                "support": [
                    {
                        "summary": "医疗、金融、法律等高风险场景必须保留人工确认和升级路径。",
                        "source_ids": ["cap_model_routing_01"],
                    }
                ],
            },
            {
                "statement": "缓存只应复用稳定的中间产物，并带版本和 TTL。",
                "reasoning": "模型升级或事实变化后，旧答案继续返回会造成系统性错误。",
                "support": [
                    {
                        "summary": "缓存查询改写、实体归一化和网页摘要，而不是缓存所有最终答案。",
                        "source_ids": ["cap_model_routing_01"],
                    }
                ],
            },
        ],
        "boundaries": [
            "任务类型难以在入口判断时，路由本身会增加不确定性和延迟。",
            "小模型置信度校准不可靠时，低置信度升级策略可能失效。",
        ],
        "action_items": [
            "盘点请求类型与占比，先路由高并发低难度任务。",
            "设计轻量层、标准层、推理层和可解释降级路径。",
            "同时监控质量、综合成本、P50/P95 和超时/降级率。",
        ],
    },
    {
        "id": "card_learning_method",
        "title": "新领域学习者的“资料→卡片→专题”三阶认知内化法",
        "kind": "method",
        "theme": "行业与产品拆解",
        "capture_ids": ["cap_pkm_teardown_01", "cap_pm_interview_01"],
        "related": ["card_pkm_soul_view", "card_episodic_memory_fact", "card_real_demand_method"],
        "claim": "先把原始材料保真保存为资料，再把可复用判断提炼成卡片，最后围绕长期问题组织成专题；三阶分别解决证据、认知和方向。",
        "confidence": "high",
        "when_to_use": [
            "进入一个新领域并快速建立知识体系时",
            "收藏很多但说不出自己观点时",
        ],
        "judgments": [
            {
                "statement": "资料层必须保真，否则后续所有提炼都建立在二手信息上。",
                "reasoning": "原文中的限定条件、案例和数字是判断的根，压缩后就无法回查。",
                "support": [
                    {
                        "summary": "保存动作必须保真，AI 只能补充导读、标签和分类建议。",
                        "source_ids": ["cap_pm_interview_01"],
                    }
                ],
            },
            {
                "statement": "卡片层只保留可复用认知，不是文章摘要。",
                "reasoning": "摘要随文章结束而失效，卡片可以在新场景中反复调用。",
                "support": [
                    {
                        "summary": "卡片只保存可复用的观点、方法、事实或风险，不是每篇文章摘要。",
                        "source_ids": ["cap_pkm_teardown_01"],
                    }
                ],
            },
            {
                "statement": "专题层用长期问题把资料和卡片组织起来，形成方向感。",
                "reasoning": "没有专题，知识只是散点；有了专题，才能看出缺口和下一步输入。",
                "support": [
                    {
                        "summary": "资料是证据，卡片是判断，专题是长期问题意识。",
                        "source_ids": ["cap_pkm_teardown_01"],
                    }
                ],
            },
        ],
        "boundaries": [
            "不适合只做一次性查询的场景，三阶方法需要持续投入。",
            "卡片过多而没有专题时，会退化成另一种收藏夹。",
        ],
        "action_items": [
            "每次保存资料时保留原文，并写一句为什么保存。",
            "每读完一份材料，只提炼 1–2 张真正可复用的卡片。",
            "每月回看专题，删除不再重要的卡片并补充新证据。",
        ],
    },
    {
        "id": "card_pkm_soul_view",
        "title": "個人知识库 RAG 的灵魂：区分“别人的原文证据”与“我内化的个人观点”",
        "kind": "viewpoint",
        "theme": "行业与产品拆解",
        "capture_ids": ["cap_pkm_teardown_01"],
        "related": ["card_episodic_memory_fact", "card_learning_method"],
        "claim": "个人知识库最重要的不是检索更多，而是清楚区分哪些是来源原文、哪些是我基于证据形成的判断；混在一起，RAG 会放大别人的观点，也会伪造自己的观点。",
        "confidence": "high",
        "when_to_use": [
            "设计知识库的数据分层与引用时",
            "发现自己说不清某个观点是原创还是看来的时",
        ],
        "judgments": [
            {
                "statement": "原文证据与个人观点必须分层存储、分别引用。",
                "reasoning": "只有来源可追溯，才能判断一个结论是证据支持还是个人推断。",
                "support": [
                    {
                        "summary": "NotebookLM 的来源约束提升信任，因为用户知道答案边界在哪里。",
                        "source_ids": ["cap_pkm_teardown_01"],
                    }
                ],
            },
            {
                "statement": "把摘要当成原文，会让检索系统放大二手观点。",
                "reasoning": "摘要已经过一次模型判断，再被检索和引用会形成证据失真。",
                "support": [
                    {
                        "summary": "收藏一篇好文不等于理解它，组织共享也不等于个人形成判断。",
                        "source_ids": ["cap_pkm_teardown_01"],
                    }
                ],
            },
        ],
        "boundaries": [
            "个人观点也需要标注置信度和形成时间，不能当成永恒结论。",
            "来源引用不能替代独立思考，引用多不等于判断对。",
        ],
        "action_items": [
            "资料卡片保留原文链接和引用片段。",
            "个人观点卡单独标注「我的判断」和置信度。",
            "回答问题时同时给出证据来源与自己的推论。",
        ],
    },
    {
        "id": "card_approval_risk",
        "title": "高频输入场景切忌设置人工审批（Draft-Confirm）卡点",
        "kind": "risk",
        "theme": "AI 产品面试",
        "capture_ids": ["cap_pm_interview_01"],
        "related": ["card_real_demand_method"],
        "claim": "在高频、低风险的输入场景里要求用户逐条审批草稿，会让保存成本高于保存收益，最终用户放弃使用；审批应留给高风险写操作，而不是所有 AI 整理。",
        "confidence": "medium",
        "when_to_use": [
            "设计保存、整理、入库流程时",
            "发现草稿转化率低、用户抱怨步骤多时",
        ],
        "judgments": [
            {
                "statement": "高频场景的首要目标是降低摩擦，而不是追求每一步都可控。",
                "reasoning": "用户每次保存都要确认，会把工具变成负担，转而回到收藏夹。",
                "support": [
                    {
                        "summary": "用户在前 15 秒没有进展就会怀疑系统卡死，30 秒后大量放弃。",
                        "source_ids": ["cap_pm_interview_01"],
                    }
                ],
            },
            {
                "statement": "审批应基于失败代价分层，而不是统一加卡点。",
                "reasoning": "低风险整理可自动完成并支持事后修改，高风险写操作才需要确认。",
                "support": [
                    {
                        "summary": "需求判断要看不做的机会成本，流程设计同样要看卡点的机会成本。",
                        "source_ids": ["cap_pm_interview_01"],
                    }
                ],
            },
        ],
        "boundaries": [
            "涉及删除、外发、付费或权限变更的操作仍必须确认。",
            "低风险不代表无风险，需要提供可撤销和可追溯机制。",
        ],
        "action_items": [
            "按操作风险分级设计确认流程。",
            "高频保存默认自动完成，保留事后编辑和撤销。",
            "监控草稿转化率与放弃率，作为流程摩擦指标。",
        ],
    },
    {
        "id": "card_agent_latency_view",
        "title": "重型 Agent 反思环的边际收益递减：130 秒延迟踩坑复盘",
        "kind": "viewpoint",
        "theme": "Agent 与大模型",
        "capture_ids": ["cap_agent_arch_02"],
        "related": ["card_agent_telephone_fact", "card_model_routing_method"],
        "claim": "反思环不是越多越好；当质量提升低于用户感知阈值时，额外的 Agent 轮次只会增加延迟、成本和失真概率，应该用早停和分层链路替代固定流程。",
        "confidence": "high",
        "when_to_use": [
            "设计多轮反思或多 Agent 评审时",
            "延迟指标恶化但质量提升不明显时",
        ],
        "judgments": [
            {
                "statement": "固定反思环的边际质量收益可能远低于其延迟成本。",
                "reasoning": "离线评分只从 3.9 提到 4.0，P95 却从 45 秒涨到 130 秒。",
                "support": [
                    {
                        "summary": "反思与修订占 45 秒，却经常只是把已经正确的段落改写一遍。",
                        "source_ids": ["cap_agent_arch_02"],
                    }
                ],
            },
            {
                "statement": "早停与分层链路能把深推理留给真正需要的问题。",
                "reasoning": "按证据覆盖和置信度决定是否继续，避免大多数请求陪跑完整流程。",
                "support": [
                    {
                        "summary": "重构后 P95 降到 20 秒，人工评分保持 4.0，复杂问题才进入深链路。",
                        "source_ids": ["cap_agent_arch_02"],
                    }
                ],
            },
        ],
        "boundaries": [
            "高风险或高价值任务仍值得多轮反思，不能一刀切早停。",
            "早停阈值需要持续校准，过低会漏掉关键证据。",
        ],
        "action_items": [
            "为每个阶段定义足够好的阈值和超时降级。",
            "同时监控 P50、P95、超时率和人工质量评分。",
            "定期审计反思轮次是否真的改变了最终结论。",
        ],
    },
]

THEME_GROUPS: list[dict[str, Any]] = [
    {
        "id": "pq_theme_agent_architecture",
        "question_text": "专题：AI Agent 架构与工程化（传话筒效应、工具收敛、延迟 ROI）",
        "capture_ids": ["cap_agent_arch_01", "cap_agent_arch_02", "cap_agent_mcp_01"],
    },
    {
        "id": "pq_theme_ai_pm_interview",
        "question_text": "专题：AI 产品经理面试与需求判断（真伪需求、Model Routing）",
        "capture_ids": ["cap_pm_interview_01", "cap_model_routing_01"],
    },
    {
        "id": "pq_theme_pkm_learning",
        "question_text": "专题：个人知识库与认知内化（竞品拆解、资料到卡片）",
        "capture_ids": ["cap_pkm_teardown_01", "cap_pm_interview_01"],
    },
]

_CONCEPT_CACHE: dict[str, np.ndarray] = {}


def _concept_vector(name: str) -> np.ndarray:
    cached = _CONCEPT_CACHE.get(name)
    if cached is not None:
        return cached
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big") % (2**32 - 1)
    rng = np.random.default_rng(seed)
    vector = rng.normal(0.0, 1.0, DIM).astype(np.float32)
    vector = vector / np.linalg.norm(vector)
    _CONCEPT_CACHE[name] = vector
    return vector


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm == 0.0:
        return vector.astype(np.float32)
    return (vector / norm).astype(np.float32)


def _topic_vector(theme: str, keywords: list[str]) -> np.ndarray:
    """Same theme => high cosine; keywords add a stable distinguishing tilt."""
    vector = 0.8 * _concept_vector(f"theme:{theme}")
    if keywords:
        weight = 0.2 / len(keywords)
        for keyword in keywords:
            vector = vector + weight * _concept_vector(f"keyword:{keyword}")
    return _normalize(vector)


def _section_vector(base: np.ndarray, capture_id: str, title: str) -> np.ndarray:
    tilt = _concept_vector(f"section:{capture_id}:{title}")
    return _normalize(0.9 * base + 0.1 * tilt)


def _embedding_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _resolve_path(value: str | Path, base: Path) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _frontmatter_block(frontmatter: dict[str, Any]) -> str:
    return (
        "---\n"
        f"{yaml.dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False)}"
        "---\n\n"
    )


def _render_capture_note(capture: dict[str, Any]) -> str:
    frontmatter = {
        "id": capture["id"],
        "title": capture["title"],
        "date": capture["date"],
        "created": f"{capture['date']}T09:30:00+08:00",
        "source": capture["source"],
        "source_type": "web",
        "tags": capture["tags"],
        "story": capture["summary"],
        "word_count": len(capture["body"]),
        "folder_path": capture["folder"],
        "title_source": "ai",
        "entry_mode": "golden_demo",
    }
    return _frontmatter_block(frontmatter) + capture["body"].strip() + "\n"


def _render_raw_note(capture: dict[str, Any]) -> str:
    frontmatter = {
        "id": capture["id"],
        "title": capture["title"],
        "date": capture["date"],
        "source": capture["source"],
        "source_type": "web",
        "status": "raw",
        "folder_path": capture["folder"],
    }
    return _frontmatter_block(frontmatter) + capture["raw"].strip() + "\n"


def _render_card_markdown(card: dict[str, Any]) -> str:
    lines = [
        f"# {card['title']}",
        "",
        f"> {card['claim']}",
        "",
        "## 什么时候用",
        *[f"- {item}" for item in card["when_to_use"]],
        "",
        "## 核心判断",
    ]
    for index, judgment in enumerate(card["judgments"], 1):
        lines.append(f"{index}. {judgment['statement']}")
        if judgment.get("reasoning"):
            lines.append(f"   - 理由：{judgment['reasoning']}")
        for support in judgment.get("support", []):
            lines.append(f"   - 依据：{support['summary']}")
    lines.extend(["", "## 边界与反例", *[f"- {item}" for item in card["boundaries"]]])
    lines.extend(["", "## 行动启发", *[f"- {item}" for item in card["action_items"]]])
    return "\n".join(lines).strip() + "\n"


def _delete_demo_rows(conn) -> None:
    capture_ids = [capture["id"] for capture in CAPTURES]
    card_ids = [card["id"] for card in CARD_DEFINITIONS]
    question_ids = [group["id"] for group in THEME_GROUPS]
    capture_marks = ", ".join("?" for _ in capture_ids)
    card_marks = ", ".join("?" for _ in card_ids)
    question_marks = ", ".join("?" for _ in question_ids)

    for table in ("capture_metadata", "capture_embeddings"):
        conn.execute(f"DELETE FROM {table} WHERE capture_id IN ({capture_marks})", capture_ids)
    conn.execute(f"DELETE FROM captures WHERE id IN ({capture_marks})", capture_ids)
    conn.execute(f"DELETE FROM garden_cards WHERE id IN ({card_marks})", card_ids)
    conn.execute(f"DELETE FROM pending_questions WHERE id IN ({question_marks})", question_ids)
    conn.execute(
        "DELETE FROM knowledge_edges WHERE source_id IN ({}) OR target_id IN ({})".format(
            ", ".join("?" for _ in [*capture_ids, *card_ids, *question_ids]),
            ", ".join("?" for _ in [*capture_ids, *card_ids, *question_ids]),
        ),
        [*capture_ids, *card_ids, *question_ids] * 2,
    )
    conn.commit()


def _insert_capture(conn, capture: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    file_path = f"captures/{capture['slug']}.md"
    raw_path = f"_archive/raw/{capture['slug']}.md"
    identity = _topic_vector(capture["theme"], capture["keywords"])
    conn.execute(
        """INSERT OR REPLACE INTO captures
           (id, file_path, raw_path, embedding, review_state, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'new', ?, ?)""",
        (
            capture["id"],
            file_path,
            raw_path,
            _embedding_blob(identity),
            f"{capture['date']}T09:30:00+08:00",
            now,
        ),
    )
    outline = [
        {"level": 2, "title": title, "summary": summary}
        for title, summary in capture["sections"]
    ]
    metadata = {
        "folder_path": capture["folder"],
        "summary": capture["summary"],
        "outline": outline,
        "section_count": len(capture["sections"]),
        "tags": capture["tags"],
        "source_theme": capture["folder"],
        "seeded_by": "seed_interview_demo",
    }
    conn.execute(
        """INSERT OR REPLACE INTO capture_metadata
           (capture_id, outline_json, outline_source_hash, directory_rendered_at,
            metadata_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            capture["id"],
            json.dumps(outline, ensure_ascii=False),
            hashlib.sha256(json.dumps(outline, ensure_ascii=False).encode("utf-8")).hexdigest(),
            now,
            json.dumps(metadata, ensure_ascii=False),
            now,
            now,
        ),
    )
    identity_text = (
        f"标题：{capture['title']}\n摘要：{capture['summary']}\n"
        f"标签：{', '.join(capture['tags'])}\n目录纲要："
        + "；".join(title for title, _summary in capture["sections"])
    )
    _insert_embedding(
        conn,
        capture_id=capture["id"],
        kind="identity",
        section_id="",
        section_title="",
        index_text=identity_text,
        vector=_vector_for(identity_text, identity),
        file_path=file_path,
        now=now,
    )
    for index, (title, summary) in enumerate(capture["sections"], 1):
        section_text = f"文章：{capture['title']}\n章节：{title}\n内容：{summary}"
        _insert_embedding(
            conn,
            capture_id=capture["id"],
            kind="section",
            section_id=f"section-{index}",
            section_title=title,
            index_text=section_text,
            vector=_vector_for(
                section_text, _section_vector(identity, capture["id"], title)
            ),
            file_path=file_path,
            now=now,
        )


def _insert_embedding(
    conn,
    *,
    capture_id: str,
    kind: str,
    section_id: str,
    section_title: str,
    index_text: str,
    vector: np.ndarray,
    file_path: str,
    now: str,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO capture_embeddings
           (id, capture_id, kind, section_id, section_title, index_text,
            embedding, source_hash, file_path, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            uuid.uuid4().hex[:12],
            capture_id,
            kind,
            section_id,
            section_title,
            index_text,
            _embedding_blob(vector),
            hashlib.sha256(index_text.encode("utf-8")).hexdigest(),
            file_path,
            now,
            now,
        ),
    )


def _insert_card(conn, card: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "schema_version": "garden_card.v2",
        "title": card["title"],
        "card_kind": card["kind"],
        "claim": card["claim"],
        "one_liner": card["claim"],
        "confidence": card["confidence"],
        "when_to_use": card["when_to_use"],
        "core_judgments": [
            {
                "id": f"j{index}",
                "statement": judgment["statement"],
                "reasoning": judgment.get("reasoning", ""),
                "support": judgment.get("support", []),
            }
            for index, judgment in enumerate(card["judgments"], 1)
        ],
        "support": [
            support
            for judgment in card["judgments"]
            for support in judgment.get("support", [])
        ],
        "boundaries": card["boundaries"],
        "action_items": card["action_items"],
        "related_card_ids": card["related"],
        "source_ids": card["capture_ids"],
        "source_theme": card["theme"],
        "content_markdown": _render_card_markdown(card),
    }
    normalized = normalize_garden_payload(payload)
    payload = {**payload, **normalized, "source_theme": card["theme"]}
    legacy = project_legacy_fields(payload)
    conn.execute(
        """INSERT OR REPLACE INTO garden_cards
           (id, title, one_liner, card_kind, schema_version, payload_json,
            core_arguments, knowledge_links, key_materials, my_view, status,
            capture_ids, created_at, updated_at, last_reviewed_at,
            review_interval, skip_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?, ?, ?, '3d', 0)""",
        (
            card["id"],
            normalized["title"],
            normalized["claim"],
            normalized["card_kind"],
            "garden_card.v2",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            json.dumps(legacy["core_arguments"], ensure_ascii=False),
            json.dumps(legacy["knowledge_links"], ensure_ascii=False),
            json.dumps(legacy["key_materials"], ensure_ascii=False),
            legacy["my_view"],
            json.dumps(card["capture_ids"], ensure_ascii=False),
            now,
            now,
            now,
        ),
    )
    # The API view reads the column first, so it must be populated here too.
    conn.execute(
        "UPDATE garden_cards SET source_theme = ? WHERE id = ?",
        (card["theme"], card["id"]),
    )
    if _REAL_EMBEDDER is not None:
        # Cards get a real vector too, so the column is populated and ready for
        # the (not yet wired) card-vector retrieval.
        statements = "；".join(
            str(judgment.get("statement") or "") for judgment in card["judgments"]
        )
        card_text = (
            f"标题：{card['title']}\n结论：{card['claim']}\n论点：{statements}"
        )[:2000]
        conn.execute(
            "UPDATE garden_cards SET embedding = ? WHERE id = ?",
            (_embedding_blob(_REAL_EMBEDDER(card_text)), card["id"]),
        )


def _insert_theme_groups(conn) -> None:
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(days=365)).isoformat()
    for group in THEME_GROUPS:
        conn.execute(
            """INSERT OR REPLACE INTO pending_questions
               (id, agent_type, question_text, context_ref, status,
                related_capture_ids, matched_response, created_at, expires_at)
               VALUES (?, 'theme', ?, 'golden_demo', 'candidate', ?, '', ?, ?)""",
            (
                group["id"],
                group["question_text"],
                json.dumps(group["capture_ids"], ensure_ascii=False),
                now.isoformat(),
                expires,
            ),
        )


def _build_real_embedder(config: Any | None):
    """Return ``text -> np.ndarray`` backed by DashScope ``text-embedding-v4``."""
    from agents.embedder import DIM as EMBED_DIM
    from agents.embedder import Embedder

    config = config or load_config()
    api_key = str(getattr(config, "qwen_api_key", "") or "").strip()
    if not api_key:
        raise RuntimeError("qwen_api_key is empty")
    embedder = Embedder(api_key=api_key)

    def _embed(text: str) -> np.ndarray:
        blob = embedder.embed(str(text or ""))
        return np.frombuffer(blob, dtype=np.float32).astype(np.float32).reshape(EMBED_DIM)

    return _embed


def seed(
    conn,
    vault: Path,
    *,
    use_real_embeddings: bool = False,
    config: Any | None = None,
) -> dict[str, Any]:
    """Seed the demo dataset.

    ``use_real_embeddings`` stays **off by default** so unit tests never touch
    the network; the CLI turns it on when a DashScope key is configured.
    """
    global _REAL_EMBEDDER
    previous_embedder = _REAL_EMBEDDER
    _REAL_EMBEDDER = None
    embedding_mode = "synthetic"
    if use_real_embeddings:
        try:
            _REAL_EMBEDDER = _build_real_embedder(config)
            embedding_mode = "dashscope"
        except Exception as exc:  # noqa: BLE001 - seeding must still work offline
            print(f"[seed] real embeddings unavailable, using synthetic: {exc}")
            _REAL_EMBEDDER = None

    vault.mkdir(parents=True, exist_ok=True)
    (vault / "captures").mkdir(parents=True, exist_ok=True)
    (vault / "_archive" / "raw").mkdir(parents=True, exist_ok=True)
    (vault / ".hermes").mkdir(parents=True, exist_ok=True)

    _delete_demo_rows(conn)

    for capture in CAPTURES:
        capture["source"] = capture.get("source") or f"https://mp.weixin.qq.com/s/{capture['slug']}"
        atomic_write_text(vault / "captures" / f"{capture['slug']}.md", _render_capture_note(capture))
        atomic_write_text(
            vault / "_archive" / "raw" / f"{capture['slug']}.md",
            _render_raw_note(capture),
        )
        _insert_capture(conn, capture)

    for card in CARD_DEFINITIONS:
        _insert_card(conn, card)

    _insert_theme_groups(conn)
    write_folder_store(vault, DEMO_FOLDERS)
    conn.commit()

    # Cards are deleted and re-inserted above; the ledger keeps its rows, so
    # the cached counters must be rebuilt instead of silently resetting to 0.
    from bridge.rag_bridge import recompute_card_citation_counts

    recompute_card_citation_counts(conn)

    graph = KnowledgeGraphService(conn, vault).refresh()
    _REAL_EMBEDDER = previous_embedder
    return {
        "captures": len(CAPTURES),
        "cards": len(CARD_DEFINITIONS),
        "theme_groups": len(THEME_GROUPS),
        "graph": graph,
        "embedding_mode": embedding_mode,
    }


def verify(conn, vault: Path) -> dict[str, Any]:
    from api.routes.garden import _card_view
    from bridge.library_view import list_library_items

    listing = list_library_items(conn, vault, limit=100)
    card_rows = conn.execute(
        "SELECT * FROM garden_cards WHERE status = 'accepted' ORDER BY id"
    ).fetchall()
    cards = [_card_view(conn, vault, row) for row in card_rows]
    focus = KnowledgeGraphService(conn, vault).focus_graph(
        focus_kind="capture",
        focus_id=CAPTURES[0]["id"],
        limit=200,
    )
    all_edges = conn.execute("SELECT relation_type FROM knowledge_edges").fetchall()
    relations = [str(row["relation_type"]) for row in all_edges]
    return {
        "library_total": listing["total"],
        "library_titles": [item["title"] for item in listing["items"]],
        "capture_folders": sorted({item["folder_path"] for item in listing["items"]}),
        "card_total": len(cards),
        "card_kinds": sorted({card["kind"] for card in cards}),
        "card_themes": sorted({card["source_theme"] for card in cards}),
        "cards_without_sources": [card["id"] for card in cards if not card["sources"]],
        "graph_nodes": len(focus["nodes"]),
        "graph_focus_edges": len(focus["edges"]),
        "graph_edges": len(relations),
        "graph_relation_counts": {
            relation: relations.count(relation)
            for relation in (
                "card_uses_capture",
                "card_links_card",
                "theme_groups_capture",
                "semantic_similarity",
            )
        },
        "embedding_count": conn.execute(
            "SELECT COUNT(*) FROM capture_embeddings WHERE capture_id IN ({})".format(
                ", ".join("?" for _ in CAPTURES)
            ),
            [capture["id"] for capture in CAPTURES],
        ).fetchone()[0],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the Hermes Golden Demo dataset")
    parser.add_argument("--config", default=str(REPO_ROOT / "config.yaml"))
    parser.add_argument("--db", default="")
    parser.add_argument("--vault", default="")
    parser.add_argument("--json", action="store_true", help="print only the JSON summary")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="force deterministic offline vectors even when an API key exists",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    use_real_embeddings = bool(
        not args.offline and str(getattr(config, "qwen_api_key", "") or "").strip()
    )
    db_path = _resolve_path(args.db or config.db_path, REPO_ROOT)
    vault_path = _resolve_path(args.vault or config.vault_path, REPO_ROOT)
    conn = init_db(str(db_path))
    try:
        seeded = seed(
            conn,
            vault_path,
            use_real_embeddings=use_real_embeddings,
            config=config,
        )
        verified = verify(conn, vault_path)
    finally:
        conn.close()

    summary = {
        "ok": True,
        "db_path": str(db_path),
        "vault_path": str(vault_path),
        "seeded": seeded,
        "verified": verified,
    }
    if not args.json:
        print("Golden Demo dataset seeded.")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

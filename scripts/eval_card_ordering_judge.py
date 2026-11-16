"""Experiment 2 (semantic half) — controlled ordering ablation with adversarial pairs.

Why this corpus
---------------
The first attempt reused the retrieval benchmark, whose cards are one-line
conclusions and whose articles contain no boundary conditions.  The judge
correctly said NO to everything: there was no boundary to surface.

Here each topic is built as an adversarial pair:

    card      : an over-general claim distilled from the article
    sections  : claim restated, two distractors, then the boundary section

Ordering A (card first) spends one of the four context slots on the card, so the
boundary section (rank 4) falls out.  Ordering B (evidence first) keeps it.
Same model, same judge, only the order changes.

Usage::

    python scripts/eval_card_ordering_judge.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.capture_agent import load_config  # noqa: E402
from agents.rag_agent import build_rag_prompt  # noqa: E402

TOPICS = [
    {
        "query": "多级 Agent 里原文应该怎么传给工具？",
        "card": "原文可以概括后再传给工具，反正模型能抓住重点。",
        "claim": "主 Agent 可以把用户长文概括成三百字再填进工具参数。",
        "distractors": [
            "路由 Agent 需要判断用户意图，通常用短消息就够。",
            "工具调用返回结果建议做结构化输出，便于渲染。",
        ],
        "boundary": "边界：长材料严禁经过模型参数，概括会丢失原文；只有短指令可以压缩。",
    },
    {
        "query": "知识库里卡片和资料应该怎么分工？",
        "card": "卡片是最高优先级的来源，应该永远排在资料前面。",
        "claim": "卡片是用户自己沉淀的认知，检索时应当无条件置顶。",
        "distractors": [
            "资料是证据层，保存时必须保真且带原文归档。",
            "专题用于把跨篇内容聚合成一个输出单元。",
        ],
        "boundary": "边界：卡片可能片面或过时，无原文绑定的卡片不应压过原文证据，需按来源数降权。",
    },
    {
        "query": "工具是不是越多越好？",
        "card": "工具越多覆盖面越广，路由准确率不会下降。",
        "claim": "把每个能力都做成独立工具，模型自己会选对的。",
        "distractors": [
            "MCP 把工具接入从 M×N 降到 M+N。",
            "工具参数应该用 JSON Schema 约束。",
        ],
        "boundary": "边界：工具超过二十个且描述重叠时，路由准确率明显下降，应收敛到八到十四个主干工具。",
    },
    {
        "query": "RAG 的余弦阈值应该怎么定？",
        "card": "阈值固定 0.20 就够了，所有 embedding 通用。",
        "claim": "向量检索设一个 0.20 的最低分门槛即可。",
        "distractors": [
            "两阶段检索先按 identity 粗排再按 section 精排。",
            "检索结果要带引用编号，方便溯源。",
        ],
        "boundary": "边界：不同 embedding 的分数分布不同，阈值必须按引擎重新标定；正负分布重叠时单阈值无法分离。",
    },
    {
        "query": "长文切分是不是越细越好？",
        "card": "切得越细召回越准，直接按固定字数硬切即可。",
        "claim": "每 500 字切一段，不需要保留章节标题和上下文。",
        "distractors": [
            "章节索引要保留标题，方便引用时定位。",
            "分块摘要可以降低长文进入 prompt 的成本。",
        ],
        "boundary": "边界：硬切会切断定义、前提和指代关系；必须优先按标题层级切，并保留章节标题与必要上下文。",
    },
    {
        "query": "知识卡片的复习间隔应该怎么设？",
        "card": "所有卡片统一每天复习一次，频率越高记得越牢。",
        "claim": "固定每天复习全部卡片，不需要根据掌握程度调整。",
        "distractors": [
            "复习结果可以记录为掌握、模糊、遗忘三档。",
            "卡片可以关联来源资料，方便回查证据。",
        ],
        "boundary": "边界：统一高频复习会增加负担并造成“熟悉感”误判；应按掌握程度拉开间隔，遗忘时再缩短。",
    },
    {
        "query": "微信端回答是不是越长越完整？",
        "card": "回答越长信息越全，直接把完整报告发到微信即可。",
        "claim": "把所有来源和完整推理一次性发到微信，用户自己会挑重点。",
        "distractors": [
            "微信消息适合短段落和明确的下一步指令。",
            "完整回答可以导出为文件，便于回看。",
        ],
        "boundary": "边界：微信有消息长度和阅读体验限制；应先给结论和下一步，完整内容走文件或分页，不能把长报告硬塞进单条消息。",
    },
    {
        "query": "知识图谱是不是连得越多越好？",
        "card": "图谱边越多说明知识越丰富，所有相似卡片都应该连起来。",
        "claim": "只要余弦相似度不为零就加一条边，让图谱尽量密集。",
        "distractors": [
            "图谱可以按卡片、资料、专题三类节点展示。",
            "关系边应该可解释，方便用户回查。",
        ],
        "boundary": "边界：低阈值会让图谱变成“什么都连”的噪声；边必须设阈值并支持人工反馈，孤立节点不应被强行连边。",
    },
]


def _chat(config, messages: list[dict], *, max_tokens: int = 600) -> str:
    base = (config.llm_base_url or "https://api.deepseek.com").rstrip("/")
    payload = {
        "model": config.llm_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    with httpx.Client(timeout=120.0) as client:
        response = client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {config.llm_api_key}"},
            json=payload,
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"] or "").strip()


JUDGE = """用户问题：{query}

AI 回答：
{answer}

判断：回答是否指出了相关结论的适用边界、前提或局限（而不只是复述结论）？
只输出 YES 或 NO。"""


def _unit(unit_id: str, title: str, body: str) -> dict:
    return {"id": unit_id, "matched_kind": "section", "title": title, "body": body}


def _contexts(topic: dict) -> tuple[list[dict], list[dict]]:
    card = _unit("card", "卡片", f"结论：{topic['card']}")
    claim = _unit("s1", "要点", topic["claim"])
    d1 = _unit("s2", "补充", topic["distractors"][0])
    d2 = _unit("s3", "补充", topic["distractors"][1])
    boundary = _unit("s4", "适用边界", topic["boundary"])
    card_first = [card, claim, d1, d2]
    evidence_first = [claim, d1, d2, boundary]
    return card_first, evidence_first


def _judge(config, query: str, answer: str) -> str:
    """Ask the judge for YES/NO.

    The configured model is a *reasoning* model: it spends completion tokens on
    ``reasoning_content`` before any visible content, so a small ``max_tokens``
    yields an empty string and every verdict silently becomes "NO".  Give it
    room and retry once before recording a failure.
    """
    prompt = JUDGE.format(query=query, answer=answer)
    for max_tokens in (512, 1024):
        verdict = _chat(config, [{"role": "user", "content": prompt}], max_tokens=max_tokens)
        if verdict:
            return verdict
    return ""


def run() -> dict:
    config = load_config()
    rows = []
    for index, topic in enumerate(TOPICS, 1):
        card_first, evidence_first = _contexts(topic)
        row = {"id": f"t{index}", "query": topic["query"]}
        for label, units in (("card_first", card_first), ("evidence_first", evidence_first)):
            prompt = build_rag_prompt(topic["query"], units)
            answer = _chat(
                config,
                [
                    {"role": "system", "content": "你是用户的知识库助手，用中文回答。"},
                    {"role": "user", "content": prompt},
                ],
            )
            verdict = _judge(config, topic["query"], answer)
            row[label] = {
                "boundary_mentioned": "YES" in verdict.upper(),
                "answer_len": len(answer),
                "sources": [
                    {
                        "id": unit.get("id", ""),
                        "title": unit.get("title", ""),
                        "section": unit.get("section_title", ""),
                    }
                    for unit in units
                ],
                "answer": answer,
                "judge_raw_output": verdict,
                "judge_error": "" if verdict else "empty completion",
            }
        rows.append(row)
    total = max(1, len(rows))
    return {
        "rerun": True,
        "judge": "deepseek chat completion, temperature default",
        "summary": {
            label: {
                "boundary_rate": round(
                    sum(1 for row in rows if row[label]["boundary_mentioned"]) / total, 3
                )
            }
            for label in ("card_first", "evidence_first")
        }
        | {"n_topics": len(rows)},
        "results": rows,
    }


def main() -> int:
    report = run()
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    out = PROJECT_ROOT / "docs" / "card-ordering-judge.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Legacy one-off migration for old Markdown Garden Cards to garden_card.v2.

This script predates the Knowledge File + Web/DB primary model. It is retained
only for manual legacy recovery and must not run as a normal Knowledge workflow.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.garden_card_payload import (
    GARDEN_CARD_SCHEMA_VERSION,
    payload_to_json,
    project_legacy_fields,
    render_garden_card_markdown,
)
from agents.markdown_writer import read_note
from db.schema import init_db


DB_PATH = Path("/Users/pipi/hermes-custom/data/captures.db")
CONFIRMATION_PHRASE = "MIGRATE LEGACY GARDEN MARKDOWN"

PAYLOADS = {
    "b02c7904e8d3": {
        "schema_version": GARDEN_CARD_SCHEMA_VERSION,
        "title": "治理先行，分层渐进搭建取数Agent",
        "card_kind": "method",
        "claim": "搭建企业级取数Agent应先治理核心指标和可信数据路径，再分层引入技能、降级与验证机制。",
        "confidence": "high",
        "when_to_use": [
            "评审企业级取数/问数 Agent 架构时，检查是否先建立核心指标的官方口径。",
            "当问数 Agent 准确率长期卡在 90% 以下时，用它排查是模型问题还是数据治理和流程封装问题。",
            "规划从小范围试点扩展到更多指标域时，用它判断是否已经跑通可定义、可找到、可验证、可更新的闭环。",
        ],
        "core_judgments": [
            {
                "id": "j1",
                "statement": "先治理官方标准数据集，再让 Agent 回答高频指标问题。",
                "reasoning": "企业问数失败往往不是模型不会生成 SQL，而是指标口径、官方表和字段定义没有被固化。先把高频核心指标治理成可信入口，Agent 才有稳定的判断对象。",
                "support": [
                    {
                        "summary": "材料强调应先圈定核心数据、权威数据集和核心字段，再用 Agent 承接高频指标查询。",
                        "source_ids": ["9434418a03c9"],
                    }
                ],
            },
            {
                "id": "j2",
                "statement": "检索优先级应从语义层和血缘/文档开始，历史 SQL 只能作为提炼后的规则使用。",
                "reasoning": "历史 SQL 常有临时过滤和个人习惯，直接召回会把旧错误迁移到新系统。语义层、数据血缘和指标文档更适合作为可审计的知识入口。",
                "support": [
                    {
                        "summary": "材料区分指标语义层、元数据血缘、历史 SQL，并提示历史 SQL 要提炼为规则而非直接照搬。",
                        "source_ids": ["9434418a03c9"],
                    }
                ],
            },
            {
                "id": "j3",
                "statement": "知识技能和流程技能要固化分析师工作流，包括路由、澄清和对抗审查。",
                "reasoning": "取数 Agent 不是单轮 SQL 生成器，而是要把分析师平时的判断流程产品化：先理解意图，必要时澄清，再选择技能链路并检查结果。",
                "support": [
                    {
                        "summary": "材料提出知识技能与流程技能，并强调意图识别、澄清、审核和结果追溯。",
                        "source_ids": ["9434418a03c9"],
                    }
                ],
            },
            {
                "id": "j4",
                "statement": "架构应分层降级：Text-to-Metrics 优先，历史 SQL 匹配辅助，Text-to-SQL 兜底。",
                "reasoning": "分层策略能把确定性高的查询留在受控路径里，把开放 SQL 生成限制在兜底场景，降低幻觉和错表风险。",
                "support": [
                    {
                        "summary": "材料建议从 Text-to-Metrics、历史 SQL 匹配到 Text-to-SQL 逐层处理。",
                        "source_ids": ["9434418a03c9"],
                    }
                ],
            },
            {
                "id": "j5",
                "statement": "长期质量依赖离线评测、输出追溯和用户纠错闭环。",
                "reasoning": "企业级 Agent 的可靠性需要持续评估和反馈，不应只依赖一次 prompt 调优。可追溯输出和纠错闭环让系统能持续收敛。",
                "support": [
                    {
                        "summary": "材料强调离线评测、日志追踪、用户反馈和增量知识更新。",
                        "source_ids": ["9434418a03c9"],
                    }
                ],
            },
        ],
        "boundaries": [
            "不适用于只做一次性临时查询、且不需要长期复用口径的场景。",
            "如果企业没有稳定指标定义，单纯换更强模型不会解决自信选错表的问题。",
        ],
        "action_items": [
            "先列出 5 个最高频、最容易出错的核心指标。",
            "为每个指标指定唯一官方表、字段和计算口径。",
            "建立离线评测题库和用户纠错闭环后再横向扩展。",
        ],
        "related_card_ids": [],
        "source_ids": ["9434418a03c9"],
    },
    "b01505c4f649": {
        "schema_version": GARDEN_CARD_SCHEMA_VERSION,
        "title": "Subagent策略须适配训练分布",
        "card_kind": "viewpoint",
        "claim": "Subagent委派策略要适配模型是否被训练过委派判断力，不能盲目照搬某一家产品的多Agent设计。",
        "confidence": "medium",
        "when_to_use": [
            "设计 agent harness 或多 agent 架构时，用它判断该让模型主动委派还是只被动响应明确指令。",
            "比较 Codex、Claude Code、Cursor 等工具差异时，用它解释为什么同样的 subagent 策略效果不同。",
            "当系统频繁过度委派、漏委派或委派后难以归因时，用它检查训练目标和委派策略是否错配。",
        ],
        "core_judgments": [
            {
                "id": "j1",
                "statement": "Codex 的训练目标偏指令遵循和测试通过，不适合默认让模型自主决定何时创建 subagent。",
                "reasoning": "如果训练分布主要奖励完成显式任务，主动创建子代理这种元决策就容易变成不稳定行为。此时触发条件应更明确。",
                "support": [
                    {
                        "summary": "材料讨论 Codex 更像被训练成 coding executor，而不是主动委派管理者。",
                        "source_ids": ["6de5161b66f8"],
                    }
                ],
            },
            {
                "id": "j2",
                "statement": "Claude 的训练哲学更重视元判断力，因此更适合主动委派和自我管理。",
                "reasoning": "当模型在训练中更常面对任务分解、角色选择和自我管理，主动委派才更可能成为可靠策略。",
                "support": [
                    {
                        "summary": "材料把 Claude Code 的 subagent 设计与 Anthropic 对模型元能力的训练取向关联起来。",
                        "source_ids": ["6de5161b66f8"],
                    }
                ],
            },
            {
                "id": "j3",
                "statement": "Cursor 对第三方模型缺乏训练控制，只能用系统提示和固定角色降低对元能力的依赖。",
                "reasoning": "如果产品不能控制底层模型训练目标，就不应把关键链路押在模型自发形成的委派判断上，而应通过 UI、规则和角色边界约束行为。",
                "support": [
                    {
                        "summary": "材料指出 Cursor 类产品更依赖外部模型，难以像模型提供方一样训练委派策略。",
                        "source_ids": ["6de5161b66f8"],
                    }
                ],
            },
            {
                "id": "j4",
                "statement": "设计自己的 agent 系统前，应先评估模型是否具备被训练过的元决策能力。",
                "reasoning": "多 agent 架构不是越自动越好。模型能力、奖励归因和可观测性决定了委派应自动、半自动还是显式触发。",
                "support": [
                    {
                        "summary": "材料核心判断是 subagent 策略必须适配训练分布和产品控制边界。",
                        "source_ids": ["6de5161b66f8"],
                    }
                ],
            },
        ],
        "boundaries": [
            "这不是说 Codex 不能使用 subagent，而是 subagent 触发方式应更明确、更可归因。",
            "如果未来模型训练目标发生变化，委派策略也应重新评估。",
        ],
        "action_items": [
            "先写清楚 subagent 何时由用户/主 agent 明确触发。",
            "为每类 subagent 定义输入、输出和验收标准，避免奖励归因模糊。",
            "分别用过度委派、漏委派、错误委派三个 case 做回归测试。",
        ],
        "related_card_ids": [],
        "source_ids": ["6de5161b66f8"],
    },
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Legacy manual migration for old garden/*.md cards. "
            "Current Hermes stores Knowledge Cards as structured Web/DB objects."
        )
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--legacy-store", required=True)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    if args.confirm != CONFIRMATION_PHRASE:
        parser.error(f"requires --confirm {CONFIRMATION_PHRASE!r}")

    db_path = Path(args.db).expanduser()
    vault_path = Path(args.legacy_store).expanduser()
    backup_dir = vault_path / "_archive" / "garden_migrations" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=True)
    conn = init_db(str(db_path))
    conn.row_factory = None
    migrated: list[str] = []
    try:
        for card_id, payload in PAYLOADS.items():
            row = conn.execute(
                "SELECT id, title, status, capture_ids, created_at, updated_at FROM garden_cards WHERE id = ?",
                (card_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"garden card not found: {card_id}")
            _, title, status, capture_ids_json, created_at, updated_at = row
            source_ids = json.loads(capture_ids_json or "[]")
            source_links, source_ref_map = _source_links(conn, vault_path, source_ids)
            card_path = _card_path(vault_path, card_id)
            if card_path.exists():
                shutil.copy2(card_path, backup_dir / card_path.name)
            legacy = project_legacy_fields(payload)
            markdown = render_garden_card_markdown(
                payload,
                status=status or "seed",
                created_at=created_at or "",
                updated_at=updated_at or "",
                source_links=source_links,
                source_ref_map=source_ref_map,
                related_links=[],
                source_theme="",
            )
            card_path.write_text(markdown, encoding="utf-8")
            conn.execute(
                """UPDATE garden_cards
                   SET title = ?,
                       one_liner = ?,
                       card_kind = ?,
                       schema_version = ?,
                       payload_json = ?,
                       core_arguments = ?,
                       key_materials = ?,
                       knowledge_links = ?,
                       my_view = ?,
                       capture_ids = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (
                    legacy["title"],
                    legacy["one_liner"],
                    legacy["card_kind"],
                    GARDEN_CARD_SCHEMA_VERSION,
                    payload_to_json(payload),
                    json.dumps(legacy["core_arguments"], ensure_ascii=False),
                    json.dumps(legacy["key_materials"], ensure_ascii=False),
                    json.dumps(legacy["knowledge_links"], ensure_ascii=False),
                    legacy["my_view"],
                    json.dumps(legacy["capture_ids"], ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                    card_id,
                ),
            )
            migrated.append(f"{card_id}:{title}")
        conn.commit()
    finally:
        conn.close()
    print(f"backup_dir={backup_dir}")
    print("migrated=" + ",".join(migrated))


def _card_path(vault_path: Path, card_id: str) -> Path:
    matches = sorted((vault_path / "garden").glob(f"*-{card_id}.md"))
    if not matches:
        raise RuntimeError(f"garden markdown not found: {card_id}")
    return matches[0]


def _source_links(conn, vault_path: Path, source_ids: list[str]) -> tuple[list[str], dict[str, str]]:
    links: list[str] = []
    ref_map: dict[str, str] = {}
    for source_id in source_ids:
        row = conn.execute("SELECT id, file_path FROM captures WHERE id = ?", (source_id,)).fetchone()
        if not row:
            continue
        _, file_path = row
        title = Path(file_path).stem
        note = read_note(vault_path, file_path)
        if note and note[0].get("title"):
            title = str(note[0]["title"])
        link = f"[[{file_path.removesuffix('.md')}|{title}]]"
        links.append(link)
        ref_map[source_id] = link
    return links, ref_map


if __name__ == "__main__":
    main()

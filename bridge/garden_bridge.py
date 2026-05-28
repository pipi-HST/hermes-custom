"""Garden Agent bridge — 知识卡片合成入口."""
from __future__ import annotations
import json
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import load_config
from agents.markdown_writer import read_note
from agents.garden_agent import GardenAgent, create_garden_card, update_garden_card
from bridge.result_envelope import artifact_ref, error_envelope, next_action, success_envelope
from db.schema import init_db
from db.repo import CaptureRepo

logger = logging.getLogger(__name__)


def _hydrate_capture(vault_path: Path, row: dict) -> dict:
    """从 .md 文件补充 title/summary/cached_text。"""
    file_path = row.get("file_path") or row.get("raw_path") or ""
    if not file_path:
        return row
    note = read_note(vault_path, file_path)
    if not note:
        return row
    frontmatter, body = note
    return {
        **row,
        "title": frontmatter.get("title", ""),
        "summary": frontmatter.get("story", ""),
        "tags": frontmatter.get("tags", []),
        "cached_text": body,
    }


def synthesize_garden(capture_ids: list[str] = None,
                      question_id: str = "",
                      title: str = "") -> dict:
    """合成知识花园卡片。

    Args:
        capture_ids: 要合成的 capture ID 列表（直接指定）
        question_id: pending_questions 表的 ID（从主题聚类问题触发）
        title: 可选标题，不填则由 LLM 生成

    Returns:
        Agent Result Envelope.
    """
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    conn = repo.conn
    vault_path = Path(config.vault_path).expanduser()

    # 确定要合成的 captures
    if question_id:
        q_row = conn.execute(
            "SELECT * FROM pending_questions WHERE id = ?", (question_id,)
        ).fetchone()
        if not q_row:
            return error_envelope(
                agent="garden",
                intent="synthesize_garden",
                error=f"问题 {question_id} 不存在",
            )
        try:
            capture_ids = json.loads(q_row["related_capture_ids"])
        except (json.JSONDecodeError, TypeError):
            capture_ids = []

    if not capture_ids:
        return error_envelope(
            agent="garden",
            intent="synthesize_garden",
            error="未指定要合成的内容，请提供 capture_ids 或 question_id",
        )

    # 获取并 hydrate captures
    raw_rows = []
    for cid in capture_ids:
        row = conn.execute("SELECT * FROM captures WHERE id = ?", (cid,)).fetchone()
        if row:
            raw_rows.append(dict(row))

    if not raw_rows:
        return error_envelope(
            agent="garden",
            intent="synthesize_garden",
            error="未找到任何有效的 capture",
        )

    captures = [_hydrate_capture(vault_path, r) for r in raw_rows]

    # 获取现有 garden cards（用于关联）
    existing_cards = [
        dict(r) for r in conn.execute("SELECT * FROM garden_cards").fetchall()
    ]

    # 调用 GardenAgent
    agent = GardenAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    result = agent.synthesize(captures, existing_cards, title=title)

    if "error" in result:
        return error_envelope(
            agent="garden",
            intent="synthesize_garden",
            error=result["error"],
        )

    # 写入 garden_cards 表
    garden_data = {
        "title": result.get("title", ""),
        "one_liner": result.get("one_liner", ""),
        "core_arguments": result.get("core_arguments", []),
        "key_materials": result.get("key_materials", []),
        "knowledge_links": result.get("knowledge_links", []),
        "capture_ids": capture_ids,
    }
    card_id = create_garden_card(repo, garden_data)

    # 如果有 question_id，标记为已处理
    if question_id:
        conn.execute(
            "UPDATE pending_questions SET status='answered', matched_response='synthesized' WHERE id=?",
            (question_id,),
        )
        conn.commit()

    # 写 garden/ 目录的 .md 文件
    garden_dir = vault_path / "garden"
    garden_dir.mkdir(exist_ok=True)
    md_path = garden_dir / f"{card_id}.md"
    md_path.write_text(result.get("markdown", ""), encoding="utf-8")

    title_text = result.get("title", "")
    one_liner = result.get("one_liner", "")
    return success_envelope(
        agent="garden",
        intent="synthesize_garden",
        chat_preview=f"已生成知识卡片《{title_text}》：{one_liner}",
        durable_artifacts=[
            artifact_ref(
                type="garden_card",
                id=card_id,
                title=title_text,
                path=f"garden/{card_id}.md",
                status="seed",
            )
        ],
        next_actions=[
            next_action(
                label="确认采用观点",
                intent="accept_garden_card",
                payload={"card_id": card_id},
                requires_confirmation=True,
            )
        ],
        meta={
            "title": title_text,
            "one_liner": one_liner,
            "capture_ids": capture_ids,
        },
    )

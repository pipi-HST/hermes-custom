"""Theme Agent bridge — Cron 入口."""
from __future__ import annotations
import logging, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import load_config
from agents.markdown_writer import append_links, read_note, update_moc, write_moc
from agents.theme_agent import ThemeAgent, create_pending_question
from db.schema import init_db
from db.repo import CaptureRepo

logger = logging.getLogger(__name__)


def _hydrate_capture(vault_path: Path, row: dict) -> dict:
    """从 .md 文件补充 title/summary/tags/body（DB 已不存这些字段）。"""
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

def _inject_wiki_links(vault_path: Path, cluster: dict) -> int:
    """Inject [[wiki-links]] between new_capture and related_captures.

    Returns the total number of links appended across all files.
    """
    new_cap = cluster["new_capture"]
    related = cluster["related_captures"]
    total = 0

    new_title = new_cap.get("title") or "未命名"
    new_file = new_cap.get("file_path") or ""
    new_summary = new_cap.get("summary", "")[:60]

    # Links TO related captures FROM new_capture.
    if new_file:
        links_to_related = [
            {"title": c.get("title") or "未命名", "reason": c.get("summary", "")[:60]}
            for c in related
        ]
        n = append_links(vault_path, new_file, links_to_related)
        if n > 0:
            total += n

    # Back-links FROM each related capture TO new_capture.
    for c in related:
        c_file = c.get("file_path") or ""
        if not c_file:
            continue
        n = append_links(vault_path, c_file, [
            {"title": new_title, "reason": new_summary},
        ])
        if n > 0:
            total += n

    return total


def _create_moc(vault_path: Path, agent, cluster: dict) -> str | None:
    """Create a MOC page for a cluster with ≥3 captures.

    Returns the MOC file path or None if not created.
    """
    all_captures = [cluster["new_capture"]] + cluster["related_captures"]
    if len(all_captures) < 3:
        return None

    theme_name = agent.generate_theme_name(all_captures)
    body = agent.build_moc(theme_name, all_captures)

    # Collect tags from all captures.
    all_tags: list[str] = []
    for c in all_captures:
        for tag in c.get("tags", []):
            if tag not in all_tags:
                all_tags.append(tag)

    return write_moc(
        vault_path,
        theme_name=theme_name,
        body=body,
        tags=all_tags,
        capture_count=len(all_captures),
    )


def _evolve_themes(vault_path: Path, agent, unclustered_caps: list[dict]) -> int:
    """Match unclustered captures to existing MOCs and update them.

    Returns the number of MOCs updated.
    """
    updated = 0
    for cap in unclustered_caps:
        try:
            moc_path = agent.match_to_themes(vault_path, cap)
            if moc_path and update_moc(vault_path, moc_path, cap):
                updated += 1
        except Exception as e:
            logger.warning(f"Theme evolution failed for {cap.get('id')}: {e}")
    return updated


def run_theme_scan() -> str:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    conn = repo.conn

    rows = conn.execute(
        "SELECT * FROM captures WHERE embedding IS NOT NULL "
        "AND theme_scanned_at IS NULL ORDER BY created_at ASC LIMIT 50"
    ).fetchall()

    if not rows:
        return "没有待扫描的新内容。"

    vault_path = Path(config.vault_path).expanduser()
    new_caps = [_hydrate_capture(vault_path, dict(r)) for r in rows]
    all_caps = [_hydrate_capture(vault_path, c) for c in repo.list_all(limit=200)]

    agent = ThemeAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
    )

    clusters = agent.scan(new_caps, all_caps)

    # Layer 1: inject wiki-links between related captures.
    # Layer 2: create MOC pages for clusters with ≥3 captures.
    links_injected = 0
    mocs_created = 0
    for cluster in clusters:
        try:
            links_injected += _inject_wiki_links(vault_path, cluster)
        except Exception as e:
            logger.warning(f"Wiki-link injection failed: {e}")
        try:
            if _create_moc(vault_path, agent, cluster):
                mocs_created += 1
        except Exception as e:
            logger.warning(f"MOC creation failed: {e}")

    results = []
    for cluster in clusters:
        try:
            q = agent.generate_question(cluster)
            create_pending_question(
                repo, "theme", q,
                context_ref=f"theme-{cluster['new_capture']['id'][:8]}",
                related_capture_ids=(
                    [c["id"] for c in cluster["related_captures"]]
                    + [cluster["new_capture"]["id"]]
                ),
            )
            results.append(q[:80])
        except Exception as e:
            logger.warning(f"Theme question failed: {e}")

    # Layer 3: match unclustered captures to existing themes.
    clustered_ids = {c["new_capture"]["id"] for c in clusters}
    unclustered = [c for c in new_caps if c["id"] not in clustered_ids]
    mocs_updated = _evolve_themes(vault_path, agent, unclustered)

    now_iso = datetime.now(timezone.utc).isoformat()
    for c in new_caps:
        conn.execute("UPDATE captures SET theme_scanned_at=? WHERE id=?",
                     (now_iso, c["id"]))
    conn.commit()

    return (
        f"Theme 扫描完成: {len(new_caps)} 条, {len(results)} 个聚类, "
        f"{links_injected} 条 wiki-link, {mocs_created} 个 MOC 创建, "
        f"{mocs_updated} 个 MOC 更新。"
    )


def run_graph_report() -> str:
    """Generate a knowledge graph health report (cron entry point)."""
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    vault_path = Path(config.vault_path).expanduser()

    agent = ThemeAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    return agent.graph_report(vault_path)

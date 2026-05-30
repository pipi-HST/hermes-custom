"""Theme Agent bridge — Cron 入口."""
from __future__ import annotations
import logging, sys
from datetime import datetime, timezone
from pathlib import Path
import yaml
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import load_config
from agents.markdown_writer import append_links, read_moc, read_note, update_moc, write_moc
from agents.theme_agent import ThemeAgent, create_pending_question
from bridge.result_envelope import artifact_ref, success_envelope
from db.schema import init_db
from db.repo import CaptureRepo

logger = logging.getLogger(__name__)


def _graph_report_preview(report: str) -> str:
    lines = [line.strip() for line in report.splitlines() if line.strip()]
    if not lines:
        return "知识图谱健康报告已生成。"
    return "\n".join(lines[:6])


def _identity_index_map(conn) -> dict[str, dict]:
    rows = conn.execute(
        """SELECT * FROM capture_embeddings
           WHERE kind = 'identity' AND embedding IS NOT NULL"""
    ).fetchall()
    return {str(row["capture_id"]): dict(row) for row in rows}


def _unindexed_capture_count(conn, *, rescan_all: bool) -> int:
    scan_filter = "" if rescan_all else "AND c.theme_scanned_at IS NULL"
    row = conn.execute(
        f"""SELECT COUNT(*) AS count
            FROM captures c
            WHERE c.file_path IS NOT NULL
              AND c.file_path != ''
              {scan_filter}
              AND NOT EXISTS (
                SELECT 1
                FROM capture_embeddings e
                WHERE e.capture_id = c.id
                  AND e.kind = 'identity'
                  AND e.embedding IS NOT NULL
              )"""
    ).fetchone()
    return int(row["count"] or 0) if row else 0


def _identity_summary(index_text: str) -> str:
    for line in (index_text or "").splitlines():
        if line.startswith("摘要："):
            return line.removeprefix("摘要：").strip()
    return (index_text or "")[:120]


def _hydrate_capture(
    vault_path: Path,
    row: dict,
    identity_indexes: dict[str, dict] | None = None,
) -> dict:
    """从 .md 文件补充 title/summary/tags/body（DB 已不存这些字段）。"""
    file_path = row.get("file_path") or row.get("raw_path") or ""
    identity = (identity_indexes or {}).get(row.get("id") or "") or {}
    embedding = identity.get("embedding") or row.get("embedding")
    index_text = identity.get("index_text") or ""
    if not file_path:
        return {**row, "embedding": embedding, "index_text": index_text}
    note = read_note(vault_path, file_path)
    if not note:
        return {**row, "embedding": embedding, "index_text": index_text}
    frontmatter, body = note
    return {
        **row,
        "embedding": embedding,
        "index_text": index_text,
        "title": frontmatter.get("title", ""),
        "summary": frontmatter.get("story", "") or _identity_summary(index_text),
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


def _tag_captures_with_theme(
    vault_path: Path, captures: list[dict], theme_name: str
) -> int:
    """Add theme_name to each capture note's linked_themes frontmatter."""
    updated = 0
    for cap in captures:
        file_path = cap.get("file_path") or ""
        if not file_path:
            continue
        full_path = vault_path / file_path
        if not full_path.exists():
            continue

        text = full_path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue

        try:
            frontmatter = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            continue

        linked = frontmatter.get("linked_themes") or []
        if isinstance(linked, str):
            linked = [linked]
        if theme_name in linked:
            continue

        frontmatter["linked_themes"] = [*linked, theme_name]
        header = yaml.dump(
            frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False
        )
        full_path.write_text(f"---\n{header}---{parts[2]}", encoding="utf-8")
        updated += 1
    return updated


def _evolve_themes(vault_path: Path, agent, unclustered_caps: list[dict]) -> int:
    """Match unclustered captures to existing MOCs and update them.

    Returns the number of MOCs updated.
    """
    updated = 0
    for cap in unclustered_caps:
        try:
            moc_path = agent.match_to_themes(vault_path, cap)
            if moc_path and update_moc(vault_path, moc_path, cap):
                moc = read_moc(vault_path, moc_path)
                if moc:
                    frontmatter, _ = moc
                    theme_name = frontmatter.get("theme_name") or Path(moc_path).stem
                    _tag_captures_with_theme(vault_path, [cap], theme_name)
                updated += 1
        except Exception as e:
            logger.warning(f"Theme evolution failed for {cap.get('id')}: {e}")
    return updated


def run_theme_scan(*, rescan_all: bool = False) -> dict:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    conn = repo.conn

    scan_filter = "" if rescan_all else "AND c.theme_scanned_at IS NULL"
    rows = conn.execute(
        f"""SELECT c.* FROM captures c
           JOIN capture_embeddings e
             ON e.capture_id = c.id
            AND e.kind = 'identity'
            AND e.embedding IS NOT NULL
           WHERE 1 = 1
             {scan_filter}
           ORDER BY c.created_at ASC
           LIMIT 50"""
    ).fetchall()

    if not rows:
        unindexed_count = _unindexed_capture_count(conn, rescan_all=rescan_all)
        if unindexed_count:
            return success_envelope(
                agent="theme",
                intent="run_theme_scan",
                chat_preview=(
                    f"有 {unindexed_count} 篇新内容索引未完成，"
                    "主题扫描暂时不会处理它们。请先执行知识库安全修复/索引维护。"
                ),
                meta={
                    "scanned_count": 0,
                    "rescan_all": rescan_all,
                    "unindexed_count": unindexed_count,
                },
            )
        return success_envelope(
            agent="theme",
            intent="run_theme_scan",
            chat_preview="没有待扫描的新内容。",
            meta={"scanned_count": 0, "rescan_all": rescan_all, "unindexed_count": 0},
        )

    vault_path = Path(config.vault_path).expanduser()
    identity_indexes = _identity_index_map(conn)
    new_caps = [_hydrate_capture(vault_path, dict(r), identity_indexes) for r in rows]
    all_caps = [
        _hydrate_capture(vault_path, c, identity_indexes)
        for c in repo.list_all(limit=200)
    ]

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
    capture_theme_tags = 0
    theme_artifacts = []
    for cluster in clusters:
        try:
            links_injected += _inject_wiki_links(vault_path, cluster)
        except Exception as e:
            logger.warning(f"Wiki-link injection failed: {e}")
        try:
            moc_path = _create_moc(vault_path, agent, cluster)
            if moc_path:
                mocs_created += 1
                moc = read_moc(vault_path, moc_path)
                if moc:
                    frontmatter, _ = moc
                    theme_name = frontmatter.get("theme_name") or Path(moc_path).stem
                    capture_theme_tags += _tag_captures_with_theme(
                        vault_path,
                        [cluster["new_capture"]] + cluster["related_captures"],
                        theme_name,
                    )
                theme_artifacts.append(
                    artifact_ref(
                        type="theme_page",
                        id=moc_path,
                        title=Path(moc_path).stem,
                        path=moc_path,
                        status="created",
                    )
                )
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

    chat_preview = (
        f"Theme 扫描完成: {len(new_caps)} 条, {len(results)} 个聚类, "
        f"{links_injected} 条 wiki-link, {mocs_created} 个 MOC 创建, "
        f"{mocs_updated} 个 MOC 更新, {capture_theme_tags} 条主题标记。"
    )
    return success_envelope(
        agent="theme",
        intent="run_theme_scan",
        chat_preview=chat_preview,
        durable_artifacts=theme_artifacts,
        meta={
            "scanned_count": len(new_caps),
            "rescan_all": rescan_all,
            "cluster_count": len(results),
            "links_injected": links_injected,
            "theme_pages_created": mocs_created,
            "theme_pages_updated": mocs_updated,
            "capture_theme_tags": capture_theme_tags,
        },
    )


def run_graph_report() -> dict:
    """Generate a knowledge graph health report (cron entry point)."""
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    vault_path = Path(config.vault_path).expanduser()

    agent = ThemeAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    report = agent.graph_report(vault_path)
    report_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_rel_path = f"data/reports/graph-report-{report_id}.md"
    report_path = Path(__file__).parent.parent / report_rel_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return success_envelope(
        agent="theme",
        intent="run_graph_report",
        chat_preview=_graph_report_preview(report),
        durable_artifacts=[
            artifact_ref(
                type="librarian_report",
                id=report_id,
                title="知识图谱健康报告",
                path=report_rel_path,
                status="reported",
            )
        ],
        meta={"report_length": len(report)},
    )

"""Theme Agent bridge — Cron 入口."""
from __future__ import annotations
import json
import logging, sys
from datetime import datetime, timezone
from pathlib import Path
import yaml
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import load_config
from agents.markdown_writer import (
    append_links,
    read_moc,
    read_note,
    update_moc,
    write_theme_page,
)
from agents.theme_agent import ThemeAgent, create_pending_question
from agents.theme_graph import (
    ThemeCluster,
    ThemeNode,
    ThemeSection,
    build_relationship_edges,
    cluster_relationship_graph,
    filter_theme_nodes,
)
from bridge.context import KnowledgeContext
from bridge.file_artifacts import write_markdown_export
from bridge.result_envelope import artifact_ref, next_action, success_envelope
from bridge.run_ledger import run_with_ledger
from db.schema import init_db
from db.repo import CaptureRepo
from db.write_job_repo import KnowledgeWriteJobRepo

logger = logging.getLogger(__name__)


def _config_repo(context: KnowledgeContext | None = None):
    if context is not None:
        return context.config, context.capture_repo()
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    return config, CaptureRepo(init_db(config.db_path))


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


def _indexed_capture_count(conn, *, rescan_all: bool) -> int:
    scan_filter = "" if rescan_all else "AND c.theme_scanned_at IS NULL"
    row = conn.execute(
        f"""SELECT COUNT(DISTINCT c.id) AS count
            FROM captures c
            JOIN capture_embeddings e
              ON e.capture_id = c.id
             AND e.kind = 'identity'
             AND e.embedding IS NOT NULL
            WHERE c.file_path IS NOT NULL
              AND c.file_path != ''
              {scan_filter}"""
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


def _build_theme_nodes(vault_path: Path, rows: list[dict], conn) -> list[ThemeNode]:
    embeddings = conn.execute("SELECT * FROM capture_embeddings").fetchall()
    by_capture: dict[str, list[dict]] = {}
    for row in embeddings:
        by_capture.setdefault(str(row["capture_id"]), []).append(dict(row))

    nodes: list[ThemeNode] = []
    for row in rows:
        capture_id = str(row["id"])
        file_path = row.get("file_path") or ""
        note = read_note(vault_path, file_path)
        frontmatter, _body = note if note else ({}, "")
        identity_embedding = None
        section_embeddings: list[ThemeSection] = []
        summary = ""
        for emb in by_capture.get(capture_id, []):
            if emb["kind"] == "identity":
                identity_embedding = emb["embedding"]
                summary = _identity_summary(emb.get("index_text") or "")
            elif emb["kind"] == "section" and emb.get("embedding"):
                section_embeddings.append(
                    ThemeSection(
                        section_id=emb.get("section_id") or "",
                        section_title=emb.get("section_title") or "",
                        index_text=emb.get("index_text") or "",
                        embedding=emb["embedding"],
                    )
                )
        nodes.append(
            ThemeNode(
                capture_id=capture_id,
                title=frontmatter.get("title") or Path(file_path).stem,
                file_path=file_path,
                summary=frontmatter.get("story") or summary,
                tags=tuple(frontmatter.get("tags") or ()),
                created_at=row.get("created_at") or "",
                identity_embedding=identity_embedding,
                section_embeddings=tuple(section_embeddings),
            )
        )
    return nodes

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
    """Create a Theme page for a cluster with ≥3 captures.

    Returns the Theme page file path or None if not created.
    """
    all_captures = [cluster["new_capture"]] + cluster["related_captures"]
    if len(all_captures) < 3:
        return None

    theme_name = agent.generate_theme_name(all_captures)
    if callable(getattr(type(agent), "build_theme_page", None)):
        body = agent.build_theme_page(theme_name, all_captures)
    else:
        body = agent.build_moc(theme_name, all_captures)

    # Collect tags from all captures.
    all_tags: list[str] = []
    for c in all_captures:
        for tag in c.get("tags", []):
            if tag not in all_tags:
                all_tags.append(tag)

    return write_theme_page(
        vault_path,
        theme_name=theme_name,
        body=body,
        captures=all_captures,
        garden_cards=[],
        tags=all_tags,
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


def _cluster_to_legacy_dict(cluster: ThemeCluster, node_by_id: dict[str, ThemeNode]) -> dict:
    capture_dicts = [
        {
            "id": node.capture_id,
            "title": node.title,
            "file_path": node.file_path,
            "summary": node.summary,
            "tags": list(node.tags),
        }
        for node in (node_by_id[cid] for cid in cluster.capture_ids)
        if node
    ]
    return {
        "new_capture": capture_dicts[0],
        "related_captures": capture_dicts[1:],
        "score": cluster.score,
        "edges": cluster.edges,
    }


def _theme_scan_preview(
    *,
    scanned_real_count: int,
    skipped_test_count: int,
    cluster_questions: list[str],
    partial: bool,
) -> str:
    if cluster_questions:
        status = "Theme 扫描只完成了一部分" if partial else "我扫完了这批内容"
        lines = [
            f"{status}：已扫描 {scanned_real_count} 篇真实笔记，发现 {len(cluster_questions)} 组值得整理的关联。",
        ]
        if skipped_test_count:
            lines.append(f"已跳过 {skipped_test_count} 篇测试材料。")
        lines.append("")
        for index, question in enumerate(cluster_questions[:3], 1):
            lines.append(f"{index}. {question}")
        lines.append("")
        lines.append("要我先展开哪一组？也可以说“展开最值得保留的那个”。")
        return "\n".join(lines)

    lines = [
        "这次没有发现足够强的主题关联。",
        f"已扫描真实笔记 {scanned_real_count} 篇。",
    ]
    if skipped_test_count:
        lines.append(f"已跳过 {skipped_test_count} 篇测试材料。")
    if partial:
        lines.append("这次扫描只完成了一部分，未处理内容会留到下次继续。")
    else:
        lines.append("这不是扫描失败，只是当前材料之间的相似度不够。")
    return "\n".join(lines)


def _json_list(text: str) -> list[str]:
    try:
        value = json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _short_question_text(text: str, max_len: int = 48) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:max_len]


def _candidate_group_key(capture_ids: list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(capture_ids)))


def list_theme_candidates(
    *,
    limit: int = 5,
    context: KnowledgeContext | None = None,
) -> dict:
    _, repo = _config_repo(context)
    max_limit = max(1, min(int(limit or 5), 10))
    rows = repo.conn.execute(
        """SELECT * FROM pending_questions
           WHERE agent_type='theme'
             AND status='pending'
             AND expires_at > ?
           ORDER BY created_at DESC""",
        (datetime.now(timezone.utc).isoformat(),),
    ).fetchall()
    latest_by_group: dict[tuple[str, ...], dict] = {}
    for row in rows:
        capture_ids = _json_list(row["related_capture_ids"])
        if len(set(capture_ids)) < 2:
            continue
        key = _candidate_group_key(capture_ids)
        if key not in latest_by_group:
            latest_by_group[key] = dict(row)
    candidates = sorted(
        latest_by_group.values(),
        key=lambda row: row["created_at"],
    )[:max_limit]

    if not candidates:
        return success_envelope(
            agent="theme",
            intent="list_theme_candidates",
            chat_preview="现在没有可直接展开的 Theme 候选组。可以先扫描最近内容，或明确要求全库扫描。",
            meta={"candidate_count": 0},
        )

    lines = [f"现在有 {len(candidates)} 组可展开成知识卡片的候选：", ""]
    actions = []
    for index, row in enumerate(candidates, 1):
        capture_ids = _json_list(row["related_capture_ids"])
        lines.append(
            f"{index}. {_short_question_text(row['question_text'])}"
            f"（{len(capture_ids)} 篇材料）"
        )
        actions.append(
            next_action(
                label=f"展开第 {index} 组为知识卡片",
                intent="synthesize_garden",
                payload={"question_id": row["id"], "capture_ids": capture_ids},
            )
        )
    lines.append("")
    lines.append("回复编号即可展开，例如“第一组”。")
    return success_envelope(
        agent="theme",
        intent="list_theme_candidates",
        chat_preview="\n".join(lines),
        next_actions=actions,
        meta={"candidate_count": len(candidates)},
    )


def run_theme_scan(
    *,
    rescan_all: bool = False,
    export_file: bool = False,
    context: KnowledgeContext | None = None,
) -> dict:
    config, repo = _config_repo(context)
    write_jobs = KnowledgeWriteJobRepo(repo.conn)
    job_id = write_jobs.start(
        intent="run_theme_scan",
        object_type="theme_scan",
        object_id="latest",
        payload={"rescan_all": rescan_all, "export_file": export_file},
    )

    def operation() -> dict:
        try:
            write_jobs.mark_step(job_id, status="writing_file", step="run_theme_scan")
            result = _run_theme_scan(
                config,
                repo,
                rescan_all=rescan_all,
                export_file=export_file,
            )
            if result.get("status") == "error":
                write_jobs.fail(job_id, step="run_theme_scan", error="; ".join(result.get("errors") or []))
            else:
                artifacts = result.get("durable_artifacts") or []
                first_path = ""
                for artifact in artifacts:
                    if isinstance(artifact, dict) and artifact.get("path"):
                        first_path = str(artifact["path"])
                        break
                write_jobs.complete(
                    job_id,
                    file_path=first_path,
                    db_table="captures,pending_questions",
                )
            return result
        except Exception as exc:
            write_jobs.repair_needed(
                job_id,
                step="run_theme_scan",
                error=str(exc),
                db_table="captures,pending_questions",
            )
            raise

    return run_with_ledger(
        repo.conn,
        agent="theme",
        intent="run_theme_scan",
        trigger="wechat",
        input_ref={"rescan_all": rescan_all, "export_file": export_file},
        operation=operation,
    )


def _run_theme_scan(
    config,
    repo: CaptureRepo,
    *,
    rescan_all: bool = False,
    export_file: bool = False,
) -> dict:
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
            envelope = success_envelope(
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
                    "export_file": export_file,
                },
            )
            if export_file:
                envelope["durable_artifacts"] = [
                    _theme_scan_report_artifact(
                        title="Theme 扫描报告",
                        chat_preview=envelope["chat_preview"],
                        meta=envelope["meta"],
                    )
                ]
            return envelope
        envelope = success_envelope(
            agent="theme",
            intent="run_theme_scan",
            chat_preview="没有待扫描的新内容。",
            meta={
                "scanned_count": 0,
                "rescan_all": rescan_all,
                "unindexed_count": 0,
                "export_file": export_file,
            },
        )
        if export_file:
            envelope["durable_artifacts"] = [
                _theme_scan_report_artifact(
                    title="Theme 扫描报告",
                    chat_preview=envelope["chat_preview"],
                    meta=envelope["meta"],
                )
            ]
        return envelope

    vault_path = Path(config.vault_path).expanduser()
    row_dicts = [dict(r) for r in rows]
    scan_nodes = _build_theme_nodes(vault_path, row_dicts, conn)
    all_nodes = _build_theme_nodes(vault_path, repo.list_all(limit=200), conn)
    eligible_nodes, filter_stats = filter_theme_nodes(all_nodes)
    eligible_scan_nodes, _scan_filter_stats = filter_theme_nodes(scan_nodes)
    partial = _indexed_capture_count(conn, rescan_all=rescan_all) > len(row_dicts)

    agent = ThemeAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
        llm_timeout=25.0,
    )

    eligible_scan_ids = {
        node.capture_id
        for node in eligible_scan_nodes
    }
    node_by_id = {node.capture_id: node for node in eligible_nodes}
    edges = build_relationship_edges(eligible_nodes)
    graph_clusters = [
        cluster for cluster in cluster_relationship_graph(edges)
        if eligible_scan_ids & set(cluster.capture_ids)
    ]
    clusters = [_cluster_to_legacy_dict(cluster, node_by_id) for cluster in graph_clusters]

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
                cluster["_theme_page_path"] = moc_path
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
    question_actions = []
    for cluster in clusters:
        try:
            q = agent.generate_question(cluster)
            related_capture_ids = (
                [c["id"] for c in cluster["related_captures"]]
                + [cluster["new_capture"]["id"]]
            )
            question = create_pending_question(
                repo, "theme", q,
                context_ref=(
                    f"theme:{cluster['_theme_page_path']}"
                    if cluster.get("_theme_page_path")
                    else f"theme-{cluster['new_capture']['id'][:8]}"
                ),
                related_capture_ids=related_capture_ids,
            )
            results.append(q[:80])
            question_actions.append(
                next_action(
                    label=f"展开第 {len(question_actions) + 1} 组为知识卡片",
                    intent="synthesize_garden",
                    payload={
                        "question_id": question["id"],
                        "capture_ids": related_capture_ids,
                    },
                )
            )
        except Exception as e:
            logger.warning(f"Theme question failed: {e}")

    # Layer 3: match unclustered captures to existing themes.
    clustered_ids = {
        capture_id
        for cluster in clusters
        for capture_id in (
            [cluster["new_capture"]["id"]]
            + [c["id"] for c in cluster["related_captures"]]
        )
    }
    new_caps = [
        {
            "id": node.capture_id,
            "title": node.title,
            "file_path": node.file_path,
            "summary": node.summary,
            "tags": list(node.tags),
        }
        for node in eligible_scan_nodes
    ]
    unclustered = [c for c in new_caps if c["id"] not in clustered_ids]
    mocs_updated = _evolve_themes(vault_path, agent, unclustered)

    mark_scanned_ids = {node.capture_id for node in scan_nodes}
    now_iso = datetime.now(timezone.utc).isoformat()
    for node in scan_nodes:
        if node.capture_id not in mark_scanned_ids:
            continue
        conn.execute("UPDATE captures SET theme_scanned_at=? WHERE id=?",
                     (now_iso, node.capture_id))
    conn.commit()

    skipped_test_count = int(filter_stats.get("skipped_test_garbage") or 0)
    chat_preview = _theme_scan_preview(
        scanned_real_count=len(eligible_scan_ids),
        skipped_test_count=skipped_test_count,
        cluster_questions=results,
        partial=partial,
    )
    if export_file:
        theme_artifacts.append(
            _theme_scan_report_artifact(
                title="Theme 扫描报告",
                chat_preview=chat_preview,
                meta={
                    "scanned_count": len(new_caps),
                    "rescan_all": rescan_all,
                    "partial": partial,
                    "cluster_count": len(results),
                    "links_injected": links_injected,
                    "theme_pages_created": mocs_created,
                    "theme_pages_updated": mocs_updated,
                    "capture_theme_tags": capture_theme_tags,
                    "relationship_edges": len(edges),
                    "skipped_test_garbage": skipped_test_count,
                    "scanned_real_count": len(eligible_scan_ids),
                },
                next_actions=question_actions,
            )
        )
    return success_envelope(
        agent="theme",
        intent="run_theme_scan",
        chat_preview=chat_preview,
        durable_artifacts=theme_artifacts,
        next_actions=question_actions,
        meta={
            "scanned_count": len(new_caps),
            "rescan_all": rescan_all,
            "partial": partial,
            "cluster_count": len(results),
            "links_injected": links_injected,
            "theme_pages_created": mocs_created,
            "theme_pages_updated": mocs_updated,
            "capture_theme_tags": capture_theme_tags,
            "relationship_edges": len(edges),
            "skipped_test_garbage": skipped_test_count,
            "scanned_real_count": len(eligible_scan_ids),
            "export_file": export_file,
        },
    )


def _theme_scan_report_artifact(
    *,
    title: str,
    chat_preview: str,
    meta: dict,
    next_actions: list[dict] | None = None,
    export_dir: Path | None = None,
) -> dict:
    lines = [
        f"# {title or 'Theme 扫描报告'}",
        "",
        "## 摘要",
        "",
        (chat_preview or "").strip() or "无摘要。",
        "",
        "## 统计",
        "",
    ]
    for key, value in sorted((meta or {}).items()):
        lines.append(f"- {key}: {value}")
    actions = next_actions or []
    if actions:
        lines.extend(["", "## 候选动作", ""])
        for index, action in enumerate(actions, 1):
            payload = action.get("payload") or {}
            lines.append(f"{index}. {action.get('label') or '候选'}")
            if payload.get("question_id"):
                lines.append(f"   - question_id: {payload['question_id']}")
            if payload.get("capture_ids"):
                lines.append(f"   - capture_ids: {', '.join(payload['capture_ids'])}")
    base_dir = export_dir or (Path(__file__).parent.parent / "data" / "exports" / "theme")
    path = write_markdown_export(
        base_dir=base_dir,
        stem="theme-scan-report",
        content="\n".join(lines),
    )
    return artifact_ref(
        type="theme_scan_report",
        id=Path(path).stem,
        title=title or "Theme 扫描报告",
        path=path,
        status="created",
    )


def run_graph_report(context: KnowledgeContext | None = None) -> dict:
    """Generate a knowledge graph health report (cron entry point)."""
    config = context.config if context is not None else load_config(str(Path(__file__).parent.parent / "config.yaml"))
    vault_path = Path(config.vault_path).expanduser()

    agent = ThemeAgent(
        llm_api_key=config.llm_api_key,
        llm_model=config.llm_model,
        llm_base_url=config.llm_base_url or "https://api.deepseek.com",
        llm_timeout=25.0,
    )
    report = agent.graph_report(vault_path)
    report_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = write_markdown_export(
        base_dir=Path(__file__).parent.parent / "data" / "exports" / "theme",
        stem=f"graph-report-{report_id}",
        content=report,
    )
    return success_envelope(
        agent="theme",
        intent="run_graph_report",
        chat_preview=_graph_report_preview(report),
        durable_artifacts=[
            artifact_ref(
                type="graph_report",
                id=report_id,
                title="知识图谱健康报告",
                path=report_path,
                status="reported",
            )
        ],
        meta={"report_length": len(report)},
    )

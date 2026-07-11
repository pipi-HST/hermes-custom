"""Library Agent maintenance for committed Knowledge Files."""
from __future__ import annotations

import re
import sqlite3
import hashlib
import unicodedata
import shutil
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agents.capture_indexer import build_identity_index, build_section_indexes
from agents.managed_sections import is_managed_section_title
from db.embedding_repo import CaptureEmbeddingRepo
from db.metadata_repo import CaptureMetadataRepo
from db.schema import init_db
from scripts.repair_data import repair_data


DEFAULT_OUTLINE_CHAR_THRESHOLD = 2000
DIRECTORY_HEADING = "## 目录纲要"
CAPTURE_REQUIRED_FIELDS = (
    "type",
    "status",
    "id",
    "title",
    "date",
    "source",
    "source_type",
    "reviewable",
    "importance",
)
RAW_REQUIRED_FIELDS = (
    "type",
    "status",
    "id",
    "capture_id",
    "title",
    "date",
    "source",
    "source_type",
)


@dataclass(frozen=True)
class CaptureBodyRenderResult:
    capture_body: str
    outline_metadata: list[dict]
    warnings: list[str]
    directory_rendered: bool = False
    heading_count: int = 0


class LibraryAgent:
    """Maintain vault-level metadata without rewriting note prose."""

    def __init__(
        self,
        vault_path: str | Path,
        *,
        outliner: Any,
        db_path: str | Path | None = None,
        db_conn: sqlite3.Connection | None = None,
        embedder: Any | None = None,
        min_outline_chars: int = DEFAULT_OUTLINE_CHAR_THRESHOLD,
    ) -> None:
        self.vault_path = Path(vault_path).expanduser()
        self.outliner = outliner
        self.db_path = Path(db_path).expanduser() if db_path else None
        self.db_conn = db_conn
        self.embedder = embedder
        self.min_outline_chars = min_outline_chars

    def maintain_database_sync(
        self,
        *,
        apply: bool = False,
        rename_readable: bool = True,
    ) -> dict[str, Any]:
        if self.db_path is None:
            return {
                "dry_run": not apply,
                "skipped": True,
                "reason": "db_path_not_configured",
            }
        return repair_data(
            self.db_path,
            self.vault_path,
            apply=apply,
            rename_readable=rename_readable,
        )

    def audit_property_gaps(self) -> dict[str, Any]:
        capture_gaps = _property_gaps(
            self.vault_path,
            "captures",
            CAPTURE_REQUIRED_FIELDS,
        )
        raw_gaps = _property_gaps(
            self.vault_path,
            "_archive/raw",
            RAW_REQUIRED_FIELDS,
        )
        return {
            "capture_notes_scanned": _markdown_count(self.vault_path / "captures"),
            "raw_notes_scanned": _markdown_count(self.vault_path / "_archive" / "raw"),
            "capture_gap_count": len(capture_gaps),
            "raw_gap_count": len(raw_gaps),
            "capture_gaps": capture_gaps[:20],
            "raw_gaps": raw_gaps[:20],
        }

    def maintain_property_gaps(self, *, apply: bool = False) -> dict[str, Any]:
        before = self.audit_property_gaps()
        actions = self._property_backfill_actions()
        applied_count = 0

        if apply:
            for action in actions:
                if _apply_property_backfill(self.vault_path, action):
                    applied_count += 1

        return {
            "dry_run": not apply,
            "planned_action_count": len(actions),
            "applied_action_count": applied_count,
            "planned_actions": actions[:50],
            "before": before,
            "after": self.audit_property_gaps() if apply else before,
        }

    def _property_backfill_actions(self) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        raw_capture_ids = self._raw_capture_id_map()

        for path in sorted((self.vault_path / "captures").glob("*.md")):
            rel_path = path.relative_to(self.vault_path).as_posix()
            parsed = _read_markdown_parts(path)
            frontmatter = parsed[0] if parsed else {}
            fields = _missing_field_values(
                frontmatter,
                {"type": "capture", "status": "processed"},
            )
            if fields:
                actions.append(
                    {
                        "type": "backfill_capture_properties",
                        "file_path": rel_path,
                        "fields": fields,
                    }
                )

        for path in sorted((self.vault_path / "_archive" / "raw").glob("*.md")):
            rel_path = path.relative_to(self.vault_path).as_posix()
            parsed = _read_markdown_parts(path)
            frontmatter = parsed[0] if parsed else {}
            defaults = {"type": "raw", "status": "archived"}
            capture_id = raw_capture_ids.get(rel_path)
            if capture_id:
                defaults["capture_id"] = capture_id
            fields = _missing_field_values(frontmatter, defaults)
            if fields:
                actions.append(
                    {
                        "type": "backfill_raw_properties",
                        "file_path": rel_path,
                        "fields": fields,
                    }
                )

        return actions

    def maintain_capture(
        self,
        capture_id: str,
        *,
        apply: bool = False,
        render_outline: bool = True,
        force_directory: bool = False,
    ) -> dict[str, Any]:
        """Run Library maintenance only for one newly committed capture."""
        report = {
            "dry_run": not apply,
            "capture_id": capture_id,
            "property_gaps": {
                "planned_action_count": 0,
                "applied_action_count": 0,
                "planned_actions": [],
            },
            "outlines": {
                "dry_run": not apply,
                "scanned": 0,
                "needs_outline": 0,
                "updated": 0,
                "skipped_short": 0,
                "skipped_existing": 0,
                "errors": 0,
                "items": [],
            },
            "embedding_index": {
                "dry_run": not apply,
                "scanned": 0,
                "missing_or_stale": 0,
                "updated": 0,
                "skipped_missing_file": 0,
                "errors": 0,
                "items": [],
            },
            "pipeline": {
                "directory_rendered": False,
                "outline_metadata_ready": False,
                "heading_count": 0,
                "section_embedding_count": 0,
                "identity_embedding_ready": False,
                "rag_section_ready": False,
                "warnings": [],
            },
        }
        conn = self._index_db_conn()
        if conn is None:
            report["skipped"] = True
            report["reason"] = "db_not_configured"
            return report
        row = conn.execute(
            "SELECT id, file_path, raw_path FROM captures WHERE id = ?",
            (capture_id,),
        ).fetchone()
        if row is None:
            report["skipped"] = True
            report["reason"] = "capture_not_found"
            return report

        file_path = str(row["file_path"] or "")
        raw_path = str(row["raw_path"] or "")
        target_paths = {path for path in (file_path, raw_path) if path}
        actions = [
            action
            for action in self._property_backfill_actions()
            if str(action.get("file_path") or "") in target_paths
        ]
        applied_count = 0
        if apply:
            for action in actions:
                if _apply_property_backfill(self.vault_path, action):
                    applied_count += 1
        report["property_gaps"] = {
            "planned_action_count": len(actions),
            "applied_action_count": applied_count,
            "planned_actions": actions,
        }

        if file_path:
            note_path = self.vault_path / file_path
            if note_path.exists():
                report["outlines"]["scanned"] += 1
                try:
                    self._maintain_one(
                        note_path,
                        file_path,
                        apply,
                        report["outlines"],
                        render_outline=render_outline,
                        force_directory=force_directory,
                    )
                except Exception as exc:  # pragma: no cover - defensive per-note isolation
                    report["outlines"]["errors"] += 1
                    report["outlines"]["items"].append(
                        {"file_path": file_path, "action": "error", "error": str(exc)}
                    )

        report["embedding_index"] = self._maintain_embedding_index_rows([row], apply=apply)
        report["pipeline"] = self._capture_pipeline_status(capture_id, file_path, report["outlines"])
        return report

    def _raw_capture_id_map(self) -> dict[str, str]:
        conn = self.db_conn
        should_close = False
        if conn is None:
            if self.db_path is None or not self.db_path.exists():
                return {}
            conn = sqlite3.connect(str(self.db_path))
            should_close = True
        try:
            rows = conn.execute(
                "SELECT id, raw_path FROM captures "
                "WHERE raw_path IS NOT NULL AND raw_path != ''"
            ).fetchall()
        except sqlite3.Error:
            return {}
        try:
            return {str(raw_path): str(capture_id) for capture_id, raw_path in rows}
        finally:
            if should_close:
                conn.close()

    def maintain_outlines(self, *, apply: bool = False) -> dict[str, Any]:
        report: dict[str, Any] = {
            "dry_run": not apply,
            "scanned": 0,
            "needs_outline": 0,
            "updated": 0,
            "skipped_short": 0,
            "skipped_existing": 0,
            "errors": 0,
            "items": [],
        }

        captures_dir = self.vault_path / "captures"
        if not captures_dir.exists():
            return report

        for note_path in sorted(captures_dir.glob("*.md")):
            rel_path = note_path.relative_to(self.vault_path).as_posix()
            report["scanned"] += 1
            try:
                self._maintain_one(note_path, rel_path, apply, report)
            except Exception as exc:  # pragma: no cover - defensive per-note isolation
                report["errors"] += 1
                report["items"].append(
                    {"file_path": rel_path, "action": "error", "error": str(exc)}
                )

        return report

    def maintain_embedding_index(self, *, apply: bool = False) -> dict[str, Any]:
        conn = self._index_db_conn()
        if conn is None:
            return {
                "dry_run": not apply,
                "scanned": 0,
                "missing_or_stale": 0,
                "updated": 0,
                "skipped_missing_file": 0,
                "errors": 0,
                "items": [],
                "skipped": True,
                "reason": "db_not_configured",
            }
        rows = conn.execute(
            "SELECT id, file_path FROM captures ORDER BY created_at ASC"
        ).fetchall()
        return self._maintain_embedding_index_rows(rows, apply=apply)

    def repair_long_capture_directories(self, *, apply: bool = False) -> dict[str, Any]:
        """Audit or repair committed long captures whose visible body lacks a usable directory."""
        report: dict[str, Any] = {
            "dry_run": not apply,
            "scanned": 0,
            "needs_repair": 0,
            "repaired": 0,
            "skipped": 0,
            "errors": 0,
            "items": [],
        }
        conn = self._index_db_conn()
        if conn is None:
            return {
                **report,
                "skipped": True,
                "reason": "db_not_configured",
            }

        metadata_repo = CaptureMetadataRepo(conn)
        rows = conn.execute("SELECT id, file_path FROM captures ORDER BY created_at ASC").fetchall()
        for row in rows:
            capture_id = str(row["id"] or "")
            file_path = str(row["file_path"] or "")
            if not file_path:
                continue
            note_path = self.vault_path / file_path
            if not note_path.exists():
                report["skipped"] += 1
                report["items"].append(
                    {"capture_id": capture_id, "file_path": file_path, "action": "missing_file"}
                )
                continue
            parsed = _read_markdown_parts(note_path)
            if parsed is None:
                report["errors"] += 1
                report["items"].append(
                    {"capture_id": capture_id, "file_path": file_path, "action": "parse_error"}
                )
                continue
            frontmatter, body = parsed
            if len(body) < self.min_outline_chars:
                continue
            report["scanned"] += 1
            metadata = metadata_repo.get(capture_id) or {}
            outline = metadata.get("outline") or []
            augmented_outline = _augment_outline_with_explicit_sections(body, outline)
            needs_repair = (
                not _has_directory(body)
                or not _directory_block_is_title_only_links(body)
                or _has_directory_content_heading_coverage_gap(body)
                or not _directory_links_match_content_headings(body)
                or not _directory_absolute_url_free(body)
                or _has_capture_processing_note(body)
            )
            if not needs_repair:
                continue
            report["needs_repair"] += 1
            if not apply:
                report["items"].append(
                    {
                        "capture_id": capture_id,
                        "file_path": file_path,
                        "action": "needs_directory_repair",
                        "missing_directory": not _has_directory(body),
                        "has_directory_coverage_gap": _has_directory_content_heading_coverage_gap(body),
                        "directory_link_ready": _directory_links_match_content_headings(body),
                        "directory_absolute_url_free": _directory_absolute_url_free(body),
                        "has_processing_note": _has_capture_processing_note(body),
                    }
                )
                continue
            if not augmented_outline and not _has_real_markdown_sections(body):
                report["skipped"] += 1
                report["items"].append(
                    {
                        "capture_id": capture_id,
                        "file_path": file_path,
                        "action": "skipped_no_safe_outline",
                    }
                )
                continue
            render_result = render_capture_body(body, augmented_outline, render_directory=True)
            render_result = _ensure_directory_covers_markdown_headings(render_result)
            if not render_result.directory_rendered:
                report["skipped"] += 1
                report["items"].append(
                    {
                        "capture_id": capture_id,
                        "file_path": file_path,
                        "action": "skipped_directory_not_rendered",
                        "warnings": render_result.warnings,
                    }
                )
                continue
            backup_path = _backup_markdown_file(note_path, "long-directory")
            _write_markdown_parts(note_path, frontmatter, render_result.capture_body)
            metadata_repo.upsert_outline(
                capture_id,
                render_result.outline_metadata,
                source_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                directory_rendered_at=datetime.now(timezone.utc).isoformat(),
            )
            synced_drafts = _sync_committed_capture_drafts(
                conn,
                capture_id,
                render_result.capture_body,
            )
            report["repaired"] += 1
            item = {
                "capture_id": capture_id,
                "file_path": file_path,
                "action": "repaired_directory",
                "backup_path": str(backup_path),
                "synced_committed_drafts": synced_drafts,
            }
            if render_result.warnings:
                item["warnings"] = render_result.warnings
            report["items"].append(item)
        return report

    def _maintain_embedding_index_rows(self, rows, *, apply: bool = False) -> dict[str, Any]:
        report: dict[str, Any] = {
            "dry_run": not apply,
            "scanned": 0,
            "missing_or_stale": 0,
            "updated": 0,
            "skipped_missing_file": 0,
            "errors": 0,
            "items": [],
        }

        conn = self._index_db_conn()
        if conn is None:
            return {
                **report,
                "skipped": True,
                "reason": "db_not_configured",
            }

        repo = CaptureEmbeddingRepo(conn)
        for row in rows:
            capture_id = str(row["id"])
            file_path = str(row["file_path"] or "")
            if not file_path:
                continue
            note_path = self.vault_path / file_path
            if not note_path.exists():
                report["skipped_missing_file"] += 1
                continue
            parsed = _read_markdown_parts(note_path)
            if parsed is None:
                report["errors"] += 1
                report["items"].append(
                    {"capture_id": capture_id, "file_path": file_path, "action": "parse_error"}
                )
                continue

            report["scanned"] += 1
            frontmatter, body = parsed
            metadata = CaptureMetadataRepo(conn).get(capture_id)
            metadata_outline = metadata.get("outline") if metadata else None
            index_items = [
                build_identity_index(
                    capture_id,
                    file_path,
                    frontmatter,
                    body,
                    outline=metadata_outline,
                ),
                *build_section_indexes(
                    capture_id,
                    file_path,
                    frontmatter,
                    body,
                    outline=metadata_outline,
                ),
            ]
            current_keys = {(item.kind, item.section_id) for item in index_items}
            stale_deleted = 0
            if apply:
                stale_deleted = repo.delete_missing_for_capture(capture_id, current_keys)
            stale_items = [
                item for item in index_items
                if repo.needs_reindex(
                    item.capture_id,
                    item.kind,
                    item.section_id,
                    item.source_hash,
                )
            ]
            if not stale_items and not stale_deleted:
                continue

            report["missing_or_stale"] += len(stale_items) + stale_deleted
            if not apply:
                report["items"].append(
                    {
                        "capture_id": capture_id,
                        "file_path": file_path,
                        "action": "needs_embedding_index",
                        "items": len(stale_items),
                    }
                )
                continue
            if stale_deleted:
                report["items"].append(
                    {
                        "capture_id": capture_id,
                        "file_path": file_path,
                        "action": "deleted_stale_embedding_rows",
                        "items": stale_deleted,
                    }
                )

            if self.embedder is None:
                report["errors"] += 1
                report["items"].append(
                    {
                        "capture_id": capture_id,
                        "file_path": file_path,
                        "action": "embedding_error",
                        "error": "embedder_not_configured",
                        "items": len(stale_items),
                    }
                )
                continue

            for item in stale_items:
                try:
                    embedding = self.embedder.embed(item.index_text)
                    repo.upsert(
                        capture_id=item.capture_id,
                        kind=item.kind,
                        section_id=item.section_id,
                        section_title=item.section_title,
                        index_text=item.index_text,
                        embedding=embedding,
                        source_hash=item.source_hash,
                        file_path=item.file_path,
                    )
                    report["updated"] += 1
                except Exception as exc:
                    report["errors"] += 1
                    report["items"].append(
                        {
                            "capture_id": capture_id,
                            "file_path": file_path,
                            "action": "embedding_error",
                            "error": str(exc),
                            "kind": item.kind,
                            "section_id": item.section_id,
                        }
                    )

        return report

    def _capture_pipeline_status(
        self,
        capture_id: str,
        file_path: str,
        outlines_report: dict[str, Any],
    ) -> dict[str, Any]:
        conn = self._index_db_conn()
        note_path = self.vault_path / file_path if file_path else None
        body = ""
        if note_path and note_path.exists():
            parsed = _read_markdown_parts(note_path)
            if parsed is not None:
                _frontmatter, body = parsed
        metadata = CaptureMetadataRepo(conn).get(capture_id) if conn is not None else None
        rows = []
        if conn is not None:
            rows = conn.execute(
                """SELECT kind, section_title, embedding
                   FROM capture_embeddings
                   WHERE capture_id = ?""",
                (capture_id,),
            ).fetchall()
        section_count = len([row for row in rows if row["kind"] == "section"])
        identity_ready = any(row["kind"] == "identity" and row["embedding"] is not None for row in rows)
        warnings: list[str] = []
        for item in outlines_report.get("items") or []:
            warnings.extend(str(warning) for warning in item.get("warnings") or [])
        directory_heading_coverage_ready = not _has_directory_content_heading_coverage_gap(body)
        directory_link_ready = _directory_links_match_content_headings(body)
        directory_absolute_url_free = _directory_absolute_url_free(body)
        has_processing_note = _has_capture_processing_note(body)
        if not directory_heading_coverage_ready:
            warnings.append("capture_body_directory_heading_coverage_gap")
        if _has_directory(body) and not directory_link_ready:
            warnings.append("capture_body_directory_link_mismatch")
        if not directory_absolute_url_free:
            warnings.append("capture_body_directory_absolute_url")
        if has_processing_note:
            warnings.append("capture_body_processing_note")
        return {
            "directory_rendered": _has_directory(body),
            "directory_heading_coverage_ready": directory_heading_coverage_ready,
            "directory_link_ready": directory_link_ready,
            "directory_absolute_url_free": directory_absolute_url_free,
            "processing_note_free": not has_processing_note,
            "outline_metadata_ready": bool(metadata and metadata.get("outline")),
            "heading_count": len(_markdown_section_titles(body)),
            "section_embedding_count": section_count,
            "identity_embedding_ready": identity_ready,
            "rag_section_ready": section_count > 0,
            "warnings": warnings,
        }

    def _index_db_conn(self) -> sqlite3.Connection | None:
        if self.db_conn is not None:
            return self.db_conn
        if self.db_path is None:
            return None
        self.db_conn = init_db(str(self.db_path))
        return self.db_conn

    def _metadata_repo(self) -> CaptureMetadataRepo | None:
        conn = self._index_db_conn()
        return CaptureMetadataRepo(conn) if conn is not None else None

    def _capture_id_for_file(self, rel_path: str, frontmatter: dict[str, Any]) -> str:
        frontmatter_id = str(frontmatter.get("id") or "")
        if frontmatter_id:
            return frontmatter_id
        conn = self._index_db_conn()
        if conn is None:
            return ""
        row = conn.execute(
            "SELECT id FROM captures WHERE file_path = ?",
            (rel_path,),
        ).fetchone()
        return str(row["id"]) if row else ""

    def _maintain_one(
        self,
        note_path: Path,
        rel_path: str,
        apply: bool,
        report: dict[str, Any],
        *,
        render_outline: bool = True,
        force_directory: bool = False,
    ) -> None:
        parsed = _read_markdown_parts(note_path)
        if parsed is None:
            report["errors"] += 1
            report["items"].append(
                {"file_path": rel_path, "action": "parse_error"}
            )
            return

        frontmatter, body = parsed
        legacy_outline = frontmatter.get("outline") or []
        is_short = len(body) < self.min_outline_chars
        has_markdown_sections = _has_real_markdown_sections(body)
        if is_short and not force_directory and not legacy_outline and not has_markdown_sections:
            report["skipped_short"] += 1
            return

        metadata_repo = self._metadata_repo()
        capture_id = self._capture_id_for_file(rel_path, frontmatter)
        metadata = metadata_repo.get(capture_id) if metadata_repo and capture_id else None
        metadata_outline = metadata.get("outline") if metadata else []
        outline = metadata_outline or legacy_outline
        has_outline = bool(outline)
        has_directory = _has_directory(body)
        has_pseudo_heading_duplicates = _has_adjacent_outline_pseudo_heading_duplicates(body, outline)
        has_outline_body_artifacts = _has_outline_body_artifacts(body, outline)
        has_directory_body_artifacts = has_directory and not _directory_block_is_title_only_links(body)
        has_directory_coverage_gap = _has_directory_content_heading_coverage_gap(body)
        has_directory_link_gap = has_directory and not _directory_links_match_content_headings(body)
        has_directory_absolute_url = has_directory and not _directory_absolute_url_free(body)
        has_processing_note = _has_capture_processing_note(body)
        if (
            metadata_outline
            and not legacy_outline
            and has_directory
            and not has_pseudo_heading_duplicates
            and not has_outline_body_artifacts
            and not has_directory_body_artifacts
            and not has_directory_coverage_gap
            and not has_directory_link_gap
            and not has_directory_absolute_url
            and not has_processing_note
        ):
            report["skipped_existing"] += 1
            return

        report["needs_outline"] += 1
        if not apply:
            report["items"].append(
                {
                    "file_path": rel_path,
                    "action": "needs_outline",
                    "missing_metadata_outline": not has_outline,
                    "legacy_frontmatter_outline": bool(legacy_outline),
                    "missing_directory": not has_directory,
                    "has_directory_coverage_gap": has_directory_coverage_gap,
                    "has_directory_link_gap": has_directory_link_gap,
                    "has_directory_absolute_url": has_directory_absolute_url,
                    "has_processing_note": has_processing_note,
                    "has_outline_body_artifacts": has_outline_body_artifacts,
                    "has_directory_body_artifacts": has_directory_body_artifacts,
                    "body_chars": len(body),
                }
            )
            return

        if not outline:
            if is_short and has_markdown_sections:
                outline = _augment_outline_with_explicit_sections(body, [])
            else:
                try:
                    outline = self.outliner.generate(body, len(body))
                except Exception as exc:
                    report["errors"] += 1
                    report["items"].append(
                        {
                            "file_path": rel_path,
                            "action": "outline_error",
                            "error": str(exc),
                        }
                    )
                    return

        if not outline:
            report["items"].append(
                {"file_path": rel_path, "action": "outline_empty", "body_chars": len(body)}
            )
            return
        outline = _augment_outline_with_explicit_sections(body, outline)
        render_result = (
            render_capture_body(body, outline, render_directory=force_directory or not is_short)
            if render_outline
            else None
        )
        if render_result is not None:
            render_result = _ensure_directory_covers_markdown_headings(render_result)
        metadata_outline = render_result.outline_metadata if render_result else outline

        if metadata_repo and capture_id:
            metadata_repo.upsert_outline(
                capture_id,
                metadata_outline,
                source_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                directory_rendered_at=(
                    datetime.now(timezone.utc).isoformat()
                    if render_result and _has_directory(render_result.capture_body)
                    else None
                ),
            )
        if not render_outline:
            if legacy_outline:
                frontmatter.pop("outline", None)
                _write_markdown_parts(note_path, frontmatter, body)
            report["updated"] += 1
            report["items"].append(
                {
                    "file_path": rel_path,
                    "action": "updated_outline_metadata",
                    "outline_items": len(metadata_outline),
                }
            )
            return
        frontmatter.pop("outline", None)
        if render_result is None:
            render_result = render_capture_body(body, outline, render_directory=not is_short)
            render_result = _ensure_directory_covers_markdown_headings(render_result)
        updated_body = render_result.capture_body
        _write_markdown_parts(note_path, frontmatter, updated_body)
        report["updated"] += 1
        item = {
            "file_path": rel_path,
            "action": "updated_outline",
            "outline_items": len(render_result.outline_metadata),
        }
        if render_result.warnings:
            item["warnings"] = render_result.warnings
        report["items"].append(item)


def render_capture_body(
    body: str,
    outline: list[dict],
    *,
    render_directory: bool = True,
) -> CaptureBodyRenderResult:
    """Render a readable Capture Body without changing source prose."""
    warnings: list[str] = []
    cleaned_body, noise_warnings = strip_capture_noise_blocks(body)
    warnings.extend(noise_warnings)

    outline_metadata = _augment_outline_with_explicit_sections(cleaned_body, outline)
    sections = detect_explicit_sections(cleaned_body)
    bound_outline, bind_warnings = bind_outline_to_sections(outline_metadata, sections)
    warnings.extend(bind_warnings)

    if _should_render_from_paragraph_indexes(sections, bound_outline, outline_metadata):
        bound_outline, paragraph_warnings = bind_outline_to_paragraphs(
            cleaned_body,
            outline_metadata,
        )
        warnings.extend(paragraph_warnings)
        updated_body = _render_body_from_paragraph_indexes(
            cleaned_body,
            bound_outline,
            render_directory=render_directory,
        )
    else:
        bound_outline = _assign_outline_anchor_ids(bound_outline)
        updated_body = _render_body_from_explicit_sections(
            cleaned_body,
            bound_outline,
            sections,
            render_directory=render_directory,
        )
    updated_body = _remove_adjacent_outline_pseudo_heading_duplicates(updated_body, bound_outline)
    updated_body = _cleanup_outline_body_artifacts(updated_body, bound_outline)

    validation_warnings = validate_capture_body_structure(
        cleaned_body,
        updated_body,
        bound_outline,
    )
    warnings.extend(validation_warnings)
    if validation_warnings:
        updated_body = _remove_directory_block(cleaned_body).strip()
        bound_outline = []
    updated_body = _restore_readability_emphasis(cleaned_body, updated_body)
    return CaptureBodyRenderResult(
        updated_body,
        _outline_metadata_for_storage(outline_metadata, bound_outline),
        warnings,
        directory_rendered=_has_directory(updated_body),
        heading_count=len(_markdown_section_titles(updated_body)),
    )


def _restore_readability_emphasis(source_body: str, rendered_body: str) -> str:
    if re.search(r"\*\*[^*\n]+\*\*", rendered_body or ""):
        return rendered_body
    source_phrases = [
        match.group(1).strip()
        for match in re.finditer(r"\*\*([^*\n]+)\*\*", source_body or "")
        if match.group(1).strip()
    ]
    if not source_phrases:
        return rendered_body

    lines = (rendered_body or "").splitlines()
    prose_indexes = [
        index
        for index, line in enumerate(lines)
        if line.strip()
        and not re.match(r"^(?:#{1,6}\s+|[-*+]\s+|>\s*|\|)", line.strip())
        and not re.match(r"^\d+[.)、]\s*", line.strip())
    ]
    for phrase in source_phrases:
        for index in prose_indexes:
            start = lines[index].find(phrase)
            if start >= 0:
                lines[index] = (
                    f"{lines[index][:start]}**{phrase}**"
                    f"{lines[index][start + len(phrase):]}"
                )
                return _join_rendered_lines(lines, rendered_body)

    for index in prose_indexes:
        stripped = lines[index].strip()
        phrase = re.split(r"[。！？!?；;]", stripped, maxsplit=1)[0].strip()
        if len(phrase) > 48:
            phrase = phrase[:48].rstrip()
        if len(phrase) < 4:
            continue
        start = lines[index].find(phrase)
        lines[index] = (
            f"{lines[index][:start]}**{phrase}**"
            f"{lines[index][start + len(phrase):]}"
        )
        return _join_rendered_lines(lines, rendered_body)
    return rendered_body


def _join_rendered_lines(lines: list[str], original: str) -> str:
    result = "\n".join(lines)
    return result + "\n" if original.endswith("\n") else result


def _read_markdown_parts(path: Path) -> tuple[dict[str, Any], str] | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter = yaml.safe_load(parts[1]) or {}
    body = parts[2]
    if body.startswith("\n\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    return frontmatter, body


def _write_markdown_parts(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    content = (
        "---\n"
        + yaml.dump(frontmatter, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + body
    )
    path.write_text(content, encoding="utf-8")


def _backup_markdown_file(path: Path, label: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_path = path.with_suffix(path.suffix + f".bak-{label}-{timestamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def _sync_committed_capture_drafts(
    conn: sqlite3.Connection,
    capture_id: str,
    body: str,
) -> int:
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(capture_drafts)").fetchall()
    }
    if not {"committed_capture_id", "draft_text", "word_count"}.issubset(columns):
        return 0
    updated_at_sql = ", updated_at = datetime('now')" if "updated_at" in columns else ""
    cursor = conn.execute(
        f"""UPDATE capture_drafts
            SET draft_text = ?, word_count = ?{updated_at_sql}
            WHERE committed_capture_id = ?
              AND COALESCE(status, '') = 'committed'""",
        (body, len(body), capture_id),
    )
    conn.commit()
    return int(cursor.rowcount or 0)


def _has_directory(body: str) -> bool:
    return re.search(r"^##\s+目录纲要\s*$", body, flags=re.MULTILINE) is not None


def _has_real_markdown_sections(body: str) -> bool:
    return bool(_markdown_section_titles(body))


def _insert_directory(body: str, outline: list[dict]) -> str:
    directory = _render_directory(outline)
    h1 = re.match(r"^(#\s+.+?)(\n{2,}|\n|$)", body, flags=re.DOTALL)
    if h1:
        start = h1.group(0)
        rest = body[h1.end() :]
        return f"{start.rstrip()}\n\n{directory}\n\n{rest.lstrip()}"
    return f"{directory}\n\n{body}"


def _render_directory(outline: list[dict]) -> str:
    lines = [DIRECTORY_HEADING, ""]
    levels: list[int] = []
    for item in outline:
        try:
            levels.append(max(int(item.get("level") or 1), 1))
        except (TypeError, ValueError):
            levels.append(1)
    base_level = min(levels, default=1)
    seen_anchors: dict[str, int] = {}
    for item in outline:
        try:
            level = max(int(item.get("level") or 1), 1)
        except (TypeError, ValueError):
            level = 1
        indent = "  " * max(level - base_level, 0)
        title = _single_line(str(item.get("title") or "未命名章节"))
        anchor_id = _single_line(str(item.get("anchor_id") or ""))
        if not anchor_id:
            base_anchor = _heading_anchor_id(title)
            count = seen_anchors.get(base_anchor, 0) + 1
            seen_anchors[base_anchor] = count
            anchor_id = base_anchor if count == 1 else f"{base_anchor}-{count}"
        line = f"{indent}- [{_escape_markdown_link_text(title)}](#{anchor_id})"
        lines.append(line)
    return "\n".join(lines)


def _escape_markdown_link_text(title: str) -> str:
    return title.replace("[", r"\[").replace("]", r"\]")


def _heading_anchor_id(title: str) -> str:
    text = _single_line(re.sub(r"`([^`]+)`", r"\1", title or ""))
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    normalized = unicodedata.normalize("NFKC", text).strip()
    parts: list[str] = []
    last_was_dash = False
    for char in normalized:
        category = unicodedata.category(char)
        if category[:1] in {"L", "N"}:
            parts.append(char)
            last_was_dash = False
        elif not last_was_dash:
            parts.append("-")
            last_was_dash = True
    anchor = "".join(parts).strip("-")
    return anchor or "section"


def _assign_outline_anchor_ids(outline: list[dict]) -> list[dict]:
    seen: dict[str, int] = {}
    assigned: list[dict] = []
    for item in outline:
        normalized = dict(item)
        title = _single_line(str(normalized.get("title") or "未命名章节"))
        base = _heading_anchor_id(title)
        count = seen.get(base, 0) + 1
        seen[base] = count
        normalized["anchor_id"] = base if count == 1 else f"{base}-{count}"
        assigned.append(normalized)
    return assigned


def detect_explicit_sections(body: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    lines = body.splitlines()
    directory_indexes = _directory_line_indexes(lines)
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^(```|~~~)", stripped):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if index in directory_indexes:
            continue
        title = _explicit_section_title(line)
        if not title:
            continue
        sections.append(
            {
                "line_index": index,
                "title": title,
                "level": _explicit_section_level_from_line(line),
                "source_line": line,
            }
        )
    return sections


def bind_outline_to_sections(
    outline: list[dict],
    sections: list[dict[str, Any]],
) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    bound: list[dict] = []
    used_outline_indexes: set[int] = set()
    for section in sections:
        section_title = str(section.get("title") or "")
        match_index = _matching_outline_index(section_title, outline, used_outline_indexes)
        if match_index is None:
            continue
        item = dict(outline[match_index])
        item["title"] = _single_line(str(item.get("title") or section_title))
        item["level"] = int(section.get("level") or item.get("level") or 1)
        item["_section_line_index"] = int(section["line_index"])
        item["_heading_markdown_level"] = int(section.get("level") or item.get("level") or 1)
        item["source"] = item.get("source") or "heading"
        bound.append(item)
        used_outline_indexes.add(match_index)
    unbound_count = len(
        [
            item
            for index, item in enumerate(outline)
            if index not in used_outline_indexes and str(item.get("title") or "").strip()
        ]
    )
    if unbound_count:
        warnings.append(f"capture_body_unbound_outline_items:{unbound_count}")
    return bound, warnings


def strip_capture_noise_blocks(body: str) -> tuple[str, list[str]]:
    if not body:
        return body, []
    lines = body.splitlines()
    kept: list[str] = []
    removed = 0
    stop_at_tail = False
    for line in lines:
        original_line = line
        line = _strip_noise_prefix(line)
        if line != original_line:
            removed += 1
        stripped = line.strip()
        if _is_capture_processing_note_line(stripped):
            removed += 1
            continue
        if not stripped:
            kept.append(line)
            continue
        tail_index = _noise_tail_start_index(line)
        if tail_index is not None:
            prefix = line[:tail_index].rstrip()
            if prefix:
                kept.append(prefix)
            removed += 1
            stop_at_tail = True
            break
        if _is_high_confidence_noise_line(stripped):
            removed += 1
            continue
        kept.append(line)
    if not stop_at_tail and kept == lines:
        return body, []
    cleaned = "\n".join(kept)
    cleaned, inline_removed = _strip_inline_noise_fragments(cleaned)
    removed += inline_removed
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if body.endswith("\n") and cleaned:
        cleaned += "\n"
    warnings = [f"capture_body_noise_blocks_removed:{removed}"] if removed else []
    return cleaned, warnings


def _strip_noise_prefix(line: str) -> str:
    cleaned = re.sub(
        r"^[▲▼▽△\s]*(?:戳|点|点击).{0,12}(?:蓝色字|名片).{0,12}关注(?:我们|[^！!。]{0,12})[！!]?[“\"']?",
        "",
        line,
    )
    return cleaned.lstrip() if cleaned != line else line


def _is_capture_processing_note_line(stripped: str) -> bool:
    if not stripped:
        return False
    stripped = re.sub(r"^>\s*", "", stripped).strip()
    cleaned = re.sub(r"^\*\*\s*(.+?)\s*\*\*$", r"\1", stripped)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned).strip()
    return bool(
        re.match(
            r"^整理说明\s*[：:].{0,80}(?:音频转写|可读性整理稿|只修正明显|尽量保留原文)",
            cleaned,
        )
        or re.match(
            r"^以下为.{0,30}(?:音频转写|转写后).{0,40}(?:可读性整理稿|整理稿)",
            cleaned,
        )
    )


def _has_capture_processing_note(body: str) -> bool:
    return any(_is_capture_processing_note_line(line.strip()) for line in (body or "").splitlines())


def _strip_inline_noise_fragments(text: str) -> tuple[str, int]:
    patterns = [
        r"[^。！？；\n]{0,60}\s+图源[:：][^一二三四五六七八九十0-9。！？；\n]{1,60}(?=[一二三四五六七八九十0-9]|$)",
    ]
    removed = 0
    cleaned = text
    for pattern in patterns:
        cleaned, count = re.subn(pattern, "", cleaned)
        removed += count
    return cleaned, removed


def validate_capture_body_structure(
    source_body: str,
    rendered_body: str,
    bound_outline: list[dict],
) -> list[str]:
    warnings: list[str] = []
    if not _structure_diff_safe(source_body, rendered_body):
        warnings.append("capture_body_render_unsafe_structure_diff")
    if bound_outline and not _directory_matches_headings(rendered_body, bound_outline):
        warnings.append("capture_body_directory_heading_mismatch")
    return warnings


def _render_body_from_explicit_sections(
    body: str,
    bound_outline: list[dict],
    sections: list[dict[str, Any]],
    *,
    render_directory: bool,
) -> str:
    body_without_directory = _remove_directory_block(body).strip()
    if not bound_outline:
        return body_without_directory

    original_lines = body.splitlines()
    directory_indexes = _directory_line_indexes(original_lines)
    content_lines = [
        line
        for index, line in enumerate(original_lines)
        if index not in directory_indexes
    ]
    original_to_content_index: dict[int, int] = {}
    content_index = 0
    for original_index, _line in enumerate(original_lines):
        if original_index in directory_indexes:
            continue
        original_to_content_index[original_index] = content_index
        content_index += 1

    bound_by_content_index = {
        original_to_content_index[int(item["_section_line_index"])]: item
        for item in bound_outline
        if int(item["_section_line_index"]) in original_to_content_index
    }

    rendered_lines: list[str] = []
    for index, line in enumerate(content_lines):
        item = bound_by_content_index.get(index)
        if item is None:
            rendered_lines.append(line)
            continue
        title = _single_line(str(item.get("title") or ""))
        if rendered_lines and rendered_lines[-1].strip():
            rendered_lines.append("")
        rendered_lines.append(f"{_heading_level(item)} {title}")
        rendered_lines.append("")

    rendered = "\n".join(rendered_lines)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
    if render_directory:
        rendered = _insert_directory(rendered, bound_outline)
    if body.endswith("\n"):
        rendered += "\n"
    return rendered


def _should_render_from_paragraph_indexes(
    sections: list[dict[str, Any]],
    bound_outline: list[dict],
    outline: list[dict],
) -> bool:
    if bound_outline:
        return False
    if len(sections) > 1:
        return False
    return any(_coerce_paragraph_index(item.get("start_paragraph_index")) is not None for item in outline)


def bind_outline_to_paragraphs(
    body: str,
    outline: list[dict],
) -> tuple[list[dict], list[str]]:
    paragraph_targets = _body_paragraph_targets(body)
    targets_by_index = {target["paragraph_index"]: target for target in paragraph_targets}
    bound: list[dict] = []
    last_index = 0
    for item in outline:
        title = _single_line(str(item.get("title") or ""))
        if not title:
            continue
        paragraph_index = _coerce_paragraph_index(item.get("start_paragraph_index"))
        if (
            paragraph_index is None
            or paragraph_index <= last_index
            or paragraph_index not in targets_by_index
        ):
            return [], ["capture_body_invalid_outline_paragraph_indexes"]
        normalized = dict(item)
        normalized["title"] = title
        normalized["level"] = _outline_level(item)
        normalized["_paragraph_line_index"] = targets_by_index[paragraph_index]["line_index"]
        normalized["source"] = "paragraph_index"
        bound.append(normalized)
        last_index = paragraph_index
    if not bound:
        return [], ["capture_body_invalid_outline_paragraph_indexes"]
    return _assign_outline_anchor_ids(bound), []


def _render_body_from_paragraph_indexes(
    body: str,
    bound_outline: list[dict],
    *,
    render_directory: bool,
) -> str:
    body_without_directory = _remove_directory_block(body).strip()
    if not bound_outline:
        return body_without_directory

    lines = body_without_directory.splitlines()
    bound_by_line_index = {
        int(item["_paragraph_line_index"]): item
        for item in bound_outline
        if "_paragraph_line_index" in item
    }
    rendered_lines: list[str] = []
    for index, line in enumerate(lines):
        item = bound_by_line_index.get(index)
        if item is not None:
            title = _single_line(str(item.get("title") or ""))
            if rendered_lines and rendered_lines[-1].strip():
                rendered_lines.append("")
            rendered_lines.append(f"{_heading_level(item)} {title}")
            rendered_lines.append("")
        rendered_lines.append(line)

    rendered = "\n".join(rendered_lines)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
    if render_directory:
        rendered = _insert_directory(rendered, bound_outline)
    if body.endswith("\n"):
        rendered += "\n"
    return rendered


def _body_paragraph_targets(body: str) -> list[dict[str, Any]]:
    body_without_directory = _remove_directory_block(body).strip()
    lines = body_without_directory.splitlines()
    code_indexes = _code_fence_line_indexes(lines)
    blocks: list[tuple[int, list[str]]] = []
    start: int | None = None
    current: list[str] = []
    for index, line in enumerate(lines):
        if line.strip():
            if start is None:
                start = index
            current.append(line)
            continue
        if start is not None:
            blocks.append((start, current))
            start = None
            current = []
    if start is not None:
        blocks.append((start, current))

    targets: list[dict[str, Any]] = []
    for start_index, block_lines in blocks:
        if any(start_index + offset in code_indexes for offset in range(len(block_lines))):
            continue
        first = next((line.strip() for line in block_lines if line.strip()), "")
        if not first or re.match(r"^#{1,6}\s+", first):
            continue
        if re.fullmatch(r"##\s+目录纲要", first):
            continue
        if _is_directory_item(first):
            continue
        targets.append(
            {
                "paragraph_index": len(targets) + 1,
                "line_index": start_index,
                "text": _single_line(" ".join(line.strip() for line in block_lines)),
            }
        )
    return targets


def _code_fence_line_indexes(lines: list[str]) -> set[int]:
    indexes: set[int] = set()
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^(```|~~~)", stripped):
            indexes.add(index)
            in_fence = not in_fence
            continue
        if in_fence:
            indexes.add(index)
    return indexes


def _coerce_paragraph_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(?:段)?\s*(\d+)\s*", value)
        if match:
            parsed = int(match.group(1))
            return parsed if parsed > 0 else None
    return None


def _matching_outline_index(
    section_title: str,
    outline: list[dict],
    used: set[int],
) -> int | None:
    for index, item in enumerate(outline):
        if index in used:
            continue
        title = _single_line(str(item.get("title") or ""))
        if title and _outline_title_has_existing_heading(section_title, {title}):
            return index
    return None


def _directory_matches_headings(body: str, bound_outline: list[dict]) -> bool:
    if not bound_outline:
        return not _has_directory(body)
    directory_titles = _directory_titles(body)
    heading_titles = _existing_markdown_section_titles(body)
    expected_titles = [_single_line(str(item.get("title") or "")) for item in bound_outline]
    return directory_titles == expected_titles and all(title in heading_titles for title in expected_titles)


def _directory_titles(body: str) -> list[str]:
    start, end = _directory_block_offsets(body)
    if start < 0:
        return []
    titles: list[str] = []
    for line in body[start:end].splitlines():
        stripped = line.strip()
        if not _is_directory_item(stripped):
            continue
        title = re.sub(r"^[-*+]\s+", "", stripped)
        titles.append(_single_line(_plain_directory_title(title)))
    return titles


def _directory_block_offsets(body: str) -> tuple[int, int]:
    lines = body.splitlines(keepends=True)
    in_directory = False
    saw_item = False
    offset = 0
    start = -1
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"##\s+目录纲要", stripped):
            in_directory = True
            saw_item = False
            start = offset
        elif in_directory and stripped and saw_item and not _is_directory_line(stripped):
            return start, offset

        if in_directory and _is_directory_item(stripped):
            saw_item = True
        offset += len(line)
    return (start, len(body)) if in_directory else (-1, 0)


def _directory_is_title_only(body: str, outline: list[dict]) -> bool:
    start, end = _directory_block_offsets(body)
    if start < 0:
        return False
    expected = _render_directory(outline).strip()
    actual = body[start:end].strip()
    return actual == expected


def _directory_block_is_title_only_links(body: str) -> bool:
    start, end = _directory_block_offsets(body)
    if start < 0:
        return False
    item_count = 0
    for line in body[start:end].splitlines():
        stripped = line.strip()
        if not _is_directory_item(stripped):
            continue
        item_count += 1
        item = re.sub(r"^[-*+]\s+", "", stripped)
        if not re.fullmatch(r"\[[^\]]+\]\(#[^)]+\)", item):
            return False
    return item_count > 0


def _directory_absolute_url_free(body: str) -> bool:
    start, end = _directory_block_offsets(body)
    if start < 0:
        return True
    for line in body[start:end].splitlines():
        stripped = line.strip()
        if not _is_directory_item(stripped):
            continue
        item = re.sub(r"^[-*+]\s+", "", stripped).strip()
        if re.search(r"\]\(\s*https?://", item, flags=re.IGNORECASE):
            return False
    return True


def _ensure_directory_covers_markdown_headings(
    render_result: CaptureBodyRenderResult,
) -> CaptureBodyRenderResult:
    body = render_result.capture_body
    if _directory_links_match_content_headings(body):
        return render_result
    heading_outline = _outline_from_existing_markdown_headings(body)
    if not heading_outline:
        return render_result
    updated_body = _insert_directory(_remove_directory_block(body).strip(), heading_outline)
    return CaptureBodyRenderResult(
        updated_body,
        _merge_outline_metadata_for_heading_directory(
            render_result.outline_metadata,
            heading_outline,
        ),
        [
            *render_result.warnings,
            "capture_body_directory_rebuilt_from_existing_headings",
        ],
        directory_rendered=_has_directory(updated_body),
        heading_count=len(_markdown_section_titles(updated_body)),
    )


def _directory_links_match_content_headings(body: str) -> bool:
    expected = [
        (
            _single_line(str(item.get("title") or "")),
            _single_line(str(item.get("anchor_id") or "")),
        )
        for item in _outline_from_existing_markdown_headings(body)
    ]
    if not expected:
        return not _has_directory(body)
    actual = _directory_link_items(body)
    return actual == expected


def _directory_link_items(body: str) -> list[tuple[str, str]]:
    start, end = _directory_block_offsets(body)
    if start < 0:
        return []
    items: list[tuple[str, str]] = []
    for line in body[start:end].splitlines():
        stripped = line.strip()
        if not _is_directory_item(stripped):
            continue
        item = re.sub(r"^[-*+]\s+", "", stripped)
        match = re.fullmatch(r"\[((?:\\.|[^\]])+)\]\(#([^)]+)\)", item)
        if not match:
            return []
        title = match.group(1).replace(r"\[", "[").replace(r"\]", "]").strip()
        anchor = match.group(2).strip()
        items.append((_single_line(title), _single_line(anchor)))
    return items


def _has_directory_content_heading_coverage_gap(body: str) -> bool:
    if not _has_directory(body):
        return False
    expected_titles = [
        _single_line(str(item.get("title") or ""))
        for item in _outline_from_existing_markdown_headings(body)
    ]
    if not expected_titles:
        return False
    return _directory_titles(body) != expected_titles


def _outline_from_existing_markdown_headings(body: str) -> list[dict]:
    items: list[dict] = []
    lines = (body or "").splitlines()
    directory_indexes = _directory_line_indexes(lines)
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^(```|~~~)", stripped):
            in_fence = not in_fence
            continue
        if in_fence or index in directory_indexes:
            continue
        match = re.match(r"^(#{2,3})\s+(.+?)\s*$", stripped)
        if not match:
            continue
        title = _single_line(match.group(2))
        if not title or _is_non_content_section_title(title):
            continue
        items.append(
            {
                "level": len(match.group(1)) - 1,
                "title": title,
                "summary": "",
                "source": "heading_scan",
            }
        )
    return _assign_outline_anchor_ids(items)


def _merge_outline_metadata_for_heading_directory(
    existing_metadata: list[dict],
    heading_outline: list[dict],
) -> list[dict]:
    existing_by_key: dict[str, list[dict]] = {}
    for item in existing_metadata:
        key = _normalize_outline_text(str(item.get("title") or ""))
        if key:
            existing_by_key.setdefault(key, []).append(item)

    merged: list[dict] = []
    for item in heading_outline:
        title = _single_line(str(item.get("title") or ""))
        key = _normalize_outline_text(title)
        existing = existing_by_key.get(key, [])
        source = dict(existing.pop(0)) if existing else {}
        source.update(
            {
                "level": _outline_level(item),
                "title": title,
                "anchor_id": _single_line(str(item.get("anchor_id") or "")),
                "source": source.get("source") or item.get("source") or "heading_scan",
            }
        )
        if "summary" not in source:
            source["summary"] = ""
        merged.append(_storage_outline_item(source))
    return merged


def _has_directory_primary_heading_coverage_gap(body: str) -> bool:
    if not _has_directory(body):
        return False
    primary_titles = _primary_markdown_section_titles(body)
    if not primary_titles:
        return False
    directory_titles = _directory_titles(body)
    search_start = 0
    for title in primary_titles:
        try:
            match_index = directory_titles.index(title, search_start)
        except ValueError:
            return True
        search_start = match_index + 1
    return False


def _primary_markdown_section_titles(body: str) -> list[str]:
    titles: list[str] = []
    lines = (body or "").splitlines()
    directory_indexes = _directory_line_indexes(lines)
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^(```|~~~)", stripped):
            in_fence = not in_fence
            continue
        if in_fence or index in directory_indexes:
            continue
        match = re.match(r"^##\s+(.+?)\s*$", stripped)
        if not match:
            continue
        title = _single_line(match.group(1))
        if _is_non_content_section_title(title):
            continue
        titles.append(title)
    return titles


def _directory_line_indexes(lines: list[str]) -> set[int]:
    indexes: set[int] = set()
    in_directory = False
    saw_item = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.fullmatch(r"##\s+目录纲要", stripped):
            in_directory = True
            saw_item = False
        if not in_directory:
            continue
        if index:
            if stripped and saw_item and not _is_directory_line(stripped):
                in_directory = False
                continue
            if _is_directory_item(stripped):
                saw_item = True
        indexes.add(index)
    return indexes


def _is_directory_line(stripped: str) -> bool:
    return not stripped or _is_directory_item(stripped)


def _is_directory_item(stripped: str) -> bool:
    return bool(re.match(r"^[-*+]\s+", stripped) or re.match(r"^\d+[.)、]\s+", stripped))


def _plain_directory_title(value: str) -> str:
    title = (value or "").strip()
    link = re.match(r"^\[((?:\\.|[^\]])+)\]\(#[^)]+\)(?:\s*[：:].*)?$", title)
    if link:
        return link.group(1).replace(r"\[", "[").replace(r"\]", "]").strip()
    return re.split(r"[：:]", title, maxsplit=1)[0].strip()


def _existing_markdown_section_titles(body: str) -> set[str]:
    return set(_markdown_section_titles(body))


def _markdown_section_titles(body: str) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    in_fence = False
    for line in (body or "").splitlines():
        stripped = line.strip()
        if re.match(r"^(```|~~~)", stripped):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^#{2,3}\s+(.+?)\s*$", stripped)
        if not match:
            continue
        title = _single_line(match.group(1))
        if title and title != "目录纲要" and title not in seen:
            titles.append(title)
            seen.add(title)
    return titles

def _has_adjacent_outline_pseudo_heading_duplicates(body: str, outline: list[dict]) -> bool:
    return _remove_adjacent_outline_pseudo_heading_duplicates(body, outline) != body


def _has_outline_body_artifacts(body: str, outline: list[dict]) -> bool:
    return _cleanup_outline_body_artifacts(body, outline) != body


def _cleanup_outline_body_artifacts(body: str, outline: list[dict]) -> str:
    if not body:
        return body
    lines = body.splitlines()
    directory_indexes = _directory_line_indexes(lines)
    updated: list[str] = []
    changed = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if index not in directory_indexes and re.fullmatch(r"#{1,6}", stripped):
            changed = True
            continue
        if (
            index not in directory_indexes
            and _outline_pseudo_line_has_adjacent_heading(lines, index, outline)
        ):
            changed = True
            continue
        if index not in directory_indexes and _is_bare_image_placeholder(stripped):
            updated.append(f"图片占位：{stripped}")
            changed = True
            continue
        updated.append(line)
    result = "\n".join(updated).rstrip()
    if body.endswith("\n"):
        result += "\n"
    else:
        result = result.rstrip()
    return result if changed else body


def _is_bare_image_placeholder(stripped: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?i)(?:image|img|screenshot|screen-shot|picture|photo)[-_ ]*\d*\.(?:png|jpe?g|gif|webp|heic)",
            stripped,
        )
    )


def _outline_pseudo_line_has_adjacent_heading(
    lines: list[str],
    index: int,
    outline: list[dict],
) -> bool:
    stripped = lines[index].strip()
    if not stripped or not _is_heading_like_line(stripped):
        return False
    if not _heading_matches_outline(stripped, outline):
        return False
    for direction in (-1, 1):
        adjacent = index + direction
        while 0 <= adjacent < len(lines) and not lines[adjacent].strip():
            adjacent += direction
        if not 0 <= adjacent < len(lines):
            continue
        heading_match = re.match(r"^#{2,3}\s+(.+?)\s*$", lines[adjacent].strip())
        if heading_match and _outline_title_match_keys(stripped) & _outline_title_match_keys(heading_match.group(1)):
            return True
    return False


def _remove_adjacent_outline_pseudo_heading_duplicates(body: str, outline: list[dict]) -> str:
    if not body or not outline:
        return body
    lines = body.splitlines()
    updated: list[str] = []
    index = 0
    changed = False
    while index < len(lines):
        line = lines[index]
        updated.append(line)
        heading_match = re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
        if not heading_match:
            index += 1
            continue
        heading_title = _single_line(heading_match.group(2))
        if heading_title == "目录纲要" or not _heading_matches_outline(heading_title, outline):
            index += 1
            continue
        duplicate_index = index + 1
        while duplicate_index < len(lines) and not lines[duplicate_index].strip():
            duplicate_index += 1
        if duplicate_index >= len(lines) or not _is_duplicate_pseudo_heading_line(
            lines[duplicate_index].strip(),
            heading_title,
        ):
            index += 1
            continue
        if updated[-1].strip():
            updated.append("")
        index = duplicate_index + 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        changed = True
    if not changed:
        return body
    result = "\n".join(updated).rstrip()
    if body.endswith("\n"):
        result += "\n"
    return result


def _heading_matches_outline(heading_title: str, outline: list[dict]) -> bool:
    return any(
        _outline_title_has_existing_heading(
            str(item.get("title") or ""),
            {heading_title},
        )
        for item in outline
        if str(item.get("title") or "").strip()
    )


def _is_duplicate_pseudo_heading_line(line: str, heading_title: str) -> bool:
    stripped = line.strip()
    bold = re.fullmatch(r"\*\*\s*(.+?)\s*\*\*", stripped)
    if bold:
        stripped = bold.group(1).strip()
    if _normalize_outline_text(stripped) == _normalize_outline_text(heading_title):
        return True
    if not _is_heading_like_line(stripped):
        return False
    if re.match(r"^#{1,6}\s+", stripped):
        return False
    return bool(_outline_title_match_keys(heading_title) & _outline_title_match_keys(stripped))


def _missing_outline_heading_titles(body: str, outline: list[dict]) -> list[str]:
    existing_titles = _existing_markdown_section_titles(body)
    missing: list[str] = []
    for item in outline:
        title = _single_line(str(item.get("title") or ""))
        if not title:
            continue
        if not _outline_title_has_existing_heading(title, existing_titles):
            missing.append(title)
    return missing


def _outline_title_has_existing_heading(title: str, existing_titles: set[str]) -> bool:
    title_keys = _outline_title_match_keys(title)
    for existing in existing_titles:
        existing_keys = _outline_title_match_keys(existing)
        if title_keys & existing_keys:
            return True
    return False


def _is_heading_like_line(stripped: str) -> bool:
    if not stripped or len(stripped) > 90:
        return False
    if stripped.startswith(("#", "-", "*", "+", ">", "|")):
        return False
    if re.search(r"[。！？；]$", stripped):
        return False
    return bool(
        re.match(
            r"^("
            r"(?:引言|导语|结语|写在最后)[：:]"
            r"|[一二三四五六七八九十]+读[：:]"
            r"|"
            r"[一二三四五六七八九十]+、"
            r"|[一二三四五六七八九十0-9]+\s+"
            r"|[（(]?[一二三四五六七八九十0-9]+[)）.、]"
            r"|第[一二三四五六七八九十零0-9]+[层部分章节幕]"
            r"|策略[一二三四五六七八九十0-9]+[：:]"
            r"|坑[一二三四五六七八九十0-9]+[：:]"
            r"|阶段[一二三四五六七八九十0-9]+[：:]"
            r")",
            stripped,
        )
    )


def _heading_level(item: dict) -> str:
    try:
        level = int(item.get("_heading_markdown_level") or item.get("level") or 1)
    except (TypeError, ValueError):
        level = 1
    return "##" if level <= 1 else "###"


def _outline_level(item: dict) -> int:
    try:
        level = int(item.get("level") or 1)
    except (TypeError, ValueError):
        level = 1
    return max(level, 1)


def _outline_metadata_for_storage(outline: list[dict], bound_outline: list[dict]) -> list[dict]:
    used_bound_indexes: set[int] = set()
    stored: list[dict] = []
    for item in outline:
        normalized = _storage_outline_item(item)
        title = str(normalized.get("title") or "")
        match_index = None
        for index, bound in enumerate(bound_outline):
            if index in used_bound_indexes:
                continue
            bound_title = _single_line(str(bound.get("title") or ""))
            if title and _outline_title_has_existing_heading(title, {bound_title}):
                match_index = index
                break
        if match_index is not None:
            bound = bound_outline[match_index]
            used_bound_indexes.add(match_index)
            if bound.get("anchor_id"):
                normalized["anchor_id"] = str(bound["anchor_id"])
            if bound.get("source"):
                normalized["source"] = str(bound["source"])
        stored.append(normalized)
    return stored


def _storage_outline_item(item: dict) -> dict:
    normalized = {
        "level": _outline_level(item),
        "title": _single_line(str(item.get("title") or "")),
        "summary": _single_line(str(item.get("summary") or "")),
    }
    if item.get("start_paragraph_index") is not None:
        normalized["start_paragraph_index"] = item.get("start_paragraph_index")
    if item.get("range"):
        normalized["range"] = _single_line(str(item.get("range") or ""))
    if item.get("anchor_id"):
        normalized["anchor_id"] = _single_line(str(item.get("anchor_id") or ""))
    if item.get("source"):
        normalized["source"] = _single_line(str(item.get("source") or ""))
    return normalized


def _outline_title_match_keys(title: str) -> set[str]:
    variants = _outline_title_variants(title)
    return {_normalize_outline_text(variant) for variant in variants if _normalize_outline_text(variant)}


def _outline_title_variants(title: str) -> list[str]:
    title = _single_line(title)
    variants = [title]
    without_prefix = _strip_outline_title_prefix(title)
    if without_prefix and without_prefix != title:
        variants.append(without_prefix)
    without_parenthetical = re.sub(r"\s*[（(][^）)]{1,80}[）)]\s*$", "", title).strip()
    if without_parenthetical and without_parenthetical != title:
        variants.append(without_parenthetical)
    for value in list(variants):
        head = re.split(r"[：:—-]{1,2}", value, maxsplit=1)[0].strip()
        if head and head != value:
            variants.append(head)
        tail = re.split(r"[：:—-]{1,2}", value, maxsplit=1)
        if len(tail) > 1 and tail[1].strip():
            variants.append(tail[1].strip())
    for value in list(variants):
        for part in re.split(r"[，,、；;]", value):
            part = part.strip()
            if part and part != value:
                variants.append(part)
        variants.append(value.replace("的", ""))
        variants.append(value.replace("学会用", "学会使用"))
        variants.append(value.replace("学会使用", "学会用"))
    if title.startswith(("附：", "附:")):
        variants.append("附上")
    return list(dict.fromkeys(item for item in variants if item))


def _strip_outline_title_prefix(title: str) -> str:
    return re.sub(
        r"^\s*(?:经验|策略|阶段|坑|附录?)[一二三四五六七八九十百千\d]*[：:、.．)）\-\s]*",
        "",
        title,
    ).strip()


def _normalize_outline_text(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text, flags=re.UNICODE).lower()


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _structure_diff_safe(source_body: str, rendered_body: str) -> bool:
    source_key = _body_fidelity_key(source_body)
    rendered_key = _body_fidelity_key(rendered_body)
    return not source_key or source_key in rendered_key


def _body_fidelity_key(body: str) -> str:
    without_directory = _remove_directory_block(body or "")
    lines = without_directory.splitlines()
    kept_lines: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            continue
        if _line_is_adjacent_heading_duplicate(lines, index):
            continue
        heading_candidate = _strip_bold_markers(stripped)
        if _is_heading_like_line(heading_candidate):
            continue
        kept_lines.append(stripped)
    text = "\n".join(kept_lines)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    return _normalize_outline_text(text)


def _remove_directory_block(body: str) -> str:
    start, end = _directory_block_offsets(body)
    if start < 0:
        return body
    return (body[:start] + body[end:]).strip()


def _strip_bold_markers(value: str) -> str:
    match = re.fullmatch(r"\*\*\s*(.+?)\s*\*\*", value.strip())
    return match.group(1).strip() if match else value


def _line_is_adjacent_heading_duplicate(lines: list[str], index: int) -> bool:
    stripped = _strip_bold_markers(lines[index].strip())
    if not stripped:
        return False
    for direction in (-1, 1):
        adjacent = index + direction
        while 0 <= adjacent < len(lines) and not lines[adjacent].strip():
            adjacent += direction
        if not 0 <= adjacent < len(lines):
            continue
        heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", lines[adjacent].strip())
        if heading_match and _normalize_outline_text(heading_match.group(1)) == _normalize_outline_text(stripped):
            return True
    return False


def _augment_outline_with_explicit_sections(body: str, outline: list[dict]) -> list[dict]:
    if not body:
        return outline
    augmented = [dict(item) for item in outline]
    supplement_primary_headings_only = bool(augmented)
    existing_raw_titles = {
        _single_line(str(item.get("title") or ""))
        for item in augmented
        if str(item.get("title") or "").strip()
    }
    existing_titles = {
        _normalize_outline_text(title)
        for title in existing_raw_titles
    }
    lines = body.splitlines()
    directory_indexes = _directory_line_indexes(lines)
    code_indexes = _code_fence_line_indexes(lines)
    for index, line in enumerate(lines):
        if index in directory_indexes or index in code_indexes:
            continue
        if supplement_primary_headings_only and _explicit_section_level_from_line(line) > 1:
            continue
        title = _explicit_section_title(line)
        if not title:
            continue
        normalized = _normalize_outline_text(title)
        if (
            not normalized
            or normalized in existing_titles
            or _outline_title_has_existing_heading(title, existing_raw_titles)
        ):
            continue
        augmented.append(
            {
                "level": _explicit_section_level(title, augmented),
                "title": title,
                "range": "",
                "summary": "",
                "source": "heading_scan",
            }
        )
        existing_raw_titles.add(title)
        existing_titles.add(normalized)
    return augmented


def _explicit_section_title(line: str) -> str:
    stripped = _strip_bold_markers(line.strip())
    if not stripped:
        return ""
    heading = re.match(r"^#{2,3}\s+(.+?)\s*$", stripped)
    if heading:
        title = _single_line(heading.group(1))
        return "" if _is_non_content_section_title(title) else title
    return ""


def _is_non_content_section_title(title: str) -> bool:
    normalized = _single_line(title)
    return normalized == "来源" or is_managed_section_title(normalized)


def _explicit_section_level(title: str, outline: list[dict]) -> int:
    if re.match(r"^\d{2}\s+", title):
        for item in reversed(outline):
            existing = str(item.get("title") or "")
            if re.match(r"^\d{2}\s+", existing):
                try:
                    return int(item.get("level") or 1)
                except (TypeError, ValueError):
                    return 1
    return 1


def _explicit_section_level_from_line(line: str) -> int:
    stripped = line.strip()
    match = re.match(r"^(#{2,3})\s+", stripped)
    if match:
        return 1 if match.group(1) == "##" else 2
    return 1


def _noise_tail_start_index(line: str) -> int | None:
    patterns = [
        r"声明[:：]\s*稿件未经授权",
        r"▼\s*点击下方名片",
        r"往\s*期\s*精\s*彩",
    ]
    indexes = [match.start() for pattern in patterns if (match := re.search(pattern, line))]
    return min(indexes) if indexes else None


def _is_high_confidence_noise_line(stripped: str) -> bool:
    if not stripped:
        return False
    compact = re.sub(r"\s+", "", stripped)
    if re.search(r"(戳|点|点击).{0,8}(蓝色字|名片).{0,8}关注", stripped):
        return True
    if re.search(r"(关注我们|关注.+公众号|微信公众号)", stripped) and len(stripped) <= 40:
        return True
    if re.search(r"声明[:：].{0,20}(未经授权|不得转载)", stripped):
        return True
    if re.fullmatch(r"[▼▲▽△\s]*点击下方名片关注.*", stripped):
        return True
    if re.fullmatch(r"往\s*期\s*精\s*彩(?:\s*内\s*容)?", stripped):
        return True
    if re.fullmatch(r"(?:图源|图片来源|来源)[:：].{1,50}", stripped):
        return True
    if "图源" in stripped and len(stripped) <= 50 and not re.search(r"[。！？；]", stripped):
        return True
    if compact in {"点击下方名片关注我们", "点击下方名片关注浙江宣传"}:
        return True
    return False


def _property_gaps(
    vault_path: Path,
    folder: str,
    required_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    root = vault_path / folder
    if not root.exists():
        return []
    gaps: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.md")):
        rel_path = path.relative_to(vault_path).as_posix()
        parsed = _read_markdown_parts(path)
        if parsed is None:
            gaps.append({"file_path": rel_path, "missing_fields": list(required_fields)})
            continue
        frontmatter, _body = parsed
        missing = [
            field
            for field in required_fields
            if field not in frontmatter or frontmatter.get(field) in (None, "")
        ]
        if missing:
            gaps.append({"file_path": rel_path, "missing_fields": missing})
    return gaps


def _missing_field_values(
    frontmatter: dict[str, Any],
    defaults: dict[str, str],
) -> dict[str, str]:
    return {
        field: value
        for field, value in defaults.items()
        if field not in frontmatter or frontmatter.get(field) in (None, "")
    }


def _apply_property_backfill(vault_path: Path, action: dict[str, Any]) -> bool:
    rel_path = str(action.get("file_path") or "")
    fields = action.get("fields") or {}
    if not rel_path or not fields:
        return False

    path = vault_path / rel_path
    parsed = _read_markdown_parts(path)
    if parsed is None:
        return False

    frontmatter, body = parsed
    changed = False
    for field, value in fields.items():
        if field not in frontmatter or frontmatter.get(field) in (None, ""):
            frontmatter[field] = value
            changed = True

    if changed:
        _write_markdown_parts(path, frontmatter, body)
    return changed


def _markdown_count(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(1 for _path in root.glob("*.md"))

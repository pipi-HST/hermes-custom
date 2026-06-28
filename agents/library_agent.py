"""Library Agent maintenance for committed Knowledge Files."""
from __future__ import annotations

import re
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agents.capture_indexer import build_identity_index, build_section_indexes
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
                    )
                except Exception as exc:  # pragma: no cover - defensive per-note isolation
                    report["outlines"]["errors"] += 1
                    report["outlines"]["items"].append(
                        {"file_path": file_path, "action": "error", "error": str(exc)}
                    )

        report["embedding_index"] = self._maintain_embedding_index_rows([row], apply=apply)
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
            stale_items = [
                item for item in index_items
                if repo.needs_reindex(
                    item.capture_id,
                    item.kind,
                    item.section_id,
                    item.source_hash,
                )
            ]
            if not stale_items:
                continue

            report["missing_or_stale"] += len(stale_items)
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
        if is_short and not legacy_outline:
            report["skipped_short"] += 1
            return

        metadata_repo = self._metadata_repo()
        capture_id = self._capture_id_for_file(rel_path, frontmatter)
        metadata = metadata_repo.get(capture_id) if metadata_repo and capture_id else None
        metadata_outline = metadata.get("outline") if metadata else []
        outline = metadata_outline or legacy_outline
        has_outline = bool(outline)
        has_directory = _has_directory(body)
        missing_outline_titles = _missing_outline_heading_titles(body, outline)
        has_pseudo_heading_duplicates = _has_adjacent_outline_pseudo_heading_duplicates(body, outline)
        has_outline_body_artifacts = _has_outline_body_artifacts(body, outline)
        has_sections = _has_real_markdown_sections(body)
        if (
            metadata_outline
            and not legacy_outline
            and has_directory
            and not missing_outline_titles
            and not has_pseudo_heading_duplicates
            and not has_outline_body_artifacts
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
                    "has_outline_body_artifacts": has_outline_body_artifacts,
                    "body_chars": len(body),
                }
            )
            return

        try:
            outline = outline or self.outliner.generate(body, len(body))
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

        if metadata_repo and capture_id:
            metadata_repo.upsert_outline(
                capture_id,
                outline,
                source_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                directory_rendered_at=(
                    datetime.now(timezone.utc).isoformat()
                    if render_outline and (has_directory or len(body) >= self.min_outline_chars)
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
                    "outline_items": len(outline),
                }
            )
            return
        frontmatter.pop("outline", None)
        updated_body = body if has_directory or is_short else _insert_directory(body, outline)
        if not is_short and (
            not _has_real_markdown_sections(updated_body)
            or _missing_outline_heading_titles(updated_body, outline)
        ):
            updated_body = _insert_outline_headings(updated_body, outline)
        updated_body = _remove_adjacent_outline_pseudo_heading_duplicates(updated_body, outline)
        updated_body = _cleanup_outline_body_artifacts(updated_body, outline)
        _write_markdown_parts(note_path, frontmatter, updated_body)
        report["updated"] += 1
        report["items"].append(
            {"file_path": rel_path, "action": "updated_outline", "outline_items": len(outline)}
        )


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


def _has_directory(body: str) -> bool:
    return re.search(r"^##\s+目录纲要\s*$", body, flags=re.MULTILINE) is not None


def _has_real_markdown_sections(body: str) -> bool:
    for match in re.finditer(r"^#{2,3}\s+(.+?)\s*$", body or "", flags=re.MULTILINE):
        if match.group(1).strip() != "目录纲要":
            return True
    return False


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
    for item in outline:
        level = max(int(item.get("level") or 1), 1)
        indent = "  " * (level - 1)
        title = _single_line(str(item.get("title") or "未命名章节"))
        summary = _single_line(str(item.get("summary") or ""))
        suffix = f"：{summary}" if summary else ""
        lines.append(f"{indent}- {title}{suffix}")
    return "\n".join(lines)


def _insert_outline_headings(body: str, outline: list[dict]) -> str:
    lines = body.splitlines()
    directory_indexes = _directory_line_indexes(lines)
    existing_titles = _existing_markdown_section_titles(body)
    inserted_titles: set[str] = set()
    updated: list[str] = []

    for index, line in enumerate(lines):
        item = _matching_outline_item(line, index, outline, directory_indexes, existing_titles)
        if item:
            title = _single_line(str(item.get("title") or "未命名章节"))
            if title not in inserted_titles:
                level = _heading_level(item)
                if updated and updated[-1].strip():
                    updated.append("")
                updated.append(f"{level} {title}")
                updated.append("")
                inserted_titles.add(title)
        updated.append(line)

    result = "\n".join(updated)
    if body.endswith("\n"):
        result += "\n"
    result = _insert_inline_outline_headings(result, outline)
    return _insert_range_outline_headings(result, outline)


def _insert_inline_outline_headings(body: str, outline: list[dict]) -> str:
    directory_end = _directory_end_offset(body)
    prefix = body[:directory_end]
    suffix = body[directory_end:]

    for item in outline:
        title = _single_line(str(item.get("title") or ""))
        if not title or title in _existing_markdown_section_titles(prefix + suffix):
            continue
        match = _inline_outline_match(suffix, title)
        if match is None:
            continue

        level = _heading_level(item)
        before = suffix[: match.start()].rstrip()
        after = suffix[match.start() :].lstrip()
        heading = f"{level} {title}"
        if before:
            suffix = f"{before}\n\n{heading}\n\n{after}"
        else:
            suffix = f"{heading}\n\n{after}"

    return prefix + suffix


def _insert_range_outline_headings(body: str, outline: list[dict]) -> str:
    directory_end = _directory_end_offset(body)
    prefix = body[:directory_end]
    suffix = body[directory_end:]
    existing_titles = _existing_markdown_section_titles(prefix + suffix)
    insertions: dict[int, list[dict]] = {}

    for item in outline:
        title = _single_line(str(item.get("title") or ""))
        if not title or title in existing_titles:
            continue
        paragraph_number = _outline_range_start(str(item.get("range") or ""))
        if paragraph_number <= 0:
            continue
        insertions.setdefault(paragraph_number, []).append(item)

    if not insertions:
        return body

    blocks = [block.strip() for block in re.split(r"\n\s*\n", suffix.strip()) if block.strip()]
    updated: list[str] = []
    content_index = 0
    inserted_titles: set[str] = set()
    for block in blocks:
        if _is_content_paragraph(block):
            content_index += 1
            for item in insertions.get(content_index, []):
                title = _single_line(str(item.get("title") or ""))
                if title and title not in inserted_titles:
                    updated.append(f"{_heading_level(item)} {title}")
                    inserted_titles.add(title)
        updated.append(block)

    if not inserted_titles:
        return body
    return prefix + "\n\n".join(updated).strip()


def _outline_range_start(value: str) -> int:
    match = re.search(r"段\s*([0-9一二三四五六七八九十]+)", value)
    if not match:
        return 0
    token = match.group(1)
    if token.isdigit():
        return int(token)
    return _chinese_number_to_int(token)


def _chinese_number_to_int(value: str) -> int:
    digits = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value in digits:
        return digits[value]
    if value == "十":
        return 10
    if value.startswith("十") and len(value) == 2:
        return 10 + digits.get(value[1], 0)
    if "十" in value:
        head, _, tail = value.partition("十")
        return digits.get(head, 0) * 10 + digits.get(tail, 0)
    return 0


def _is_content_paragraph(block: str) -> bool:
    stripped = block.strip()
    if not stripped or stripped.startswith("#"):
        return False
    if stripped == DIRECTORY_HEADING:
        return False
    if all(_is_directory_line(line.strip()) for line in stripped.splitlines()):
        return False
    return True


def _inline_outline_match(text: str, title: str) -> re.Match[str] | None:
    patterns: list[str] = []
    for variant in _outline_title_variants(title):
        if not variant:
            continue
        flexible = _flexible_text_pattern(variant)
        patterns.extend(
            [
                rf"[一二三四五六七八九十]+、\s*{flexible}",
                rf"[（(]?[一二三四五六七八九十0-9]+[)）.、]\s*{flexible}",
                rf"\*\*\s*{flexible}\s*\*\*",
                flexible,
            ]
        )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match
    return None


def _directory_end_offset(body: str) -> int:
    lines = body.splitlines(keepends=True)
    in_directory = False
    saw_item = False
    offset = 0
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"##\s+目录纲要", stripped):
            in_directory = True
            saw_item = False
        elif in_directory and stripped and saw_item and not _is_directory_line(stripped):
            return offset

        if in_directory and _is_directory_item(stripped):
            saw_item = True
        offset += len(line)
    return len(body) if in_directory else 0


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


def _existing_markdown_section_titles(body: str) -> set[str]:
    titles: set[str] = set()
    for match in re.finditer(r"^#{2,3}\s+(.+?)\s*$", body or "", flags=re.MULTILINE):
        title = _single_line(match.group(1))
        if title and title != "目录纲要":
            titles.add(title)
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
    elif not _is_heading_like_line(stripped):
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


def _matching_outline_item(
    line: str,
    index: int,
    outline: list[dict],
    directory_indexes: set[int],
    existing_titles: set[str],
) -> dict | None:
    if index in directory_indexes:
        return None
    stripped = line.strip()
    if not _is_heading_like_line(stripped):
        return None
    for item in outline:
        title = _single_line(str(item.get("title") or ""))
        if (
            title
            and not _outline_title_has_existing_heading(title, existing_titles)
            and _outline_title_matches_text(title, stripped)
        ):
            return item
    return None


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
        level = int(item.get("level") or 1)
    except (TypeError, ValueError):
        level = 1
    return "##" if level <= 1 else "###"


def _outline_title_matches_text(title: str, text: str) -> bool:
    normalized_text = _normalize_outline_text(text)
    return any(key and key in normalized_text for key in _outline_title_match_keys(title))


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


def _flexible_text_pattern(text: str) -> str:
    chars = [re.escape(char) for char in text if not char.isspace()]
    return r"\s*".join(chars)


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


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

"""Library Agent maintenance for committed Obsidian capture notes."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml

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
        min_outline_chars: int = DEFAULT_OUTLINE_CHAR_THRESHOLD,
    ) -> None:
        self.vault_path = Path(vault_path).expanduser()
        self.outliner = outliner
        self.db_path = Path(db_path).expanduser() if db_path else None
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

    def _raw_capture_id_map(self) -> dict[str, str]:
        if self.db_path is None or not self.db_path.exists():
            return {}
        try:
            conn = sqlite3.connect(str(self.db_path))
            rows = conn.execute(
                "SELECT id, raw_path FROM captures "
                "WHERE raw_path IS NOT NULL AND raw_path != ''"
            ).fetchall()
        except sqlite3.Error:
            return {}
        finally:
            try:
                conn.close()
            except UnboundLocalError:
                pass
        return {str(raw_path): str(capture_id) for capture_id, raw_path in rows}

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

    def _maintain_one(
        self,
        note_path: Path,
        rel_path: str,
        apply: bool,
        report: dict[str, Any],
    ) -> None:
        parsed = _read_markdown_parts(note_path)
        if parsed is None:
            report["errors"] += 1
            report["items"].append(
                {"file_path": rel_path, "action": "parse_error"}
            )
            return

        frontmatter, body = parsed
        if len(body) < self.min_outline_chars:
            report["skipped_short"] += 1
            return

        has_frontmatter_outline = bool(frontmatter.get("outline"))
        has_directory = _has_directory(body)
        if has_frontmatter_outline and has_directory:
            report["skipped_existing"] += 1
            return

        report["needs_outline"] += 1
        if not apply:
            report["items"].append(
                {
                    "file_path": rel_path,
                    "action": "needs_outline",
                    "missing_frontmatter_outline": not has_frontmatter_outline,
                    "missing_directory": not has_directory,
                    "body_chars": len(body),
                }
            )
            return

        try:
            outline = frontmatter.get("outline") or self.outliner.generate(body, len(body))
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

        frontmatter["outline"] = outline
        updated_body = body if has_directory else _insert_directory(body, outline)
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

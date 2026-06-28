"""Watch Knowledge Files and keep Hermes DB paths aligned."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.repair_data import repair_data

DEFAULT_INTERVAL_SECONDS = 21600


def run_repair(db_path: str | Path, vault_path: str | Path) -> dict:
    """Run the safe DB repair in apply mode with readable filenames enabled."""
    return repair_data(
        Path(db_path),
        Path(vault_path),
        apply=True,
        rename_readable=True,
    )


def snapshot_vault(vault_path: Path) -> dict[str, float]:
    """Return a small snapshot of capture/raw markdown files."""
    files: dict[str, float] = {}
    for subdir in ("captures", "_archive/raw"):
        root = vault_path / subdir
        if not root.exists():
            continue
        for path in root.glob("*.md"):
            try:
                files[path.relative_to(vault_path).as_posix()] = path.stat().st_mtime
            except FileNotFoundError:
                continue
    return files


def watch(
    db_path: str | Path,
    vault_path: str | Path,
    *,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    once: bool = False,
) -> None:
    """Poll Knowledge File Markdown files and repair after detected changes."""
    db = Path(db_path).expanduser()
    vault = Path(vault_path).expanduser()
    previous = snapshot_vault(vault)
    run_repair(db, vault)
    if once:
        return

    while True:
        time.sleep(interval)
        current = snapshot_vault(vault)
        if current != previous:
            result = run_repair(db, vault)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            previous = snapshot_vault(vault)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch Knowledge File captures/raw files and sync Hermes DB."
    )
    parser.add_argument("--db", default="data/captures.db")
    parser.add_argument("--vault", default="~/Hermes/knowledge")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    watch(args.db, args.vault, interval=args.interval, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Re-index the whole library: chunk embeddings, FTS index and card vectors.

The previous version of this script embedded ``captures.summary`` — a column
that was removed when the schema was slimmed down — and only wrote
``captures.embedding``.  It could never run.  The real pipeline lives in
``LibraryAgent.maintain_embedding_index``; this script is now a thin CLI over
it so there is exactly one re-index entry point.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.capture_agent import load_config
from agents.embedder import Embedder
from agents.library_agent import LibraryAgent


def backfill(
    *,
    config_path: str = "config.yaml",
    apply: bool = True,
    vault_path: str | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    agent = LibraryAgent(
        vault_path or config.vault_path,
        outliner=None,
        db_path=config.db_path,
        embedder=Embedder(api_key=config.qwen_api_key),
    )
    return agent.maintain_embedding_index(apply=apply)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--vault", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing",
    )
    args = parser.parse_args(argv)
    report = backfill(
        config_path=args.config,
        apply=not args.dry_run,
        vault_path=args.vault,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

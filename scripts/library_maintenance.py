"""Run Library Agent maintenance tasks against configured Knowledge Files."""
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
from agents.library_agent import DEFAULT_OUTLINE_CHAR_THRESHOLD, LibraryAgent
from agents.outliner import Outliner


def run_library_maintenance(
    *,
    config_path: str = "config.yaml",
    vault_path: str | None = None,
    apply: bool = False,
    min_outline_chars: int = DEFAULT_OUTLINE_CHAR_THRESHOLD,
    outline_model: str = "deepseek-v4-flash",
) -> dict[str, Any]:
    config = load_config(config_path)
    outliner = Outliner(
        api_key=config.llm_api_key,
        model=outline_model,
        base_url=config.llm_base_url or "https://api.deepseek.com",
    )
    agent = LibraryAgent(
        vault_path or config.vault_path,
        outliner=outliner,
        db_path=config.db_path,
        embedder=Embedder(api_key=config.qwen_api_key),
        min_outline_chars=min_outline_chars,
    )
    return {
        "dry_run": not apply,
        "database_sync": agent.maintain_database_sync(apply=apply),
        "outlines": agent.maintain_outlines(apply=apply),
        "property_gaps": agent.maintain_property_gaps(apply=apply),
        "embedding_index": agent.maintain_embedding_index(apply=apply),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Maintain committed capture notes in Knowledge File storage."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--vault", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--min-outline-chars", type=int, default=DEFAULT_OUTLINE_CHAR_THRESHOLD)
    parser.add_argument("--outline-model", default="deepseek-v4-flash")
    args = parser.parse_args()

    report = run_library_maintenance(
        config_path=args.config,
        vault_path=args.vault,
        apply=args.apply,
        min_outline_chars=args.min_outline_chars,
        outline_model=args.outline_model,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

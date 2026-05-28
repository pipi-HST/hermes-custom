"""回填存量 captures — 为没有 embedding/outline 的记录补全."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.embedder import Embedder
from agents.capture_agent import load_config
from db.schema import init_db
from db.repo import CaptureRepo


def backfill(config_path: str = "config.yaml"):
    config = load_config(config_path)
    conn = init_db(config.db_path)
    repo = CaptureRepo(conn)
    embedder = Embedder(api_key=config.qwen_api_key)

    all_captures = repo.list_all(limit=10000)
    needs_embedding = [c for c in all_captures if not c.get("embedding")]

    print(f"Total: {len(all_captures)}, need embedding: {len(needs_embedding)}")

    for c in needs_embedding:
        try:
            emb = embedder.embed(c["summary"])
            repo.update(c["id"], embedding=emb)
            print(f"  embed: {c['id'][:8]} OK")
            time.sleep(0.1)
        except Exception as e:
            print(f"  embed: {c['id'][:8]} FAIL: {e}")

    print("Backfill complete.")


if __name__ == "__main__":
    backfill()

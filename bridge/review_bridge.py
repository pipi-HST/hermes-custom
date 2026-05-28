"""Review Agent bridge — Cron 入口."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.capture_agent import load_config
from agents.review_agent import ReviewAgent
from db.schema import init_db
from db.repo import CaptureRepo

def run_daily_review() -> str:
    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    repo = CaptureRepo(init_db(config.db_path))
    agent = ReviewAgent(repo, vault_path=config.vault_path)
    result = agent.run_daily()
    lines = []
    for m in result:
        lines.append(f"[{m['type']}] {m['text'][:100]}")
    return "\n".join(lines) if lines else "今日无回顾项目。"

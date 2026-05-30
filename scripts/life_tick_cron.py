#!/usr/bin/env python3
"""Run one Life Assistant tick for Hermes cron delivery."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge.life_bridge import life_tick


def main() -> int:
    envelope = life_tick(dry_run=False)
    if envelope.get("status") != "success":
        print(envelope.get("chat_preview", "Life Assistant tick failed."))
        return 1
    if envelope.get("meta", {}).get("should_notify"):
        print(envelope.get("chat_preview", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

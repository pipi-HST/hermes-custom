#!/usr/bin/env python3
"""Run the complete layered Hermes Knowledge product acceptance suite."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(
    os.environ.get(
        "HERMES_RUNTIME_ROOT",
        "/Users/pipi/Downloads/hermes-agent-main",
    )
).expanduser()

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.knowledge_pipeline_e2e import run_pipeline_e2e


def run_knowledge_product_e2e(
    *,
    work_dir: str | Path | None = None,
    live_providers: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    service_layer = run_pipeline_e2e(
        work_dir=work_dir,
        live_providers=live_providers,
    )
    browser_started = time.perf_counter()
    browser_process = subprocess.run(
        ["npm", "run", "test:knowledge:complete"],
        cwd=RUNTIME_ROOT / "web",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    browser_layer = {
        "ok": browser_process.returncode == 0,
        "returncode": browser_process.returncode,
        "elapsed_seconds": round(time.perf_counter() - browser_started, 3),
        "command": "npm run test:knowledge:complete",
        "output_tail": "\n".join(browser_process.stdout.splitlines()[-80:]),
    }
    return {
        "ok": bool(service_layer.get("ok")) and browser_layer["ok"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "layers": {
            "service_pipeline": service_layer,
            "browser_workbench": browser_layer,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run service Pipeline and browser Workbench acceptance as one product E2E.",
    )
    parser.add_argument("--work-dir", default="", help="Optional isolated service workspace.")
    parser.add_argument(
        "--live-providers",
        action="store_true",
        help="Use configured LLM and embedding providers for the service layer.",
    )
    args = parser.parse_args(argv)
    result = run_knowledge_product_e2e(
        work_dir=args.work_dir or None,
        live_providers=args.live_providers,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

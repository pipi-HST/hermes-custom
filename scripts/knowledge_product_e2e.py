#!/usr/bin/env python3
"""Run the complete layered Hermes Knowledge product acceptance suite."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(
    os.environ.get(
        "HERMES_RUNTIME_ROOT",
        str(PROJECT_ROOT.parent / "hermes-agent-main"),
    )
).expanduser()

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.knowledge_pipeline_e2e import (
    BROWSER_E2E_SIGNAL,
    BROWSER_E2E_TITLE,
    run_pipeline_e2e,
)


def run_real_browser_acceptance(
    *,
    runtime_base: str,
    runtime_headers: dict[str, str],
    paths: dict[str, Path],
    env: dict[str, str],
) -> dict[str, Any]:
    del runtime_headers
    web_root = RUNTIME_ROOT / "web"
    node = shutil.which("node")
    playwright_cli = web_root / "node_modules" / "playwright" / "cli.js"
    if node is None:
        return {
            "ok": False,
            "returncode": None,
            "elapsed_seconds": 0.0,
            "command": "node node_modules/playwright/cli.js test",
            "output_tail": "node executable not found on PATH",
        }
    if not playwright_cli.is_file():
        return {
            "ok": False,
            "returncode": None,
            "elapsed_seconds": 0.0,
            "command": "node node_modules/playwright/cli.js test",
            "output_tail": f"Playwright CLI not found at {playwright_cli}",
        }
    browser_env = dict(env)
    browser_env["HERMES_REAL_E2E_BASE_URL"] = runtime_base
    browser_env["HERMES_REAL_E2E_SIGNAL"] = BROWSER_E2E_SIGNAL
    browser_env["HERMES_REAL_E2E_TITLE"] = BROWSER_E2E_TITLE
    # Playwright's transform cache defaults to the system temp dir, which the
    # Windows test sandbox denies (EPERM). Keep it inside the isolated run.
    scratch_root = paths.get("root")
    if scratch_root is not None:
        transform_cache = Path(scratch_root) / ".playwright-transform-cache"
        transform_cache.mkdir(parents=True, exist_ok=True)
        browser_env["PWTEST_CACHE_DIR"] = str(transform_cache)
        # Chrome creates its throwaway profile with mkdtemp(); the Windows
        # sandbox denies that under the system temp dir.
        browser_temp = Path(scratch_root) / ".playwright-temp"
        browser_temp.mkdir(parents=True, exist_ok=True)
        for key in ("TEMP", "TMP", "TMPDIR"):
            browser_env[key] = str(browser_temp)
    command = [
        node,
        str(playwright_cli),
        "test",
        "e2e/knowledge-product-real.spec.ts",
        "--config=playwright.real.config.ts",
        "--reporter=list",
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=web_root,
        env=browser_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=300,
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "command": "node node_modules/playwright/cli.js test e2e/knowledge-product-real.spec.ts --config=playwright.real.config.ts",
        "output_tail": "\n".join(completed.stdout.splitlines()[-120:]),
    }


def run_knowledge_product_e2e(
    *,
    work_dir: str | Path | None = None,
    live_providers: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    service_layer = run_pipeline_e2e(
        work_dir=work_dir,
        live_providers=live_providers,
        product_runner=run_real_browser_acceptance,
    )
    browser_layer = service_layer.get("product_acceptance") or {
        "ok": False,
        "pipelines": [],
        "error": "real browser acceptance did not run",
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

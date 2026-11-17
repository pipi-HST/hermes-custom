"""HTTP-only smoke: capture -> draft -> commit -> card -> RAG.

This starts the real Knowledge API in-process, points the Runtime tools at it
with ``HERMES_KNOWLEDGE_URL`` only (no local checkout), and exercises the
personal-assistant tool handlers through the HTTP transport.

External LLM / embedding calls go to the same local fake OpenAI-compatible
server used by ``knowledge_pipeline_e2e.py``.  No real provider is contacted.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOS_ROOT = PROJECT_ROOT.parent
RUNTIME_ROOT = Path(
    os.environ.get("HERMES_RUNTIME_ROOT", str(REPOS_ROOT / "hermes-agent-main"))
).expanduser()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from scripts.knowledge_pipeline_e2e import _FakeProviderServer  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(url: str, *, timeout: float = 20.0) -> None:
    import httpx

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1.0, headers={"Authorization": "Bearer smoke-token"})
            if response.status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001 - readiness probe
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"knowledge API did not become ready: {last_error}")


def _envelope(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise AssertionError(f"tool did not return an object: {value!r}")
    return value


def _check(name: str, ok: bool, detail: Any = None) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def run_smoke(work_dir: str | Path | None = None) -> dict[str, Any]:
    import shutil

    import uvicorn

    temp_dir = None
    if work_dir is None:
        temp_root = PROJECT_ROOT / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix="http-only-", dir=temp_root)
        root = Path(temp_dir)
    else:
        base = Path(work_dir).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        # Every run gets a fresh child so a leftover DB from an interrupted
        # smoke cannot change the result.
        root = Path(tempfile.mkdtemp(prefix="run-", dir=base))
        temp_dir = str(root)

    fake = _FakeProviderServer(faults=set())
    server = None
    checks: list[dict[str, Any]] = []
    try:
        fake.start()
        vault = root / "vault"
        vault.mkdir(exist_ok=True)
        db_path = root / "captures.db"
        token = "smoke-token"
        os.environ["HERMES_RAG_LOG_PATH"] = str(root / "rag.log")

        os.environ["HERMES_API_TOKEN"] = token
        os.environ["HERMES_KNOWLEDGE_URL"] = "http://127.0.0.1:0"  # replaced below
        os.environ["HERMES_KNOWLEDGE_TOKEN"] = token
        os.environ.pop("HERMES_KNOWLEDGE_LOCAL_ROOT", None)
        os.environ.pop("HERMES_CUSTOM_ROOT", None)
        os.environ["DASHSCOPE_BASE_URL"] = fake.base_url

        # Import after DASHSCOPE_BASE_URL is final: agents.embedder reads it at
        # module import time.
        from agents.capture_agent import CaptureConfig
        from api.main import create_app

        config = CaptureConfig(
            llm_api_key="smoke-key",
            llm_provider="openai",
            llm_model="smoke-model",
            llm_base_url=fake.base_url,
            qwen_api_key="smoke-embedding-key",
            vault_path=str(vault),
            db_path=str(db_path),
        )
        app = create_app(config=config)
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        os.environ["HERMES_KNOWLEDGE_URL"] = base_url

        uvicorn_config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(uvicorn_config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        _wait_for_http(f"{base_url}/api/health")

        # Import after the env is final: the tool layer reads it at call time,
        # but a fresh process makes "no local import" unambiguous.
        from tools.capture_tool import _handler as capture_handler
        from tools.commit_capture_draft_tool import _handler as commit_capture_handler
        from tools.commit_garden_draft_tool import _handler as commit_garden_handler
        from tools.search_notes_tool import _handler as search_handler
        from tools.synthesize_garden_tool import _handler as synthesize_handler

        captured = _envelope(
            capture_handler(
                {
                    "content": (
                        "# HTTP-only smoke material\n\n"
                        "This article is long enough for the Capture extractor to accept it. "
                        "It verifies that the Runtime reaches the Knowledge Service over HTTP.\n\n"
                        "The flow must create a draft, commit it, synthesize a Knowledge Card, "
                        "commit that card, and return a RAG answer without importing a local checkout."
                    )
                },
                user_task="",
            )
        )
        draft_id = str((captured.get("meta") or {}).get("draft_id") or "")
        checks.append(_check("capture_returns_draft", captured.get("status") == "success" and bool(draft_id), captured.get("errors")))

        committed = _envelope(
            commit_capture_handler({"draft_id": draft_id}, user_task="确认存库")
        )
        commit_meta = committed.get("meta") if isinstance(committed.get("meta"), dict) else {}
        capture_id = str(
            commit_meta.get("capture_id")
            or commit_meta.get("committed_capture_id")
            or ""
        )
        checks.append(_check("commit_capture", committed.get("status") == "success" and bool(capture_id), committed.get("errors")))

        synthesized = _envelope(
            synthesize_handler({"capture_ids": [capture_id]}, user_task="生成卡片")
        )
        garden_meta = synthesized.get("meta") if isinstance(synthesized.get("meta"), dict) else {}
        garden_draft_id = str(garden_meta.get("draft_id") or "")
        checks.append(_check("synthesize_garden_over_http", synthesized.get("status") == "success" and bool(garden_draft_id), synthesized.get("errors")))

        committed_card = _envelope(
            commit_garden_handler({"draft_id": garden_draft_id}, user_task="确认卡片")
        )
        checks.append(_check("commit_card", committed_card.get("status") == "success", committed_card.get("errors")))

        searched = _envelope(search_handler({"query": "HTTP-only smoke"}))
        checks.append(_check("rag_search", searched.get("status") == "success", searched.get("errors")))

        # Differential contract: read the same objects back through the
        # Service API, not only through the Runtime tool envelopes above.
        import httpx

        card_meta = (
            committed_card.get("meta")
            if isinstance(committed_card.get("meta"), dict)
            else {}
        )
        card_id = str(card_meta.get("card_id") or "")
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        ) as service:
            capture_resp = service.get(f"/api/captures/{capture_id}")
            cards_resp = service.get("/api/garden/cards")
            search_resp = service.get(
                "/api/knowledge/search", params={"q": "HTTP-only smoke"}
            )
        capture_payload = capture_resp.json() if capture_resp.status_code == 200 else {}
        cards_payload = cards_resp.json() if cards_resp.status_code == 200 else {}
        search_payload = search_resp.json() if search_resp.status_code == 200 else {}
        card_ids = {
            str(item.get("id") or "")
            for item in cards_payload.get("items", [])
            if isinstance(item, dict)
        }
        checks.extend(
            [
                _check(
                    "service_capture_matches_runtime",
                    bool(capture_payload.get("id") == capture_id and capture_payload.get("body")),
                    capture_resp.status_code,
                ),
                _check(
                    "service_card_matches_runtime",
                    bool(card_id and card_id in card_ids),
                    {"card_id": card_id, "service_count": len(card_ids)},
                ),
                _check(
                    "service_rag_matches_runtime",
                    bool(search_payload.get("total", 0) > 0),
                    search_resp.status_code,
                ),
            ]
        )

        return {
            "ok": all(item["ok"] for item in checks),
            "base_url": base_url,
            "vault": str(vault),
            "db_path": str(db_path),
            "checks": checks,
        }
    finally:
        if server is not None:
            server.should_exit = True
        fake.stop()
        if temp_dir is not None:
            # The app keeps its SQLite handle for the process lifetime; on
            # Windows the temp DB can remain locked. Cleanup is best-effort.
            shutil.rmtree(temp_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args(argv)
    report = run_smoke(args.work_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

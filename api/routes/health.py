"""Contract v1 health endpoint.

The runtime's ``KnowledgeClient`` calls ``GET /api/health`` before it uses any
other endpoint and compares ``contract_version`` with its own
``MIN_CONTRACT_VERSION``; the value must therefore stay a plain integer and the
endpoint must answer 200 even when a dependency is down (so clients can still
detect an incompatible/older service instead of seeing a connection error).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fastapi import APIRouter

KNOWLEDGE_SERVICE_NAME = "hermes-knowledge"
KNOWLEDGE_CONTRACT_VERSION = 1


def _service_version() -> str:
    """Return a short build/version string for monitoring.

    ``HERMES_KNOWLEDGE_VERSION`` wins (deployments can pin it), otherwise the
    short git sha of the checkout is used and ``"1.0"`` is the last resort for
    source archives.  Computed once per process.
    """
    override = (os.environ.get("HERMES_KNOWLEDGE_VERSION") or "").strip()
    if override:
        return override
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            timeout=2,
        )
        sha = (result.stdout or "").strip()
        if result.returncode == 0 and sha:
            return sha
    except (OSError, subprocess.SubprocessError):
        pass
    return "1.0"


KNOWLEDGE_SERVICE_VERSION = _service_version()


def create_health_router(conn, vault_path: str | Path) -> APIRouter:
    """Build the health router for one app connection + vault configuration."""
    router = APIRouter()
    vault = Path(vault_path).expanduser()

    @router.get("/health")
    def health():
        checks = {
            "database": _database_status(conn),
            "vault": _vault_status(vault),
        }
        return {
            "service": KNOWLEDGE_SERVICE_NAME,
            "contract_version": KNOWLEDGE_CONTRACT_VERSION,
            "status": "ok" if all(value == "ok" for value in checks.values()) else "degraded",
            "version": KNOWLEDGE_SERVICE_VERSION,
            # Never echo vault/database paths: health is unauthenticated by design.
            "checks": checks,
        }

    return router


def _database_status(conn) -> str:
    try:
        conn.execute("SELECT 1").fetchone()
    except Exception:  # noqa: BLE001 - any DB failure means "degraded", never 500
        return "unavailable"
    return "ok"


def _vault_status(vault: Path) -> str:
    try:
        return "ok" if vault.is_dir() else "unavailable"
    except OSError:
        return "unavailable"

"""FastAPI application entrypoint."""
from __future__ import annotations

import ipaddress
import os
import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from db.schema import init_db
from db.repo import CaptureRepo, CaptureDraftRepo
from db.reading_repo import ReadingRepo
from api.routes.captures import create_capture_router
from api.routes.reading import create_reading_router
from api.routes.knowledge import create_knowledge_router
from api.routes.capture_drafts import create_capture_draft_router
from agents.capture_agent import load_config

DB_PATH = str(Path(__file__).parent.parent / "data" / "captures.db")
MAX_API_REQUEST_BYTES = 10 * 1024 * 1024
LOCAL_DASHBOARD_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3010",
    "http://127.0.0.1:3010",
    "http://localhost:3011",
    "http://127.0.0.1:3011",
    "http://localhost:3012",
    "http://127.0.0.1:3012",
    "http://localhost:3013",
    "http://127.0.0.1:3013",
]


def create_app(config_path: str | None = None, config: object | None = None) -> FastAPI:
    """Build the API app.

    The app is created explicitly (factory) so that importing this module has no
    side effects: no config file is read and no database connection is opened.
    Use ``uvicorn api.main:create_app --factory`` to serve it.
    """
    app = FastAPI(title="Hermes Knowledge API")
    api_token = os.environ.get("HERMES_API_TOKEN", "").strip()
    trusted_proxies = _trusted_proxies()
    allowed_hosts = _allowed_hosts()

    @app.middleware("http")
    async def auth_and_body_guard(request, call_next):
        if not _host_header_allowed(request.headers.get("host", ""), allowed_hosts):
            return JSONResponse(
                {"detail": "unexpected Host header"},
                status_code=400,
            )
        if api_token:
            authorization = request.headers.get("authorization", "")
            scheme, _, provided_token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(
                provided_token,
                api_token,
            ):
                return JSONResponse(
                    {"detail": "authentication required"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        elif not _client_is_loopback(request, trusted_proxies):
            return JSONResponse(
                {"detail": "remote access requires HERMES_API_TOKEN"},
                status_code=403,
            )
        if _request_body_too_large(request.headers.get("content-length", "")):
            return JSONResponse(
                {"detail": "request body too large"},
                status_code=413,
            )
        if not request.headers.get("content-length") and request.method in {"POST", "PUT", "PATCH"}:
            body = b""
            async for chunk in request.stream():
                body += chunk
                if len(body) > MAX_API_REQUEST_BYTES:
                    return JSONResponse(
                        {"detail": "request body too large"},
                        status_code=413,
                    )
            request._body = body
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=LOCAL_DASHBOARD_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if config is None:
        config = load_config(_resolve_config_path(config_path))
    _validate_runtime_config(config)
    db_path = str(Path(config.db_path or DB_PATH).expanduser())
    if not Path(db_path).is_absolute():
        db_path = str((Path(__file__).parent.parent / db_path).resolve())
    conn = init_db(db_path)
    repo = CaptureRepo(conn)
    draft_repo = CaptureDraftRepo(conn)
    reading_repo = ReadingRepo(conn)

    app.include_router(create_capture_router(repo, config.vault_path, config), prefix="/api")
    app.include_router(create_capture_draft_router(draft_repo), prefix="/api")
    app.include_router(
        create_reading_router(reading_repo, {"vault_path": config.vault_path}),
        prefix="/api",
    )
    app.include_router(
        create_knowledge_router(reading_repo, repo, config.vault_path),
        prefix="/api",
    )

    return app


def _validate_runtime_config(config) -> None:
    """Fail fast with an actionable message instead of a half-working service."""
    db_path = str(getattr(config, "db_path", "") or "").strip()
    vault_path = str(getattr(config, "vault_path", "") or "").strip()
    if not db_path:
        raise RuntimeError(
            "invalid configuration: database.path is required "
            "(set it in config.yaml or HERMES_CONFIG_PATH)"
        )
    if not vault_path:
        raise RuntimeError(
            "invalid configuration: vault.path is required "
            "(set it in config.yaml or HERMES_CONFIG_PATH)"
        )
    if not str(getattr(config, "llm_api_key", "") or "").strip():
        import logging

        logging.getLogger(__name__).warning(
            "llm.api_key is empty: capture/reading generation will fail until it is set"
        )
    if not str(getattr(config, "qwen_api_key", "") or "").strip():
        import logging

        logging.getLogger(__name__).warning(
            "qwen.api_key is empty: image/voice extraction will be unavailable"
        )


def _is_loopback_client(host: str) -> bool:
    value = (host or "").strip().strip("[]")
    if not value:
        return False
    if value.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _trusted_proxies() -> frozenset[str]:
    raw = os.environ.get("HERMES_API_TRUSTED_PROXIES", "").strip()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _allowed_hosts() -> frozenset[str] | None:
    raw = os.environ.get("HERMES_API_ALLOWED_HOSTS", "").strip()
    if not raw:
        return None
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _host_header_allowed(host_header: str, allowed: frozenset[str] | None) -> bool:
    """Reject DNS-rebinding style Host headers.

    By default only loopback names, IP literals and the test client host are
    accepted.  Deployments behind a hostname must set
    ``HERMES_API_ALLOWED_HOSTS`` explicitly.
    """
    host = (host_header or "").strip()
    if not host:
        return True
    if host.startswith("["):
        close = host.find("]")
        host_only = host[1:close] if close != -1 else host.strip("[]")
    else:
        host_only = host.rsplit(":", 1)[0] if ":" in host else host
    host_only = host_only.lower()
    if allowed is not None:
        return host_only in allowed
    if host_only in {"localhost", "testserver"}:
        return True
    try:
        ipaddress.ip_address(host_only)
    except ValueError:
        return False
    return True


def _client_is_loopback(request, trusted_proxies: frozenset[str]) -> bool:
    peer = request.client.host if request.client else ""
    if trusted_proxies and peer in trusted_proxies:
        forwarded = request.headers.get("x-forwarded-for", "")
        first_hop = forwarded.split(",")[0].strip()
        if first_hop:
            return _is_loopback_client(first_hop)
    return _is_loopback_client(peer)


def _request_body_too_large(content_length: str) -> bool:
    if not content_length:
        return False
    try:
        return int(content_length) > MAX_API_REQUEST_BYTES
    except ValueError:
        return True


def _resolve_config_path(config_path: str | None) -> str:
    if config_path:
        return config_path
    env_path = os.environ.get("HERMES_CONFIG_PATH", "").strip()
    if env_path:
        return env_path
    return str(Path(__file__).parent.parent / "config.yaml")



# NOTE: no module-level ``app`` on purpose. Importing this module must not read
# config.yaml nor open the SQLite database; serve with:
#   uvicorn api.main:create_app --factory


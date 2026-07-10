"""FastAPI application entrypoint."""
from __future__ import annotations

import asyncio
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


def create_app() -> FastAPI:
    app = FastAPI(title="Hermes Knowledge API")
    request_lock = asyncio.Lock()
    api_token = os.environ.get("HERMES_API_TOKEN", "").strip()

    @app.middleware("http")
    async def serialize_shared_database_requests(request, call_next):
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
        elif not _is_loopback_client(request.client.host if request.client else ""):
            return JSONResponse(
                {"detail": "remote access requires HERMES_API_TOKEN"},
                status_code=403,
            )
        if _request_body_too_large(request.headers.get("content-length", "")):
            return JSONResponse(
                {"detail": "request body too large"},
                status_code=413,
            )
        if _uses_shared_api_connection(request.method, request.url.path):
            async with request_lock:
                return await call_next(request)
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=LOCAL_DASHBOARD_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    db_path = config.db_path or DB_PATH
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


def _is_loopback_client(host: str) -> bool:
    value = (host or "").strip().strip("[]")
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _request_body_too_large(content_length: str) -> bool:
    if not content_length:
        return False
    try:
        return int(content_length) > MAX_API_REQUEST_BYTES
    except ValueError:
        return True


def _uses_shared_api_connection(method: str, path: str) -> bool:
    normalized_method = (method or "").upper()
    if normalized_method == "GET" and path.startswith("/api/"):
        return True
    if normalized_method == "PATCH" and path.startswith("/api/captures/"):
        return True
    return normalized_method == "POST" and path == "/api/knowledge/graph/refresh"


app = create_app()

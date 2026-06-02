"""FastAPI application entrypoint."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db.schema import init_db
from db.repo import CaptureRepo
from api.routes.captures import create_capture_router
from agents.capture_agent import load_config

DB_PATH = str(Path(__file__).parent.parent / "data" / "captures.db")
LOCAL_DASHBOARD_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3010",
    "http://127.0.0.1:3010",
]


def create_app() -> FastAPI:
    app = FastAPI(title="Capture Agent API")
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
    app.include_router(create_capture_router(repo, config.vault_path), prefix="/api")

    return app


app = create_app()

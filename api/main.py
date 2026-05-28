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


def create_app() -> FastAPI:
    app = FastAPI(title="Capture Agent API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    config = load_config(str(Path(__file__).parent.parent / "config.yaml"))
    conn = init_db(config.db_path or DB_PATH)
    repo = CaptureRepo(conn)
    app.include_router(create_capture_router(repo, config.vault_path), prefix="/api")

    return app


app = create_app()

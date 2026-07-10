from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api import main as api_main


def test_create_app_does_not_mount_life_routes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        api_main,
        "load_config",
        lambda _path: SimpleNamespace(
            db_path=str(tmp_path / "captures.db"),
            vault_path=str(tmp_path / "vault"),
        ),
    )

    app = api_main.create_app()
    paths = {route.path for route in app.routes}

    assert "/api/captures" in paths
    assert not any(path.startswith("/api/life") for path in paths)


def test_create_app_serializes_requests_using_shared_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        api_main,
        "load_config",
        lambda _path: SimpleNamespace(
            db_path=str(tmp_path / "captures.db"),
            vault_path=str(tmp_path / "vault"),
        ),
    )
    app = api_main.create_app()
    first_entered = Event()
    release_first = Event()
    second_entered = Event()
    count_lock = Lock()
    request_count = 0

    @app.get("/api/concurrency-probe")
    def concurrency_probe():
        nonlocal request_count
        with count_lock:
            request_count += 1
            position = request_count
        if position == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
        else:
            second_entered.set()
        return {"ok": True}

    with TestClient(app, client=("127.0.0.1", 50000)) as client, ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client.get, "/api/concurrency-probe")
        assert first_entered.wait(timeout=2)
        second = executor.submit(client.get, "/api/concurrency-probe")
        assert not second_entered.wait(timeout=0.2)
        release_first.set()
        assert first.result(timeout=2).status_code == 200
        assert second.result(timeout=2).status_code == 200


def test_create_app_blocks_remote_clients_without_api_token(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    monkeypatch.setattr(
        api_main,
        "load_config",
        lambda _path: SimpleNamespace(
            db_path=str(tmp_path / "captures.db"),
            vault_path=str(tmp_path / "vault"),
        ),
    )
    app = api_main.create_app()

    with TestClient(app, client=("192.0.2.10", 50000)) as client:
        response = client.get("/api/captures")

    assert response.status_code == 403


def test_create_app_requires_configured_bearer_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_API_TOKEN", "test-hermes-token")
    monkeypatch.setattr(
        api_main,
        "load_config",
        lambda _path: SimpleNamespace(
            db_path=str(tmp_path / "captures.db"),
            vault_path=str(tmp_path / "vault"),
        ),
    )
    app = api_main.create_app()

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        missing = client.get("/api/captures")
        accepted = client.get(
            "/api/captures",
            headers={"Authorization": "Bearer test-hermes-token"},
        )

    assert missing.status_code == 401
    assert accepted.status_code == 200


def test_create_app_rejects_oversized_request_body(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    monkeypatch.setattr(
        api_main,
        "load_config",
        lambda _path: SimpleNamespace(
            db_path=str(tmp_path / "captures.db"),
            vault_path=str(tmp_path / "vault"),
        ),
    )
    app = api_main.create_app()

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/capture-drafts",
            headers={"Content-Length": str(11 * 1024 * 1024)},
            json={"content": "small"},
        )

    assert response.status_code == 413

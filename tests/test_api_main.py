from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api import main as api_main
from db.repo import CaptureRepo
from db.schema import init_db


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


# ---------------------------------------------------------------------------
# Host header / proxy hardening
# ---------------------------------------------------------------------------

def _app(tmp_path, monkeypatch):
    monkeypatch.setattr(
        api_main,
        "load_config",
        lambda _path: SimpleNamespace(
            db_path=str(tmp_path / "captures.db"),
            vault_path=str(tmp_path / "vault"),
        ),
    )
    return api_main.create_app()


def test_foreign_host_header_is_rejected(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        resp = client.get("/api/captures", headers={"host": "evil.example"})

    assert resp.status_code == 400


def test_loopback_host_header_is_accepted(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        resp = client.get("/api/captures", headers={"host": "127.0.0.1:8000"})

    assert resp.status_code == 200


def test_host_header_helper_defaults():
    assert api_main._host_header_allowed("localhost:8000", None)
    assert api_main._host_header_allowed("127.0.0.1:8000", None)
    assert api_main._host_header_allowed("[::1]:8000", None)
    assert not api_main._host_header_allowed("evil.example", None)
    assert api_main._host_header_allowed("evil.example", frozenset({"evil.example"}))


def test_forwarded_for_only_counts_from_trusted_proxy():
    class _Request:
        def __init__(self, peer, forwarded):
            self.client = SimpleNamespace(host=peer)
            self.headers = {"x-forwarded-for": forwarded} if forwarded else {}

    trusted = frozenset({"10.0.0.2"})
    untrusted = frozenset()

    # A direct remote peer cannot spoof loopback via X-Forwarded-For.
    assert not api_main._client_is_loopback(_Request("192.0.2.9", "127.0.0.1"), untrusted)
    # A configured proxy may report the real loopback client.
    assert api_main._client_is_loopback(_Request("10.0.0.2", "127.0.0.1"), trusted)
    # ... but not a remote client.
    assert not api_main._client_is_loopback(_Request("10.0.0.2", "203.0.113.5"), trusted)


def test_ipv4_mapped_loopback_is_recognised():
    assert api_main._is_loopback_client("::ffff:127.0.0.1")


# ---------------------------------------------------------------------------
# Chunked (no Content-Length) request bodies
# ---------------------------------------------------------------------------

def _insert_capture(db_path: str) -> str:
    conn = init_db(db_path)
    try:
        capture = CaptureRepo(conn).insert(file_path="")
    finally:
        conn.close()
    return capture["id"]


def _issue_permanent_delete_token(client, capture_id: str) -> str:
    response = client.post(
        f"/api/knowledge/library/items/capture/{capture_id}/confirmation"
    )
    assert response.status_code == 200
    return response.json()["token"]


def test_chunked_delete_body_is_parsed_by_the_route(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    app = _app(tmp_path, monkeypatch)
    capture_id = _insert_capture(str(tmp_path / "captures.db"))
    url = f"/api/knowledge/library/items/capture/{capture_id}/permanent"

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        token = _issue_permanent_delete_token(client, capture_id)

        def chunks():
            # No Content-Length is sent: httpx streams this as chunked.
            yield b'{"confirmation_token": "'
            yield token.encode()
            yield b'"}'

        response = client.request(
            "DELETE",
            url,
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "permanently_deleted"


def test_oversized_chunked_delete_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    app = _app(tmp_path, monkeypatch)
    db_path = str(tmp_path / "captures.db")
    capture_id = _insert_capture(db_path)
    url = f"/api/knowledge/library/items/capture/{capture_id}/permanent"

    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        token = _issue_permanent_delete_token(client, capture_id)

        def oversized_chunks():
            yield ('{"confirmation_token": "%s", "padding": "' % token).encode()
            for _ in range(11):
                yield b"x" * (1024 * 1024)
            yield b'"}'

        response = client.request(
            "DELETE",
            url,
            content=oversized_chunks(),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413

    conn = init_db(db_path)
    try:
        assert CaptureRepo(conn).get_by_id(capture_id) is not None
    finally:
        conn.close()


def test_missing_host_header_is_rejected():
    assert api_main._host_header_allowed("", None) is False
    assert api_main._host_header_allowed("   ", None) is False
    assert api_main._host_header_allowed("", frozenset({"localhost"})) is False


def test_health_probe_can_be_public_when_enabled(tmp_path, monkeypatch):
    """Monitoring probes may call /api/health without a token (opt-in)."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HERMES_API_TOKEN", "secret-token")
    monkeypatch.setattr(
        api_main,
        "load_config",
        lambda _path: SimpleNamespace(
            db_path=str(tmp_path / "captures.db"),
            vault_path=str(tmp_path / "vault"),
        ),
    )

    # Default: authenticated like every other route.
    monkeypatch.delenv("HERMES_HEALTH_PUBLIC", raising=False)
    app = api_main.create_app()
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 401

    # Opt-in: GET/HEAD /api/health is open, everything else still needs the token.
    monkeypatch.setenv("HERMES_HEALTH_PUBLIC", "1")
    app = api_main.create_app()
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["service"] == "hermes-knowledge"
        assert client.get("/api/captures").status_code == 401
        assert (
            client.get(
                "/api/health", headers={"Authorization": "Bearer secret-token"}
            ).status_code
            == 200
        )


def test_health_service_version_can_be_overridden(monkeypatch):
    from api.routes.health import _service_version

    monkeypatch.setenv("HERMES_KNOWLEDGE_VERSION", "2026.11.14-test")
    assert _service_version() == "2026.11.14-test"

    monkeypatch.delenv("HERMES_KNOWLEDGE_VERSION", raising=False)
    version = _service_version()
    assert isinstance(version, str) and version.strip()

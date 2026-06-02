from __future__ import annotations

from types import SimpleNamespace

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


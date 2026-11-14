"""Resolve the Hermes Knowledge config file for bridge modules.

Every bridge used to hard-code ``Path(__file__).parent.parent / "config.yaml"``,
which made ``HERMES_CONFIG_PATH`` (honoured by the API and CLI) useless and
crashed any bridge imported from a checkout without a local ``config.yaml``
(the test suite, CI, wheel-installed tooling).

Centralising the lookup means one env var controls the whole service and
callers get a usable default config instead of a ``FileNotFoundError``.
"""
from __future__ import annotations

import os
from pathlib import Path


def default_config_path() -> Path:
    """Return the configured knowledge config path (env wins)."""
    configured = (os.environ.get("HERMES_CONFIG_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "config.yaml"


def load_bridge_config(path: str | Path | None = None, *, loader=None):
    """Load the bridge config, falling back to defaults when it is absent.

    ``loader`` is the calling module's ``load_config`` symbol, passed
    explicitly so tests that monkeypatch it keep working.

    Production deployments ship a config.yaml; tests, CI and tooling that only
    exercise explicit ``db_path``/``vault_path`` arguments must not fail on a
    missing file.
    """
    from agents.capture_agent import CaptureConfig, load_config

    resolve = loader or load_config
    target = Path(path).expanduser() if path is not None else default_config_path()
    try:
        return resolve(str(target))
    except FileNotFoundError:
        return CaptureConfig()

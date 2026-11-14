"""Resolve the Hermes Knowledge config file for bridge modules.

Every bridge used to hard-code ``Path(__file__).parent.parent / "config.yaml"``,
which made ``HERMES_CONFIG_PATH`` (honoured by the API and CLI) useless and
crashed any bridge imported from a checkout without a local ``config.yaml``
(the test suite, CI, wheel-installed tooling).

Centralising the lookup means one env var controls the whole service and
callers get a usable default config instead of a ``FileNotFoundError``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _checkout_config_path() -> Path:
    """Return ``<package parent>/config.yaml`` for the *imported* checkout.

    Deliberately does **not** call ``Path.resolve()``: the product E2E imports
    the service through ``<work-dir>/custom-root/bridge`` (a symlink into the
    checkout).  Resolving it would jump to the checkout root, where CI has no
    ``config.yaml``, and the bridge would silently fall back to
    ``CaptureConfig()`` defaults — writing to ``./data/captures.db`` and
    ``~/Hermes/knowledge`` instead of the isolated root.  That split an E2E run
    across two databases (browser draft 200 -> read 404).  Keep the symlink
    path so the config stays next to the root that launched the process.
    """
    return Path(__file__).parent.parent / "config.yaml"


def default_config_path() -> Path:
    """Return the configured knowledge config path (env wins)."""
    configured = (os.environ.get("HERMES_CONFIG_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser()
    # The dashboard sets this so in-process bridge calls use the same config
    # (and therefore the same DB/vault) as the service it proxies.
    custom_root = (os.environ.get("HERMES_CUSTOM_ROOT") or "").strip()
    if custom_root:
        candidate = Path(custom_root).expanduser() / "config.yaml"
        if candidate.exists():
            return candidate
    return _checkout_config_path()


def load_bridge_config(path: str | Path | None = None, *, loader=None):
    """Load the bridge config, falling back to defaults when it is absent.

    ``loader`` is the calling module's ``load_config`` symbol, passed
    explicitly so tests that monkeypatch it keep working.

    Production deployments ship a config.yaml; tests, CI and tooling that only
    exercise explicit ``db_path``/``vault_path`` arguments must not fail on a
    missing file.  The fallback is logged because it silently redirects writes
    to ``CaptureConfig()``'s defaults, which is never what a deployment wants.
    """
    from agents.capture_agent import CaptureConfig, load_config

    resolve = loader or load_config
    target = Path(path).expanduser() if path is not None else default_config_path()
    try:
        return resolve(str(target))
    except FileNotFoundError:
        logger.warning(
            "Knowledge config %s not found; falling back to CaptureConfig() "
            "defaults (db_path=%s). Set HERMES_CONFIG_PATH or ship a "
            "config.yaml next to the package.",
            target,
            CaptureConfig().db_path,
        )
        return CaptureConfig()

"""Regression tests for bridge config resolution (``bridge/config_paths.py``).

The product E2E imports the service through ``<work-dir>/custom-root/bridge``,
a symlink into the checkout, so the bridge config has to be resolved next to
the *importing* root.  Resolving the symlink silently sent every bridge to
``CaptureConfig()`` defaults: drafts landed in ``./data/captures.db`` while the
dashboard read ``HERMES_KNOWLEDGE_DB``, which surfaced as a browser
``POST 200`` immediately followed by ``GET .../capture-drafts/<id> 404`` and an
empty library.
"""
import importlib.util
import sys
from pathlib import Path

from bridge import config_paths
from tests.conftest import requires_symlink


def test_env_override_wins(monkeypatch, tmp_path):
    target = tmp_path / "elsewhere.yaml"
    monkeypatch.setenv("HERMES_CONFIG_PATH", str(target))
    monkeypatch.setenv("HERMES_CUSTOM_ROOT", str(tmp_path))
    assert config_paths.default_config_path() == target


def test_custom_root_config_used_when_present(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
    (tmp_path / "config.yaml").write_text("database:\n  path: isolated.db\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_CUSTOM_ROOT", str(tmp_path))
    assert config_paths.default_config_path() == tmp_path / "config.yaml"


def test_custom_root_without_config_falls_through(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
    monkeypatch.setenv("HERMES_CUSTOM_ROOT", str(tmp_path))
    assert config_paths.default_config_path() == config_paths._checkout_config_path()


@requires_symlink
def test_imported_through_symlink_keeps_its_own_root(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
    monkeypatch.delenv("HERMES_CUSTOM_ROOT", raising=False)
    work_root = tmp_path / "custom-root"
    work_root.mkdir()
    (work_root / "config.yaml").write_text("database:\n  path: isolated.db\n", encoding="utf-8")
    (work_root / "bridge").symlink_to(
        Path(config_paths.__file__).parent, target_is_directory=True
    )

    spec = importlib.util.spec_from_file_location(
        "symlinked_bridge_config_paths", work_root / "bridge" / "config_paths.py"
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        assert module.default_config_path() == work_root / "config.yaml"
    finally:
        sys.modules.pop("symlinked_bridge_config_paths", None)


def test_missing_config_falls_back_to_defaults(monkeypatch, tmp_path):
    from agents.capture_agent import CaptureConfig

    monkeypatch.delenv("HERMES_CONFIG_PATH", raising=False)
    monkeypatch.delenv("HERMES_CUSTOM_ROOT", raising=False)
    config = config_paths.load_bridge_config(tmp_path / "nope" / "config.yaml")
    assert config.db_path == CaptureConfig().db_path

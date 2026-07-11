import importlib
from types import SimpleNamespace

from scripts import knowledge_pipeline_e2e
from scripts import knowledge_product_e2e


def test_runtime_root_can_be_overridden_by_environment(monkeypatch, tmp_path):
    original_pipeline_root = knowledge_pipeline_e2e.RUNTIME_ROOT
    original_product_root = knowledge_product_e2e.RUNTIME_ROOT

    with monkeypatch.context() as scoped:
        scoped.setenv("HERMES_RUNTIME_ROOT", str(tmp_path))
        importlib.reload(knowledge_pipeline_e2e)
        importlib.reload(knowledge_product_e2e)

        assert knowledge_pipeline_e2e.RUNTIME_ROOT == tmp_path
        assert knowledge_product_e2e.RUNTIME_ROOT == tmp_path

    importlib.reload(knowledge_pipeline_e2e)
    importlib.reload(knowledge_product_e2e)
    assert knowledge_pipeline_e2e.RUNTIME_ROOT == original_pipeline_root
    assert knowledge_product_e2e.RUNTIME_ROOT == original_product_root


def test_complete_product_e2e_requires_service_and_browser_layers(monkeypatch, tmp_path):
    calls = {}

    def fake_service(*, work_dir, live_providers):
        calls["service"] = {"work_dir": work_dir, "live_providers": live_providers}
        return {"ok": True, "pipelines": [{"name": "capture", "ok": True}]}

    def fake_run(command, **kwargs):
        calls["browser"] = {"command": command, "cwd": kwargs["cwd"]}
        return SimpleNamespace(returncode=0, stdout="6 passed")

    monkeypatch.setattr(knowledge_product_e2e, "run_pipeline_e2e", fake_service)
    monkeypatch.setattr(knowledge_product_e2e.subprocess, "run", fake_run)

    result = knowledge_product_e2e.run_knowledge_product_e2e(
        work_dir=tmp_path,
        live_providers=True,
    )

    assert result["ok"] is True
    assert calls["service"] == {"work_dir": tmp_path, "live_providers": True}
    assert calls["browser"]["command"] == ["npm", "run", "test:knowledge:complete"]
    assert calls["browser"]["cwd"] == knowledge_product_e2e.RUNTIME_ROOT / "web"


def test_complete_product_e2e_fails_when_browser_layer_fails(monkeypatch):
    monkeypatch.setattr(
        knowledge_product_e2e,
        "run_pipeline_e2e",
        lambda **_kwargs: {"ok": True, "pipelines": []},
    )
    monkeypatch.setattr(
        knowledge_product_e2e.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="browser failed"),
    )

    result = knowledge_product_e2e.run_knowledge_product_e2e()

    assert result["ok"] is False
    assert result["layers"]["browser_workbench"]["output_tail"] == "browser failed"

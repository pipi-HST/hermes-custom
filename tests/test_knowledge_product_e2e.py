import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from scripts import knowledge_pipeline_e2e
from scripts import knowledge_product_e2e


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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

    def fake_browser_runner(**_kwargs):
        raise AssertionError("service orchestrator owns browser runner execution")

    def fake_service(*, work_dir, live_providers, product_runner):
        calls["service"] = {
            "work_dir": work_dir,
            "live_providers": live_providers,
            "product_runner": product_runner,
        }
        return {
            "ok": True,
            "pipelines": [{"name": "capture", "ok": True}],
            "product_acceptance": {"ok": True, "pipelines": [{"name": "browser", "ok": True}]},
        }

    monkeypatch.setattr(knowledge_product_e2e, "run_pipeline_e2e", fake_service)
    monkeypatch.setattr(
        knowledge_product_e2e,
        "run_real_browser_acceptance",
        fake_browser_runner,
    )

    result = knowledge_product_e2e.run_knowledge_product_e2e(
        work_dir=tmp_path,
        live_providers=True,
    )

    assert result["ok"] is True
    assert calls["service"] == {
        "work_dir": tmp_path,
        "live_providers": True,
        "product_runner": fake_browser_runner,
    }
    assert result["layers"]["browser_workbench"] == {
        "ok": True,
        "pipelines": [{"name": "browser", "ok": True}],
    }


def test_complete_product_e2e_fails_when_browser_layer_fails(monkeypatch):
    monkeypatch.setattr(
        knowledge_product_e2e,
        "run_pipeline_e2e",
        lambda **_kwargs: {
            "ok": False,
            "pipelines": [],
            "product_acceptance": {
                "ok": False,
                "pipelines": [{"name": "browser", "ok": False}],
            },
        },
    )

    result = knowledge_product_e2e.run_knowledge_product_e2e()

    assert result["ok"] is False
    assert result["layers"]["browser_workbench"] == {
        "ok": False,
        "pipelines": [{"name": "browser", "ok": False}],
    }


def test_browser_rag_queries_the_browser_capture_signal(monkeypatch, tmp_path):
    observed = {}
    result = {
        "status": "success",
        "source_map": [{"id": "browser-capture"}],
        "meta": {
            "result_count": 1,
            "used_index": True,
            "trace": {},
            "citation_validation": {
                "valid": True,
                "missing_citations": False,
                "cited_source_ids": ["browser-capture"],
            },
        },
    }

    def fake_run(command, **_kwargs):
        observed["code"] = command[-1]
        return SimpleNamespace(
            returncode=0,
            stdout="E2E_RESULT=" + json.dumps(result),
            stderr="",
        )

    monkeypatch.setattr(knowledge_pipeline_e2e.subprocess, "run", fake_run)
    pipeline = knowledge_pipeline_e2e._rag_pipeline(
        {"custom_root": tmp_path},
        {},
        {"details": {"capture_id": "browser-capture"}},
        query_signal=knowledge_pipeline_e2e.BROWSER_E2E_SIGNAL,
    )

    assert knowledge_pipeline_e2e.BROWSER_E2E_SIGNAL in observed["code"]
    assert pipeline["ok"] is True


def test_ci_installs_bridge_dependency_into_runtime_environment():
    workflow = (
        PROJECT_ROOT / ".github/workflows/knowledge-product-e2e.yml"
    ).read_text(encoding="utf-8")

    assert workflow.count(
        "uv pip install --python .venv/bin/python numpy==2.4.6"
    ) == 2


def test_ci_runs_real_browser_product_pipeline_for_deterministic_and_live_jobs():
    workflow = (
        PROJECT_ROOT / ".github/workflows/knowledge-product-e2e.yml"
    ).read_text(encoding="utf-8")

    assert workflow.count("cache-dependency-path: runtime/web/package-lock.json") == 2
    assert workflow.count("working-directory: runtime/web") == 4
    assert workflow.count("run: npx playwright install --with-deps chromium") == 2
    assert "run: .venv/bin/python scripts/knowledge_product_e2e.py\n" in workflow
    assert (
        "run: .venv/bin/python scripts/knowledge_product_e2e.py --live-providers"
        in workflow
    )

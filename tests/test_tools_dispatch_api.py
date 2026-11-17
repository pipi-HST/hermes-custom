"""Whitelisted bridge-tool dispatch endpoint (contract v1.1 tools)."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from agents.capture_agent import CaptureConfig
from api import main as api_main
from api.routes.tools import _BRIDGE_TOOL_WHITELIST
from bridge.context import KnowledgeContext

LOOPBACK = ("127.0.0.1", 50000)


def _make_app(tmp_path, monkeypatch, *, token: str | None = None):
    if token is None:
        monkeypatch.delenv("HERMES_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("HERMES_API_TOKEN", token)
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    config = CaptureConfig(
        llm_api_key="sk-test",
        qwen_api_key="",
        llm_base_url="",
        vault_path=str(vault),
        db_path=str(tmp_path / "captures.db"),
    )
    return config, api_main.create_app(config=config), vault


@pytest.fixture
def env(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch)


def _client(app):
    return TestClient(app, client=LOOPBACK)


def _dispatch(client, module: str, function: str, *, args=None, kwargs=None, **request_kwargs):
    return client.post(
        f"/api/tools/{module}/{function}",
        json={"args": args or [], "kwargs": kwargs or {}},
        **request_kwargs,
    )


def test_unknown_module_and_function_are_rejected(env):
    _, app, _ = env
    with _client(app) as client:
        for module, function in (
            ("evil_module", "run"),
            ("os", "system"),
            ("capture_bridge", "os_system"),
            ("capture_bridge", "__import__"),
            ("capture_bridge", "_load_whitelisted_module"),
            ("bridge.capture_bridge", "capture_content"),
            ("CAPTURE_BRIDGE", "capture_content"),
        ):
            response = _dispatch(client, module, function)
            assert response.status_code == 404, (module, function)
            assert response.json()["detail"] == "unknown tool"


def test_whitelist_covers_runtime_tool_calls():
    expected = {
        "agent_run_bridge": {"get_agent_run_status"},
        "answer_bridge": {"revise_answer_draft", "save_answer_to_knowledge"},
        "canvas_bridge": {"generate_canvas"},
        "capture_bridge": {
            "capture_content",
            "commit_capture_draft",
            "create_editable_capture_draft",
            "revise_capture_draft",
            "show_capture_draft",
        },
        "garden_bridge": {
            "commit_garden_draft",
            "revise_garden_draft",
            "synthesize_garden",
        },
        "librarian_bridge": {"run_librarian_audit"},
        "rag_bridge": {"search_knowledge_base"},
        "rag_continue_bridge": {"continue_latest_rag_answer"},
        "review_bridge": {
            "expand_latest_review_followup",
            "record_review_feedback",
            "run_daily_review",
        },
        "theme_bridge": {
            "list_theme_candidates",
            "run_graph_report",
            "run_theme_scan",
        },
    }
    for module_key, functions in expected.items():
        import_path, allowed = _BRIDGE_TOOL_WHITELIST[module_key]
        assert import_path == f"bridge.{module_key}"
        assert functions <= allowed
        module = importlib.import_module(import_path)
        for function_name in functions:
            assert callable(getattr(module, function_name, None)), (
                module_key,
                function_name,
            )
    for _, allowed in _BRIDGE_TOOL_WHITELIST.values():
        assert all(not name.startswith("_") for name in allowed)


def test_dispatch_forwards_args_and_injects_context(env, monkeypatch):
    config, app, vault = env
    from bridge import capture_bridge

    seen: dict = {}

    def fake_show_capture_draft(draft_id, page=1, as_file=False, context=None):
        seen.update(
            {
                "draft_id": draft_id,
                "page": page,
                "as_file": as_file,
                "context": context,
            }
        )
        return {"status": "success", "draft_id": draft_id, "page": page}

    monkeypatch.setattr(
        capture_bridge, "show_capture_draft", fake_show_capture_draft
    )

    with _client(app) as client:
        response = _dispatch(
            client,
            "capture_bridge",
            "show_capture_draft",
            args=["draft-1", 2, True],
        )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "draft_id": "draft-1", "page": 2}
    assert seen["draft_id"] == "draft-1"
    assert seen["page"] == 2
    assert seen["as_file"] is True
    assert isinstance(seen["context"], KnowledgeContext)
    assert str(seen["context"].vault_path) == str(vault)
    assert seen["context"].conn is not None


def test_caller_context_and_token_verified_are_dropped(env, monkeypatch):
    _, app, vault = env
    from bridge import capture_bridge

    seen: dict = {}

    def fake_commit(draft_id="", confirmation="", context=None, token_verified=False):
        seen.update(
            {
                "draft_id": draft_id,
                "confirmation": confirmation,
                "context": context,
                "token_verified": token_verified,
            }
        )
        return {"status": "success"}

    monkeypatch.setattr(capture_bridge, "commit_capture_draft", fake_commit)

    with _client(app) as client:
        response = _dispatch(
            client,
            "capture_bridge",
            "commit_capture_draft",
            kwargs={
                "draft_id": "draft-1",
                "confirmation": "确认存库",
                "context": {"vault_path": "/evil"},
                "token_verified": True,
            },
        )

    assert response.status_code == 200
    assert seen["draft_id"] == "draft-1"
    assert seen["confirmation"] == "确认存库"
    # Caller-supplied context/token_verified must never win over the service's.
    assert isinstance(seen["context"], KnowledgeContext)
    assert str(seen["context"].vault_path) == str(vault)
    assert seen["token_verified"] is False


def test_function_without_context_parameter_is_not_given_one(env, monkeypatch):
    _, app, _ = env
    from bridge import agent_run_bridge

    seen: dict = {}

    def fake_get_agent_run_status(limit=5):
        seen["limit"] = limit
        return {"status": "success", "runs": []}

    monkeypatch.setattr(
        agent_run_bridge, "get_agent_run_status", fake_get_agent_run_status
    )

    with _client(app) as client:
        response = _dispatch(
            client,
            "agent_run_bridge",
            "get_agent_run_status",
            kwargs={"limit": 7},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "runs": []}
    assert seen == {"limit": 7}


def test_exception_becomes_structured_500_without_traceback(env, monkeypatch):
    _, app, _ = env
    from bridge import theme_bridge

    def boom(**kwargs):
        raise RuntimeError("synthesizer exploded")

    monkeypatch.setattr(theme_bridge, "run_graph_report", boom)

    with _client(app) as client:
        response = _dispatch(client, "theme_bridge", "run_graph_report")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail == {"error_type": "RuntimeError", "message": "synthesizer exploded"}
    assert "Traceback" not in response.text
    assert "run_graph_report" not in response.text


def test_non_dict_serializable_returns_value_and_unserializable_is_500(
    env, monkeypatch
):
    _, app, _ = env
    from bridge import review_bridge, theme_bridge

    monkeypatch.setattr(
        theme_bridge, "run_graph_report", lambda **kwargs: [{"node": 1}]
    )
    monkeypatch.setattr(
        review_bridge, "run_daily_review", lambda: object()
    )

    with _client(app) as client:
        listed = _dispatch(client, "theme_bridge", "run_graph_report")
        unserializable = _dispatch(client, "review_bridge", "run_daily_review")

    assert listed.status_code == 200
    assert listed.json() == [{"node": 1}]
    assert unserializable.status_code == 500
    assert (
        unserializable.json()["detail"]["error_type"] == "SerializationError"
    )


def test_empty_call_and_body_validation(env, monkeypatch):
    _, app, _ = env
    from bridge import review_bridge

    monkeypatch.setattr(
        review_bridge, "run_daily_review", lambda: {"status": "success"}
    )

    with _client(app) as client:
        empty = client.post("/api/tools/review_bridge/run_daily_review", json={})
        assert empty.status_code == 200
        assert empty.json() == {"status": "success"}

        bad_args = client.post(
            "/api/tools/review_bridge/run_daily_review",
            json={"args": "not-a-list"},
        )
        bad_kwargs = client.post(
            "/api/tools/review_bridge/run_daily_review",
            json={"kwargs": ["not", "a", "dict"]},
        )

    assert bad_args.status_code == 422
    assert bad_kwargs.status_code == 422


def test_dispatch_is_behind_auth_middleware(tmp_path, monkeypatch):
    config, app, _ = _make_app(tmp_path, monkeypatch, token="secret-token")
    from bridge import agent_run_bridge

    monkeypatch.setattr(
        agent_run_bridge,
        "get_agent_run_status",
        lambda **kwargs: {"status": "success"},
    )

    with TestClient(app, client=LOOPBACK) as client:
        anonymous = _dispatch(client, "agent_run_bridge", "get_agent_run_status")
        authorized = _dispatch(
            client,
            "agent_run_bridge",
            "get_agent_run_status",
            headers={"Authorization": "Bearer secret-token"},
        )

    assert anonymous.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json() == {"status": "success"}


def test_reading_and_research_bridges_are_whitelisted(env, monkeypatch):
    """The remaining envelope tools call these modules directly."""
    _, app, _ = env
    from bridge import reading_bridge, research_bridge

    calls: dict[str, dict] = {}

    def fake_show(**kwargs):
        calls["show"] = kwargs
        return {"status": "success", "session": {"id": "s1"}}

    async def fake_research(**kwargs):
        calls["research"] = kwargs
        return {"status": "success", "answer": "ok"}

    monkeypatch.setattr(reading_bridge, "show_reading_session", fake_show)
    monkeypatch.setattr(research_bridge, "async_web_assist_answer", fake_research)

    with _client(app) as client:
        shown = _dispatch(client, "reading_bridge", "show_reading_session")
        researched = _dispatch(
            client, "research_bridge", "async_web_assist_answer", kwargs={"query": "q"}
        )

    assert shown.status_code == 200
    assert shown.json()["session"]["id"] == "s1"
    assert researched.status_code == 200
    assert researched.json()["answer"] == "ok"
    assert calls["research"]["query"] == "q"

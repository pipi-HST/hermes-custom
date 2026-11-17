"""Contract tests for CaptureIntent and the Capture/Library directory seam."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import agents.capture_agent as capture_agent
from agents import directory_contract
from agents.capture_agent import (
    CaptureIntent,
    capture_intent_schema,
    validate_capture_intent,
)


ROOT = Path(__file__).resolve().parents[1]


def _valid_intent() -> dict:
    return {
        "user_request": "整理成笔记",
        "material_intent": "refined_note",
        "task_type": "organize",
        "coverage": "full_article",
        "emphasis": [],
        "must_preserve": ["source_fidelity", "main_thread", "user_named_focus"],
        "output_shape": ["natural_draft"],
        "detail_level": "medium",
        "draft_guidance": "let_model_decide",
        "compression": "medium",
        "preserve": [],
        "plan_source": "fallback",
        "detail_policy": "",
        "semantic_title": "",
    }


def test_every_dataclass_field_is_documented() -> None:
    doc = (ROOT / "docs" / "capture-intent.md").read_text(encoding="utf-8")
    missing = [
        name
        for name in CaptureIntent.__dataclass_fields__
        if f"`{name}`" not in doc
    ]
    assert missing == []


def test_schema_covers_all_fields() -> None:
    schema = capture_intent_schema()
    for name in CaptureIntent.__dataclass_fields__:
        assert name in schema["properties"]
    assert schema["additionalProperties"] is True


def test_valid_intent_has_no_issues() -> None:
    assert validate_capture_intent(_valid_intent()) == []


def test_invalid_enum_is_reported() -> None:
    value = _valid_intent()
    value["compression"] = "maximum"
    issues = validate_capture_intent(value)
    assert any("compression" in issue for issue in issues)


def test_invalid_list_item_is_reported() -> None:
    value = _valid_intent()
    value["output_shape"] = ["full_text", "something_else"]
    issues = validate_capture_intent(value)
    assert any("output_shape" in issue for issue in issues)


def test_unknown_keys_are_reported_and_preserved() -> None:
    value = _valid_intent()
    value["entry_mode"] = "editable_capture"
    issues = validate_capture_intent(value)
    assert any("unknown key preserved: entry_mode" in issue for issue in issues)
    assert value["entry_mode"] == "editable_capture"


def test_capture_agent_does_not_import_private_library_helpers() -> None:
    source = inspect.getsource(capture_agent)
    for name in (
        "_directory_absolute_url_free",
        "_directory_links_match_content_headings",
        "_has_capture_processing_note",
        "_has_directory_content_heading_coverage_gap",
    ):
        assert name not in source


def test_directory_contract_exports_public_callables() -> None:
    for name in directory_contract.__all__:
        assert callable(getattr(directory_contract, name))

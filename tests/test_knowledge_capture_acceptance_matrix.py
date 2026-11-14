from __future__ import annotations
from tests.conftest import requires_runtime, requires_symlink  # noqa: E402


def test_knowledge_capture_acceptance_matrix_runs_all_scenarios(tmp_path):
    from scripts.knowledge_capture_acceptance_matrix import run_acceptance_matrix

    result = run_acceptance_matrix(work_dir=tmp_path)

    assert result["ok"] is True
    scenarios = {item["name"]: item for item in result["scenarios"]}
    assert {
        "editable_draft_metadata_contract",
        "editable_draft_parse_failure_defaults",
        "commit_reuses_metadata_and_writes_frontmatter",
        "long_commit_outline_and_duplicate_heading_repair",
        "link_capture_stage_timings",
        "frontend_legacy_empty_suggestion_fallbacks",
    } <= set(scenarios)
    assert all(item["ok"] for item in scenarios.values())

pytestmark = requires_runtime

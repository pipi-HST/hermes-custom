import json

from scripts import promote_research_fixture


def _fixture(case_id="new_case"):
    return {
        "id": case_id,
        "category": "concept",
        "mode": "web_only",
        "query": "什么是测试",
        "planner": {"queries": ["什么是测试"]},
        "search_results": [{"title": "测试", "url": "https://example.com"}],
        "extract_results": [{"url": "https://example.com", "content": "测试内容"}],
        "critic_decisions": [],
        "expect": {"web_search_attempted": True},
    }


def test_promote_research_fixture_dry_run_does_not_append(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    fixture_path = tmp_path / "case.fixture.json"
    fixture_path.write_text(json.dumps(_fixture(), ensure_ascii=False), encoding="utf-8")

    result = promote_research_fixture.promote_fixture(fixture_path, cases_path, dry_run=True)

    assert result["id"] == "new_case"
    assert not cases_path.exists()


def test_promote_research_fixture_appends_normalized_jsonl(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    fixture_path = tmp_path / "case.fixture.json"
    fixture_path.write_text(json.dumps(_fixture(), ensure_ascii=False), encoding="utf-8")

    promote_research_fixture.promote_fixture(fixture_path, cases_path, dry_run=False)

    lines = cases_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["id"] == "new_case"
    assert list(payload)[:4] == ["id", "category", "mode", "query"]


def test_promote_research_fixture_rejects_duplicate_id(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(json.dumps(_fixture("new_case"), ensure_ascii=False) + "\n", encoding="utf-8")
    fixture_path = tmp_path / "case.fixture.json"
    fixture_path.write_text(json.dumps(_fixture("new_case"), ensure_ascii=False), encoding="utf-8")

    try:
        promote_research_fixture.promote_fixture(fixture_path, cases_path, dry_run=False)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate fixture id should fail")

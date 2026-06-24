from scripts import eval_research_quality


def test_load_runtime_env_delegates_to_runtime_loader(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    called = {}

    def fake_loader(**kwargs):
        called.update(kwargs)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        return [tmp_path / ".env"]

    loaded = eval_research_quality.load_runtime_env(
        hermes_home=tmp_path,
        project_env=tmp_path / "project.env",
        loader=fake_loader,
    )

    assert loaded == [tmp_path / ".env"]
    assert called == {"hermes_home": tmp_path, "project_env": tmp_path / "project.env"}
    assert eval_research_quality.os.environ["DASHSCOPE_API_KEY"] == "test-key"


def test_load_runtime_env_falls_back_without_python_dotenv(tmp_path, monkeypatch):
    hermes_home = tmp_path / "home"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text(
        "DASHSCOPE_API_KEY=test-key\nDASHSCOPE_WORKSPACE_ID=workspace-1\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_WORKSPACE_ID", raising=False)

    def broken_loader(**_kwargs):
        raise ModuleNotFoundError("dotenv")

    loaded = eval_research_quality.load_runtime_env(
        hermes_home=hermes_home,
        project_env=tmp_path / "missing.env",
        loader=broken_loader,
    )

    assert loaded == [hermes_home / ".env"]
    assert eval_research_quality.os.environ["DASHSCOPE_API_KEY"] == "test-key"
    assert eval_research_quality.os.environ["DASHSCOPE_WORKSPACE_ID"] == "workspace-1"


def test_live_run_loads_runtime_env_before_research(monkeypatch):
    called = []

    def fake_load_runtime_env():
        called.append("loaded")
        return []

    def fake_research_answer(**_kwargs):
        assert called == ["loaded"]
        return {"meta": {}, "chat_preview": "ok", "warnings": []}

    monkeypatch.setattr(eval_research_quality, "load_runtime_env", fake_load_runtime_env)
    monkeypatch.setattr(eval_research_quality.research_bridge, "research_answer", fake_research_answer)

    result = eval_research_quality.run_case({"id": "live_env", "query": "test"}, live=True)

    assert result["passed"] is True


def test_research_quality_fixture_eval_passes():
    cases = eval_research_quality.load_cases(eval_research_quality.DEFAULT_CASES)

    results = [eval_research_quality.run_case(case) for case in cases]

    failures = {
        item["id"]: item["failures"]
        for item in results
        if not item["passed"]
    }
    assert failures == {}


def test_research_quality_eval_writes_jsonl_and_markdown_reports(tmp_path):
    cases = eval_research_quality.load_cases(eval_research_quality.DEFAULT_CASES)[:2]
    jsonl_path = tmp_path / "eval.jsonl"
    md_path = tmp_path / "eval.md"

    exit_code = eval_research_quality.main(
        [
            "--ids",
            *[case["id"] for case in cases],
            "--output-jsonl",
            str(jsonl_path),
            "--output-md",
            str(md_path),
        ]
    )

    assert exit_code == 0
    assert len(jsonl_path.read_text(encoding="utf-8").splitlines()) == 2
    markdown = md_path.read_text(encoding="utf-8")
    assert "Hermes Research Quality Eval" in markdown
    assert "| status | id | category | stage | adopted | rejected | failures |" in markdown


def test_run_case_can_capture_research_stage_io():
    case = eval_research_quality.load_cases(eval_research_quality.DEFAULT_CASES)[0]

    result = eval_research_quality.run_case(case, capture_io=True)

    assert result["passed"] is True
    stages = [record["stage"] for record in result["io_capture"]]
    assert "planner" in stages
    assert "search" in stages
    assert "extract" in stages
    assert "critic" in stages
    assert "synthesis" in stages


def test_capture_call_records_stage_start_before_completion():
    records = []
    wrapped = eval_research_quality._capture_call(
        "search",
        lambda query, limit: {"success": True, "query": query, "limit": limit},
        records,
    )

    wrapped("OpenAI", 3)

    assert records[0]["stage"] == "search"
    assert records[0]["status"] == "started"
    assert records[1]["stage"] == "search"
    assert records[1]["status"] == "completed"
    assert records[1]["output"]["success"] is True


def test_failure_capture_writes_io_and_fixture_draft(tmp_path):
    case = dict(eval_research_quality.load_cases(eval_research_quality.DEFAULT_CASES)[0])
    case["id"] = "capture_failure_case"
    case["expect"] = {**(case.get("expect") or {}), "min_adopted_web": 99}
    result = eval_research_quality.run_case(case, capture_io=True)

    capture_files = eval_research_quality.write_failure_capture(tmp_path, case, result)

    assert result["passed"] is False
    io_payload = eval_research_quality.json.loads(
        eval_research_quality.Path(capture_files["io"]).read_text(encoding="utf-8")
    )
    fixture_payload = eval_research_quality.json.loads(
        eval_research_quality.Path(capture_files["fixture"]).read_text(encoding="utf-8")
    )
    assert io_payload["io_capture"]
    assert fixture_payload["id"] == "capture_failure_case"
    assert fixture_payload["planner"]
    assert fixture_payload["search_results"]
    assert fixture_payload["extract_results"]
    assert fixture_payload["critic_decisions"]


def test_canary_selection_uses_fixed_low_cost_suite():
    cases = eval_research_quality.load_cases(eval_research_quality.DEFAULT_CASES)

    selected = eval_research_quality.select_cases(cases, canary=True)

    assert [case["id"] for case in selected] == list(eval_research_quality.DEFAULT_LIVE_CANARY_IDS)


def test_live_budget_blocks_accidental_full_suite():
    cases = [{"id": f"case-{index}"} for index in range(6)]

    error = eval_research_quality.live_budget_error(
        cases,
        live=True,
        max_live_cases=5,
    )

    assert "Refusing to run 6 live cases" in error


def test_live_case_timeout_exceeds_research_answer_budget():
    assert (
        eval_research_quality.DEFAULT_LIVE_CASE_TIMEOUT_SECONDS
        > eval_research_quality.research_bridge.RESEARCH_ANSWER_TIMEOUT_SECONDS
    )


def test_canary_report_marks_suite_without_live_api(tmp_path):
    jsonl_path = tmp_path / "canary.jsonl"
    md_path = tmp_path / "canary.md"

    exit_code = eval_research_quality.main(
        [
            "--canary",
            "--output-jsonl",
            str(jsonl_path),
            "--output-md",
            str(md_path),
        ]
    )

    assert exit_code == 0
    assert len(jsonl_path.read_text(encoding="utf-8").splitlines()) == len(eval_research_quality.DEFAULT_LIVE_CANARY_IDS)
    markdown = md_path.read_text(encoding="utf-8")
    assert "- mode: fixture" in markdown
    assert "- suite: canary" in markdown


def test_live_claim_grading_accepts_planner_claims_and_generic_total_claim():
    meta = {
        "research_plan": {
            "answer_claims_needed": ["explain what a coding agent is and its key characteristics"]
        }
    }

    claim_ids = eval_research_quality._claim_ids_for_grading(
        "definition",
        meta,
        live=True,
        allow_generic=True,
    )

    assert claim_ids == [
        "explain what a coding agent is and its key characteristics",
        "answer_user_question",
    ]


def test_live_clue_title_expectations_only_apply_to_seen_candidates():
    case = {
        "expect": {
            "min_clue_web": 3,
            "must_clue_title_contains": ["金融波动", "历史波动率", "波动率微笑"],
        },
    }
    result = {
        "meta": {
            "web_sources": [
                {
                    "title": "联网搜索结果：",
                    "domain": "investopedia.com",
                    "supports_claims": ["answer_user_question"],
                }
            ],
            "rejected_sources": [
                {"title": "Volatility - Overview", "domain": "corporatefinanceinstitute.com"}
            ],
            "clue_sources": [],
            "research_trace": {"source_counts": {"adopted_independent_domains": 1}},
        },
        "chat_preview": "波动率解释 [W1]",
        "warnings": [],
    }

    assert eval_research_quality.grade_case(case, result, live=True) == []


def test_live_clue_minimum_is_capped_by_seen_target_titles():
    case = {
        "expect": {
            "min_clue_web": 3,
            "must_clue_title_contains": ["金融波动", "历史波动率", "波动率微笑"],
        },
    }
    result = {
        "meta": {
            "web_sources": [{"title": "波动率", "domain": "baike.baidu.com"}],
            "rejected_sources": [],
            "clue_sources": [
                {"title": "金融波动", "domain": "baike.baidu.com"},
                {"title": "波动率微笑", "domain": "baike.baidu.com"},
            ],
            "research_trace": {"source_counts": {"adopted_independent_domains": 1}},
        },
        "chat_preview": "波动率解释 [W1]",
        "warnings": [],
    }

    assert eval_research_quality.grade_case(case, result, live=True) == []


def test_live_sufficient_evidence_can_exceed_fixture_limited_upper_bounds():
    case = {
        "expect": {
            "max_adopted_web": 1,
            "max_adopted_independent_domains": 1,
            "evidence_status": "limited",
            "warning_must_contain": ["当前仅找到 1 个"],
        },
    }
    result = {
        "meta": {
            "web_sources": [
                {"title": "波动率", "domain": "baike.baidu.com", "source_authority": "reputable_secondary"},
                {
                    "title": "Volatility - Overview",
                    "domain": "corporatefinanceinstitute.com",
                    "source_authority": "reputable_secondary",
                },
            ],
            "rejected_sources": [],
            "clue_sources": [],
            "search_quality": {"evidence_status": "sufficient"},
            "research_trace": {"source_counts": {"adopted_independent_domains": 2}},
        },
        "chat_preview": "波动率解释 [W1][W2]",
        "warnings": [],
    }

    assert eval_research_quality.grade_case(case, result, live=True) == []

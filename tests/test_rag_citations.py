from agents.rag_citations import validate_citations


def _sources(count: int = 2) -> list[dict]:
    return [
        {
            "role": "evidence",
            "type": "capture_note",
            "id": f"cap{index}",
            "title": f"Source {index}",
            "path": f"captures/source-{index}.md",
            "section": "",
            "status": "",
        }
        for index in range(1, count + 1)
    ]


def test_validate_citations_accepts_single_and_compact_multi_source_markers():
    result = validate_citations(
        "这个结论来自第一份材料[1]，并由两份材料共同支持[1][2]。",
        _sources(2),
    )

    assert result.valid is True
    assert result.missing_citations is False
    assert result.unknown_citation_ids == []
    assert result.cited_source_ids == ["1", "2"]
    assert result.warnings == []


def test_validate_citations_rejects_unknown_source_index():
    result = validate_citations("这个结论引用了不存在的材料[99]。", _sources(2))

    assert result.valid is False
    assert result.missing_citations is False
    assert result.unknown_citation_ids == ["99"]
    assert "unknown_citation_ids" in result.warnings[0]


def test_validate_citations_requires_citations_when_sources_exist():
    result = validate_citations("这个回答没有任何编号引用。", _sources(2))

    assert result.valid is False
    assert result.missing_citations is True
    assert result.unknown_citation_ids == []
    assert "missing_citations" in result.warnings[0]


def test_validate_citations_allows_uncited_answer_when_no_sources_exist():
    result = validate_citations("知识库中没有找到相关信息。", [])

    assert result.valid is True
    assert result.missing_citations is False
    assert result.unknown_citation_ids == []
    assert result.cited_source_ids == []

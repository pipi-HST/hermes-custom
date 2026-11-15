"""Experiment 4 — pressure-test the body preservation guard with organize=True.

With `organize=False` (the new default) the model never touches the body, so a
100% fidelity number is trivially true.  These cases exercise the guard that
protects the *rewrite* path: a lazy summary, a paraphrase, and a legitimate
structure-only edit.
"""
from __future__ import annotations

from agents.capture_agent import _preserve_editable_body

ORIGINAL = "\n\n".join("第%d段：" % index + "甲" * 280 for index in range(1, 6))


def test_structure_only_edit_is_allowed():
    candidate = "## 整篇导读\n\n" + ORIGINAL
    body, preserved = _preserve_editable_body(ORIGINAL, candidate)
    assert preserved is False
    assert "## 整篇导读" in body


def test_lazy_summary_is_rejected():
    candidate = "这是一段三百字左右的摘要，模型把长文压缩成了结论。" * 10
    body, preserved = _preserve_editable_body(ORIGINAL, candidate)
    assert preserved is True, "长度保留率过低必须回退原文"
    assert body == ORIGINAL.strip()


def test_paraphrase_with_similar_length_is_rejected():
    # Same length, different wording: the paragraph-retention guard must catch it
    # even though the naive length check passes.
    candidate = "\n\n".join("第%d段：" % index + "乙" * 280 for index in range(1, 6))
    body, preserved = _preserve_editable_body(ORIGINAL, candidate)
    assert preserved is True, "实质段落被改写必须回退原文"
    assert body == ORIGINAL.strip()


def test_empty_candidate_keeps_original():
    body, preserved = _preserve_editable_body(ORIGINAL, "   ")
    assert preserved is False
    assert body == ORIGINAL.strip()

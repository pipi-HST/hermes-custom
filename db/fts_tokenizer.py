"""CJK-friendly tokenisation for SQLite FTS5.

Why this exists
---------------
``unicode61`` treats a run of Chinese characters as a *single* token, so a
substring query never matches: indexing
``一文搞懂 MCP 与 Agent 工具调用收敛策略`` and searching ``收敛策略`` returns 0.
``trigram`` fixes that but requires queries of at least three characters, which
rules out the two-character words that dominate Chinese (工具 / 面试 / 边界).

So we pre-tokenise CJK runs into overlapping bigrams and keep ``unicode61``::

    工具调用收敛策略 -> 工具 具调 调用 用收 收敛 敛策 策略

Indexing and querying MUST share this module.  If they drift apart the index
silently returns nothing, which looks exactly like "the notes aren't there".
"""
from __future__ import annotations

import re

# CJK Unified Ideographs + Extension A + compatibility ideographs.
_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_RUN_RE = re.compile(f"[{_CJK}]+|[0-9A-Za-z_]+")
_CJK_ONLY_RE = re.compile(f"[{_CJK}]+")


def _bigrams(run: str) -> list[str]:
    if len(run) <= 1:
        return [run]
    return [run[index : index + 2] for index in range(len(run) - 1)]


def tokenize_for_fts(text: str) -> str:
    """Turn mixed CJK/latin text into a space separated token stream.

    ``"工具调用收敛策略"`` -> ``"工具 具调 调用 用收 收敛 敛策 策略"``
    ``"MCP 130秒"``        -> ``"mcp 130 秒"``
    """
    tokens: list[str] = []
    for match in _RUN_RE.finditer(str(text or "")):
        run = match.group(0)
        if _CJK_ONLY_RE.fullmatch(run):
            tokens.extend(_bigrams(run))
        else:
            tokens.append(run.lower())
    return " ".join(tokens)


def _phrase_for(keyword: str) -> str:
    """Build one FTS5 phrase so the bigrams must appear *adjacent and in order*.

    ``收敛策略`` -> ``"收敛 敛策 策略"``

    Phrase (not ``AND``) matters: ``AND`` would also match a document where the
    bigrams are scattered across unrelated paragraphs, which is exactly the
    false-positive shape that made the old keyword fallback noisy.
    """
    tokens = tokenize_for_fts(keyword).split()
    if not tokens:
        return ""
    return '"' + " ".join(tokens) + '"'


def build_fts_match_query(keywords: list[str] | tuple[str, ...]) -> str:
    """Compile query keywords into one FTS5 ``MATCH`` expression.

    Keywords are OR-ed (any keyword may match); bigrams inside one keyword are
    a single phrase (all of them must match, adjacent).
    """
    phrases: list[str] = []
    for keyword in keywords or []:
        text = str(keyword or "").strip()
        if not text:
            continue
        phrase = _phrase_for(text)
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    return " OR ".join(phrases)

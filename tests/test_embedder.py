"""Tests for embedder."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest
from agents.embedder import Embedder, cosine_similarity


@patch("agents.embedder.httpx.Client.post")
def test_embed_calls_correct_api(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [{"embedding": [0.1] * 1024, "index": 0}]
    }
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    e = Embedder(api_key="test-key")
    e.embed("测试文本")

    call_args = mock_post.call_args
    assert "dashscope.aliyuncs.com" in call_args[0][0]
    body = call_args[1]["json"]
    assert body["model"] == "text-embedding-v4"
    assert body["input"] == "测试文本"


def test_cosine_similarity_identical():
    import struct
    v = struct.pack("1024f", *[1.0] * 1024)
    assert cosine_similarity(v, v) == pytest.approx(1.0, abs=0.01)


def test_cosine_similarity_orthogonal():
    import struct
    a = struct.pack("1024f", *([1.0] + [0.0] * 1023))
    b = struct.pack("1024f", *([0.0] + [1.0] + [0.0] * 1022))
    sim = cosine_similarity(a, b)
    assert sim < 0.1


def test_cosine_similarity_opposite():
    import struct
    a = struct.pack("1024f", *[1.0] * 1024)
    b = struct.pack("1024f", *[-1.0] * 1024)
    sim = cosine_similarity(a, b)
    assert sim == pytest.approx(-1.0, abs=0.01)


def test_find_top_k():
    from agents.embedder import find_top_k
    import struct
    q = struct.pack("1024f", *[1.0] * 1024)
    # identical
    c1 = struct.pack("1024f", *[1.0] * 1024)
    # opposite direction
    c2_vals = [-1.0] * 1024
    c2 = struct.pack("1024f", *c2_vals)
    # partial: first half positive, second half zero
    c3_vals = [0.7] * 512 + [0.0] * 512
    c3 = struct.pack("1024f", *c3_vals)

    results = find_top_k(q, [("a", c2), ("b", c1), ("c", c3)], k=2)
    assert results[0][0] == "b"  # identical top
    assert results[0][1] > results[1][1]

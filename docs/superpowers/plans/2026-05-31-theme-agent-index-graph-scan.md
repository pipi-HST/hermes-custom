# Theme Agent Index Graph Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current slow LLM-driven Theme scan with an index-first relationship graph that can discover useful MOC/Garden candidates quickly and honestly.

**Architecture:** Theme scan becomes a deterministic pipeline: collect eligible captures -> build relationship edges from identity/section embeddings and metadata -> cluster the graph -> call LLM only to explain the top clusters. MOC creation and Garden suggestions consume these clusters instead of depending on per-capture LLM filtering.

**Tech Stack:** Python, SQLite, existing `capture_embeddings` table, existing `agents.embedder.cosine_similarity`, pytest, Hermes Agent Result Envelope, Obsidian Markdown vault.

---

## Current Problem

The current Theme Agent uses embeddings only as a coarse candidate filter, then asks an LLM to judge each new capture against its top candidates. In practice this is not usable:

- Full scans can take 90 seconds or more.
- LLM filter calls time out repeatedly.
- Failures are hard to distinguish from "no relationship found."
- Section embeddings are not used, so long-article local relationships are missed.
- Tool meta is too low-level, causing the main Hermes Agent to misread counts.
- Test captures pollute the candidate pool.

The new implementation must make relationship discovery mostly deterministic and fast. LLM calls are allowed only after candidate clusters already exist.

## Success Criteria

- Recent scan returns within 10 seconds on the current local library when no LLM explanation is needed.
- Full lightweight scan returns within 30-40 seconds, preferably much less.
- Theme scan can produce candidate clusters without calling LLM.
- LLM explanation is limited to top clusters, not every capture.
- Test garbage and raw/archive-only material are excluded from Theme/Garden candidates.
- Tool response clearly says whether the result is complete, partial, or empty.
- The main Hermes Agent should not need to infer counts from raw internal IDs.
- If a scan hits the time budget, unprocessed real captures remain unscanned.

## Proposed File Structure

- Create `agents/theme_graph.py`
  - Pure deterministic graph functions.
  - Builds capture nodes, relationship edges, and clusters.
  - No LLM calls and no file writes.

- Modify `agents/theme_agent.py`
  - Keep prompt/explanation/MOC methods.
  - Replace `scan()` internals or delegate to `theme_graph`.
  - Add a small `explain_clusters()` method that calls LLM only on top clusters.

- Modify `bridge/theme_bridge.py`
  - Load identity and section embeddings.
  - Call graph scan.
  - Create MOCs / pending questions from graph clusters.
  - Return user-safe meta with explicit counts.

- Modify `/Users/pipi/Downloads/hermes-agent-main/tools/theme_scan_tool.py`
  - Update tool description so the main Agent treats Theme scan as index-first and does not reinterpret internal stats.

- Tests:
  - Modify `tests/test_theme_agent.py`
  - Modify `tests/test_theme_bridge.py`
  - Create `tests/test_theme_graph.py`
  - Update `tests/test_e2e_hermes_tools.py` only if schema text changes.

## Data Model

Use existing tables only in this iteration.

`capture_embeddings` already provides:

- `kind='identity'`: one embedding per capture, summary-level identity.
- `kind='section'`: section-level embedding for long documents.
- `index_text`: readable identity/section summary.
- `section_id` and `section_title`: local section reference.

No migration is needed for P0.

## Core Types

Implement these as plain dataclasses in `agents/theme_graph.py`.

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ThemeNode:
    capture_id: str
    title: str
    file_path: str
    summary: str
    tags: tuple[str, ...] = ()
    created_at: str = ""
    identity_embedding: bytes | None = None
    section_embeddings: tuple["ThemeSection", ...] = ()

@dataclass(frozen=True)
class ThemeSection:
    section_id: str
    section_title: str
    index_text: str
    embedding: bytes

@dataclass(frozen=True)
class ThemeEdge:
    source_id: str
    target_id: str
    score: float
    reasons: tuple[str, ...]
    section_matches: tuple[str, ...] = ()

@dataclass
class ThemeCluster:
    cluster_id: str
    capture_ids: list[str]
    score: float
    edges: list[ThemeEdge] = field(default_factory=list)
    title_hint: str = ""
    relation_hint: str = ""
```

## Scoring Rules

Use deterministic scoring first. Tune conservatively.

- Identity embedding similarity:
  - `>= 0.72`: strong identity relation.
  - `0.62-0.72`: medium identity relation.
  - `< 0.62`: normally ignored unless section match is strong.

- Section embedding similarity:
  - `>= 0.78`: strong section relation.
  - Can rescue two long captures even when identity similarity is mediocre.

- Tag overlap:
  - Shared important tag adds a small boost.
  - Exact same generic tags like `draft` or `source-archive` should not boost.

- Title keyword overlap:
  - Adds small boost only for meaningful tokens.

- Exclusion:
  - Skip obvious test captures.
  - Skip raw/source-archive-only captures unless they also have a committed capture note.

The score should be understandable, not mathematically perfect. The output must include reasons such as `identity_similarity`, `section_similarity`, `shared_tags`.

## User-Facing Output Contract

`run_theme_scan()` should return a preview like:

```text
我扫了 19 篇真实笔记，发现 2 组值得整理的关联。

1. AI Agent 工程化
这些材料都在讨论：Agent 的可靠性不是只靠模型，而是靠工具、流程和评估约束放大。
相关笔记：Building Effective AI Agents、Opus 4.8、AI 对企业系统稳定性的影响
可沉淀方向：方法 / 注意事项 / 观点

要我先展开哪一组？也可以说“展开最值得保留的那个”。
```

If no cluster:

```text
这次没有发现足够强的主题关联。
已扫描真实笔记 19 篇，跳过测试材料 37 篇。
这不是扫描失败，只是当前材料之间的相似度不够。
```

If partial:

```text
Theme 扫描只完成了一部分：已扫描真实笔记 12/19 篇，发现 1 组候选关联。
未处理的内容会保留到下次继续扫描。
```

Do not expose long internal ID lists to the main Agent by default.

---

## Task 1: Add Deterministic Graph Unit

**Files:**
- Create: `/Users/pipi/hermes-custom/agents/theme_graph.py`
- Create: `/Users/pipi/hermes-custom/tests/test_theme_graph.py`

- [ ] **Step 1: Write failing test for test-garbage exclusion**

Add to `/Users/pipi/hermes-custom/tests/test_theme_graph.py`:

```python
from agents.theme_graph import ThemeNode, filter_theme_nodes


def test_filter_theme_nodes_excludes_test_garbage():
    nodes = [
        ThemeNode(
            capture_id="junk",
            title="这是一段足够长的原始内容，用来生成待确认草稿",
            file_path="captures/junk.md",
            summary="长草稿摘要",
        ),
        ThemeNode(
            capture_id="real",
            title="AI Agent 工程化",
            file_path="captures/agent.md",
            summary="Agent 可靠性来自工具和流程约束",
        ),
    ]

    kept, stats = filter_theme_nodes(nodes)

    assert [n.capture_id for n in kept] == ["real"]
    assert stats["skipped_test_garbage"] == 1
```

- [ ] **Step 2: Verify test fails**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_graph.py::test_filter_theme_nodes_excludes_test_garbage -q
```

Expected: FAIL because `agents.theme_graph` does not exist.

- [ ] **Step 3: Implement minimal dataclasses and filter**

Create `/Users/pipi/hermes-custom/agents/theme_graph.py`:

```python
"""Deterministic Theme relationship graph builder."""
from __future__ import annotations

from dataclasses import dataclass, field

TEST_GARBAGE_MARKERS = (
    "这是一段足够长的原始内容，用来生成待确认草稿",
)


@dataclass(frozen=True)
class ThemeSection:
    section_id: str
    section_title: str
    index_text: str
    embedding: bytes


@dataclass(frozen=True)
class ThemeNode:
    capture_id: str
    title: str
    file_path: str
    summary: str
    tags: tuple[str, ...] = ()
    created_at: str = ""
    identity_embedding: bytes | None = None
    section_embeddings: tuple[ThemeSection, ...] = ()


@dataclass(frozen=True)
class ThemeEdge:
    source_id: str
    target_id: str
    score: float
    reasons: tuple[str, ...]
    section_matches: tuple[str, ...] = ()


@dataclass
class ThemeCluster:
    cluster_id: str
    capture_ids: list[str]
    score: float
    edges: list[ThemeEdge] = field(default_factory=list)
    title_hint: str = ""
    relation_hint: str = ""


def filter_theme_nodes(nodes: list[ThemeNode]) -> tuple[list[ThemeNode], dict]:
    kept: list[ThemeNode] = []
    stats = {"skipped_test_garbage": 0}
    for node in nodes:
        text = "\n".join([node.title, node.file_path, node.summary])
        if any(marker in text for marker in TEST_GARBAGE_MARKERS):
            stats["skipped_test_garbage"] += 1
            continue
        kept.append(node)
    return kept, stats
```

- [ ] **Step 4: Verify test passes**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_graph.py::test_filter_theme_nodes_excludes_test_garbage -q
```

Expected: PASS.

## Task 2: Build Relationship Edges Without LLM

**Files:**
- Modify: `/Users/pipi/hermes-custom/agents/theme_graph.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_theme_graph.py`

- [ ] **Step 1: Write failing tests for identity and section edges**

Add:

```python
import struct
from agents.embedder import DIM
from agents.theme_graph import (
    ThemeNode,
    ThemeSection,
    build_relationship_edges,
)


def _emb(value: float) -> bytes:
    return struct.pack(f"{DIM}f", *([value] * DIM))


def test_build_relationship_edges_uses_identity_similarity():
    nodes = [
        ThemeNode("a", "Agent A", "captures/a.md", "Agent 工程", identity_embedding=_emb(1.0)),
        ThemeNode("b", "Agent B", "captures/b.md", "Agent 评估", identity_embedding=_emb(1.0)),
        ThemeNode("c", "读书", "captures/c.md", "阅读方法", identity_embedding=_emb(-1.0)),
    ]

    edges = build_relationship_edges(nodes, identity_threshold=0.72)

    assert [(e.source_id, e.target_id) for e in edges] == [("a", "b")]
    assert edges[0].score >= 0.72
    assert "identity_similarity" in edges[0].reasons


def test_build_relationship_edges_can_use_section_similarity():
    nodes = [
        ThemeNode(
            "a",
            "长文 A",
            "captures/a.md",
            "宏观文章",
            identity_embedding=_emb(0.1),
            section_embeddings=(ThemeSection("s1", "Agent 部分", "讲 Agent 工程", _emb(1.0)),),
        ),
        ThemeNode(
            "b",
            "长文 B",
            "captures/b.md",
            "另一篇长文",
            identity_embedding=_emb(0.2),
            section_embeddings=(ThemeSection("s2", "工程部分", "讲 Agent 工程", _emb(1.0)),),
        ),
    ]

    edges = build_relationship_edges(
        nodes,
        identity_threshold=0.99,
        section_threshold=0.78,
    )

    assert len(edges) == 1
    assert "section_similarity" in edges[0].reasons
    assert edges[0].section_matches == ("Agent 部分 ↔ 工程部分",)
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_graph.py::test_build_relationship_edges_uses_identity_similarity /Users/pipi/hermes-custom/tests/test_theme_graph.py::test_build_relationship_edges_can_use_section_similarity -q
```

Expected: FAIL because `build_relationship_edges` is missing.

- [ ] **Step 3: Implement edge builder**

Add to `agents/theme_graph.py`:

```python
from agents.embedder import cosine_similarity


def build_relationship_edges(
    nodes: list[ThemeNode],
    *,
    identity_threshold: float = 0.72,
    section_threshold: float = 0.78,
    max_edges_per_node: int = 6,
) -> list[ThemeEdge]:
    edges: list[ThemeEdge] = []
    by_source_count: dict[str, int] = {}

    for i, left in enumerate(nodes):
        for right in nodes[i + 1:]:
            score = 0.0
            reasons: list[str] = []
            section_matches: list[str] = []

            if left.identity_embedding and right.identity_embedding:
                identity_score = cosine_similarity(
                    left.identity_embedding,
                    right.identity_embedding,
                )
                if identity_score >= identity_threshold:
                    score = max(score, identity_score)
                    reasons.append("identity_similarity")

            best_section = 0.0
            best_section_label = ""
            for left_section in left.section_embeddings:
                for right_section in right.section_embeddings:
                    section_score = cosine_similarity(
                        left_section.embedding,
                        right_section.embedding,
                    )
                    if section_score > best_section:
                        best_section = section_score
                        best_section_label = (
                            f"{left_section.section_title} ↔ "
                            f"{right_section.section_title}"
                        )
            if best_section >= section_threshold:
                score = max(score, best_section)
                reasons.append("section_similarity")
                section_matches.append(best_section_label)

            shared_tags = _shared_meaningful_tags(left.tags, right.tags)
            if shared_tags and score >= min(identity_threshold, section_threshold) - 0.08:
                score = min(1.0, score + 0.04)
                reasons.append("shared_tags")

            if not reasons:
                continue

            if by_source_count.get(left.capture_id, 0) >= max_edges_per_node:
                continue
            by_source_count[left.capture_id] = by_source_count.get(left.capture_id, 0) + 1
            edges.append(
                ThemeEdge(
                    source_id=left.capture_id,
                    target_id=right.capture_id,
                    score=round(score, 4),
                    reasons=tuple(reasons),
                    section_matches=tuple(section_matches),
                )
            )

    edges.sort(key=lambda edge: edge.score, reverse=True)
    return edges


GENERIC_TAGS = {"draft", "source-archive", "raw", "capture"}


def _shared_meaningful_tags(left: tuple[str, ...], right: tuple[str, ...]) -> set[str]:
    return (set(left) & set(right)) - GENERIC_TAGS
```

- [ ] **Step 4: Verify tests pass**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_graph.py -q
```

Expected: PASS.

## Task 3: Cluster Relationship Graph

**Files:**
- Modify: `/Users/pipi/hermes-custom/agents/theme_graph.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_theme_graph.py`

- [ ] **Step 1: Write failing cluster tests**

Add:

```python
from agents.theme_graph import ThemeEdge, cluster_relationship_graph


def test_cluster_relationship_graph_groups_connected_edges():
    edges = [
        ThemeEdge("a", "b", 0.83, ("identity_similarity",)),
        ThemeEdge("b", "c", 0.79, ("identity_similarity",)),
        ThemeEdge("x", "y", 0.60, ("identity_similarity",)),
    ]

    clusters = cluster_relationship_graph(edges, min_edge_score=0.72)

    assert len(clusters) == 1
    assert clusters[0].capture_ids == ["a", "b", "c"]
    assert clusters[0].score > 0.8


def test_cluster_relationship_graph_allows_strong_pair():
    edges = [ThemeEdge("a", "b", 0.86, ("section_similarity",))]

    clusters = cluster_relationship_graph(edges, min_edge_score=0.72)

    assert len(clusters) == 1
    assert clusters[0].capture_ids == ["a", "b"]
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_graph.py::test_cluster_relationship_graph_groups_connected_edges /Users/pipi/hermes-custom/tests/test_theme_graph.py::test_cluster_relationship_graph_allows_strong_pair -q
```

Expected: FAIL because `cluster_relationship_graph` is missing.

- [ ] **Step 3: Implement graph clustering**

Add:

```python
def cluster_relationship_graph(
    edges: list[ThemeEdge],
    *,
    min_edge_score: float = 0.72,
    max_clusters: int = 5,
) -> list[ThemeCluster]:
    strong_edges = [edge for edge in edges if edge.score >= min_edge_score]
    adjacency: dict[str, set[str]] = {}
    edge_map: dict[frozenset[str], ThemeEdge] = {}
    for edge in strong_edges:
        adjacency.setdefault(edge.source_id, set()).add(edge.target_id)
        adjacency.setdefault(edge.target_id, set()).add(edge.source_id)
        edge_map[frozenset([edge.source_id, edge.target_id])] = edge

    visited: set[str] = set()
    clusters: list[ThemeCluster] = []
    for node_id in sorted(adjacency):
        if node_id in visited:
            continue
        stack = [node_id]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency.get(current, set()) - component)
        visited.update(component)

        component_edges = [
            edge for key, edge in edge_map.items()
            if key.issubset(component)
        ]
        if not component_edges:
            continue
        avg_score = sum(edge.score for edge in component_edges) / len(component_edges)
        capture_ids = sorted(component)
        clusters.append(
            ThemeCluster(
                cluster_id=f"theme-{capture_ids[0][:8]}",
                capture_ids=capture_ids,
                score=round(avg_score, 4),
                edges=sorted(component_edges, key=lambda edge: edge.score, reverse=True),
            )
        )

    clusters.sort(key=lambda cluster: (cluster.score, len(cluster.capture_ids)), reverse=True)
    return clusters[:max_clusters]
```

- [ ] **Step 4: Verify tests pass**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_graph.py -q
```

Expected: PASS.

## Task 4: Hydrate Theme Nodes From DB

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/theme_bridge.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_theme_bridge.py`

- [ ] **Step 1: Write failing hydration test**

Add to `tests/test_theme_bridge.py`:

```python
def test_build_theme_nodes_includes_identity_and_sections(tmp_path: Path) -> None:
    from db.embedding_repo import CaptureEmbeddingRepo
    from db.schema import init_db
    from bridge.theme_bridge import _build_theme_nodes

    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    cap_file = _write_note(tmp_path, "captures", "agent.md", "Agent 工程", "正文。")
    conn.execute("INSERT INTO captures (id, file_path) VALUES (?, ?)", ("cap-id", cap_file))
    embed_repo = CaptureEmbeddingRepo(conn)
    embed_repo.upsert(
        capture_id="cap-id",
        kind="identity",
        section_id="",
        section_title="",
        index_text="标题：Agent 工程\n摘要：Agent 可靠性来自约束",
        embedding=b"identity",
        source_hash="hash-identity",
        file_path=cap_file,
    )
    embed_repo.upsert(
        capture_id="cap-id",
        kind="section",
        section_id="s1",
        section_title="工具调用",
        index_text="工具调用需要评估",
        embedding=b"section",
        source_hash="hash-section",
        file_path=cap_file,
    )

    row = dict(conn.execute("SELECT * FROM captures WHERE id = ?", ("cap-id",)).fetchone())
    nodes = _build_theme_nodes(tmp_path, [row], conn)

    assert len(nodes) == 1
    assert nodes[0].capture_id == "cap-id"
    assert nodes[0].title == "Agent 工程"
    assert nodes[0].identity_embedding == b"identity"
    assert nodes[0].section_embeddings[0].section_title == "工具调用"
```

- [ ] **Step 2: Verify test fails**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_bridge.py::test_build_theme_nodes_includes_identity_and_sections -q
```

Expected: FAIL because `_build_theme_nodes` is missing.

- [ ] **Step 3: Implement `_build_theme_nodes`**

In `bridge/theme_bridge.py`, import:

```python
from agents.theme_graph import ThemeNode, ThemeSection
```

Add helper:

```python
def _build_theme_nodes(vault_path: Path, rows: list[dict], conn) -> list[ThemeNode]:
    embeddings = conn.execute("SELECT * FROM capture_embeddings").fetchall()
    by_capture: dict[str, list[dict]] = {}
    for row in embeddings:
        by_capture.setdefault(str(row["capture_id"]), []).append(dict(row))

    nodes: list[ThemeNode] = []
    for row in rows:
        capture_id = str(row["id"])
        note = read_note(vault_path, row.get("file_path") or "")
        frontmatter, _body = note if note else ({}, "")
        identity_embedding = None
        section_embeddings: list[ThemeSection] = []
        summary = ""
        for emb in by_capture.get(capture_id, []):
            if emb["kind"] == "identity":
                identity_embedding = emb["embedding"]
                summary = _identity_summary(emb.get("index_text") or "")
            elif emb["kind"] == "section" and emb.get("embedding"):
                section_embeddings.append(
                    ThemeSection(
                        section_id=emb.get("section_id") or "",
                        section_title=emb.get("section_title") or "",
                        index_text=emb.get("index_text") or "",
                        embedding=emb["embedding"],
                    )
                )
        nodes.append(
            ThemeNode(
                capture_id=capture_id,
                title=frontmatter.get("title") or Path(row.get("file_path") or "").stem,
                file_path=row.get("file_path") or "",
                summary=frontmatter.get("story") or summary,
                tags=tuple(frontmatter.get("tags") or ()),
                created_at=row.get("created_at") or "",
                identity_embedding=identity_embedding,
                section_embeddings=tuple(section_embeddings),
            )
        )
    return nodes
```

- [ ] **Step 4: Verify test passes**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_bridge.py::test_build_theme_nodes_includes_identity_and_sections -q
```

Expected: PASS.

## Task 5: Replace Scan Core With Graph Pipeline

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/theme_bridge.py`
- Modify: `/Users/pipi/hermes-custom/agents/theme_agent.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_theme_bridge.py`

- [ ] **Step 1: Write failing bridge test for no-LLM cluster discovery**

Add:

```python
def test_run_theme_scan_builds_cluster_without_llm_filter(tmp_path: Path) -> None:
    import struct
    from agents.embedder import DIM
    from db.embedding_repo import CaptureEmbeddingRepo
    from db.schema import init_db

    def emb(value: float) -> bytes:
        return struct.pack(f"{DIM}f", *([value] * DIM))

    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    embed_repo = CaptureEmbeddingRepo(conn)
    for capture_id, title in (("a-id", "Agent A"), ("b-id", "Agent B")):
        cap_file = _write_note(tmp_path, "captures", f"{capture_id}.md", title, "Agent 工程。")
        conn.execute(
            "INSERT INTO captures (id, file_path, created_at) VALUES (?, ?, datetime('now'))",
            (capture_id, cap_file),
        )
        embed_repo.upsert(
            capture_id=capture_id,
            kind="identity",
            section_id="",
            section_title="",
            index_text=f"标题：{title}\n摘要：Agent 工程化",
            embedding=emb(1.0),
            source_hash=f"hash-{capture_id}",
            file_path=cap_file,
        )

    class FakeThemeAgent:
        def __init__(self, **_kwargs):
            pass

        def generate_question(self, cluster):
            return "发现 Agent 工程化关联。可沉淀方向：方法。要我先展开哪个？"

        def generate_theme_name(self, captures):
            return "Agent 工程化"

        def build_moc(self, theme_name, captures):
            return f"# {theme_name}\n\n## 相关笔记引用\n"

    with patch("bridge.theme_bridge.load_config") as mock_cfg, patch(
        "bridge.theme_bridge.ThemeAgent", FakeThemeAgent
    ):
        mock_cfg.return_value = MagicMock(
            vault_path=str(tmp_path),
            db_path=db_path,
            llm_api_key="sk-test",
            llm_model="test",
            llm_base_url="http://test",
        )
        from bridge.theme_bridge import run_theme_scan

        result = run_theme_scan()

    assert result["meta"]["cluster_count"] == 1
    assert "发现" in result["chat_preview"]
```

- [ ] **Step 2: Verify test fails**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_bridge.py::test_run_theme_scan_builds_cluster_without_llm_filter -q
```

Expected: FAIL because bridge still depends on `ThemeAgent.scan()`.

- [ ] **Step 3: Implement graph pipeline in bridge**

In `bridge/theme_bridge.py`:

- Stop calling `agent.scan(new_caps, all_caps, ...)`.
- Build nodes for `rows` and all captures.
- Filter nodes.
- Build graph edges from all eligible nodes.
- Cluster edges.
- Convert `ThemeCluster` into the existing cluster dict shape expected by `_inject_wiki_links`, `_create_moc`, and `generate_question`.

Add helper:

```python
def _cluster_to_legacy_dict(cluster, node_by_id: dict[str, ThemeNode]) -> dict:
    capture_dicts = [
        {
            "id": node.capture_id,
            "title": node.title,
            "file_path": node.file_path,
            "summary": node.summary,
            "tags": list(node.tags),
        }
        for node in (node_by_id[cid] for cid in cluster.capture_ids)
    ]
    return {
        "new_capture": capture_dicts[0],
        "related_captures": capture_dicts[1:],
        "score": cluster.score,
        "edges": cluster.edges,
    }
```

Use:

```python
scan_nodes = _build_theme_nodes(vault_path, [dict(r) for r in rows], conn)
all_nodes = _build_theme_nodes(vault_path, repo.list_all(limit=200), conn)
eligible_nodes, filter_stats = filter_theme_nodes(all_nodes)
edges = build_relationship_edges(eligible_nodes)
graph_clusters = cluster_relationship_graph(edges)
scan_ids = {node.capture_id for node in scan_nodes}
graph_clusters = [
    cluster for cluster in graph_clusters
    if scan_ids & set(cluster.capture_ids)
]
node_by_id = {node.capture_id: node for node in eligible_nodes}
clusters = [_cluster_to_legacy_dict(cluster, node_by_id) for cluster in graph_clusters]
```

- [ ] **Step 4: Verify test passes**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_bridge.py::test_run_theme_scan_builds_cluster_without_llm_filter -q
```

Expected: PASS.

## Task 6: Make User-Facing Preview Accurate

**Files:**
- Modify: `/Users/pipi/hermes-custom/bridge/theme_bridge.py`
- Modify: `/Users/pipi/hermes-custom/tests/test_theme_bridge.py`

- [ ] **Step 1: Write failing preview tests**

Add:

```python
def test_theme_scan_preview_does_not_expose_internal_id_lists():
    from bridge.theme_bridge import _theme_scan_preview

    preview = _theme_scan_preview(
        scanned_real_count=19,
        skipped_test_count=37,
        cluster_questions=["发现 Agent 工程化关联。可沉淀方向：方法。"],
        partial=False,
    )

    assert "19 篇真实笔记" in preview
    assert "37 篇测试材料" in preview
    assert "可沉淀方向" in preview
    assert "processed_ids" not in preview
    assert "filtered_new_ids" not in preview


def test_theme_scan_preview_empty_result_is_clear():
    from bridge.theme_bridge import _theme_scan_preview

    preview = _theme_scan_preview(
        scanned_real_count=19,
        skipped_test_count=37,
        cluster_questions=[],
        partial=False,
    )

    assert "没有发现足够强的主题关联" in preview
    assert "不是扫描失败" in preview
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_bridge.py::test_theme_scan_preview_does_not_expose_internal_id_lists /Users/pipi/hermes-custom/tests/test_theme_bridge.py::test_theme_scan_preview_empty_result_is_clear -q
```

Expected: FAIL because `_theme_scan_preview` is missing.

- [ ] **Step 3: Implement preview helper**

Add:

```python
def _theme_scan_preview(
    *,
    scanned_real_count: int,
    skipped_test_count: int,
    cluster_questions: list[str],
    partial: bool,
) -> str:
    if cluster_questions:
        status = "Theme 扫描只完成了一部分" if partial else "我扫完了这批内容"
        lines = [
            f"{status}：已扫描 {scanned_real_count} 篇真实笔记，发现 {len(cluster_questions)} 组值得整理的关联。",
        ]
        if skipped_test_count:
            lines.append(f"已跳过 {skipped_test_count} 篇测试材料。")
        lines.append("")
        for index, question in enumerate(cluster_questions[:3], 1):
            lines.append(f"{index}. {question}")
        lines.append("")
        lines.append("要我先展开哪一组？也可以说“展开最值得保留的那个”。")
        return "\n".join(lines)

    lines = [
        "这次没有发现足够强的主题关联。",
        f"已扫描真实笔记 {scanned_real_count} 篇。",
    ]
    if skipped_test_count:
        lines.append(f"已跳过 {skipped_test_count} 篇测试材料。")
    if partial:
        lines.append("这次扫描只完成了一部分，未处理内容会留到下次继续。")
    else:
        lines.append("这不是扫描失败，只是当前材料之间的相似度不够。")
    return "\n".join(lines)
```

- [ ] **Step 4: Verify preview tests pass**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/test_theme_bridge.py::test_theme_scan_preview_does_not_expose_internal_id_lists /Users/pipi/hermes-custom/tests/test_theme_bridge.py::test_theme_scan_preview_empty_result_is_clear -q
```

Expected: PASS.

## Task 7: Runtime Tool Contract Update

**Files:**
- Modify: `/Users/pipi/Downloads/hermes-agent-main/tools/theme_scan_tool.py`
- Modify or create: `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_scan_tool.py`

- [ ] **Step 1: Write failing runtime schema test**

Create `/Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_scan_tool.py`:

```python
import tools.theme_scan_tool  # noqa: F401
from tools.registry import registry


def test_theme_scan_tool_describes_index_first_contract():
    schema = registry.get_schema("theme_scan")

    assert schema is not None
    description = schema["description"]
    assert "索引" in description
    assert "不要自行推断内部统计" in description
```

- [ ] **Step 2: Verify test fails**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_scan_tool.py -q
```

Expected: FAIL because description does not contain the new contract text.

- [ ] **Step 3: Update tool description**

In `/Users/pipi/Downloads/hermes-agent-main/tools/theme_scan_tool.py`, update description to include:

```python
"Theme scan 是索引优先的关系发现工具。请优先转述 USER_VISIBLE_REPLY；"
"不要自行推断内部统计，也不要把 partial/不完整结果改写成没有关联。"
```

- [ ] **Step 4: Verify runtime test passes**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_scan_tool.py /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py -q
```

Expected: PASS.

## Task 8: End-to-End Verification

**Files:**
- No source changes unless tests reveal a bug.

- [ ] **Step 1: Run focused hermes-custom tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest \
  /Users/pipi/hermes-custom/tests/test_theme_graph.py \
  /Users/pipi/hermes-custom/tests/test_theme_agent.py \
  /Users/pipi/hermes-custom/tests/test_theme_bridge.py \
  /Users/pipi/hermes-custom/tests/test_e2e_hermes_tools.py::test_hermes_theme_scan_schema \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run full hermes-custom tests**

Run:

```bash
/Users/pipi/hermes-custom/.venv/bin/python -m pytest /Users/pipi/hermes-custom/tests/ -q
```

Expected: PASS. Current baseline before this plan was `443 passed, 2 skipped`.

- [ ] **Step 3: Run runtime focused tests**

Run:

```bash
/Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m pytest -o addopts='' \
  /Users/pipi/Downloads/hermes-agent-main/tests/tools/test_theme_scan_tool.py \
  /Users/pipi/Downloads/hermes-agent-main/tests/test_toolsets.py \
  -q
```

Expected: PASS.

- [ ] **Step 4: Restart Hermes and clear sessions**

Run:

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="/Users/pipi/.hermes/session_backups/$stamp"
mkdir -p "$backup_dir" /Users/pipi/.hermes/logs /Users/pipi/.hermes/sessions
cp -a /Users/pipi/.hermes/sessions/. "$backup_dir/" 2>/dev/null || true
find /Users/pipi/.hermes/sessions -mindepth 1 -maxdepth 1 -delete 2>/dev/null || true
pkill -f "[h]ermes_cli.main gateway run" 2>/dev/null || true
nohup /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m hermes_cli.main gateway run --replace > "/Users/pipi/.hermes/logs/gateway-restart-$stamp.log" 2>&1 &
sleep 6
pgrep -fl "hermes_cli.main gateway run"
cat /Users/pipi/.hermes/gateway_state.json
find /Users/pipi/.hermes/sessions -mindepth 1 -maxdepth 1 | wc -l | tr -d ' '
```

Expected:

- One gateway process.
- `weixin` connected.
- `feishu` connected.
- Session count is `0`.

- [ ] **Step 5: WeChat smoke test**

Ask in WeChat:

```text
扫描最近保存的内容，看看有没有可以整理成知识卡片的关联
```

Expected:

- Uses `theme_scan`.
- Does not wait more than about 10 seconds for no-new-content case.
- If clusters exist, returns 1-3 groups with reasons and "可沉淀方向".
- If no clusters exist, says no strong relation found without calling it a failure.

Ask:

```text
扫描全部知识库
```

Expected:

- Does not wait around 90 seconds.
- Does not expose raw ID lists.
- Does not ask the user to classify method/viewpoint.
- If partial, says partial and keeps unprocessed material for later.

## Out of Scope

- Physically deleting test captures from DB and Obsidian.
- Adding a background scheduler for deep full-library theme rebuild.
- Changing Garden card content structure.
- Creating bidirectional wiki-links in every capture note.
- Replacing the existing `capture_embeddings` schema.

## Risk Notes

- Existing test garbage still lives in the real DB. Filtering prevents pollution, but cleanup should be a separate user-confirmed Library task.
- Existing MOC creation expects the old cluster dict shape. Task 5 keeps a compatibility adapter to avoid rewriting MOC code in the same change.
- Section embedding quality depends on Library Agent having indexed sections. If section embeddings are missing, identity-level graph still works.
- Runtime repo and custom repo must be committed separately.

## Commit Plan

Commit in small slices:

1. `feat: add deterministic theme graph`
2. `feat: use graph clusters for theme scan`
3. `fix: return clear theme scan previews`
4. Runtime repo: `fix: clarify theme scan tool contract`

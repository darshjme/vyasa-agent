"""Tests for the Graphify v2 storage layer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.migrate_graph_v1_to_v2 import _build_plan  # noqa: E402
from vyasa_agent.graphify import (  # noqa: E402
    Edge,
    GraphStore,
    Node,
    PIILeakError,
    PIIScrubber,
    QueryFilters,
    compute_checksum,
)

# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def tmp_graph_path(tmp_path: Path) -> Path:
    return tmp_path / "graph.sqlite"


def _make_node(
    *,
    node_id: str,
    summary: str = "a fact",
    source_path: str | None = "s://unit-test",
    visibility: str = "private",
    owner: str = "prometheus",
    tags: list[str] | None = None,
    key_claims: list[str] | None = None,
    episode_id: str | None = None,
    confidence: float = 0.8,
) -> Node:
    return Node(
        id=node_id,
        type="fact",
        source_path=source_path,
        summary=summary,
        key_claims=key_claims or ["claim-1"],
        entities=[],
        symbols=[],
        owner_employee_id=owner,
        visibility=visibility,  # type: ignore[arg-type]
        subject_tags=tags or [],
        episode_id=episode_id,
        confidence_score=confidence,
    )


# --------------------------------------------------------------------------- #
# checksum
# --------------------------------------------------------------------------- #


def test_checksum_is_order_invariant() -> None:
    a = _make_node(node_id="x", key_claims=["one", "two"])
    b = _make_node(node_id="x", key_claims=["two", "one"])
    assert compute_checksum(a) == compute_checksum(b)
    assert compute_checksum(a).startswith("sha256:")


def test_checksum_changes_on_source_path_change() -> None:
    a = _make_node(node_id="x", source_path="s://a")
    b = _make_node(node_id="x", source_path="s://b")
    assert compute_checksum(a) != compute_checksum(b)


# --------------------------------------------------------------------------- #
# store: upsert + dedup
# --------------------------------------------------------------------------- #


async def test_upsert_node_is_idempotent_by_checksum(tmp_graph_path: Path) -> None:
    store = GraphStore(db_path=tmp_graph_path)
    try:
        n1 = _make_node(node_id="n_a", summary="first", key_claims=["same"])
        n2 = _make_node(node_id="n_b", summary="second-but-same-claims", key_claims=["same"])

        id_a = await store.upsert_node(n1)
        id_b = await store.upsert_node(n2)

        # Identical source_path + key_claims → same checksum → dedup to n_a.
        assert id_a == "n_a"
        assert id_b == "n_a"

        fetched = await store.get_node("n_a")
        assert fetched is not None
        # First writer wins — summary stays "first".
        assert fetched.summary == "first"
    finally:
        await store.close()


async def test_upsert_persists_all_scalar_fields(tmp_graph_path: Path) -> None:
    store = GraphStore(db_path=tmp_graph_path)
    try:
        node = _make_node(
            node_id="n_fields",
            summary="full row",
            tags=["release-1.4", "auth"],
            episode_id="ep_01HX",
            confidence=0.42,
        )
        await store.upsert_node(node)
        got = await store.get_node("n_fields")
        assert got is not None
        assert got.subject_tags == ["release-1.4", "auth"]
        assert got.episode_id == "ep_01HX"
        assert got.confidence_score == pytest.approx(0.42)
        assert got.checksum is not None and got.checksum.startswith("sha256:")
    finally:
        await store.close()


# --------------------------------------------------------------------------- #
# store: query / visibility
# --------------------------------------------------------------------------- #


async def test_query_by_visibility(tmp_graph_path: Path) -> None:
    store = GraphStore(db_path=tmp_graph_path)
    try:
        await store.upsert_node(_make_node(node_id="pv", visibility="private"))
        await store.upsert_node(
            _make_node(node_id="tm", visibility="team", key_claims=["t-claim"])
        )
        await store.upsert_node(
            _make_node(node_id="fl", visibility="fleet", key_claims=["f-claim"])
        )

        result = await store.query(
            QueryFilters(visibility_scope=["team", "fleet"], limit=10)
        )
        ids = {node.id for node in result}
        assert ids == {"tm", "fl"}

        fleet_only = await store.query(QueryFilters(visibility_scope=["fleet"]))
        assert [node.id for node in fleet_only] == ["fl"]
    finally:
        await store.close()


async def test_query_intent_matches_summary_and_tags(tmp_graph_path: Path) -> None:
    store = GraphStore(db_path=tmp_graph_path)
    try:
        await store.upsert_node(
            _make_node(node_id="a", summary="admin credentials for CRM", key_claims=["k1"])
        )
        await store.upsert_node(
            _make_node(
                node_id="b",
                summary="unrelated",
                tags=["admin"],
                key_claims=["k2"],
            )
        )
        await store.upsert_node(
            _make_node(node_id="c", summary="nothing to match", key_claims=["k3"])
        )

        result = await store.query(QueryFilters(intent="admin"))
        ids = {node.id for node in result}
        assert ids == {"a", "b"}
    finally:
        await store.close()


# --------------------------------------------------------------------------- #
# store: subgraph BFS
# --------------------------------------------------------------------------- #


async def test_subgraph_bfs_walks_both_directions(tmp_graph_path: Path) -> None:
    store = GraphStore(db_path=tmp_graph_path)
    try:
        # Build a chain: root -> mid -> leaf, plus an unrelated island.
        await store.upsert_node(_make_node(node_id="root", key_claims=["root-c"]))
        await store.upsert_node(_make_node(node_id="mid", key_claims=["mid-c"]))
        await store.upsert_node(_make_node(node_id="leaf", key_claims=["leaf-c"]))
        await store.upsert_node(_make_node(node_id="island", key_claims=["iso-c"]))

        await store.add_edge(Edge(from_node="root", to_node="mid", kind="depends_on"))
        await store.add_edge(Edge(from_node="mid", to_node="leaf", kind="evidence_for"))

        depth1 = await store.get_subgraph("root", depth=1)
        ids1 = {n.id for n in depth1.nodes}
        assert ids1 == {"root", "mid"}
        assert len(depth1.edges) == 1

        depth2 = await store.get_subgraph("root", depth=2)
        ids2 = {n.id for n in depth2.nodes}
        assert ids2 == {"root", "mid", "leaf"}
        assert len(depth2.edges) == 2
        # Island is not reachable.
        assert "island" not in ids2

        # Walking from leaf backwards also reaches root.
        reverse = await store.get_subgraph("leaf", depth=2)
        assert {n.id for n in reverse.nodes} == {"leaf", "mid", "root"}

        # Missing root yields empty subgraph, not an exception.
        empty = await store.get_subgraph("does-not-exist")
        assert empty.nodes == [] and empty.edges == []
    finally:
        await store.close()


# --------------------------------------------------------------------------- #
# PII scrubber
# --------------------------------------------------------------------------- #


def test_pii_scrubber_catches_indian_phone() -> None:
    scrubber = PIIScrubber()
    scrubbed, token_map = scrubber.scrub("call me on +91 98765 43210 asap")
    # The digit run should have been replaced with a PHONE token.
    assert "98765" not in scrubbed
    assert any(token.startswith("<PHONE_") for token in token_map)


def test_pii_scrubber_catches_email_and_aadhaar() -> None:
    scrubber = PIIScrubber()
    scrubbed, token_map = scrubber.scrub(
        "email raj@example.com, aadhaar 1234 5678 9012 on file"
    )
    assert "raj@example.com" not in scrubbed
    assert "1234 5678 9012" not in scrubbed
    kinds = {token.split("_")[0][1:] for token in token_map}
    assert {"EMAIL", "AADHAAR"}.issubset(kinds)


def test_pii_scrubber_gate_rejects_unredacted_phone() -> None:
    scrubber = PIIScrubber()
    with pytest.raises(PIILeakError):
        scrubber.check_before_write(
            summary="call +91 9876543210 for OTP 482913",
            key_claims=[],
        )


def test_pii_scrubber_gate_accepts_clean_payload() -> None:
    scrubber = PIIScrubber()
    assert scrubber.check_before_write(
        summary="deployment live at crrm.viralbolly.com",
        key_claims=["admin dashboard reachable"],
    )


# --------------------------------------------------------------------------- #
# migration script — dry-run
# --------------------------------------------------------------------------- #


def _v1_fixture() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "nodes": [
            {
                "id": "node_001_x",
                "type": "deployment",
                "source_path": "https://example.test",
                "summary": "a deployment",
                "key_claims": ["k1", "k2"],
                "entities": ["example"],
                "symbols": [],
                "linked_nodes": ["node_002_y"],
                "updated_at": "2026-04-23T22:30:00Z",
            },
            {
                "id": "node_002_y",
                "type": "reference_material",
                "source_path": "/tmp/ref",
                "summary": "a reference",
                "key_claims": ["ref-c"],
                "entities": [],
                "symbols": [],
                "linked_nodes": [],
                "updated_at": "2026-04-23T22:30:00Z",
            },
        ],
        "edges": [
            {
                "from": "node_001_x",
                "to": "node_002_y",
                "kind": "future_expansion_target",
            },
            {
                "from": "node_002_y",
                "to": "node_001_x",
                "kind": "authoritative_for",
            },
        ],
    }


def test_migration_dry_run_remaps_edges(tmp_path: Path) -> None:
    source = tmp_path / "context_graph.json"
    source.write_text(json.dumps(_v1_fixture()), encoding="utf-8")

    payload = json.loads(source.read_text(encoding="utf-8"))
    plan = _build_plan(payload)

    assert len(plan.nodes) == 2
    kinds = {edge.kind for edge in plan.edges}
    assert kinds == {"depends_on", "evidence_for"}
    assert plan.skipped_edges == []
    for node in plan.nodes:
        assert node.owner_employee_id == "dr_bose"
        assert node.visibility == "fleet"
        assert node.checksum is not None and node.checksum.startswith("sha256:")

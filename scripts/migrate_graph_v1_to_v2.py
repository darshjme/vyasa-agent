#!/usr/bin/env python3
"""Migrate a v1 ``context_graph.json`` into the v2 SQLite GraphStore.

Usage
-----

.. code-block:: shell

    python scripts/migrate_graph_v1_to_v2.py \\
        --source ~/graymatter-online-llp/graymatter_kb/context_graph.json \\
        --target ~/.vyasa/graph.sqlite

    # dry-run: parse + plan, print summary, write nothing
    python scripts/migrate_graph_v1_to_v2.py --dry-run

Mapping rules (see design-07-graphify-v2.md §7):

* Every v1 node gains ``owner_employee_id="dr_bose"``, ``visibility="fleet"``,
  ``pii_scrubbed=False``, ``confidence_score=0.9``. ``subject_tags`` is
  seeded from the v1 ``type`` + the first two ``entities`` so filter
  queries have something to grip onto before richer tagging lands.
* Two edge kinds are remapped:
    - ``future_expansion_target`` → ``depends_on``
    - ``authoritative_for``       → ``evidence_for``
* Checksums are recomputed deterministically; duplicate checksums are a
  no-op (the store dedupes for us).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Allow running the script straight from a checkout without installing.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from vyasa_agent.graphify import (  # noqa: E402
    Edge,
    GraphStore,
    Node,
    compute_checksum,
    default_graph_path,
)

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

_DEFAULT_SOURCE = Path.home() / "graymatter-online-llp" / "graymatter_kb" / "context_graph.json"

_EDGE_REMAP: dict[str, str] = {
    "future_expansion_target": "depends_on",
    "authoritative_for": "evidence_for",
    # Pass-through for any v2-shaped edges that already use the new kinds.
    "depends_on": "depends_on",
    "supersedes": "supersedes",
    "handed_off_to": "handed_off_to",
    "contradicts": "contradicts",
    "evidence_for": "evidence_for",
    "derived_from": "derived_from",
    "answers": "answers",
    "mentioned_in": "mentioned_in",
}

_OWNER = "dr_bose"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


@dataclass
class MigrationPlan:
    nodes: list[Node]
    edges: list[Edge]
    skipped_edges: list[tuple[str, str, str]]  # (from, to, kind)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _infer_tags(raw_type: str, entities: list[str]) -> list[str]:
    tags: list[str] = []
    if raw_type:
        tags.append(raw_type)
    tags.extend(entities[:2])
    # Preserve order, drop dupes.
    seen: set[str] = set()
    ordered: list[str] = []
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            ordered.append(tag)
    return ordered


def _build_plan(payload: dict[str, Any]) -> MigrationPlan:
    raw_nodes = payload.get("nodes", []) or []
    raw_edges = payload.get("edges", []) or []

    nodes: list[Node] = []
    valid_ids: set[str] = set()
    for raw in raw_nodes:
        node = Node(
            id=raw["id"],
            type=raw.get("type", "reference_material"),
            source_path=raw.get("source_path"),
            summary=raw.get("summary", ""),
            key_claims=list(raw.get("key_claims", []) or []),
            entities=list(raw.get("entities", []) or []),
            symbols=list(raw.get("symbols", []) or []),
            owner_employee_id=_OWNER,
            visibility="fleet",
            subject_tags=_infer_tags(
                raw.get("type", ""),
                list(raw.get("entities", []) or []),
            ),
            supersedes=[],
            episode_id=None,
            pii_scrubbed=False,
            embedding_vector_id=None,
            status="active",
            confidence_score=0.9,
            created_at=raw.get("updated_at") or _now_iso(),
            updated_at=raw.get("updated_at") or _now_iso(),
            updated_by="migrate_graph_v1_to_v2",
        )
        node.checksum = compute_checksum(node)
        nodes.append(node)
        valid_ids.add(node.id)

    edges: list[Edge] = []
    skipped: list[tuple[str, str, str]] = []
    for raw in raw_edges:
        from_id = raw.get("from")
        to_id = raw.get("to")
        v1_kind = raw.get("kind", "")
        if not from_id or not to_id:
            skipped.append((str(from_id), str(to_id), str(v1_kind)))
            continue
        if from_id not in valid_ids or to_id not in valid_ids:
            skipped.append((from_id, to_id, v1_kind))
            continue
        v2_kind = _EDGE_REMAP.get(v1_kind)
        if v2_kind is None:
            skipped.append((from_id, to_id, v1_kind))
            continue
        edges.append(
            Edge(
                from_node=from_id,
                to_node=to_id,
                kind=v2_kind,  # type: ignore[arg-type]
                note=f"migrated from v1 kind={v1_kind}",
                created_at=_now_iso(),
            )
        )

    return MigrationPlan(nodes=nodes, edges=edges, skipped_edges=skipped)


async def _apply_plan(plan: MigrationPlan, target: Path) -> dict[str, int]:
    store = GraphStore(db_path=target)
    try:
        for node in plan.nodes:
            await store.upsert_node(node)
        for edge in plan.edges:
            await store.add_edge(edge)
    finally:
        await store.close()

    return {
        "nodes_upserted": len(plan.nodes),
        "edges_upserted": len(plan.edges),
        "edges_skipped": len(plan.skipped_edges),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate Graphify v1 JSON into the v2 SQLite store.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=_DEFAULT_SOURCE,
        help=f"path to v1 context_graph.json (default: {_DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="v2 SQLite path (default: VYASA_GRAPH_PATH or ~/.vyasa/graph.sqlite)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and plan only; do not touch the target store",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    source: Path = args.source.expanduser()
    target: Path = args.target.expanduser() if args.target else default_graph_path()

    if not source.exists():
        print(f"[migrate] source not found: {source}", file=sys.stderr)
        return 2

    payload = json.loads(source.read_text(encoding="utf-8"))
    plan = _build_plan(payload)

    print(f"[migrate] source: {source}")
    print(f"[migrate] target: {target}")
    print(
        f"[migrate] planned: {len(plan.nodes)} nodes, "
        f"{len(plan.edges)} edges, "
        f"{len(plan.skipped_edges)} skipped edges"
    )
    for from_id, to_id, kind in plan.skipped_edges:
        print(f"[migrate]   skipped: {from_id} -> {to_id} ({kind})")

    if args.dry_run:
        print("[migrate] dry-run — no writes performed")
        return 0

    stats = asyncio.run(_apply_plan(plan, target))
    print(f"[migrate] wrote: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

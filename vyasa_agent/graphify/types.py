# SPDX-License-Identifier: Apache-2.0
"""Pydantic models for the Graphify v2 storage layer.

The models here are the in-memory shape the rest of the fleet speaks; the
SQLite rows in ``schema/001_init.sql`` are the on-disk projection (with
list-shaped fields serialised as ``*_json`` TEXT columns).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# enums / literal unions
# --------------------------------------------------------------------------- #

Visibility = Literal["private", "team", "fleet"]
NodeStatus = Literal["active", "archived", "flagged_stale"]
EdgeKind = Literal[
    "depends_on",
    "supersedes",
    "handed_off_to",
    "contradicts",
    "evidence_for",
    "derived_from",
    "answers",
    "mentioned_in",
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _utcnow_iso() -> str:
    """Return an ISO-8601 UTC timestamp with a trailing ``Z``."""

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# primary entities
# --------------------------------------------------------------------------- #


class Node(BaseModel):
    """A single knowledge-graph node.

    ``checksum`` is optional on construction: the store computes it on
    upsert and writes it back onto the row so downstream consumers can
    rely on ``node.checksum`` being present after ``GraphStore.upsert_node``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    source_path: str | None = None
    line_range: str | None = None
    summary: str
    key_claims: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)

    owner_employee_id: str
    visibility: Visibility = "private"
    subject_tags: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)

    episode_id: str | None = None
    pii_scrubbed: bool = False
    embedding_vector_id: str | None = None
    checksum: str | None = None

    status: NodeStatus = "active"
    confidence_score: float = Field(default=0.8, ge=0.0, le=1.0)
    archived_at: str | None = None
    ttl_days: int | None = None

    created_at: str = Field(default_factory=_utcnow_iso)
    updated_at: str = Field(default_factory=_utcnow_iso)
    updated_by: str | None = None


class Edge(BaseModel):
    """A typed, directional edge between two nodes."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    from_node: str
    to_node: str
    kind: EdgeKind
    note: str | None = None
    created_at: str = Field(default_factory=_utcnow_iso)


class Episode(BaseModel):
    """A conversation/thread container (Telegram/WhatsApp/CLI session)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    platform: str
    platform_chat_id: str | None = None
    platform_user_id: str | None = None
    started_at: str = Field(default_factory=_utcnow_iso)
    ended_at: str | None = None


# --------------------------------------------------------------------------- #
# query surface
# --------------------------------------------------------------------------- #


class QueryFilters(BaseModel):
    """Parameters accepted by :meth:`GraphStore.query`.

    ``intent`` is applied as a fuzzy ``LIKE`` against ``summary`` and the
    serialised subject-tags column; the vector-recall refinement lives in
    the Qdrant layer and is out of scope for this PR.
    """

    model_config = ConfigDict(extra="forbid")

    intent: str | None = None
    visibility_scope: list[Visibility] | None = None
    owner_employee_id: str | None = None
    episode_id: str | None = None
    tags: list[str] | None = None
    since: str | None = None
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    include_archived: bool = False
    limit: int = Field(default=20, ge=1, le=1000)


class Subgraph(BaseModel):
    """A root-anchored BFS expansion returned by ``get_subgraph``."""

    model_config = ConfigDict(extra="forbid")

    root_id: str
    depth: int
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class CompactionReport(BaseModel):
    """Summary emitted by :class:`vyasa_agent.graphify.compactor.Compactor`."""

    model_config = ConfigDict(extra="forbid")

    trigger: Literal["count", "time"]
    scanned: int = 0
    deduped: int = 0
    superseded_collapsed: int = 0
    archived_ttl: int = 0
    started_at: str = Field(default_factory=_utcnow_iso)
    finished_at: str | None = None
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #


class PIILeakError(ValueError):
    """Raised when a write attempts to persist unredacted PII."""


__all__ = [
    "CompactionReport",
    "Edge",
    "EdgeKind",
    "Episode",
    "Node",
    "NodeStatus",
    "PIILeakError",
    "QueryFilters",
    "Subgraph",
    "Visibility",
]

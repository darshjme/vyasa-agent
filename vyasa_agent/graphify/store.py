"""SQLite-backed GraphStore for Graphify v2.

The store uses the stdlib :mod:`sqlite3` module running under a single
writer connection, wrapped with ``run_in_executor`` for async callers.
This keeps the dependency surface small (no ``aiosqlite``) while still
honouring the constraint in design-03-memory.md §5: WAL-mode SQLite with
serialised writers.

The on-disk schema is declared in ``schema/001_init.sql``; this module
handles connection lifecycle, JSON (de)serialisation for list-shaped
columns, checksum-based upsert, filtered query, and BFS subgraph walks.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

from .checksum import compute_checksum
from .types import Edge, Episode, Node, QueryFilters, Subgraph

T = TypeVar("T")

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

_SCHEMA_DIR = Path(__file__).with_name("schema")
_DEFAULT_PATH_ENV = "VYASA_GRAPH_PATH"
_DEFAULT_PATH = Path.home() / ".vyasa" / "graph.sqlite"

_NODE_COLUMNS = (
    "id",
    "type",
    "source_path",
    "line_range",
    "summary",
    "key_claims_json",
    "entities_json",
    "symbols_json",
    "owner_employee_id",
    "visibility",
    "subject_tags_json",
    "supersedes_json",
    "episode_id",
    "pii_scrubbed",
    "embedding_vector_id",
    "checksum",
    "status",
    "confidence_score",
    "archived_at",
    "ttl_days",
    "created_at",
    "updated_at",
    "updated_by",
)

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def default_graph_path() -> Path:
    """Resolve the canonical graph path.

    Precedence: ``VYASA_GRAPH_PATH`` env var → ``~/.vyasa/graph.sqlite``.
    The parent directory is created if needed; the file itself is not
    touched here (the store opens it on ``__init__``).
    """

    raw = os.environ.get(_DEFAULT_PATH_ENV)
    path = Path(raw).expanduser() if raw else _DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _row_to_node(row: sqlite3.Row) -> Node:
    """Decode a ``nodes`` row into a :class:`Node`."""

    return Node(
        id=row["id"],
        type=row["type"],
        source_path=row["source_path"],
        line_range=row["line_range"],
        summary=row["summary"],
        key_claims=json.loads(row["key_claims_json"] or "[]"),
        entities=json.loads(row["entities_json"] or "[]"),
        symbols=json.loads(row["symbols_json"] or "[]"),
        owner_employee_id=row["owner_employee_id"],
        visibility=row["visibility"],
        subject_tags=json.loads(row["subject_tags_json"] or "[]"),
        supersedes=json.loads(row["supersedes_json"] or "[]"),
        episode_id=row["episode_id"],
        pii_scrubbed=bool(row["pii_scrubbed"]),
        embedding_vector_id=row["embedding_vector_id"],
        checksum=row["checksum"],
        status=row["status"],
        confidence_score=row["confidence_score"],
        archived_at=row["archived_at"],
        ttl_days=row["ttl_days"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
    )


def _row_to_edge(row: sqlite3.Row) -> Edge:
    """Decode an ``edges`` row into an :class:`Edge`."""

    return Edge(
        id=row["id"],
        from_node=row["from_node"],
        to_node=row["to_node"],
        kind=row["kind"],
        note=row["note"],
        created_at=row["created_at"],
    )


def _node_to_row(node: Node) -> tuple[Any, ...]:
    """Encode a :class:`Node` into the parameter tuple for insert."""

    return (
        node.id,
        node.type,
        node.source_path,
        node.line_range,
        node.summary,
        json.dumps(node.key_claims, ensure_ascii=False),
        json.dumps(node.entities, ensure_ascii=False),
        json.dumps(node.symbols, ensure_ascii=False),
        node.owner_employee_id,
        node.visibility,
        json.dumps(node.subject_tags, ensure_ascii=False),
        json.dumps(node.supersedes, ensure_ascii=False),
        node.episode_id,
        1 if node.pii_scrubbed else 0,
        node.embedding_vector_id,
        node.checksum,
        node.status,
        node.confidence_score,
        node.archived_at,
        node.ttl_days,
        node.created_at,
        node.updated_at,
        node.updated_by,
    )


# --------------------------------------------------------------------------- #
# GraphStore
# --------------------------------------------------------------------------- #


class GraphStore:
    """Typed facade over a WAL-mode SQLite database.

    All public methods are async and delegate to a thread executor so
    the stdlib ``sqlite3`` driver never blocks the event loop. A single
    writer connection is used; SQLite itself serialises writes.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path: Path = (
            Path(db_path).expanduser() if db_path is not None else default_graph_path()
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn: sqlite3.Connection = sqlite3.connect(
            str(self.db_path),
            isolation_level=None,  # autocommit; we manage txns explicitly
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._configure_connection()
        self._run_migrations()

    # ------------------------------------------------------------------ #
    # setup
    # ------------------------------------------------------------------ #

    def _configure_connection(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    def _run_migrations(self) -> None:
        if not _SCHEMA_DIR.exists():
            return
        scripts = sorted(_SCHEMA_DIR.glob("*.sql"))
        for script in scripts:
            sql = script.read_text(encoding="utf-8")
            self._conn.executescript(sql)

    async def _run(self, fn: Callable[[], T]) -> T:
        """Execute ``fn`` on a worker thread, serialised by ``self._lock``."""

        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, fn)

    # ------------------------------------------------------------------ #
    # node upsert
    # ------------------------------------------------------------------ #

    async def upsert_node(self, node: Node) -> str:
        """Insert or refresh ``node``; return the stored node id.

        Dedup is driven by ``checksum``. If an existing row already holds
        the computed checksum, the stored id is returned unchanged (the
        incoming id is discarded so callers can safely regenerate ids
        on each run without inflating the graph). Otherwise we insert or
        update-by-id.
        """

        # Compute + persist checksum onto the caller's model so they
        # can see what was written.
        node.checksum = compute_checksum(node)

        def _op() -> str:
            cursor = self._conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                existing = cursor.execute(
                    "SELECT id FROM nodes WHERE checksum = ?",
                    (node.checksum,),
                ).fetchone()
                if existing is not None:
                    cursor.execute("COMMIT")
                    return str(existing["id"])

                row = _node_to_row(node)
                placeholders = ", ".join(["?"] * len(_NODE_COLUMNS))
                columns = ", ".join(_NODE_COLUMNS)
                updates = ", ".join(
                    f"{col}=excluded.{col}" for col in _NODE_COLUMNS if col != "id"
                )
                cursor.execute(
                    f"INSERT INTO nodes ({columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT(id) DO UPDATE SET {updates}",
                    row,
                )
                cursor.execute("COMMIT")
                return node.id
            except Exception:
                cursor.execute("ROLLBACK")
                raise
            finally:
                cursor.close()

        return await self._run(_op)

    # ------------------------------------------------------------------ #
    # edges
    # ------------------------------------------------------------------ #

    async def add_edge(self, edge: Edge) -> int:
        """Insert ``edge`` and return the auto-assigned row id."""

        def _op() -> int:
            cursor = self._conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute(
                    "INSERT INTO edges (from_node, to_node, kind, note, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (edge.from_node, edge.to_node, edge.kind, edge.note, edge.created_at),
                )
                new_id = int(cursor.lastrowid or 0)
                cursor.execute("COMMIT")
                return new_id
            except Exception:
                cursor.execute("ROLLBACK")
                raise
            finally:
                cursor.close()

        return await self._run(_op)

    # ------------------------------------------------------------------ #
    # episodes
    # ------------------------------------------------------------------ #

    async def upsert_episode(self, episode: Episode) -> str:
        """Insert or refresh an episode; return its id."""

        def _op() -> str:
            cursor = self._conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute(
                    """
                    INSERT INTO episodes (
                        id, platform, platform_chat_id, platform_user_id,
                        started_at, ended_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        platform=excluded.platform,
                        platform_chat_id=excluded.platform_chat_id,
                        platform_user_id=excluded.platform_user_id,
                        started_at=excluded.started_at,
                        ended_at=excluded.ended_at
                    """,
                    (
                        episode.id,
                        episode.platform,
                        episode.platform_chat_id,
                        episode.platform_user_id,
                        episode.started_at,
                        episode.ended_at,
                    ),
                )
                cursor.execute("COMMIT")
                return episode.id
            except Exception:
                cursor.execute("ROLLBACK")
                raise
            finally:
                cursor.close()

        return await self._run(_op)

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #

    async def get_node(self, id: str) -> Node | None:
        """Fetch a single node by id, or ``None`` if missing."""

        def _op() -> Node | None:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", (id,)
            ).fetchone()
            return _row_to_node(row) if row is not None else None

        return await self._run(_op)

    async def query(self, filters: QueryFilters) -> list[Node]:
        """Filter-based query against the nodes table.

        See :class:`QueryFilters` for semantics. ``intent`` is applied
        as a case-insensitive ``LIKE`` against both ``summary`` and the
        serialised subject-tags column; semantic ranking arrives with
        the Qdrant layer.
        """

        clauses: list[str] = []
        params: list[Any] = []

        if not filters.include_archived:
            clauses.append("status != 'archived'")

        if filters.intent:
            needle = f"%{filters.intent}%"
            clauses.append("(summary LIKE ? OR subject_tags_json LIKE ?)")
            params.extend([needle, needle])

        if filters.visibility_scope:
            placeholders = ", ".join(["?"] * len(filters.visibility_scope))
            clauses.append(f"visibility IN ({placeholders})")
            params.extend(filters.visibility_scope)

        if filters.owner_employee_id:
            clauses.append("owner_employee_id = ?")
            params.append(filters.owner_employee_id)

        if filters.episode_id:
            clauses.append("episode_id = ?")
            params.append(filters.episode_id)

        if filters.tags:
            # Match any tag via JSON string containment. Good enough
            # for filter-and-rerank; precise filtering lands with the
            # vector store's payload index.
            for tag in filters.tags:
                clauses.append("subject_tags_json LIKE ?")
                params.append(f"%{json.dumps(tag, ensure_ascii=False)}%")

        if filters.since:
            clauses.append("updated_at >= ?")
            params.append(filters.since)

        if filters.min_confidence > 0:
            clauses.append("confidence_score >= ?")
            params.append(filters.min_confidence)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM nodes {where} "
            f"ORDER BY confidence_score DESC, updated_at DESC "
            f"LIMIT ?"
        )
        params.append(filters.limit)

        def _op() -> list[Node]:
            rows = self._conn.execute(sql, params).fetchall()
            return [_row_to_node(row) for row in rows]

        return await self._run(_op)

    async def get_subgraph(self, root_id: str, depth: int = 2) -> Subgraph:
        """Return the BFS expansion rooted at ``root_id`` up to ``depth``.

        Edges are walked in both directions so readers can reason about
        ancestors (e.g. ``supersedes``) as well as dependents. The root
        node itself is included; missing roots yield an empty subgraph
        carrying the requested ``depth`` unchanged.
        """

        if depth < 0:
            raise ValueError("depth must be >= 0")

        def _op() -> Subgraph:
            seen_nodes: dict[str, Node] = {}
            seen_edge_ids: set[int] = set()
            edges: list[Edge] = []

            root_row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ?", (root_id,)
            ).fetchone()
            if root_row is None:
                return Subgraph(root_id=root_id, depth=depth, nodes=[], edges=[])

            seen_nodes[root_id] = _row_to_node(root_row)
            frontier: list[str] = [root_id]

            for _ in range(depth):
                if not frontier:
                    break
                placeholders = ", ".join(["?"] * len(frontier))
                edge_rows = self._conn.execute(
                    f"SELECT * FROM edges "
                    f"WHERE from_node IN ({placeholders}) "
                    f"   OR to_node IN ({placeholders})",
                    (*frontier, *frontier),
                ).fetchall()

                next_frontier: list[str] = []
                for edge_row in edge_rows:
                    edge_id = int(edge_row["id"])
                    if edge_id in seen_edge_ids:
                        continue
                    seen_edge_ids.add(edge_id)
                    edges.append(_row_to_edge(edge_row))
                    for neighbour in (edge_row["from_node"], edge_row["to_node"]):
                        if neighbour in seen_nodes:
                            continue
                        node_row = self._conn.execute(
                            "SELECT * FROM nodes WHERE id = ?", (neighbour,)
                        ).fetchone()
                        if node_row is None:
                            continue
                        seen_nodes[neighbour] = _row_to_node(node_row)
                        next_frontier.append(neighbour)

                frontier = next_frontier

            return Subgraph(
                root_id=root_id,
                depth=depth,
                nodes=list(seen_nodes.values()),
                edges=edges,
            )

        return await self._run(_op)

    # ------------------------------------------------------------------ #
    # maintenance (used by Compactor)
    # ------------------------------------------------------------------ #

    async def iter_active_nodes(self, batch_size: int = 500) -> list[Node]:
        """Return all active nodes. Caller-paginated batch convenience."""

        def _op() -> list[Node]:
            rows = self._conn.execute(
                "SELECT * FROM nodes WHERE status = 'active' "
                "ORDER BY updated_at ASC LIMIT ?",
                (batch_size,),
            ).fetchall()
            return [_row_to_node(row) for row in rows]

        return await self._run(_op)

    async def nodes_changed_since(
        self, since_iso: str, limit: int = 100
    ) -> list[Node]:
        """Return active nodes whose ``updated_at`` is strictly after ``since_iso``.

        The graph_diff MCP tool uses this to serve incremental sync callers
        (routines, peers, offline replicas). Results are ordered oldest-first
        so a consumer can walk a cursor forward one batch at a time. The
        ``since_iso`` comparison is string-lexicographic, which matches
        Python's ``datetime.isoformat`` ordering as long as both sides are
        stored in UTC (the store enforces this at write time via
        ``_utcnow_iso``).
        """

        if limit < 1:
            raise ValueError("limit must be >= 1")

        def _op() -> list[Node]:
            rows = self._conn.execute(
                "SELECT * FROM nodes "
                "WHERE status != 'archived' AND updated_at > ? "
                "ORDER BY updated_at ASC LIMIT ?",
                (since_iso, limit),
            ).fetchall()
            return [_row_to_node(row) for row in rows]

        return await self._run(_op)

    async def mark_archived(self, node_ids: Sequence[str], archived_at: str) -> int:
        """Flip status→archived for ``node_ids``. Return the affected count."""

        if not node_ids:
            return 0

        def _op() -> int:
            cursor = self._conn.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                placeholders = ", ".join(["?"] * len(node_ids))
                cursor.execute(
                    f"UPDATE nodes SET status='archived', archived_at=?, "
                    f"       updated_at=? "
                    f"WHERE id IN ({placeholders}) AND status='active'",
                    (archived_at, archived_at, *node_ids),
                )
                changed = cursor.rowcount
                cursor.execute("COMMIT")
                return int(changed or 0)
            except Exception:
                cursor.execute("ROLLBACK")
                raise
            finally:
                cursor.close()

        return await self._run(_op)

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    async def close(self) -> None:
        """Close the underlying SQLite connection."""

        def _op() -> None:
            self._conn.close()

        await self._run(_op)


__all__ = ["GraphStore", "default_graph_path"]

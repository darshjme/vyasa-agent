"""Vector-store interface stub.

The SQLite storage layer in this PR is intentionally self-sufficient.
Semantic recall (Qdrant / ``all-mpnet-base-v2``) lands in a later PR —
until then, any call into this module raises :class:`NotImplementedError`
so accidental uses fail loudly instead of silently returning empty hits.

The interface below is frozen so the future wiring is a drop-in without
ripple changes to the store or the MCP server.
"""

from __future__ import annotations

from typing import Protocol

from .types import Node


class VectorStore(Protocol):
    """Minimal surface the store needs from a vector backend."""

    async def upsert(self, node: Node) -> str:
        """Embed ``node.summary`` + claims and persist the vector.

        Returns the backend-assigned vector id, intended to be written
        back onto ``node.embedding_vector_id`` by the caller.
        """
        ...

    async def search(
        self,
        intent: str,
        *,
        visibility_scope: list[str] | None = None,
        owner_employee_id: str | None = None,
        limit: int = 20,
    ) -> list[tuple[str, float]]:
        """Return ``(node_id, score)`` pairs ranked by cosine similarity."""
        ...

    async def delete(self, node_id: str) -> None:
        """Remove the vector associated with ``node_id``."""
        ...

    async def close(self) -> None:
        """Flush and tear down any open client connections."""
        ...


class PendingVectorStore:
    """Placeholder that refuses every call until the real wiring lands."""

    async def upsert(self, node: Node) -> str:  # pragma: no cover - stub
        raise NotImplementedError("vector backend not wired in this build")

    async def search(
        self,
        intent: str,
        *,
        visibility_scope: list[str] | None = None,
        owner_employee_id: str | None = None,
        limit: int = 20,
    ) -> list[tuple[str, float]]:  # pragma: no cover - stub
        raise NotImplementedError("vector backend not wired in this build")

    async def delete(self, node_id: str) -> None:  # pragma: no cover - stub
        raise NotImplementedError("vector backend not wired in this build")

    async def close(self) -> None:  # pragma: no cover - stub
        raise NotImplementedError("vector backend not wired in this build")


__all__ = ["PendingVectorStore", "VectorStore"]

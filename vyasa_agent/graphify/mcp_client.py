"""Typed Python client for Graphify. Two transports: in-process (default) and stdio MCP.

In-process talks to :class:`GraphStore` directly; stdio launches the MCP server
via ``python -m vyasa_agent.graphify.mcp_server``. Same surface either way.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Literal

from .pii import PIIScrubber
from .store import GraphStore
from .types import Node, QueryFilters

Transport = Literal["inproc", "stdio"]


def _ensure_mcp_available() -> None:
    try:
        import mcp  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "The 'mcp' package is required for stdio transport. "
            "Install the admin extra:  uv pip install 'vyasa-agent[admin]'."
        ) from e


class GraphifyClient:
    """Async client for Graphify. Prefer this over calling the store directly."""

    def __init__(
        self,
        db_path: str | Path = "~/.vyasa/graph.sqlite",
        *,
        transport: Transport = "inproc",
        employee_sig: str | None = None,
    ) -> None:
        self._db_path = Path(os.path.expanduser(str(db_path))).resolve()
        self._transport: Transport = transport
        self._employee_sig = employee_sig or os.environ.get("VYASA_EMPLOYEE_SIG", "dev-unsigned")
        self._store: GraphStore | None = None
        self._scrubber = PIIScrubber()
        self._session: Any = None  # mcp ClientSession when transport="stdio"
        self._exit_stack: AsyncExitStack | None = None

    async def __aenter__(self) -> GraphifyClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._transport == "inproc":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._store = GraphStore(self._db_path)
            return
        _ensure_mcp_available()
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "vyasa_agent.graphify.mcp_server", "--db", str(self._db_path)],
            env={**os.environ, "VYASA_EMPLOYEE_SIG": self._employee_sig},
        )
        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(stdio_client(params))
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def close(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None

    async def graph_read(self, node_id: str) -> dict[str, Any] | None:
        if self._transport == "inproc":
            assert self._store is not None
            node = await self._store.get_node(node_id)
            return node.model_dump() if node else None
        return await self._call("graph_read", {"node_id": node_id})

    async def graph_query(
        self,
        intent: str,
        *,
        visibility_scope: str = "team",
        owner_employee_id: str | None = None,
        episode_id: str | None = None,
        tags: list[str] | None = None,
        since: str | None = None,
        limit: int = 20,
        min_confidence: float = 0.0,
    ) -> list[dict[str, Any]]:
        args: dict[str, Any] = {
            "intent": intent,
            "visibility_scope": visibility_scope,
            "owner_employee_id": owner_employee_id,
            "episode_id": episode_id,
            "tags": list(tags or []),
            "since": since,
            "limit": limit,
            "min_confidence": min_confidence,
        }
        if self._transport == "inproc":
            assert self._store is not None
            filters = QueryFilters(
                intent=intent,
                visibility_scope=[visibility_scope] if visibility_scope else None,
                owner_employee_id=owner_employee_id,
                episode_id=episode_id,
                tags=list(tags or []) or None,
                since=since,
                limit=limit,
                min_confidence=min_confidence,
            )
            rows = await self._store.query(filters)
            return [r.model_dump() for r in rows]
        return await self._call("graph_query", args)

    async def graph_write(
        self, node_payload: dict[str, Any], author_employee_id: str
    ) -> dict[str, Any]:
        if self._transport == "inproc":
            assert self._store is not None
            node = Node.model_validate(node_payload)
            # Strict PII gate: raises PIILeakError from the scrubber when
            # any residue survives. Successful gate also stamps updated_by
            # so the audit trail records who requested the write.
            self._scrubber.check_before_write(node.summary, node.key_claims)
            node.pii_scrubbed = True
            if not node.updated_by:
                node.updated_by = author_employee_id
            stored_id = await self._store.upsert_node(node)
            saved = await self._store.get_node(stored_id)
            if saved is None:
                raise RuntimeError(f"node {stored_id} vanished after upsert")
            return saved.model_dump()
        return await self._call(
            "graph_write",
            {"node_payload": node_payload, "author_employee_id": author_employee_id},
        )

    async def graph_diff(self, since_iso: str) -> dict[str, Any]:
        if self._transport == "inproc":
            assert self._store is not None
            nodes = await self._store.nodes_changed_since(since_iso)
            return {"nodes": [n.model_dump() for n in nodes]}
        return await self._call("graph_diff", {"since_iso": since_iso})

    async def handoff_node(self, from_id: str, to_id: str, context: str) -> str:
        """Record a ``handed_off_to`` edge as a compact handoff node; return its id."""
        assert self._store is not None or self._session is not None, "call connect() first"
        payload: dict[str, Any] = {
            "type": "handoff",
            "source_path": f"handoff://{from_id}->{to_id}",
            "summary": context[:1200],
            "key_claims": [f"handoff from {from_id} to {to_id}"],
            "entities": [from_id, to_id],
            "owner_employee_id": to_id,
            "visibility": "team",
            "subject_tags": ["handoff"],
            "confidence_score": 1.0,
        }
        saved = await self.graph_write(payload, author_employee_id=from_id)
        return saved["id"]

    async def _call(self, tool: str, arguments: dict[str, Any]) -> Any:
        assert self._session is not None, "stdio client not connected"
        result = await self._session.call_tool(tool, arguments=arguments)
        for block in getattr(result, "content", []):
            text = getattr(block, "text", None)
            if text is not None:
                return json.loads(text)
        return None

"""Graphify stdio MCP server — memory-fabric tool surface for the Vyasa fleet.

Tools: ``graph_read``, ``graph_query``, ``graph_write``, ``graph_diff``.
Auth: ``X-Employee-Sig`` header, presence-only in v0.1.
Entry point: ``python -m vyasa_agent.graphify.mcp_server --db ~/.vyasa/graph.sqlite``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from .pii import PIIScrubber
from .store import GraphStore
from .types import Node, PIILeakError

_MCP_MISSING = (
    "The 'mcp' package is required to run the Graphify MCP server. "
    "Install the admin extra:  uv pip install 'vyasa-agent[admin]'  "
    "(or:  uv pip install 'mcp>=1.2')."
)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError as _exc:  # pragma: no cover - exercised at startup only
    raise ImportError(_MCP_MISSING) from _exc


log = logging.getLogger("vyasa.graphify.mcp")


# --------------------------------------------------------------------------- #
# Tool schemas                                                                #
# --------------------------------------------------------------------------- #


def _tool_schemas() -> list[Tool]:
    """JSON-schema contracts for the four graph tools."""
    return [
        Tool(
            name="graph_read",
            description="Fetch a Graphify node by id. Returns the full node payload.",
            inputSchema={
                "type": "object",
                "required": ["node_id"],
                "properties": {"node_id": {"type": "string"}},
            },
        ),
        Tool(
            name="graph_query",
            description=(
                "Recall a scoped subgraph. Filters by owner, visibility, episode, "
                "tags, and recency; returns nodes ranked by intent."
            ),
            inputSchema={
                "type": "object",
                "required": ["intent"],
                "properties": {
                    "intent": {"type": "string"},
                    "visibility_scope": {
                        "type": "string",
                        "enum": ["private", "team", "fleet"],
                        "default": "team",
                    },
                    "owner_employee_id": {"type": ["string", "null"]},
                    "episode_id": {"type": ["string", "null"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "since": {"type": ["string", "null"], "format": "date-time"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                    "min_confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "default": 0.0,
                    },
                },
            },
        ),
        Tool(
            name="graph_write",
            description=(
                "Scrub for PII then upsert a node. Raises PIILeakError on dirty "
                "input. Dedup is checksum-based."
            ),
            inputSchema={
                "type": "object",
                "required": ["node_payload", "author_employee_id"],
                "properties": {
                    "node_payload": {"type": "object"},
                    "author_employee_id": {"type": "string"},
                },
            },
        ),
        Tool(
            name="graph_diff",
            description="Return nodes updated since an ISO-8601 timestamp.",
            inputSchema={
                "type": "object",
                "required": ["since_iso"],
                "properties": {"since_iso": {"type": "string", "format": "date-time"}},
            },
        ),
    ]


# --------------------------------------------------------------------------- #
# Auth (v0.1: header presence only)                                           #
# --------------------------------------------------------------------------- #


class AuthError(RuntimeError):
    """Raised when a required auth header is missing."""


def _assert_employee_sig(ctx_env: dict[str, str]) -> None:
    """Require an ``X-Employee-Sig`` header. Signature verification is a v0.2 job."""
    if not ctx_env.get("X-Employee-Sig") and not os.environ.get("VYASA_EMPLOYEE_SIG"):
        raise AuthError(
            "X-Employee-Sig header missing. Set VYASA_EMPLOYEE_SIG in the server "
            "env or pass the signed token via the MCP client context."
        )


# --------------------------------------------------------------------------- #
# Server wiring                                                               #
# --------------------------------------------------------------------------- #


def _as_text(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, default=str))]


def build_server(db_path: Path) -> Server:
    """Build the stdio MCP server bound to the Graphify store at *db_path*."""
    store = GraphStore(db_path)
    scrubber = PIIScrubber()
    server: Server = Server("graphify-vyasa")

    @server.list_tools()
    async def _list() -> list[Tool]:
        return _tool_schemas()

    @server.call_tool()
    async def _dispatch(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        _assert_employee_sig(dict(os.environ))
        try:
            if name == "graph_read":
                node = store.read(arguments["node_id"])
                return _as_text(node.model_dump() if node else None)
            if name == "graph_query":
                rows = store.query(
                    intent=arguments["intent"],
                    visibility_scope=arguments.get("visibility_scope", "team"),
                    owner_employee_id=arguments.get("owner_employee_id"),
                    episode_id=arguments.get("episode_id"),
                    tags=arguments.get("tags", []),
                    since=arguments.get("since"),
                    limit=int(arguments.get("limit", 20)),
                    min_confidence=float(arguments.get("min_confidence", 0.0)),
                )
                return _as_text([r.model_dump() for r in rows])
            if name == "graph_write":
                node = Node.model_validate(arguments["node_payload"])
                if not scrubber.check_before_write(node):
                    raise PIILeakError(f"PII detected in node {node.id}; write refused.")
                saved = store.write(node, author_employee_id=arguments["author_employee_id"])
                return _as_text(saved.model_dump())
            if name == "graph_diff":
                changed = store.diff(arguments["since_iso"])
                return _as_text({"nodes": [n.model_dump() for n in changed]})
            raise ValueError(f"Unknown tool: {name}")
        except PIILeakError as e:
            log.warning("graph_write blocked: %s", e)
            return _as_text({"error": "PIILeakError", "detail": str(e)})
        except AuthError as e:
            return _as_text({"error": "AuthError", "detail": str(e)})
        except Exception as e:
            log.exception("tool %s failed", name)
            return _as_text({"error": type(e).__name__, "detail": str(e)})

    return server


async def _serve(db_path: Path) -> None:
    server = build_server(db_path)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="vyasa-graphify-mcp")
    p.add_argument("--db", default="~/.vyasa/graph.sqlite", help="Path to Graphify sqlite file.")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    db_path = Path(os.path.expanduser(args.db)).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(_serve(db_path))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

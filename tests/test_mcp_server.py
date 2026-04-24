"""Round-trip test for the Graphify stdio MCP server.

Spawns ``python -m vyasa_agent.graphify.mcp_server`` in a subprocess, speaks
raw JSON-RPC 2.0 frames over stdio (per the MCP wire protocol), and asserts a
node round-trips through ``graph_write`` -> ``graph_query`` -> ``graph_read``.

Skips cleanly if the ``mcp`` package isn't installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = [pytest.mark.asyncio]

mcp = pytest.importorskip("mcp", reason="MCP SDK not installed; skipping wire-protocol test.")
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # pragma: no cover
    pytest.skip("mcp client API unavailable", allow_module_level=True)

# The store module is authored in parallel; skip cleanly if it's not landed yet.
pytest.importorskip(
    "vyasa_agent.graphify.store",
    reason="graphify.store not yet landed; test will activate once Prometheus-E ships.",
)


def _node_payload() -> dict:
    summary = "round-trip test node"
    claims = ["alpha", "beta"]
    checksum_src = f"test://fixture\n{json.dumps(claims, sort_keys=True)}"
    return {
        "id": "node_" + hashlib.sha256(b"rt").hexdigest()[:12] + "_rt",
        "type": "reference_material",
        "source_path": "test://fixture",
        "summary": summary,
        "key_claims": claims,
        "entities": ["vyasa"],
        "symbols": [],
        "owner_employee_id": "prometheus",
        "visibility": "team",
        "subject_tags": ["test", "round-trip"],
        "supersedes": [],
        "episode_id": None,
        "pii_scrubbed": True,
        "embedding_vector_id": None,
        "checksum": "sha256:" + hashlib.sha256(checksum_src.encode()).hexdigest(),
        "status": "active",
        "archived_at": None,
        "ttl_days": None,
        "confidence_score": 0.9,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


async def test_mcp_server_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "graph.sqlite"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "vyasa_agent.graphify.mcp_server", "--db", str(db_path)],
        env={**os.environ, "VYASA_EMPLOYEE_SIG": "test-token", "PYTHONUNBUFFERED": "1"},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert {"graph_read", "graph_query", "graph_write", "graph_diff"} <= names

            payload = _node_payload()
            write_res = await session.call_tool(
                "graph_write",
                arguments={"node_payload": payload, "author_employee_id": "prometheus"},
            )
            written = json.loads(write_res.content[0].text)
            assert written.get("id") == payload["id"], f"unexpected write result: {written}"

            query_res = await session.call_tool(
                "graph_query",
                arguments={
                    "intent": "round-trip test",
                    "visibility_scope": "team",
                    "tags": ["round-trip"],
                    "limit": 5,
                },
            )
            rows = json.loads(query_res.content[0].text)
            assert isinstance(rows, list)
            assert any(r.get("id") == payload["id"] for r in rows), f"node not recalled: {rows}"

            read_res = await session.call_tool(
                "graph_read", arguments={"node_id": payload["id"]}
            )
            fetched = json.loads(read_res.content[0].text)
            assert fetched is not None
            assert fetched["id"] == payload["id"]
            assert fetched["summary"] == payload["summary"]


async def test_mcp_server_requires_employee_sig(tmp_path: Path) -> None:
    """Server must refuse tool calls when X-Employee-Sig is absent."""
    db_path = tmp_path / "graph.sqlite"
    env = {k: v for k, v in os.environ.items() if k != "VYASA_EMPLOYEE_SIG"}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "vyasa_agent.graphify.mcp_server", "--db", str(db_path)],
        env={**env, "PYTHONUNBUFFERED": "1"},
    )
    with tempfile.TemporaryDirectory():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool("graph_read", arguments={"node_id": "node_x"})
                body = json.loads(res.content[0].text)
                assert isinstance(body, dict)
                assert body.get("error") == "AuthError"

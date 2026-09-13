"""Async provider runtime with bounded, persistent per-conversation history.

Uses an OpenAI-compatible endpoint. Provider credentials stay in environment
variables on the fleet host. No subprocess or host filesystem tools are exposed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI


def get_tool_definitions():
    return [
        {
            "type": "function",
            "function": {
                "name": "graph_read",
                "description": "Search notes in this conversation",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "graph_write",
                "description": "Save a factual note for this conversation",
                "parameters": {
                    "type": "object",
                    "properties": {"summary": {"type": "string", "maxLength": 4000}},
                    "required": ["summary"],
                    "additionalProperties": False,
                },
            },
        },
    ]


class AIAgent:
    def __init__(
        self,
        *,
        ephemeral_system_prompt: str,
        session_db: str,
        model: str,
        provider: str,
        enabled_toolsets=None,
        pre_tool_call=None,
        post_tool_call=None,
        employee_id="vyasa",
        graph_client=None,
        **kwargs,
    ):
        self.pre_tool = pre_tool_call
        self.post_tool = post_tool_call
        self.employee_id = employee_id
        self.graph = graph_client
        self.allowed = set(enabled_toolsets or [])
        self.prompt = ephemeral_system_prompt
        self.model = os.environ.get("VYASA_MODEL") or model.removeprefix("openrouter/")
        provider = os.environ.get("VYASA_PROVIDER", provider)
        endpoints = {
            "openrouter": "https://openrouter.ai/api/v1",
            "openai": "https://api.openai.com/v1",
            "ollama": "http://127.0.0.1:11434/v1",
        }
        self.base_url = os.environ.get("VYASA_BASE_URL") or endpoints.get(provider)
        if not self.base_url:
            raise ValueError("Set VYASA_BASE_URL to an OpenAI-compatible endpoint")
        self.key = os.environ.get("VYASA_API_KEY") or os.environ.get(
            f"{provider.upper()}_API_KEY", ""
        )
        if not self.key and provider != "ollama":
            raise ValueError("Set VYASA_API_KEY (or the provider API key) on the fleet host")
        self.db_path = Path(session_db)
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS conversations (session TEXT PRIMARY KEY, messages TEXT NOT NULL)"
            )
        self.db_path.chmod(0o600)

    async def run_conversation(self, message: str, *, session_id: str) -> dict[str, Any]:
        if not message.strip():
            raise ValueError("Message must not be empty")
        if len(message) > 32000:
            raise ValueError("Message exceeds 32000 characters")
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT messages FROM conversations WHERE session=?", (session_id,)
            ).fetchone()
        history = json.loads(row[0]) if row else []
        history.append({"role": "user", "content": message})
        # Retain complete recent turns, bounded independently of model context size.
        while len(history) > 21 or sum(len(m["content"] or "") for m in history) > 64000:
            if len(history) <= 1:
                break
            del history[:2]
        tools = [
            d
            for d in get_tool_definitions()
            if d["function"]["name"] in self.allowed and self.graph is not None
        ]
        messages = [{"role": "system", "content": self.prompt}, *history]
        calls = []
        async with AsyncOpenAI(
            api_key=self.key or "local", base_url=self.base_url, timeout=100, max_retries=1
        ) as client:
            for _ in range(8):
                reply = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=4096,
                    **({"tools": tools} if tools else {}),
                )
                answer = reply.choices[0].message
                if not answer.tool_calls:
                    text = answer.content
                    if not text:
                        raise RuntimeError("Model returned no text")
                    break
                messages.append(answer.model_dump(exclude_none=True))
                for call in answer.tool_calls:
                    name = call.function.name
                    if name not in self.allowed or not any(
                        t["function"]["name"] == name for t in tools
                    ):
                        raise PermissionError("Model requested an unavailable tool")
                    args = json.loads(call.function.arguments)
                    started = time.monotonic()
                    if self.pre_tool:
                        await self.pre_tool(name, args, session_id)
                    result = await self._memory_tool(name, args, session_id)
                    if self.post_tool:
                        await self.post_tool(
                            name,
                            "memory operation completed",
                            int((time.monotonic() - started) * 1000),
                            trace_id=session_id,
                            args=args,
                        )
                    calls.append({"name": name, "status": "completed"})
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
                    )
            else:
                raise RuntimeError("Model exceeded the eight-round tool budget")
        history.append({"role": "assistant", "content": text})
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO conversations VALUES (?, ?) ON CONFLICT(session) DO UPDATE SET messages=excluded.messages",
                (session_id, json.dumps(history)),
            )
        return {"text": text, "tool_calls": calls}

    async def _memory_tool(self, name, args, session_id):
        from vyasa_agent.graphify.types import Node, QueryFilters

        if name == "graph_read":
            if set(args) != {"query"} or not isinstance(args["query"], str):
                raise ValueError("graph_read requires a query string")
            nodes = await self.graph.query(
                QueryFilters(
                    intent=args["query"],
                    owner_employee_id=self.employee_id,
                    episode_id=session_id,
                    limit=8,
                )
            )
            return [{"id": n.id, "summary": n.summary} for n in nodes]
        if (
            set(args) != {"summary"}
            or not isinstance(args["summary"], str)
            or not 1 <= len(args["summary"]) <= 4000
        ):
            raise ValueError("graph_write requires a summary of 1-4000 characters")
        node = Node(
            id=uuid.uuid4().hex,
            type="note",
            summary=args["summary"],
            owner_employee_id=self.employee_id,
            episode_id=session_id,
        )
        await self.graph.upsert_node(node)
        return {"id": node.id, "saved": True}

    async def close(self):
        pass

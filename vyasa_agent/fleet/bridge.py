# SPDX-License-Identifier: Apache-2.0
"""AgentRuntimeBridge — adapter between EmployeeActor and the vendored AIAgent.

Lazy construction + Layer A boot filter + Layer B/C runtime hooks
(design-02 §3, design-04 §2). Hook install tries ``pre_tool_call`` /
``post_tool_call`` kwargs first; fallback wraps ``get_tool_definitions`` and
exposes :meth:`invoke_tool`. Vendored module imported lazily so tests can
monkey-patch ``vyasa_internals``."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import time
from pathlib import Path
from typing import Any

from .audit import AuditSink
from .capability import CapabilityMatrix
from .descriptor import EmployeeDescriptor, FleetConfig
from .hooks import boot_tool_filter, post_tool_call, pre_tool_call
from .registry_resolver import resolve_prompt
from .types import Turn, TurnResult

logger = logging.getLogger("vyasa.fleet.bridge")

_VENDOR_MODULE = "vyasa_internals"
DEFAULT_STATE_ROOT = Path.home() / ".vyasa" / "employees"


class AgentRuntimeBridge:
    """Per-employee adapter wrapping the vendored AIAgent."""

    def __init__(
        self,
        descriptor: EmployeeDescriptor,
        fleet_config: FleetConfig,
        matrix: CapabilityMatrix | None = None,
        graph_client: Any | None = None,
        audit_sink: AuditSink | None = None,
        *,
        state_root: Path | None = None,
    ) -> None:
        self.descriptor = descriptor
        self.fleet_config = fleet_config
        self.matrix = matrix
        self.graph_client = graph_client
        self.audit_sink = audit_sink
        self._state_root: Path = state_root or DEFAULT_STATE_ROOT
        self._agent: Any | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._allowed_tools: list[str] = []
        self._hook_mode: str = "unwired"

    async def ensure_agent(self) -> Any:
        if self._agent is not None:
            return self._agent
        async with self._lock:
            if self._agent is None:
                self._agent = await asyncio.to_thread(self._build_agent)
            return self._agent

    def _build_agent(self) -> Any:
        module = importlib.import_module(_VENDOR_MODULE)
        ai_agent_cls = module.AIAgent
        get_tool_definitions = getattr(module, "get_tool_definitions", lambda: [])

        self._allowed_tools = boot_tool_filter(
            self.descriptor, _tool_names(get_tool_definitions()), matrix=self.matrix)
        session_db = self._state_root / self.descriptor.id / "state.db"
        session_db.parent.mkdir(parents=True, exist_ok=True)

        kwargs: dict[str, Any] = {
            "ephemeral_system_prompt": resolve_prompt(self.descriptor.system_prompt_ref),
            "enabled_toolsets": self._allowed_tools,
            "session_db": str(session_db),
            "log_prefix": f"vyasa.{self.descriptor.id}",
            "gateway_session_key": self.descriptor.memory_namespace,
            "persist_session": True,
            "model": self.descriptor.model_preference.default,
            "provider": self.descriptor.model_preference.provider,
        }
        try:
            sig_params = set(inspect.signature(ai_agent_cls).parameters)
        except (TypeError, ValueError):
            sig_params = set()
        if {"pre_tool_call", "post_tool_call"} <= sig_params:
            kwargs["pre_tool_call"] = self._wrap_pre_hook()
            kwargs["post_tool_call"] = self._wrap_post_hook()
            self._hook_mode = "kwarg"

        agent = ai_agent_cls(**kwargs)
        if self._hook_mode == "unwired":
            self._install_registry_wrap(agent, get_tool_definitions)

        set_ctx = getattr(module, "set_session_context", None)
        if callable(set_ctx):
            try:
                set_ctx(fleet_name=self.fleet_config.fleet_name,
                        employee_id=self.descriptor.id, graph_client=self.graph_client)
            except TypeError:
                pass

        logger.info("bridge.agent.ready", extra={
            "event": "bridge.agent.ready", "employee_id": self.descriptor.id,
            "tool_count": len(self._allowed_tools), "hook_mode": self._hook_mode})
        return agent

    async def turn(self, turn: Turn) -> TurnResult:
        agent = await self.ensure_agent()
        result = agent.run_conversation(turn.text, session_id=turn.trace_id)
        if inspect.isawaitable(result):
            result = await result
        return TurnResult(
            employee_id=self.descriptor.id, trace_id=turn.trace_id,
            text=_coerce_text(result, self.descriptor.display_name),
            tool_calls=list(_field(result, "tool_calls") or []),
            confidence_score=1.0)

    async def close(self) -> None:
        agent, self._agent = self._agent, None
        if agent is None:
            return
        for name in ("_persist_session", "persist_session", "close"):
            method = getattr(agent, name, None)
            if method is None:
                continue
            try:
                maybe = method()
                if inspect.isawaitable(maybe):
                    await maybe
            except Exception:  # pragma: no cover
                logger.exception("bridge.close.error", extra={
                    "event": "bridge.close.error", "method": name,
                    "employee_id": self.descriptor.id})

    async def invoke_tool(self, tool_name: str, args: dict[str, Any],
                          trace_id: str = "") -> Any:
        """Gate a single tool dispatch through Layer B + C hooks."""
        if self.matrix is not None:
            await pre_tool_call(self.descriptor.id, tool_name, args, self.matrix,
                                trace_id=trace_id, audit_sink=self.audit_sink)
        start = time.monotonic()
        agent = await self.ensure_agent()
        dispatch = getattr(agent, "dispatch_tool", None) or getattr(
            agent, "_dispatch_tool", None)
        if dispatch is None:
            raise RuntimeError(f"AIAgent missing dispatch_tool for {tool_name!r}")
        outcome = dispatch(tool_name, args)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if self.audit_sink is not None:
            await post_tool_call(
                self.descriptor.id, tool_name, _summary(outcome),
                int((time.monotonic() - start) * 1000), self.audit_sink,
                trace_id=trace_id, args=args)
        return outcome

    def _wrap_pre_hook(self):
        async def _pre(tool_name: str, args: dict[str, Any], trace_id: str = "") -> None:
            if self.matrix is not None:
                await pre_tool_call(self.descriptor.id, tool_name, args, self.matrix,
                                    trace_id=trace_id, audit_sink=self.audit_sink)
        return _pre

    def _wrap_post_hook(self):
        async def _post(tool_name: str, result_summary: str, duration_ms: int,
                        *, trace_id: str = "", args: dict[str, Any] | None = None) -> None:
            if self.audit_sink is not None:
                await post_tool_call(self.descriptor.id, tool_name, result_summary,
                                     duration_ms, self.audit_sink,
                                     trace_id=trace_id, args=args)
        return _post

    def _install_registry_wrap(self, agent: Any, get_tool_definitions) -> None:
        """Fallback: filter ``get_tool_definitions`` + expose :meth:`invoke_tool`."""
        allowed, original = set(self._allowed_tools), get_tool_definitions
        def _filtered() -> list[Any]:
            return [d for d in (original() or []) if _tool_def_name(d) in allowed]
        for attr, val in (("get_tool_definitions", _filtered),
                          ("bridge_invoke_tool", self.invoke_tool)):
            try:
                setattr(agent, attr, val)
            except (AttributeError, TypeError):
                pass
        self._hook_mode = "registry_wrap"


def _tool_names(defs: Any) -> list[str]:
    return [n for n in (_tool_def_name(d) for d in (defs or [])) if n]

def _tool_def_name(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("name") or (entry.get("function") or {}).get("name") or ""
    return getattr(entry, "name", "") or ""

def _field(result: Any, key: str) -> Any:
    v = getattr(result, key, None)
    return v if v is not None else (result.get(key) if isinstance(result, dict) else None)

def _coerce_text(result: Any, display_name: str) -> str:
    text = _field(result, "text") or str(result)
    prefix = f"{display_name}:"
    return text if text.startswith(prefix) else f"{prefix} {text}"

def _summary(outcome: Any) -> str:
    try:
        return str(outcome)[:512]
    except Exception:  # pragma: no cover
        return "<unrepresentable>"

def stub_bridge_enabled() -> bool:
    """True when ``VYASA_STUB_BRIDGE=1`` disables the vendored runtime."""
    return os.environ.get("VYASA_STUB_BRIDGE", "") == "1"

__all__ = ["AgentRuntimeBridge", "stub_bridge_enabled"]

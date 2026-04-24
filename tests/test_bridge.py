"""AgentRuntimeBridge tests.

Installs a fake ``vyasa_internals`` module into ``sys.modules`` so the bridge
wires against a deterministic AIAgent. Validates:

1. ``TurnResult.text`` carries the ``Dr. Sarabhai:`` prefix when bridging that
   employee's descriptor.
2. The pre_tool_call hook fires (raising :class:`CapabilityError`) before a
   denied tool reaches the fake agent's dispatcher.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pydantic")

from vyasa_agent.fleet.capability import (  # noqa: E402
    Capability,
    CapabilityCell,
    CapabilityError,
    CapabilityMatrix,
    Decision,
)
from vyasa_agent.fleet.descriptor import (  # noqa: E402
    EmployeeDescriptor,
    FleetConfig,
    ModelPreference,
)
from vyasa_agent.fleet.types import Turn  # noqa: E402


# ---------------------------------------------------------------------------
# Fake vyasa_internals
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class _FakeAIAgent:
    """Echoes the length of ``ephemeral_system_prompt`` in its reply text."""

    instances: list["_FakeAIAgent"] = []
    dispatch_calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.system_prompt = kwargs.get("ephemeral_system_prompt", "")
        self.enabled_toolsets = list(kwargs.get("enabled_toolsets") or [])
        _FakeAIAgent.instances.append(self)

    def run_conversation(self, text: str, *, session_id: str | None = None) -> _FakeResult:
        return _FakeResult(text=f"sp_len={len(self.system_prompt)}; echo={text}")

    def dispatch_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        _FakeAIAgent.dispatch_calls.append((tool_name, args))
        return f"dispatched:{tool_name}"


def _install_fake_internals(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    mod = types.ModuleType("vyasa_internals")
    mod.AIAgent = _FakeAIAgent  # type: ignore[attr-defined]
    mod.get_tool_definitions = lambda: [  # type: ignore[attr-defined]
        {"name": "run_bash"},
        {"name": "web_fetch"},
        {"name": "file_read"},
    ]
    mod.set_session_context = lambda **kwargs: None  # type: ignore[attr-defined]
    mod.run_conversation = lambda *a, **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vyasa_internals", mod)
    _FakeAIAgent.instances.clear()
    _FakeAIAgent.dispatch_calls.clear()
    return mod


# ---------------------------------------------------------------------------
# Descriptor + matrix helpers
# ---------------------------------------------------------------------------


def _dr_sarabhai_descriptor() -> EmployeeDescriptor:
    return EmployeeDescriptor(
        id="dr-sarabhai",
        display_name="Dr. Sarabhai",
        registry_source="vyasa",
        role_key="orchestrator",
        system_prompt_ref="vyasa:orchestrator",
        allowed_tools=["web_fetch", "file_read"],
        memory_namespace="dr-sarabhai",
        model_preference=ModelPreference(
            default="anthropic/claude-opus-4.6",
            provider="anthropic",
        ),
    )


def _matrix_for(employee_id: str, *, allow: set[Capability], deny: set[Capability]) -> CapabilityMatrix:
    row: dict[Capability, CapabilityCell] = {}
    for cap in allow:
        row[cap] = CapabilityCell(decision=Decision.ALLOW, rationale="test-allow")
    for cap in deny:
        row[cap] = CapabilityCell(decision=Decision.DENY, rationale="test-deny")
    return CapabilityMatrix(cells={employee_id: row})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_bridge_prefixes_dr_sarabhai(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_internals(monkeypatch)
    from vyasa_agent.fleet.bridge import AgentRuntimeBridge  # noqa: WPS433

    descriptor = _dr_sarabhai_descriptor()
    matrix = _matrix_for(
        descriptor.id,
        allow={Capability.WEB_FETCH, Capability.FS_READ},
        deny={Capability.BASH},
    )

    bridge = AgentRuntimeBridge(
        descriptor, FleetConfig(), matrix=matrix,
        graph_client=None, audit_sink=None, state_root=tmp_path,
    )
    try:
        turn = Turn(text="hello fleet", employee_id=descriptor.id)
        result = await bridge.turn(turn)

        assert result.employee_id == descriptor.id
        assert result.trace_id == turn.trace_id
        assert result.text.startswith("Dr. Sarabhai:")
        assert "echo=hello fleet" in result.text

        assert len(_FakeAIAgent.instances) == 1
        agent = _FakeAIAgent.instances[0]
        # Layer A filter: declared allowlist intersected with matrix + registry.
        assert set(agent.enabled_toolsets) == {"web_fetch", "file_read"}
        # Session DB sits under the supplied state root (per design-02 §3).
        assert Path(agent.kwargs["session_db"]).is_relative_to(tmp_path)
    finally:
        await bridge.close()


async def test_pre_tool_call_blocks_denied_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_internals(monkeypatch)
    from vyasa_agent.fleet.bridge import AgentRuntimeBridge  # noqa: WPS433

    descriptor = _dr_sarabhai_descriptor()
    matrix = _matrix_for(
        descriptor.id,
        allow={Capability.WEB_FETCH, Capability.FS_READ},
        deny={Capability.BASH},
    )

    bridge = AgentRuntimeBridge(
        descriptor, FleetConfig(), matrix=matrix, state_root=tmp_path,
    )
    try:
        # Build the agent and then try a denied dispatch through the hook seam.
        await bridge.ensure_agent()
        with pytest.raises(CapabilityError) as exc:
            await bridge.invoke_tool("run_bash", {"cmd": "rm -rf /"}, trace_id="t1")

        assert exc.value.capability is Capability.BASH
        assert exc.value.decision is Decision.DENY
        # Crucially: the hook fired *before* the fake dispatcher ran.
        assert _FakeAIAgent.dispatch_calls == []

        # Allowed dispatches pass through the hook and reach the dispatcher.
        outcome = await bridge.invoke_tool("web_fetch", {"url": "https://x"}, trace_id="t2")
        assert outcome == "dispatched:web_fetch"
        assert _FakeAIAgent.dispatch_calls == [("web_fetch", {"url": "https://x"})]
    finally:
        await bridge.close()


async def test_stub_bridge_env_skips_real_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``VYASA_STUB_BRIDGE=1`` bypasses the bridge so no vendored import fires."""
    monkeypatch.setenv("VYASA_STUB_BRIDGE", "1")
    # Sanity: the fake is NOT installed; if the bridge were invoked the
    # import would explode.  The stub path returns immediately instead.
    monkeypatch.delitem(sys.modules, "vyasa_internals", raising=False)

    from vyasa_agent.fleet.actor import EmployeeActor

    actor = EmployeeActor(
        _dr_sarabhai_descriptor(), FleetConfig(), state_root=tmp_path,
    )
    await actor.start()
    try:
        result = await actor._execute_turn(
            Turn(text="ping", employee_id="dr-sarabhai")
        )
        assert result.text.endswith("gnip")
        assert result.error is None
    finally:
        actor._state = "stopped"  # quiet teardown; start()/run() not wired

"""FleetManager + EmployeeActor runtime-skeleton tests.

Boots a two-employee test fleet using :meth:`FleetManager.register_actor` so
the test does not depend on ``~/.vyasa`` YAML layout (that path is covered by
Prometheus-A's descriptor suite).  Submits a turn to each actor, asserts the
stub ``_execute_turn`` returns the expected shape, confirms graceful shutdown.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vyasa_agent.fleet.actor import EmployeeActor
from vyasa_agent.fleet.descriptor import EmployeeDescriptor, FleetConfig, ModelPreference
from vyasa_agent.fleet.manager import FleetManager
from vyasa_agent.fleet.types import Turn


@pytest.fixture(autouse=True)
def _stub_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these runtime-skeleton tests on the offline stub path.

    The real :class:`AgentRuntimeBridge` vendored hook is covered by
    ``tests/test_bridge.py``; here we only care about the actor lifecycle.
    """
    monkeypatch.setenv("VYASA_STUB_BRIDGE", "1")


def _mock_descriptor(employee_id: str, display_name: str) -> EmployeeDescriptor:
    return EmployeeDescriptor(
        id=employee_id,
        display_name=display_name,
        registry_source="vyasa",
        role_key=employee_id,
        system_prompt_ref=f"vyasa:{employee_id}",
        allowed_tools=["bash"],
        memory_namespace=employee_id,
        model_preference=ModelPreference(
            default="anthropic/claude-opus-4.6",
            provider="anthropic",
        ),
    )


def _mock_fleet_config() -> FleetConfig:
    return FleetConfig(fleet_name="vyasa-fleet-test")


@pytest.fixture
def fleet_config() -> FleetConfig:
    return _mock_fleet_config()


@pytest.fixture
def descriptors() -> list[EmployeeDescriptor]:
    return [
        _mock_descriptor("prometheus", "Prometheus"),
        _mock_descriptor("sarabhai", "Dr. Vikram Sarabhai"),
    ]


async def test_fleet_boot_dispatch_shutdown(
    tmp_path: Path,
    fleet_config: FleetConfig,
    descriptors: list[EmployeeDescriptor],
) -> None:
    state_root = tmp_path / "state"
    manager = FleetManager()
    try:
        for descriptor in descriptors:
            actor = EmployeeActor(descriptor, fleet_config, state_root=state_root)
            await manager.register_actor(actor)

        assert sorted(manager.employee_ids) == ["prometheus", "sarabhai"]
        assert all(h.state == "ready" for h in manager.directory())

        for descriptor in descriptors:
            turn = Turn(text="ping", employee_id=descriptor.id)
            result = await manager.dispatch(descriptor.id, turn)

            assert result.employee_id == descriptor.id
            assert descriptor.display_name in result.text
            assert result.text.endswith("gnip")  # "ping" reversed by stub
            assert result.trace_id == turn.trace_id
            assert result.error is None
            assert result.confidence_score == 1.0

        directory = manager.directory()
        assert {h.employee_id: h.turns_handled for h in directory} == {
            "prometheus": 1,
            "sarabhai": 1,
        }
        assert all(h.error_count == 0 for h in directory)

        for employee_id in manager.employee_ids:
            db_path = state_root / employee_id / "state.db"
            assert db_path.exists() is False  # not yet; stop() creates it
    finally:
        await manager.shutdown()

    for descriptor in descriptors:
        assert (state_root / descriptor.id / "state.db").exists()
    assert manager.employee_ids == []


async def test_fleet_handoff_routes_between_actors(
    tmp_path: Path,
    fleet_config: FleetConfig,
    descriptors: list[EmployeeDescriptor],
) -> None:
    manager = FleetManager()
    try:
        for descriptor in descriptors:
            actor = EmployeeActor(descriptor, fleet_config, state_root=tmp_path)
            await manager.register_actor(actor)

        result = await manager.handoff(
            from_id="prometheus",
            to_id="sarabhai",
            payload={"text": "plan the release"},
        )
        assert result.employee_id == "sarabhai"
        assert "Sarabhai" in result.text
    finally:
        await manager.shutdown()


async def test_actor_rejects_submit_when_draining(
    tmp_path: Path,
    fleet_config: FleetConfig,
) -> None:
    actor = EmployeeActor(
        _mock_descriptor("prometheus", "Prometheus"),
        fleet_config,
        state_root=tmp_path,
    )
    await actor.start()
    loop = asyncio.get_running_loop()
    task = loop.create_task(actor.run())
    actor.attach_run_task(task)

    # Kick the state into draining without fully stopping.
    actor._drain_requested = True  # type: ignore[attr-defined]
    actor._state = "draining"  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match="draining"):
        await actor.submit(Turn(text="late", employee_id="prometheus"))

    await actor.stop(drain=False)

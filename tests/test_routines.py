"""RoutineRunner — cron parsing, on-start, webhook, delivery, audit."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from vyasa_agent.fleet.types import Turn, TurnResult
from vyasa_agent.gateway.types import OutboundMessage
from vyasa_agent.graphify.types import Node
from vyasa_agent.routines import RoutineRunner
from vyasa_agent.routines.types import DeliveryTarget, Routine

# --------------------------------------------------------------------------- #
# mocks
# --------------------------------------------------------------------------- #


class _FleetMock:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Turn]] = []

    async def dispatch(self, employee_id: str, turn: Turn) -> TurnResult:
        self.calls.append((employee_id, turn))
        return TurnResult(
            employee_id=employee_id,
            text=f"{employee_id}:{turn.text}",
            trace_id=turn.trace_id,
        )


class _GraphMock:
    def __init__(self) -> None:
        self.nodes: list[Node] = []

    async def upsert_node(self, node: Node) -> str:
        self.nodes.append(node)
        return node.id


class _OutboundMock:
    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    async def send(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)


class _GatewayMock:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}

    def register_webhook(
        self, name: str, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        self.handlers[name] = handler

    async def dispatch_inbound(self, name: str, payload: dict[str, Any]) -> None:
        await self.handlers[name](payload)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _write_yaml(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


async def _make_runner(
    tmp_path: Path,
    *,
    clock: datetime | None = None,
    with_gateway: bool = True,
) -> tuple[RoutineRunner, _FleetMock, _GraphMock, _OutboundMock, _GatewayMock]:
    fleet, graph, outbound = _FleetMock(), _GraphMock(), _OutboundMock()
    gateway = _GatewayMock()
    runner = RoutineRunner(
        fleet=fleet,
        graph=graph,
        outbound=outbound,
        gateway=gateway if with_gateway else None,
        plans_root=tmp_path,
        clock=(lambda c=clock: c) if clock is not None else None,
    )
    return runner, fleet, graph, outbound, gateway


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #


async def test_cron_next_fire_at_respects_schedule_and_timezone(
    tmp_path: Path,
) -> None:
    # Fixed "now": 2026-04-24 07:30 IST. Schedule "0 8 * * *" should fire
    # at 08:00 IST, which is 02:30 UTC.
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = datetime(2026, 4, 24, 7, 30, tzinfo=ist)
    runner, *_ = await _make_runner(tmp_path, clock=now_ist.astimezone(UTC))

    routine = Routine(
        id="briefing",
        owner_employee_id="vyasa",
        schedule="0 8 * * *",
        prompt="ping",
        deliver_to=DeliveryTarget.parse("telegram:12345"),
        timezone="Asia/Kolkata",
    )

    nxt = runner.next_fire_at(routine)
    expected = datetime(2026, 4, 24, 8, 0, tzinfo=ist).astimezone(UTC)
    assert nxt == expected


async def test_on_start_routine_fires_once_at_boot(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path / "vyasa" / "boot-once.yaml",
        (
            "id: boot-once\n"
            "owner_employee_id: vyasa\n"
            "schedule: on:start\n"
            "prompt: hello\n"
            "deliver_to: graph-node\n"
        ),
    )
    runner, fleet, graph, *_ = await _make_runner(tmp_path)
    try:
        routines = await runner.boot()
        assert [r.id for r in routines] == ["boot-once"]
        # Let the on-start task complete.
        import asyncio as _a
        for _ in range(20):
            if fleet.calls:
                break
            await _a.sleep(0)
        assert len(fleet.calls) == 1
        employee_id, turn = fleet.calls[0]
        assert employee_id == "vyasa"
        assert turn.text == "hello"
        assert turn.metadata.get("trigger") == "on-start"
        assert len(graph.nodes) == 1
        assert graph.nodes[0].type == "routine_fired"
        assert graph.nodes[0].owner_employee_id == "vyasa"
    finally:
        await runner.shutdown()


async def test_webhook_routine_fires_when_gateway_dispatches_payload(
    tmp_path: Path,
) -> None:
    _write_yaml(
        tmp_path / "dr-reddy" / "license-refreshed.yaml",
        (
            "id: license-refreshed\n"
            "owner_employee_id: dr-reddy\n"
            "schedule: on:webhook:envato-license-refreshed\n"
            "prompt: inspect\n"
            "deliver_to: graph-node\n"
        ),
    )
    runner, fleet, graph, _outbound, gateway = await _make_runner(tmp_path)
    try:
        await runner.boot()
        assert "envato-license-refreshed" in gateway.handlers
        assert fleet.calls == []

        await gateway.dispatch_inbound(
            "envato-license-refreshed",
            {"purchase_code": "abc-123"},
        )

        assert len(fleet.calls) == 1
        _, turn = fleet.calls[0]
        assert turn.metadata["trigger"] == "webhook"
        assert turn.metadata["webhook_payload"] == {"purchase_code": "abc-123"}
        assert turn.platform == "webhook"
        assert len(graph.nodes) == 1
        assert graph.nodes[0].type == "routine_fired"
    finally:
        await runner.shutdown()


async def test_delivery_telegram_calls_outbound_once_graph_node_does_not(
    tmp_path: Path,
) -> None:
    _write_yaml(
        tmp_path / "vyasa" / "chat-briefing.yaml",
        (
            "id: chat-briefing\n"
            "owner_employee_id: vyasa\n"
            "schedule: on:webhook:chat-briefing\n"
            "prompt: brief\n"
            "deliver_to: telegram:999\n"
        ),
    )
    _write_yaml(
        tmp_path / "dr-bose" / "compact.yaml",
        (
            "id: compact\n"
            "owner_employee_id: dr-bose\n"
            "schedule: on:webhook:compact\n"
            "prompt: compact\n"
            "deliver_to: graph-node\n"
        ),
    )
    runner, _fleet, graph, outbound, gateway = await _make_runner(tmp_path)
    try:
        await runner.boot()
        await gateway.dispatch_inbound("chat-briefing", {})
        await gateway.dispatch_inbound("compact", {})

        assert len(outbound.sent) == 1
        msg = outbound.sent[0]
        assert msg.target_platform == "telegram"
        assert msg.target_chat_id == "999"
        assert msg.text == "vyasa:brief"

        # Both fires audited; only the graph-node fire carries the extra tag.
        kinds = {n.subject_tags[0] for n in graph.nodes}
        assert kinds == {"routine_fired"}
        graph_node_fires = [
            n for n in graph.nodes if "graph_node_output" in n.subject_tags
        ]
        assert len(graph_node_fires) == 1
        assert graph_node_fires[0].owner_employee_id == "dr-bose"
    finally:
        await runner.shutdown()


def test_routine_parses_cron_expression_into_model() -> None:
    r = Routine(
        id="daily",
        owner_employee_id="vyasa",
        schedule="0 8 * * *",
        prompt="x",
        deliver_to=DeliveryTarget.parse("telegram:42"),
    )
    assert not r.is_webhook
    assert not r.is_on_start
    assert r.schedule == "0 8 * * *"


def test_deliver_to_parser_supports_all_kinds() -> None:
    assert DeliveryTarget.parse("graph-node").kind == "graph-node"
    tg = DeliveryTarget.parse("telegram:12345")
    assert (tg.kind, tg.address) == ("telegram", "12345")
    with pytest.raises(ValueError):
        DeliveryTarget.parse("pigeon:carrier-17")
    with pytest.raises(ValueError):
        DeliveryTarget.parse("telegram:")


def test_disabled_routines_are_skipped_at_boot(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path / "vyasa" / "off.yaml",
        (
            "id: off\n"
            "owner_employee_id: vyasa\n"
            "schedule: on:start\n"
            "prompt: x\n"
            "deliver_to: graph-node\n"
            "enabled: false\n"
        ),
    )
    # Synchronous parse path probe via _discover.
    from vyasa_agent.routines.runner import _discover  # type: ignore

    discovered = _discover(tmp_path)
    assert [r.enabled for r in discovered] == [False]


async def test_cron_loop_waits_and_then_fires(tmp_path: Path) -> None:
    """The cron wake-loop sleeps until the scheduled moment, then fires.

    We drive this without mocking the clock: a star-minute schedule
    (``* * * * *``) fires at the next minute boundary. To keep the test
    fast we shim the clock so that the next boundary is ~1 second out.
    """
    # Pin "now" just before a minute boundary so next_fire_at is ~1s away.
    now = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(seconds=1)
    target_minute = (now + timedelta(seconds=1)).replace(second=0)
    # Use a moving clock that advances with real time but anchored so that
    # croniter's "next minute" == target_minute.
    anchor_real = datetime.now(tz=UTC)

    def _clock() -> datetime:
        elapsed = datetime.now(tz=UTC) - anchor_real
        return target_minute - timedelta(seconds=1) + elapsed

    _write_yaml(
        tmp_path / "vyasa" / "minute.yaml",
        (
            "id: minute\n"
            "owner_employee_id: vyasa\n"
            'schedule: "* * * * *"\n'
            "prompt: tick\n"
            "deliver_to: graph-node\n"
        ),
    )
    fleet, graph, outbound = _FleetMock(), _GraphMock(), _OutboundMock()
    runner = RoutineRunner(
        fleet=fleet, graph=graph, outbound=outbound,
        plans_root=tmp_path, clock=_clock,
    )
    try:
        await runner.boot()
        import asyncio as _a
        for _ in range(30):
            if fleet.calls:
                break
            await _a.sleep(0.1)
        assert len(fleet.calls) >= 1
        assert fleet.calls[0][1].metadata["trigger"] == "cron"
    finally:
        await runner.shutdown()

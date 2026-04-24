"""Unit tests for the gateway message router."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vyasa_agent.gateway.router import (
    CapabilityMismatchError,
    EmployeeDescriptor,
    MessageRouter,
    StickyBindingStore,
    UnknownEmployeeError,
)
from vyasa_agent.gateway.types import HandoffRequest, InboundMessage


class FakeFleet:
    """Minimal FleetManager stand-in for tests."""

    def __init__(self, employees: list[EmployeeDescriptor], dead: set[str] | None = None) -> None:
        self._employees = list(employees)
        self._dead = dead or set()

    def directory(self) -> list[EmployeeDescriptor]:
        return list(self._employees)

    def is_alive(self, employee_id: str) -> bool:
        return employee_id not in self._dead

    def kill(self, employee_id: str) -> None:
        self._dead.add(employee_id)


def _fleet() -> FakeFleet:
    return FakeFleet([
        EmployeeDescriptor(
            id="vyasa",
            display_name="Vyasa",
            aliases=("vyasa-01", "orchestrator"),
            capabilities=("route", "orchestrate", "graphify"),
        ),
        EmployeeDescriptor(
            id="dr-sarabhai",
            display_name="Dr. Vikram Sarabhai",
            aliases=("sarabhai", "promo"),
            capabilities=("route", "product", "launch"),
        ),
        EmployeeDescriptor(
            id="dr-reddy",
            display_name="Dr. Meera Reddy",
            aliases=("reddy", "security"),
            capabilities=("pentest", "audit"),
        ),
        EmployeeDescriptor(
            id="dr-bose",
            display_name="Dr. Siddhant Bose",
            aliases=("bose",),
            capabilities=("graphify",),
        ),
        EmployeeDescriptor(
            id="prometheus",
            display_name="Prometheus",
            aliases=("prom",),
            capabilities=("build", "refactor"),
        ),
    ])


def _inbound(text: str, user: str = "user-42", platform: str = "telegram") -> InboundMessage:
    return InboundMessage(
        platform=platform,  # type: ignore[arg-type]
        platform_user_id=user,
        platform_chat_id=f"chat-{user}",
        text=text,
        trace_id="trc_test",
        received_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_slash_ask_resolves_alias():
    router = MessageRouter(_fleet())
    target = await router.route(_inbound("/ask reddy please pentest this binary"))
    assert target == "dr-reddy"


@pytest.mark.asyncio
async def test_slash_ask_with_at_prefix():
    router = MessageRouter(_fleet())
    target = await router.route(_inbound("/ask @sarabhai launch the listing"))
    assert target == "dr-sarabhai"


@pytest.mark.asyncio
async def test_mention_resolves_alias():
    router = MessageRouter(_fleet())
    target = await router.route(_inbound("hey @bose, graphify this doc please"))
    assert target == "dr-bose"


@pytest.mark.asyncio
async def test_mention_case_and_separator_insensitive():
    router = MessageRouter(_fleet())
    target = await router.route(_inbound("ping @Dr.Reddy please"))
    assert target == "dr-reddy"


@pytest.mark.asyncio
async def test_sticky_binding_reused_for_followup():
    router = MessageRouter(_fleet())
    first = _inbound("@reddy scan the host")
    target1 = await router.route(first)
    await router.record_dispatch(first, target1)

    followup = _inbound("and also scan port 8080")  # no mention
    target2 = await router.route(followup)
    assert target1 == target2 == "dr-reddy"


@pytest.mark.asyncio
async def test_default_routes_technical_to_vyasa():
    router = MessageRouter(_fleet())
    target = await router.route(_inbound("can you build and deploy this service"))
    assert target == "vyasa"


@pytest.mark.asyncio
async def test_default_routes_product_to_sarabhai():
    router = MessageRouter(_fleet())
    target = await router.route(_inbound("write the envato listing copy for launch"))
    assert target == "dr-sarabhai"


@pytest.mark.asyncio
async def test_unknown_alias_falls_back_to_orchestrator():
    router = MessageRouter(_fleet())
    target = await router.route(_inbound("/ask ghostemployee do something"))
    assert target == "vyasa"  # generic text, no product keywords


@pytest.mark.asyncio
async def test_unknown_mention_falls_back():
    router = MessageRouter(_fleet())
    target = await router.route(_inbound("@nobody please fix launch copy"))
    # mention fails; no sticky; falls back to product heuristic (launch/copy)
    assert target == "dr-sarabhai"


@pytest.mark.asyncio
async def test_dead_employee_skipped_in_sticky_binding():
    fleet = _fleet()
    router = MessageRouter(fleet)
    first = _inbound("@reddy run a pentest")
    target1 = await router.route(first)
    await router.record_dispatch(first, target1)
    fleet.kill("dr-reddy")

    followup = _inbound("more please")
    target2 = await router.route(followup)
    assert target2 != "dr-reddy"
    assert target2 == "vyasa"


@pytest.mark.asyncio
async def test_handoff_to_unknown_employee_raises():
    router = MessageRouter(_fleet())
    req = HandoffRequest(
        from_employee_id="vyasa",
        to="ghost",  # type: ignore[call-arg]
        intent="graphify.compress",
    )
    with pytest.raises(UnknownEmployeeError):
        await router.resolve_handoff(req)


@pytest.mark.asyncio
async def test_handoff_capability_mismatch_raises():
    router = MessageRouter(_fleet())
    req = HandoffRequest(
        from_employee_id="vyasa",
        to="dr-reddy",  # type: ignore[call-arg]
        intent="graphify.compress",  # reddy has no 'graphify' capability
    )
    with pytest.raises(CapabilityMismatchError):
        await router.resolve_handoff(req)


@pytest.mark.asyncio
async def test_handoff_capability_match_returns_id():
    router = MessageRouter(_fleet())
    req = HandoffRequest(
        from_employee_id="vyasa",
        to="dr-bose",  # type: ignore[call-arg]
        intent="graphify.compress",
    )
    assert await router.resolve_handoff(req) == "dr-bose"


@pytest.mark.asyncio
async def test_sticky_binding_ttl_expires():
    store = StickyBindingStore(ttl_seconds=0.05)
    key = ("telegram", "u1")
    await store.set(key, "vyasa")
    assert await store.get(key) == "vyasa"

    import asyncio
    await asyncio.sleep(0.08)
    assert await store.get(key) is None


@pytest.mark.asyncio
async def test_alias_rebuild_after_fleet_mutation():
    fleet = _fleet()
    router = MessageRouter(fleet)
    # initially resolves
    assert await router.route(_inbound("@reddy hello")) == "dr-reddy"

    # mutate: disable reddy
    fleet._employees = [
        EmployeeDescriptor(id=e.id, display_name=e.display_name, aliases=e.aliases,
                           capabilities=e.capabilities, enabled=(e.id != "dr-reddy"))
        for e in fleet._employees
    ]
    await router.rebuild_aliases()

    # mention now falls through to default
    target = await router.route(_inbound("@reddy hello"))
    assert target != "dr-reddy"

"""Gateway message router.

Decision tree (per design-01 §5, design-06 §1):
  1. ``/ask <alias> ...`` (optionally ``/ask @<alias> ...``).
  2. ``@<alias>`` anywhere in the text.
  3. Sticky binding ``(platform, user_id) → last_employee`` (10-min TTL).
  4. Orchestrator fallback — keyword heuristic picks ``vyasa`` (technical) or
     ``dr-sarabhai`` (product/business). Caller writes the binding after a
     successful dispatch so follow-ups stay with the same employee.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .types import HandoffRequest, InboundMessage

DEFAULT_TECHNICAL_ORCHESTRATOR = "vyasa"
DEFAULT_PRODUCT_ORCHESTRATOR = "dr-sarabhai"

_TECH_KEYWORDS = frozenset(
    {"build", "refactor", "debug", "deploy", "pentest", "test", "lint", "trace", "migrate", "rollback"}
)
_PRODUCT_KEYWORDS = frozenset(
    {"listing", "launch", "copy", "envato", "customer", "pricing", "landing", "growth", "market", "positioning"}
)

_ASK_RE = re.compile(r"^/ask\s+@?(?P<alias>[A-Za-z][A-Za-z0-9._-]{1,63})\b", re.IGNORECASE)
_MENTION_RE = re.compile(r"(?<![\w@])@(?P<alias>[A-Za-z][A-Za-z0-9._-]{1,63})")


@dataclass(frozen=True)
class EmployeeDescriptor:
    """Directory row — the fleet owns the source of truth."""

    id: str
    display_name: str
    aliases: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    enabled: bool = True


@runtime_checkable
class FleetManager(Protocol):
    """Subset of FleetManager the router needs."""

    def directory(self) -> list[EmployeeDescriptor]: ...
    def is_alive(self, employee_id: str) -> bool: ...


class UnknownEmployeeError(KeyError):
    """Handoff targets an id that isn't in the directory (or is disabled)."""


class CapabilityMismatchError(ValueError):
    """Handoff target lacks the capability implied by ``intent``."""


def _normalise(raw: str) -> str:
    """Case-fold + collapse separators so ``Dr.Reddy`` ≡ ``dr-reddy`` ≡ ``dr_reddy``."""
    lowered = raw.strip().lower()
    for ch in (".", "_", " "):
        lowered = lowered.replace(ch, "-")
    while "--" in lowered:
        lowered = lowered.replace("--", "-")
    return lowered.strip("-")


def _capability_for_intent(intent: str) -> str | None:
    if not intent:
        return None
    head = intent.split(".", 1)[0].strip().lower()
    return head or None


@dataclass
class StickyBindingStore:
    """``(platform, user) → employee_id`` with TTL, asyncio-locked."""

    ttl_seconds: float = 600.0
    _data: dict[tuple[str, str], tuple[str, float]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def get(self, key: tuple[str, str]) -> str | None:
        async with self._lock:
            row = self._data.get(key)
            if row is None:
                return None
            employee_id, expires_at = row
            if expires_at < time.monotonic():
                self._data.pop(key, None)
                return None
            return employee_id

    async def set(self, key: tuple[str, str], employee_id: str) -> None:
        async with self._lock:
            self._data[key] = (employee_id, time.monotonic() + self.ttl_seconds)

    async def clear(self, key: tuple[str, str]) -> None:
        async with self._lock:
            self._data.pop(key, None)


class AliasResolver:
    """Alias → employee_id, built once at boot, rebuilt on fleet mutation."""

    def __init__(self, fleet: FleetManager) -> None:
        self._fleet = fleet
        self._alias_map: dict[str, str] = {}
        self._enabled: set[str] = set()
        self._lock = asyncio.Lock()
        self._rebuild_sync()

    def _rebuild_sync(self) -> None:
        alias_map: dict[str, str] = {}
        enabled: set[str] = set()
        for emp in self._fleet.directory():
            if not emp.enabled:
                continue
            enabled.add(emp.id)
            for raw in (emp.id, emp.display_name, *emp.aliases):
                if raw:
                    alias_map[_normalise(raw)] = emp.id
        self._alias_map, self._enabled = alias_map, enabled

    async def rebuild(self) -> None:
        async with self._lock:
            self._rebuild_sync()

    def resolve(self, alias: str) -> str | None:
        employee_id = self._alias_map.get(_normalise(alias))
        return employee_id if employee_id and employee_id in self._enabled else None

    def is_enabled(self, employee_id: str) -> bool:
        return employee_id in self._enabled


class MessageRouter:
    """Resolves an inbound message to a target employee id."""

    def __init__(
        self,
        fleet: FleetManager,
        *,
        binding_ttl_seconds: float = 600.0,
        technical_orchestrator: str = DEFAULT_TECHNICAL_ORCHESTRATOR,
        product_orchestrator: str = DEFAULT_PRODUCT_ORCHESTRATOR,
    ) -> None:
        self._fleet = fleet
        self._aliases = AliasResolver(fleet)
        self.bindings = StickyBindingStore(ttl_seconds=binding_ttl_seconds)
        self._technical_orchestrator = technical_orchestrator
        self._product_orchestrator = product_orchestrator

    async def rebuild_aliases(self) -> None:
        await self._aliases.rebuild()

    async def route(self, inbound: InboundMessage) -> str:
        text = inbound.text or ""

        ask = _ASK_RE.match(text.lstrip())
        if ask:
            target = self._aliases.resolve(ask.group("alias"))
            if target and self._fleet.is_alive(target):
                return target

        for m in _MENTION_RE.finditer(text):
            target = self._aliases.resolve(m.group("alias"))
            if target and self._fleet.is_alive(target):
                return target

        bound = await self.bindings.get(inbound.binding_key)
        if bound and self._aliases.is_enabled(bound) and self._fleet.is_alive(bound):
            return bound

        return self._pick_orchestrator(text)

    async def record_dispatch(self, inbound: InboundMessage, employee_id: str) -> None:
        """Write the sticky binding after a successful dispatch."""
        await self.bindings.set(inbound.binding_key, employee_id)

    def _pick_orchestrator(self, text: str) -> str:
        lowered = text.lower()
        tech_hit = any(kw in lowered for kw in _TECH_KEYWORDS)
        product_hit = any(kw in lowered for kw in _PRODUCT_KEYWORDS)
        preferred = self._product_orchestrator if (product_hit and not tech_hit) else self._technical_orchestrator
        for candidate in (preferred, self._technical_orchestrator, self._product_orchestrator):
            if self._aliases.is_enabled(candidate) and self._fleet.is_alive(candidate):
                return candidate
        for emp in self._fleet.directory():
            if emp.enabled and self._fleet.is_alive(emp.id):
                return emp.id
        raise RuntimeError("no employees available to route message")

    async def resolve_handoff(self, request: HandoffRequest) -> str:
        """Validate a handoff target: exists, enabled, capability-fit."""
        target_id = request.to_employee_id
        descriptor = next(
            (e for e in self._fleet.directory() if e.id == target_id), None
        )
        if descriptor is None or not descriptor.enabled:
            raise UnknownEmployeeError(f"unknown or disabled employee id: {target_id!r}")
        required = _capability_for_intent(request.intent)
        if required and required not in descriptor.capabilities:
            raise CapabilityMismatchError(
                f"{target_id!r} lacks capability {required!r} for intent {request.intent!r}"
            )
        return descriptor.id


__all__ = ["AliasResolver", "CapabilityMismatchError", "EmployeeDescriptor", "FleetManager",
           "MessageRouter", "StickyBindingStore", "UnknownEmployeeError"]

"""RoutineRunner — cron + webhook task scheduler per employee.

Discovers ``plans/<employee_id>/*.yaml``, registers cron loops (``croniter``
+ ``asyncio.sleep``), ``on:start`` one-shots, or webhook callbacks. Fires
dispatch via :meth:`FleetManager.dispatch`, delivers through ``deliver_to``,
audits a ``routine_fired`` node to Graphify.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import yaml
from croniter import croniter

from vyasa_agent.fleet.types import Turn, TurnResult
from vyasa_agent.gateway.types import OutboundMessage
from vyasa_agent.graphify.types import Node

from .types import DeliveryTarget, Routine, RoutineFire

logger = logging.getLogger("vyasa.routines")
_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


class _FleetLike(Protocol):
    async def dispatch(self, employee_id: str, turn: Turn) -> TurnResult: ...

class _GraphLike(Protocol):
    async def upsert_node(self, node: Node) -> str: ...

class _OutboundLike(Protocol):
    async def send(self, msg: OutboundMessage) -> None: ...

class _GatewayLike(Protocol):
    def register_webhook(
        self, name: str, handler: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None: ...


def _expand_env(v: str) -> str:
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), v)


def _parse(path: Path, fallback_owner: str) -> Routine:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: routine YAML must be a mapping")
    d = raw.get("deliver_to")
    if not isinstance(d, str):
        raise ValueError(f"{path}: deliver_to must be a string")
    return Routine(
        id=raw.get("id") or path.stem,
        owner_employee_id=raw.get("owner_employee_id") or fallback_owner,
        schedule=raw.get("schedule", ""), prompt=raw.get("prompt", ""),
        deliver_to=DeliveryTarget.parse(_expand_env(d)),
        enabled=bool(raw.get("enabled", True)),
        timezone=raw.get("timezone", "UTC"),
        visibility=raw.get("visibility", "private"),
        description=raw.get("description"))


def _discover(root: Path) -> list[Routine]:
    if not root.exists():
        return []
    out: list[Routine] = []
    for emp in sorted(p for p in root.iterdir() if p.is_dir()):
        for y in sorted(emp.glob("*.yaml")):
            try:
                out.append(_parse(y, emp.name))
            except Exception as exc:
                logger.warning("routines.parse_failed",
                               extra={"path": str(y), "error": str(exc)})
    return out


class RoutineRunner:
    """Boot, schedule, fire, deliver, audit."""

    def __init__(
        self, *, fleet: _FleetLike, graph: _GraphLike,
        outbound: _OutboundLike | None = None, gateway: _GatewayLike | None = None,
        plans_root: Path | str = Path("plans"),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._fleet, self._graph = fleet, graph
        self._outbound, self._gateway = outbound, gateway
        self._plans_root = Path(plans_root)
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._routines: dict[str, Routine] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._webhooks: dict[str, Routine] = {}
        self._stop, self._started = asyncio.Event(), False

    async def boot(self) -> list[Routine]:
        if self._started:
            raise RuntimeError("RoutineRunner already booted")
        self._started = True
        for r in _discover(self._plans_root):
            if not r.enabled:
                continue
            self._routines[r.id] = r
            if r.is_webhook:
                self._register_webhook(r)
            else:
                coro = (self._fire(r, trigger="on-start") if r.is_on_start
                        else self._cron_loop(r))
                self._tasks.append(asyncio.create_task(coro, name=f"r-{r.id}"))
        logger.info("routines.booted", extra={"count": len(self._routines)})
        return list(self._routines.values())

    async def shutdown(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False

    def next_fire_at(self, r: Routine, *, after: datetime | None = None) -> datetime:
        base = after or self._clock()
        tz = ZoneInfo(r.timezone) if r.timezone != "UTC" else UTC
        nxt: datetime = croniter(r.schedule, base.astimezone(tz)).get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=tz)
        return nxt.astimezone(UTC)

    async def _cron_loop(self, r: Routine) -> None:
        while not self._stop.is_set():
            try:
                delay = max(0.0, (self.next_fire_at(r) - self._clock()).total_seconds())
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    return
                except TimeoutError:
                    pass
                await self._fire(r, trigger="cron")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("routines.cron_loop_failed", extra={"routine_id": r.id})
                await asyncio.sleep(5.0)

    def _register_webhook(self, r: Routine) -> None:
        name = r.webhook_name or r.id
        self._webhooks[name] = r
        if self._gateway is not None:
            async def _h(payload: dict[str, Any], _r: Routine = r) -> None:
                await self._fire(_r, trigger="webhook", payload=payload)
            self._gateway.register_webhook(name, _h)

    async def trigger_webhook(
        self, name: str, payload: dict[str, Any] | None = None,
    ) -> RoutineFire:
        r = self._webhooks.get(name)
        if r is None:
            raise KeyError(f"no routine bound to webhook {name!r}")
        return await self._fire(r, trigger="webhook", payload=payload or {})

    async def _fire(
        self, r: Routine, *, trigger: str, payload: dict[str, Any] | None = None,
    ) -> RoutineFire:
        # TODO(Dharma HIGH): ``payload`` here comes from an external webhook
        # and is placed verbatim into ``meta["webhook_payload"]``. Downstream
        # prompt templates that interpolate ``metadata`` into the model
        # context are vulnerable to prompt injection via a crafted body.
        # Either sanitise to a whitelisted shape here, or render into the
        # prompt via a structured section the model treats as untrusted.
        meta: dict[str, Any] = {"routine_id": r.id, "trigger": trigger}
        if payload:
            meta["webhook_payload"] = payload
        turn = Turn(text=r.prompt, employee_id=r.owner_employee_id,
                    platform="webhook" if trigger == "webhook" else "cli",
                    user_id=f"routine:{r.id}", metadata=meta)
        ok, error, result = True, None, None
        try:
            result = await self._fleet.dispatch(r.owner_employee_id, turn)
            await self._deliver(r, result)
        except Exception as exc:
            ok, error = False, str(exc)
            logger.exception("routines.fire_failed", extra={"routine_id": r.id})
        fire = RoutineFire(
            routine_id=r.id, owner_employee_id=r.owner_employee_id,
            trigger=trigger, delivery_kind=r.deliver_to.kind,
            trace_id=(result.trace_id if result else turn.trace_id),
            ok=ok, error=error)
        await self._audit(r, fire, result)
        return fire

    async def _deliver(self, r: Routine, result: TurnResult) -> None:
        t = r.deliver_to
        if t.kind == "graph-node":
            return
        if self._outbound is None:
            logger.warning("routines.no_outbound", extra={"routine_id": r.id})
            return
        await self._outbound.send(OutboundMessage(
            target_platform=t.kind, target_chat_id=t.address or "",  # type: ignore[arg-type]
            text=result.text, trace_id=result.trace_id))

    async def _audit(
        self, r: Routine, fire: RoutineFire, result: TurnResult | None,
    ) -> None:
        summary = result.text[:280] if result and result.text else (fire.error or "(empty)")
        tags = ["routine_fired", fire.trigger, r.owner_employee_id]
        if fire.delivery_kind == "graph-node":
            tags.append("graph_node_output")
        try:
            await self._graph.upsert_node(Node(
                id=f"routine-fire-{r.id}-{fire.trace_id}", type="routine_fired",
                summary=summary, owner_employee_id=r.owner_employee_id,
                key_claims=[f"routine={r.id}", f"ok={fire.ok}", f"trigger={fire.trigger}"],
                visibility=r.visibility, subject_tags=tags,
                confidence_score=1.0 if fire.ok else 0.5, updated_by="routine-runner"))
        except Exception:
            logger.exception("routines.audit_failed", extra={"routine_id": r.id})


__all__ = ["RoutineRunner"]

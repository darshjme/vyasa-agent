"""FleetManager — supervises N :class:`EmployeeActor` coroutines.

Boot one-shot reads ``~/.vyasa/`` via :func:`vyasa_agent.fleet.descriptor.load_fleet`,
spawns one actor per descriptor, and exposes dispatch + handoff + directory for the
gateway router.  Single process, in-memory queues only (design-01 §2 ADR-1).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from vyasa_agent.admin_panel.settings_store import SettingsStore
from vyasa_agent.fleet.actor import EmployeeActor
from vyasa_agent.fleet.audit import AuditSink
from vyasa_agent.fleet.capability import CapabilityMatrix
from vyasa_agent.fleet.descriptor import EmployeeDescriptor, FleetConfig, load_fleet
from vyasa_agent.fleet.settings_bridge import SettingsOverlay, apply_overlay
from vyasa_agent.fleet.types import EmployeeHealth, Turn, TurnResult

logger = logging.getLogger("vyasa.fleet.manager")

DEFAULT_SHUTDOWN_TIMEOUT_S: float = 30.0
_HAS_TASKGROUP = sys.version_info >= (3, 11)


class FleetManager:
    """In-process supervisor for the 28-employee fleet."""

    def __init__(
        self,
        *,
        shutdown_timeout_s: float = DEFAULT_SHUTDOWN_TIMEOUT_S,
    ) -> None:
        self._actors: dict[str, EmployeeActor] = {}
        self._run_tasks: dict[str, asyncio.Task[None]] = {}
        self._fleet_config: FleetConfig | None = None
        self._shutdown_timeout_s: float = shutdown_timeout_s
        self._booted: bool = False
        self._overlay: SettingsOverlay | None = None
        self._matrix: CapabilityMatrix | None = None
        self._graph_client: object | None = None
        self._audit_sink: AuditSink | None = None

    # ---------- boot / shutdown ---------------------------------------------

    async def boot(
        self,
        root: Path,
        *,
        settings_store: SettingsStore | None = None,
        matrix: CapabilityMatrix | None = None,
        graph_client: object | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        """Load fleet from ``root`` and start every actor concurrently.

        When ``settings_store`` is supplied the manager wraps it in a
        :class:`SettingsOverlay`, merges admin overrides onto every YAML
        descriptor before spawning the actor, and registers a hot-reload
        callback so future ``POST /v1/admin/settings`` writes flip actor
        state without a restart (constitution §4 row 1).
        """
        if self._booted:
            raise RuntimeError("FleetManager already booted")

        config, descriptors = load_fleet(Path(root))
        self._fleet_config = config

        overlay: SettingsOverlay | None = None
        if settings_store is not None:
            overlay = SettingsOverlay(settings_store)
            descriptors = [apply_overlay(d, overlay) for d in descriptors]
        self._overlay = overlay

        # One matrix + graph_client + audit_sink per fleet, shared across
        # every actor so Layer B/C hooks and audit land in one place
        # (design-04 §2).
        if matrix is None:
            matrix_path = Path(root) / "capabilities.yaml"
            if matrix_path.exists():
                matrix = CapabilityMatrix.load(matrix_path)
        if audit_sink is None:
            audit_sink = AuditSink()
        self._matrix = matrix
        self._graph_client = graph_client
        self._audit_sink = audit_sink

        actors: list[EmployeeActor] = []
        for descriptor in descriptors:
            enabled = overlay.get_employee_enabled(descriptor.id) if overlay else True
            actor = EmployeeActor(
                descriptor,
                config,
                state_root=root / "employees" / "state",
                enabled=enabled,
                matrix=matrix,
                graph=graph_client,
                audit=audit_sink,
            )
            actors.append(actor)

        for actor in actors:
            if actor.id in self._actors:
                raise ValueError(f"duplicate employee id: {actor.id}")
            self._actors[actor.id] = actor

        if actors:
            await asyncio.gather(*(actor.start() for actor in actors))
            loop = asyncio.get_running_loop()
            for actor in actors:
                task = loop.create_task(actor.run(), name=f"employee-{actor.id}")
                actor.attach_run_task(task)
                self._run_tasks[actor.id] = task

        if overlay is not None:
            overlay.watch(self._on_settings_change)

        self._booted = True
        logger.info(
            "fleet.booted",
            extra={"event": "fleet.booted", "count": len(self._actors)},
        )

    async def register_actor(self, actor: EmployeeActor) -> None:
        """Attach a pre-built actor (tests, dynamic spawns).

        Uses :class:`asyncio.TaskGroup` when available (Python 3.11+), falling
        back to ``gather``.  Mirrors the pattern in :meth:`boot` but for a
        single actor.
        """
        if actor.id in self._actors:
            raise ValueError(f"duplicate employee id: {actor.id}")
        self._actors[actor.id] = actor
        await actor.start()
        loop = asyncio.get_running_loop()
        task = loop.create_task(actor.run(), name=f"employee-{actor.id}")
        actor.attach_run_task(task)
        self._run_tasks[actor.id] = task

    async def shutdown(self) -> None:
        """Drain all actors (30 s), then cancel any still running."""
        if not self._actors:
            # Even with zero actors we must flip the boot flag so a
            # subsequent ``boot`` call is accepted — otherwise an empty
            # fleet gets stuck in "already booted" state forever.
            self._booted = False
            return

        async def _drain(actor: EmployeeActor) -> None:
            await actor.stop(drain=True)

        try:
            await asyncio.wait_for(
                asyncio.gather(*(_drain(a) for a in self._actors.values()),
                               return_exceptions=True),
                timeout=self._shutdown_timeout_s,
            )
        except TimeoutError:
            logger.warning("fleet.shutdown.timeout",
                           extra={"event": "fleet.shutdown.timeout",
                                  "timeout_s": self._shutdown_timeout_s})
            await asyncio.gather(
                *(actor.stop(drain=False) for actor in self._actors.values()),
                return_exceptions=True,
            )

        for task in self._run_tasks.values():
            if not task.done():
                task.cancel()
        if self._run_tasks:
            await asyncio.gather(*self._run_tasks.values(), return_exceptions=True)

        self._actors.clear()
        self._run_tasks.clear()
        self._booted = False
        logger.info("fleet.shutdown.complete", extra={"event": "fleet.shutdown.complete"})

    # ---------- dispatch -----------------------------------------------------

    async def dispatch(self, target_id: str, turn: Turn) -> TurnResult:
        """Route ``turn`` to the actor whose id matches ``target_id``."""
        actor = self._get(target_id)
        # Preserve the caller-supplied target so downstream consumers see a
        # consistent employee_id even if the router rewrote it upstream.
        if turn.employee_id != target_id:
            turn = turn.model_copy(update={"employee_id": target_id})
        return await actor.submit(turn)

    async def handoff(
        self, from_id: str, to_id: str, payload: dict[str, object]
    ) -> TurnResult:
        """One actor delegates to another.  ``payload`` must include ``text``."""
        if from_id not in self._actors:
            raise KeyError(f"unknown employee: {from_id}")
        to_actor = self._get(to_id)
        text = payload.get("text")
        if not isinstance(text, str):
            raise ValueError("handoff payload missing string 'text'")
        metadata = {
            "handoff_from": from_id,
            **(payload.get("metadata") or {}),  # type: ignore[arg-type]
        }
        trace_id = payload.get("trace_id")
        turn_kwargs: dict[str, object] = {
            "text": text,
            "employee_id": to_id,
            "metadata": metadata,
        }
        if isinstance(trace_id, str):
            turn_kwargs["trace_id"] = trace_id
        return await to_actor.submit(Turn(**turn_kwargs))

    # ---------- settings bridge ---------------------------------------------

    def attach_overlay(self, overlay: SettingsOverlay) -> None:
        """Register an overlay on a manager booted via :meth:`register_actor`.

        The standard ``boot`` path installs the overlay automatically; this
        helper is for tests and dynamic wiring where actors are attached one
        at a time.  Safe to call idempotently — re-attaching replaces the
        previous callback hook.
        """
        self._overlay = overlay
        overlay.watch(self._on_settings_change)
        # Apply current enabled state to every actor already registered.
        for actor in self._actors.values():
            actor.set_enabled(overlay.get_employee_enabled(actor.id))

    def _on_settings_change(self, key: str, value: object) -> None:
        """Hot-reload hook.

        Only enablement flips are applied to actors; every other tunable is
        consumed lazily via the overlay (no restart required).  Unknown keys
        are ignored — other subscribers (channel adapters, branding) handle
        those.
        """
        if key == "fleet.employees.disabled":
            if not isinstance(value, list):
                return
            disabled = {str(x).strip().lower().replace(".", "-") for x in value}
            for actor in self._actors.values():
                canon = actor.id.lower().replace(".", "-")
                actor.set_enabled(canon not in disabled)
            return

        if key == "fleet.employees.enabled":
            # Allowlist semantics: empty / unset ⇒ allow all.
            if not isinstance(value, list) or not value:
                for actor in self._actors.values():
                    actor.set_enabled(True)
                return
            allowed = {str(x).strip().lower().replace(".", "-") for x in value}
            for actor in self._actors.values():
                canon = actor.id.lower().replace(".", "-")
                actor.set_enabled(canon in allowed)
            return

        if key.startswith("fleet.employee.") and key.endswith(".enabled"):
            remainder = key[len("fleet.employee."):-len(".enabled")]
            canon = remainder.strip().lower().replace(".", "-")
            for actor in self._actors.values():
                if actor.id.lower().replace(".", "-") == canon:
                    actor.set_enabled(bool(value))
                    return

    @property
    def overlay(self) -> SettingsOverlay | None:
        return self._overlay

    # ---------- introspection -----------------------------------------------

    def directory(self) -> list[EmployeeHealth]:
        return [actor.health for actor in self._actors.values()]

    def get(self, employee_id: str) -> EmployeeActor | None:
        return self._actors.get(employee_id)

    @property
    def fleet_config(self) -> FleetConfig | None:
        return self._fleet_config

    @property
    def employee_ids(self) -> list[str]:
        return list(self._actors.keys())

    # ---------- internals ----------------------------------------------------

    def _get(self, employee_id: str) -> EmployeeActor:
        actor = self._actors.get(employee_id)
        if actor is None:
            raise KeyError(f"unknown employee: {employee_id}")
        return actor


__all__ = ["DEFAULT_SHUTDOWN_TIMEOUT_S", "FleetManager"]

# NEXT: Python 3.11+ TaskGroup variant of boot/shutdown (cleaner exception
# aggregation) — guarded by ``_HAS_TASKGROUP`` flag above.  Kept the gather
# fallback for forward-compat when we drop 3.11 support.
_ = EmployeeDescriptor  # re-export stabiliser for type checkers

"""EmployeeActor — one asyncio coroutine per employee turn queue. Runtime skeleton
only; the hermes AIAgent is not instantiated yet (see design-01 §3, design-02 §3,
recon-04 §2)."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vyasa_agent.fleet.descriptor import EmployeeDescriptor, FleetConfig
from vyasa_agent.fleet.types import EmployeeHealth, EmployeeState, Turn, TurnResult

if TYPE_CHECKING:
    from vyasa_agent.fleet.audit import AuditSink
    from vyasa_agent.fleet.bridge import AgentRuntimeBridge
    from vyasa_agent.fleet.capability import CapabilityMatrix

logger = logging.getLogger("vyasa.fleet.actor")

# Runtime knobs.  descriptor.FleetConfig is extra='forbid' so these stay here
# for now; Prometheus-A can promote them when the schema admits new keys.
DEFAULT_STATE_ROOT: Path = Path.home() / ".vyasa" / "employees" / "state"
DEFAULT_QUEUE_MAX_SIZE: int = 64
DEFAULT_PER_TURN_TIMEOUT_S: float = 120.0
DEFAULT_SUBMIT_TIMEOUT_S: float = 150.0


class EmployeeActor:
    """Single-employee asyncio consumer of :class:`Turn` objects.

    One turn at a time (design-01 §6), 120 s watchdog, cancel-safe.
    """

    def __init__(
        self,
        descriptor: EmployeeDescriptor,
        fleet_config: FleetConfig,
        *,
        state_root: Path | None = None,
        queue_max_size: int = DEFAULT_QUEUE_MAX_SIZE,
        per_turn_timeout_s: float = DEFAULT_PER_TURN_TIMEOUT_S,
        submit_timeout_s: float = DEFAULT_SUBMIT_TIMEOUT_S,
        enabled: bool = True,
        matrix: "CapabilityMatrix | None" = None,
        graph: Any | None = None,
        audit: "AuditSink | None" = None,
    ) -> None:
        self.descriptor = descriptor
        self.fleet_config = fleet_config
        self.id: str = descriptor.id
        self.display_name: str = descriptor.display_name
        self._state_root: Path = state_root or DEFAULT_STATE_ROOT
        self._queue_max_size: int = queue_max_size
        self._per_turn_timeout_s: float = per_turn_timeout_s
        self._submit_timeout_s: float = submit_timeout_s
        self._state: EmployeeState = "booting"
        self._state_db_path: Path | None = None
        self._queue: asyncio.Queue[tuple[Turn, asyncio.Future[TurnResult]]] | None = None
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(1)
        self._run_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._drain_requested: bool = False
        self._turns_handled: int = 0
        self._error_count: int = 0
        self._last_activity_at: datetime | None = None
        # Admin-overlay toggle.  When ``False`` the actor answers politely
        # with an "unavailable" :class:`TurnResult` instead of invoking the
        # downstream agent.  Set via :meth:`set_enabled` from the settings
        # bridge hot-reload path (design-08 §7).
        self._enabled: bool = enabled
        # Runtime dependencies shared across the fleet.  FleetManager.boot
        # constructs one matrix + graph_client + audit_sink and passes them
        # to every actor (design-04 §2).
        self.matrix = matrix
        self.graph = graph
        self.audit = audit
        self._bridge: "AgentRuntimeBridge | None" = None

    # ---------- lifecycle ----------------------------------------------------

    async def start(self) -> None:
        employee_dir = self._state_root / self.id
        (employee_dir / "logs").mkdir(parents=True, exist_ok=True)
        self._state_db_path = employee_dir / "state.db"
        self._queue = asyncio.Queue(maxsize=self._queue_max_size)
        self._state = "ready"
        self._log("info", "actor.started", db=str(self._state_db_path))

    async def submit(self, turn: Turn) -> TurnResult:
        if self._queue is None or self._state in {"booting", "stopped"}:
            raise RuntimeError(f"actor {self.id!r} not started (state={self._state})")
        if self._state == "draining":
            raise RuntimeError(f"actor {self.id!r} draining; rejecting new work")
        future: asyncio.Future[TurnResult] = asyncio.get_running_loop().create_future()
        try:
            self._queue.put_nowait((turn, future))
        except asyncio.QueueFull as exc:
            raise RuntimeError(
                f"actor {self.id!r} queue full (depth={self._queue.qsize()})"
            ) from exc
        try:
            return await asyncio.wait_for(future, timeout=self._submit_timeout_s)
        except TimeoutError:
            if not future.done():
                future.cancel()
            raise

    async def run(self) -> None:
        if self._queue is None:
            raise RuntimeError(f"actor {self.id!r} run() before start()")
        self._log("info", "actor.run.enter")
        try:
            while not self._stop_event.is_set():
                item = await self._next_item()
                if item is None:
                    break
                await self._handle_item(*item)
        finally:
            self._state = "stopped"
            self._log("info", "actor.run.exit", turns_handled=self._turns_handled)

    async def stop(self, drain: bool = True) -> None:
        self._drain_requested = drain
        self._state = "draining" if drain else "stopped"
        self._stop_event.set()
        if self._run_task is not None and not self._run_task.done():
            if not drain:
                self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
        await self._persist_session()
        self._state = "stopped"
        self._log("info", "actor.stopped", drain=drain)

    # ---------- introspection ------------------------------------------------

    @property
    def health(self) -> EmployeeHealth:
        return EmployeeHealth(
            employee_id=self.id,
            display_name=self.display_name,
            state=self._state,
            last_activity_at=self._last_activity_at,
            turns_handled=self._turns_handled,
            error_count=self._error_count,
            queue_depth=self._queue.qsize() if self._queue is not None else 0,
        )

    def attach_run_task(self, task: asyncio.Task[None]) -> None:
        self._run_task = task

    # ---------- admin-overlay toggles ---------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable this actor at runtime.

        Disabled actors answer every turn with a polite unavailable result;
        the run loop, queue, and state-db stay intact so re-enabling resumes
        normal operation on the next submit without a restart.
        """
        previous = self._enabled
        self._enabled = bool(enabled)
        if previous != self._enabled:
            self._log(
                "info",
                "actor.enabled.changed",
                enabled=self._enabled,
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---------- internals ----------------------------------------------------

    async def _next_item(self) -> tuple[Turn, asyncio.Future[TurnResult]] | None:
        assert self._queue is not None
        get_task = asyncio.ensure_future(self._queue.get())
        stop_task = asyncio.ensure_future(self._stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                {get_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (get_task, stop_task):
                if not t.done():
                    t.cancel()
        if get_task in done:
            return get_task.result()
        if self._drain_requested and self._queue.qsize() > 0:
            return await self._queue.get()
        return None

    async def _handle_item(
        self, turn: Turn, future: asyncio.Future[TurnResult]
    ) -> None:
        async with self._semaphore:
            self._state = "busy"
            try:
                result = await asyncio.wait_for(
                    self._execute_turn(turn), timeout=self._per_turn_timeout_s
                )
                self._turns_handled += 1
                self._resolve(future, result)
            except TimeoutError:
                self._error_count += 1
                self._log("warning", "actor.turn.timeout", trace_id=turn.trace_id)
                self._resolve(future, self._error_result(
                    turn, "timeout",
                    f"turn timed out after {self._per_turn_timeout_s}s",
                ))
            except Exception as exc:
                self._error_count += 1
                self._log("exception", "actor.turn.error",
                          trace_id=turn.trace_id, error=repr(exc))
                self._resolve(future, self._error_result(turn, repr(exc), "internal error"))
            finally:
                self._last_activity_at = datetime.now(tz=UTC)
                self._state = "draining" if self._drain_requested else "ready"

    def _resolve(self, future: asyncio.Future[TurnResult], result: TurnResult) -> None:
        if not future.done():
            future.set_result(result)

    def _error_result(self, turn: Turn, error: str, text: str) -> TurnResult:
        return TurnResult(
            employee_id=self.id,
            text=f"[{self.display_name}] {text}",
            trace_id=turn.trace_id,
            tool_calls=[],
            confidence_score=0.0,
            error=error,
        )

    async def _execute_turn(self, turn: Turn) -> TurnResult:
        # Admin disabled this employee — return a polite unavailable result
        # without invoking the downstream agent.  No side-effects, no tool
        # calls, no budget spend.
        if not self._enabled:
            return TurnResult(
                employee_id=self.id,
                text=(
                    f"[{self.display_name}] is temporarily unavailable. "
                    f"Please try another partner or check back shortly."
                ),
                trace_id=turn.trace_id,
                tool_calls=[],
                confidence_score=0.0,
                error="employee_disabled",
            )

        # Test / offline path — stubbed reverse-echo keeps the actor contract
        # intact when ``VYASA_STUB_BRIDGE=1`` disables real inference.
        if os.environ.get("VYASA_STUB_BRIDGE", "") == "1":
            return TurnResult(
                employee_id=self.id,
                text=f"[{self.display_name}] {turn.text[::-1]}",
                trace_id=turn.trace_id,
                tool_calls=[],
                confidence_score=1.0,
            )

        if self._bridge is None:
            from vyasa_agent.fleet.bridge import AgentRuntimeBridge
            self._bridge = AgentRuntimeBridge(
                self.descriptor, self.fleet_config, self.matrix,
                self.graph, self.audit,
            )
        return await self._bridge.turn(turn)

    async def _persist_session(self) -> None:
        # Flush the bridge (SQLite + JSONL dual-sink per recon-04 §8) before
        # the run loop exits.  Bridge is lazy so it may be unset in the
        # stub / never-submitted path — still touch the state file so the
        # on-disk layout is stable.
        if self._bridge is not None:
            try:
                await self._bridge.close()
            except Exception:
                logger.exception("actor.persist.error",
                                 extra={"employee_id": self.id})
        if self._state_db_path is not None:
            self._state_db_path.touch(exist_ok=True)

    def _log(self, level: str, event: str, **fields: object) -> None:
        extra = {"employee_id": self.id, "event": event, **fields}
        getattr(logger, "error" if level == "exception" else level)(
            event, extra=extra, exc_info=(level == "exception")
        )

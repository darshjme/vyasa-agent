# SPDX-License-Identifier: Apache-2.0
"""Fleet runtime Pydantic models.

These are the wire / in-process types exchanged between the gateway, the
:class:`FleetManager`, and each :class:`EmployeeActor`.  Keep this module
dependency-light: no hermes imports, no I/O, no logging config.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

EmployeeState = Literal["booting", "ready", "busy", "draining", "stopped", "errored"]


def _new_trace_id() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Turn(BaseModel):
    """One inbound user request targeted at a single employee."""

    model_config = ConfigDict(extra="forbid")

    text: str
    employee_id: str
    trace_id: str = Field(default_factory=_new_trace_id)
    platform: str = "cli"
    user_id: str = "local"
    metadata: dict[str, Any] = Field(default_factory=dict)
    submitted_at: datetime = Field(default_factory=_utcnow)


class TurnResult(BaseModel):
    """Result returned by an :class:`EmployeeActor` after executing a turn."""

    model_config = ConfigDict(extra="forbid")

    employee_id: str
    text: str
    trace_id: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    confidence_score: float = 1.0
    error: str | None = None
    finished_at: datetime = Field(default_factory=_utcnow)


class EmployeeHealth(BaseModel):
    """Snapshot of a single actor's health for the directory endpoint."""

    model_config = ConfigDict(extra="forbid")

    employee_id: str
    display_name: str
    state: EmployeeState
    last_activity_at: datetime | None = None
    turns_handled: int = 0
    error_count: int = 0
    queue_depth: int = 0


class HandoffRequest(BaseModel):
    """One employee asking the manager to delegate to another."""

    model_config = ConfigDict(extra="forbid")

    from_id: str
    to_id: str
    payload: dict[str, Any]
    trace_id: str = Field(default_factory=_new_trace_id)

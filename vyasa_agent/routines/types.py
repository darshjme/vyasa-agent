# SPDX-License-Identifier: Apache-2.0
"""Pydantic models for the routines layer.

A ``Routine`` is the YAML-declared instruction — owner, schedule, prompt,
delivery target.  A ``RoutineFire`` is the audit row written to Graphify when
the routine actually executes.  A ``DeliveryTarget`` is the parsed form of
the YAML ``deliver_to`` string (``telegram:<chat_id>``, ``graph-node``,
``slack:<channel>``, ``email:<addr>``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DeliveryKind = Literal["telegram", "graph-node", "slack", "email"]


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class DeliveryTarget(BaseModel):
    """Parsed ``deliver_to`` entry.

    The raw YAML is a single string so operators can write
    ``telegram:${VYASA_OWNER_CHAT_ID}`` without hand-crafting a mapping.
    """

    model_config = ConfigDict(extra="forbid")

    kind: DeliveryKind
    address: str | None = Field(
        default=None,
        description="Kind-specific locator. None for graph-node (write-only).",
    )

    @classmethod
    def parse(cls, raw: str) -> DeliveryTarget:
        head, _, tail = raw.partition(":")
        head = head.strip().lower()
        address = tail.strip() or None
        if head == "graph-node":
            return cls(kind="graph-node", address=None)
        if head not in ("telegram", "slack", "email"):
            raise ValueError(f"unsupported deliver_to kind: {head!r}")
        if not address:
            raise ValueError(f"deliver_to {head!r} requires an address after ':'")
        return cls(kind=head, address=address)  # type: ignore[arg-type]


class Routine(BaseModel):
    """One YAML routine file under ``plans/<employee_id>/<name>.yaml``."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    owner_employee_id: str = Field(..., min_length=1)
    schedule: str = Field(
        ...,
        description="Cron expression, ``on:webhook:<name>``, or ``on:start``.",
    )
    prompt: str = Field(..., min_length=1)
    deliver_to: DeliveryTarget
    enabled: bool = True
    timezone: str = "UTC"
    visibility: Literal["private", "team", "fleet"] = "private"
    description: str | None = None

    @field_validator("schedule")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("schedule must not be empty")
        return v

    @property
    def is_webhook(self) -> bool:
        return self.schedule.startswith("on:webhook:")

    @property
    def is_on_start(self) -> bool:
        return self.schedule.strip().lower() == "on:start"

    @property
    def webhook_name(self) -> str | None:
        if not self.is_webhook:
            return None
        return self.schedule.split(":", 2)[2].strip() or None


class RoutineFire(BaseModel):
    """Audit record emitted on every routine fire."""

    model_config = ConfigDict(extra="forbid")

    routine_id: str
    owner_employee_id: str
    fired_at: str = Field(default_factory=_utcnow_iso)
    trigger: Literal["cron", "webhook", "on-start", "manual"]
    delivery_kind: DeliveryKind
    trace_id: str
    ok: bool
    error: str | None = None


__all__ = ["DeliveryKind", "DeliveryTarget", "Routine", "RoutineFire"]

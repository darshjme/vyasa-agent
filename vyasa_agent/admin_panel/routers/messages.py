"""Inbound message, dispatch, and handoff endpoints (design-06 §2 rows 1–3).

Gateway-bearer protected. Handoffs enforce design-06 §4: when the downstream
score falls below the requested threshold, response is ``rejected`` and no
side effect is persisted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..deps import get_fleet_manager, require_gateway

router = APIRouter()


class InboundMessageBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    adapter: str = Field(..., min_length=1)
    adapter_msg_id: str = Field(..., min_length=1)
    sender: str = Field(..., min_length=1)
    channel: str = Field(..., min_length=1)
    text: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    received_at: datetime | None = None


class DispatchBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    intent: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    context_node_ids: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class HandoffBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    from_employee_id: str = Field(..., min_length=1)
    to_employee_id: str = Field(..., min_length=1)
    intent: str = Field(..., min_length=1)
    context_node_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_output: str | None = None
    confidence_threshold: float = Field(0.80, ge=0.0, le=1.0)
    deadline_ms: int = Field(30_000, ge=1, le=600_000)
    idempotency_key: str | None = None


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _trace(request: Request) -> str:
    return getattr(request.state, "trace_id", None) or uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@router.post("/v1/messages", status_code=status.HTTP_202_ACCEPTED)
async def inbound_message(
    body: InboundMessageBody,
    request: Request,
    _auth: dict[str, str] = Depends(require_gateway),
    fleet: Any = Depends(get_fleet_manager),
) -> dict[str, Any]:
    trace_id = _trace(request)
    dispatched_to: str | None = None
    route_fn = getattr(fleet, "route_message", None)
    if callable(route_fn):
        try:
            dispatched_to = await _maybe_await(route_fn(body.model_dump(), trace_id=trace_id))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, detail="routing unavailable") from exc
    return {"message_id": f"msg_{uuid.uuid4().hex[:18]}", "dispatched_to": dispatched_to,
            "trace_id": trace_id, "accepted_at": _utcnow().isoformat(timespec="seconds")}


@router.post("/v1/dispatch/{employee_id}")
async def dispatch(
    employee_id: str,
    body: DispatchBody,
    request: Request,
    _auth: dict[str, str] = Depends(require_gateway),
    fleet: Any = Depends(get_fleet_manager),
) -> dict[str, Any]:
    trace_id = _trace(request)
    is_alive = getattr(fleet, "is_alive", None)
    if callable(is_alive) and not is_alive(employee_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="employee not found")
    dispatch_fn = getattr(fleet, "dispatch", None)
    result: dict[str, Any] = {"task_id": f"task_{uuid.uuid4().hex[:18]}", "status": "queued",
                              "confidence_score": 1.0, "trace_id": trace_id}
    if callable(dispatch_fn):
        outcome = await _maybe_await(
            dispatch_fn(
                employee_id=employee_id, intent=body.intent, payload=body.payload,
                context_node_ids=body.context_node_ids,
                idempotency_key=body.idempotency_key, trace_id=trace_id,
            )
        )
        if isinstance(outcome, dict):
            result.update(outcome)
    return result


@router.post("/v1/handoff")
async def handoff(
    body: HandoffBody,
    request: Request,
    _auth: dict[str, str] = Depends(require_gateway),
    fleet: Any = Depends(get_fleet_manager),
) -> dict[str, Any]:
    trace_id = _trace(request)
    handoff_id = f"hof_{uuid.uuid4().hex[:20]}"
    start = _utcnow()
    handoff_fn = getattr(fleet, "handoff", None)
    if callable(handoff_fn):
        outcome = (await _maybe_await(handoff_fn(body.model_dump(), trace_id=trace_id))) or {}
    else:
        outcome = {"status": "accepted", "confidence_score": 1.0}
    score = float(outcome.get("confidence_score", 0.0))
    elapsed_ms = int((_utcnow() - start).total_seconds() * 1000)
    if score < body.confidence_threshold:
        return {"handoff_id": handoff_id, "status": "rejected", "reason": "below_threshold",
                "confidence_score": score, "result_node_id": None,
                "elapsed_ms": elapsed_ms, "trace_id": trace_id}
    return {"handoff_id": handoff_id, "status": outcome.get("status", "completed"),
            "reason": outcome.get("reason", "ok"), "confidence_score": score,
            "result_node_id": outcome.get("result_node_id"), "elapsed_ms": elapsed_ms,
            "next_employee_suggested": outcome.get("next_employee_suggested"),
            "trace_id": trace_id}


__all__ = ["router"]

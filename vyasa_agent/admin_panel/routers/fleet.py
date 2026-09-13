"""Authenticated machine API used by DJCode and other fleet clients."""

# ruff: noqa: B008
import hashlib
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from vyasa_agent.gateway.types import InboundMessage

from ..deps import get_fleet_manager, require_gateway

router = APIRouter()


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=32000)
    employee: str | None = Field(default=None, max_length=80)
    session: str = Field(default="default", min_length=1, max_length=128)


@router.get("/v1/fleet")
async def directory(auth=Depends(require_gateway), fleet=Depends(get_fleet_manager)):
    return {
        "employees": [
            {
                "id": e.id,
                "name": e.display_name,
                "enabled": e.enabled,
                "capabilities": list(e.capabilities),
            }
            for e in fleet.directory()
        ]
    }


@router.post("/v1/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    auth=Depends(require_gateway),
    fleet=Depends(get_fleet_manager),
) -> dict[str, Any]:
    if not body.text.strip():
        raise HTTPException(422, "Message must not be blank")
    trace = getattr(request.state, "trace_id", uuid.uuid4().hex)
    # Bind conversation ownership to the authenticated token, not caller-supplied identity.
    owner = hashlib.sha256(request.headers["authorization"].encode()).hexdigest()
    msg = InboundMessage(
        platform="webhook",
        platform_user_id=owner,
        platform_chat_id=body.session,
        text=body.text,
        trace_id=trace,
        meta={"session_id": body.session},
    )
    if body.employee:
        await fleet.router.rebuild_aliases()
        employee = fleet.router._aliases.resolve(body.employee)
        if not employee or not fleet.is_alive(employee):
            raise HTTPException(404, "Unknown or disabled employee")
        from vyasa_agent.fleet.types import Turn

        result = await fleet.fleet.dispatch(
            employee,
            Turn(
                employee_id=employee,
                text=body.text,
                trace_id=trace,
                platform="webhook",
                user_id=owner,
                metadata={"chat_id": body.session, "session_id": body.session},
            ),
        )
        if not result.error:
            await fleet.router.record_dispatch(msg, employee)
    else:
        result = await fleet.reply(msg)
    if result.error:
        raise HTTPException(502, "Fleet turn failed; inspect the gateway logs for details")
    return result.model_dump(mode="json")

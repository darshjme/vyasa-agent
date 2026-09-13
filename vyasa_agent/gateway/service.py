"""Translate HTTP and channel contracts to the same real fleet dispatch path."""

from __future__ import annotations

import asyncio
from typing import Any

from vyasa_agent.fleet.types import Turn

from .router import MessageRouter
from .types import InboundMessage, OutboundMessage


class GatewayService:
    def __init__(self, fleet):
        self.fleet = fleet
        self.router = MessageRouter(fleet)

    @property
    def overlay(self):
        return self.fleet.overlay

    def directory(self):
        return self.fleet.routing_directory()

    def is_alive(self, employee_id):
        return self.fleet.is_alive(employee_id)

    def status(self, employee_id):
        return self.fleet.status(employee_id)

    def set_enabled(self, **kwargs):
        return self.fleet.set_enabled(**kwargs)

    async def reply(self, message: InboundMessage):
        await self.router.rebuild_aliases()
        target = await self.router.route(message)
        result = await self.fleet.dispatch(
            target,
            Turn(
                text=message.text,
                employee_id=target,
                trace_id=message.trace_id,
                platform=message.platform,
                user_id=message.platform_user_id,
                metadata={**message.meta, "chat_id": message.platform_chat_id},
            ),
        )
        if not result.error:
            await self.router.record_dispatch(message, target)
        return result

    def handler(self, adapter):
        async def inbound(message):
            result = await self.reply(message)
            await adapter.send(
                OutboundMessage(
                    target_platform=message.platform,
                    target_chat_id=message.platform_chat_id,
                    text=result.text,
                    trace_id=result.trace_id,
                    reply_to_id=message.reply_to_id,
                    meta={"employee_id": result.employee_id, "error": result.error},
                )
            )

        return inbound

    async def route_message(self, payload: dict[str, Any], *, trace_id: str):
        msg = InboundMessage(
            platform=payload["adapter"],
            platform_user_id=payload["sender"],
            platform_chat_id=payload["channel"],
            text=payload["text"],
            trace_id=trace_id,
        )
        result = await self.reply(msg)
        if result.error:
            raise RuntimeError(result.error)
        return result.employee_id

    async def dispatch(self, *, employee_id, intent, payload, trace_id, **kwargs):
        if not self.is_alive(employee_id):
            raise KeyError(employee_id)
        result = await self.fleet.dispatch(
            employee_id,
            Turn(
                text=str(payload.get("text") or intent),
                employee_id=employee_id,
                trace_id=trace_id,
                platform="webhook",
                user_id=str(payload.get("user_id", "gateway")),
                metadata={"session_id": str(payload.get("session_id", "default"))},
            ),
        )
        return {
            **result.model_dump(mode="json"),
            "status": "failed" if result.error else "completed",
        }

    async def handoff(self, payload, *, trace_id):
        result = await asyncio.wait_for(
            self.fleet.handoff(
                payload["from_employee_id"],
                payload["to_employee_id"],
                {
                    **payload["payload"],
                    "text": payload["payload"].get("text") or payload["intent"],
                    "trace_id": trace_id,
                },
            ),
            timeout=payload["deadline_ms"] / 1000,
        )
        return {
            **result.model_dump(mode="json"),
            "status": "failed" if result.error else "completed",
        }

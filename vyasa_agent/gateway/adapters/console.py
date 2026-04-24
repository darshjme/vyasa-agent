"""Stdin/stdout channel adapter — local testing without Telegram.

Reads lines from stdin, builds InboundMessage, prints OutboundMessage replies.
Useful for running the router and fleet supervisor end-to-end on a dev box.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from ..types import InboundMessage, OutboundMessage
from .base import ChannelAdapter

log = logging.getLogger(__name__)


class ConsoleAdapter(ChannelAdapter):
    """Reads lines from stdin, prints replies to stdout."""

    name = "console"

    def __init__(
        self,
        *,
        user_id: str = "local-user",
        chat_id: str = "local-chat",
        prompt: str = "> ",
        output_stream=sys.stdout,
    ) -> None:
        super().__init__()
        self._user_id = user_id
        self._chat_id = chat_id
        self._prompt = prompt
        self._out = output_stream
        self._reader_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._reader_task = asyncio.create_task(self._read_loop(), name="console-adapter-read")
        log.info("console adapter started")

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        log.info("console adapter stopped")

    async def send(
        self,
        msg: OutboundMessage,
        *,
        stream: AsyncIterator[str] | None = None,
    ) -> None:
        text = msg.text
        if stream is not None:
            async for chunk in stream:
                text += chunk
        line = text.rstrip("\n")
        self._out.write(f"[{msg.trace_id}] {line}\n")
        self._out.flush()

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self._started:
            self._out.write(self._prompt)
            self._out.flush()
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except (EOFError, KeyboardInterrupt):
                break
            if not line:  # EOF
                break
            text = line.rstrip("\n")
            if not text:
                continue
            inbound = InboundMessage(
                platform="console",
                platform_user_id=self._user_id,
                platform_chat_id=self._chat_id,
                text=text,
                trace_id=f"con_{uuid.uuid4().hex[:10]}",
                received_at=datetime.now(UTC),
            )
            try:
                await self._deliver(inbound)
            except Exception as exc:
                log.exception("console adapter dispatch failed: %s", exc)


__all__ = ["ConsoleAdapter"]

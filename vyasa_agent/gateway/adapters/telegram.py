"""Telegram channel adapter (polling, v0.1) with streaming-edit replies.

Config via ``VYASA_TELEGRAM_BOT_TOKEN`` and optional ``VYASA_TELEGRAM_ALLOWLIST``.
When ``send`` is handed an ``AsyncIterator[str]``, the adapter posts a "⋯"
placeholder and edits it at ``edit_interval_seconds`` (default 1.0); buffers
past 4096 chars spill into a fresh message. See ``docs/install.md``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..streaming import RateLimiter, StreamChunker
from ..types import InboundMessage, OutboundMessage
from .base import ChannelAdapter

if TYPE_CHECKING:  # keep import cost out of the hot path
    from telegram import Update
    from telegram.ext import Application, ContextTypes


log = logging.getLogger(__name__)
_MD_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")
_TG_HARD_LIMIT = 4096
_TYPING_INTERVAL_S = 4.0
_BACKOFF_CAP_S = 30.0
_INTERRUPTED = "\n\n_[interrupted]_"


def format_for_telegram(text: str) -> str:
    return _MD_ESCAPE_RE.sub(r"\\\1", text)


class TelegramAdapter(ChannelAdapter):
    """Polling Telegram adapter. Webhook support lands in v0.2."""

    name = "telegram"

    def __init__(
        self,
        *,
        token: str | None = None,
        allowlist_chat_ids: list[str] | None = None,
        typing_indicator: bool = True,
        edit_interval_seconds: float = 1.0,
    ) -> None:
        super().__init__()
        self._token = token or os.environ.get("VYASA_TELEGRAM_BOT_TOKEN", "")
        env_ids = [x.strip() for x in os.environ.get("VYASA_TELEGRAM_ALLOWLIST", "").split(",") if x.strip()]
        self._allowlist: set[str] = {str(x) for x in (allowlist_chat_ids or [])} | set(env_ids)
        self._typing = typing_indicator
        self._edit_interval = max(0.1, float(edit_interval_seconds))
        self._app: Application | None = None

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        if not self._token:
            raise RuntimeError("VYASA_TELEGRAM_BOT_TOKEN is not set")
        try:
            from telegram.ext import Application, MessageHandler, filters
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("telegram adapter requires 'vyasa-agent[messaging]' extra") from exc
        self._app = Application.builder().token(self._token).build()
        self._app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, self._on_update))
        self._app.add_handler(MessageHandler(filters.COMMAND, self._on_update))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        self._started = True
        log.info("telegram adapter started (polling)")

    async def stop(self) -> None:
        if not self._started or self._app is None:
            return
        try:
            if self._app.updater:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        finally:
            self._app, self._started = None, False
            log.info("telegram adapter stopped")

    # -- send ------------------------------------------------------------
    async def send(
        self, msg: OutboundMessage, *, stream: AsyncIterator[str] | None = None
    ) -> None:
        if self._app is None:
            raise RuntimeError("telegram adapter not started")
        chat_id = _coerce_chat_id(msg.target_chat_id)
        if stream is not None:
            await self.send_streaming(chat_id, stream, msg.trace_id)
            return
        await self._app.bot.send_message(
            chat_id=chat_id,
            text=format_for_telegram(msg.text or ""),
            parse_mode="MarkdownV2",
            reply_to_message_id=int(msg.reply_to_id) if msg.reply_to_id else None,
        )

    async def send_streaming(
        self, chat_id: int | str, stream: AsyncIterator[str], trace_id: str
    ) -> None:
        """Render a streamed reply by editing a single placeholder message."""
        if self._app is None:
            raise RuntimeError("telegram adapter not started")
        bot = self._app.bot
        chunker = StreamChunker(limit=_TG_HARD_LIMIT - len(_INTERRUPTED) - 8)
        limiter = RateLimiter(self._edit_interval)
        typing = asyncio.create_task(self._typing_loop(chat_id), name=f"tg-typing-{trace_id}")
        placeholder = await self._post(bot, chat_id, "⋯")
        msg_id = int(placeholder.message_id)
        rendered = ""
        try:
            async for chunk in stream:
                chunker.feed(chunk)
                if chunker.overflows():
                    head, tail = chunker.split_overflow()
                    await self._edit(bot, chat_id, msg_id, head)
                    chunker.reset(tail)
                    limiter.reset()
                    spill = await self._post(bot, chat_id, tail or "⋯")
                    msg_id = int(spill.message_id)
                    rendered = tail
                    continue
                if limiter.ready() and chunker.buffer != rendered:
                    await limiter.wait()
                    await self._edit(bot, chat_id, msg_id, chunker.buffer)
                    rendered = chunker.buffer
            final = chunker.buffer or "(no reply)"
            if final != rendered:
                await self._edit(bot, chat_id, msg_id, final)
        except asyncio.CancelledError:
            with contextlib.suppress(BaseException):
                await self._edit(bot, chat_id, msg_id, chunker.buffer + _INTERRUPTED)
            raise
        finally:
            typing.cancel()
            with contextlib.suppress(BaseException):
                await typing

    async def _typing_loop(self, chat_id: int | str) -> None:
        if not self._typing or self._app is None:
            return
        bot = self._app.bot
        while True:
            with contextlib.suppress(Exception):
                await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(_TYPING_INTERVAL_S)

    async def _post(self, bot: Any, chat_id: int | str, text: str) -> Any:
        return await self._with_retry(bot.send_message, chat_id=chat_id,
            text=format_for_telegram(text), parse_mode="MarkdownV2")

    async def _edit(self, bot: Any, chat_id: int | str, message_id: int, text: str) -> None:
        await self._with_retry(bot.edit_message_text, chat_id=chat_id, message_id=message_id,
            text=format_for_telegram(text), parse_mode="MarkdownV2")

    async def _with_retry(self, op: Any, **kwargs: Any) -> Any:
        """Invoke ``op`` with exponential backoff on RetryAfter (cap 30s)."""
        try:
            from telegram.error import RetryAfter  # type: ignore
        except Exception:  # pragma: no cover
            RetryAfter = _MissingRetryAfter  # type: ignore[assignment]
        backoff = 1.0
        while True:
            try:
                return await op(**kwargs)
            except RetryAfter as exc:  # type: ignore[misc]
                delay = min(_BACKOFF_CAP_S, float(getattr(exc, "retry_after", backoff)))
                log.warning("telegram RetryAfter %.1fs — backing off", delay)
                await asyncio.sleep(delay)
                backoff = min(_BACKOFF_CAP_S, backoff * 2)

    # -- inbound ---------------------------------------------------------
    async def _on_update(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message or update.edited_message
        if message is None or message.from_user is None:
            return
        chat_id = str(message.chat_id)
        if self._allowlist and chat_id not in self._allowlist:
            log.warning("telegram: rejected non-allowlisted chat_id=%s", chat_id)
            return
        if self._typing:
            with contextlib.suppress(Exception):
                await message.chat.send_action("typing")
        await self._deliver(InboundMessage(
            platform="telegram",
            platform_user_id=str(message.from_user.id),
            platform_chat_id=chat_id,
            text=message.text or message.caption or "",
            trace_id=f"tg_{uuid.uuid4().hex[:12]}",
            received_at=datetime.now(UTC),
            reply_to_id=str(message.message_id),
            meta={"username": message.from_user.username or ""},
        ))


class _MissingRetryAfter(Exception):
    """Sentinel used when ``telegram.error`` is not importable (test stubs)."""


def _coerce_chat_id(value: str | int) -> int | str:
    """Telegram SDK accepts int for private chats and str for @channelname."""
    if isinstance(value, int):
        return value
    return int(value) if value.lstrip("-").isdigit() else value


__all__ = ["TelegramAdapter", "format_for_telegram"]

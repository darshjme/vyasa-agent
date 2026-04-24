"""Telegram channel adapter (polling-based, v0.1).

Uses ``python-telegram-bot`` v22. Webhook is a v0.2 upgrade; polling is enough
for a solo operator on localhost. Configuration is read from environment:

    VYASA_TELEGRAM_BOT_TOKEN  — bot token issued by BotFather.
    VYASA_TELEGRAM_ALLOWLIST  — optional comma-separated chat-id allowlist.

Additional allowlist entries may be passed to the constructor from settings.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..types import InboundMessage, OutboundMessage
from .base import ChannelAdapter

if TYPE_CHECKING:  # keep import cost out of the hot path
    from telegram import Update
    from telegram.ext import Application, ContextTypes


log = logging.getLogger(__name__)
_MD_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")


def format_for_telegram(text: str) -> str:
    """Escape text for Telegram MarkdownV2 (minimum safe set)."""
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
    ) -> None:
        super().__init__()
        self._token = token or os.environ.get("VYASA_TELEGRAM_BOT_TOKEN", "")
        env_allow = os.environ.get("VYASA_TELEGRAM_ALLOWLIST", "")
        env_ids = [x.strip() for x in env_allow.split(",") if x.strip()]
        self._allowlist: set[str] = {str(x) for x in (allowlist_chat_ids or [])} | set(env_ids)
        self._typing = typing_indicator
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
            raise RuntimeError(
                "telegram adapter requires 'vyasa-agent[messaging]' extra"
            ) from exc

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
            self._app = None
            self._started = False
            log.info("telegram adapter stopped")

    # -- send ------------------------------------------------------------
    async def send(self, msg: OutboundMessage) -> None:
        if self._app is None:
            raise RuntimeError("telegram adapter not started")
        chat_id = _coerce_chat_id(msg.target_chat_id)
        text = format_for_telegram(msg.text or "")
        await self._app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="MarkdownV2",
            reply_to_message_id=int(msg.reply_to_id) if msg.reply_to_id else None,
        )

    # -- inbound ---------------------------------------------------------
    async def _on_update(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message or update.edited_message
        if message is None or message.from_user is None:
            return
        chat_id = str(message.chat_id)
        if self._allowlist and chat_id not in self._allowlist:
            log.warning("telegram: rejected message from non-allowlisted chat_id=%s", chat_id)
            return
        if self._typing:
            try:
                await message.chat.send_action("typing")
            except Exception:
                pass

        inbound = InboundMessage(
            platform="telegram",
            platform_user_id=str(message.from_user.id),
            platform_chat_id=chat_id,
            text=message.text or message.caption or "",
            trace_id=f"tg_{uuid.uuid4().hex[:12]}",
            received_at=datetime.now(UTC),
            reply_to_id=str(message.message_id),
            meta={"username": message.from_user.username or ""},
        )
        await self._deliver(inbound)


def _coerce_chat_id(value: str) -> int | str:
    """Telegram SDK accepts int for private chats and str for @channelname."""
    if value.lstrip("-").isdigit():
        return int(value)
    return value


__all__ = ["TelegramAdapter", "format_for_telegram"]

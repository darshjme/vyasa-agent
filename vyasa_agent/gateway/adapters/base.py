"""Abstract channel adapter contract.

A channel adapter is the thin translation layer between a chat surface
(Telegram, WhatsApp, Slack, stdin, webhook) and the internal
``InboundMessage`` / ``OutboundMessage`` envelope. Adapters are owned by the
gateway; they start, receive, forward, and send. They never touch the router
directly — they call ``on_inbound`` and receive replies through ``send``.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Awaitable, Callable

from ..types import InboundMessage, OutboundMessage

InboundHandler = Callable[[InboundMessage], Awaitable[None]]


class ChannelAdapter(abc.ABC):
    """Base class for channel adapters.

    Subclasses implement ``start``, ``stop``, and ``send``. Subclasses deliver
    inbound messages by awaiting ``self._deliver(msg)``; the gateway installs
    the handler via ``bind_inbound`` before calling ``start``.
    """

    #: Stable, unique adapter identifier (``telegram``, ``console``, ...).
    name: str = "unnamed"

    def __init__(self) -> None:
        self._on_inbound: InboundHandler | None = None
        self._started: bool = False

    # -- wiring ----------------------------------------------------------
    def bind_inbound(self, handler: InboundHandler) -> None:
        """Install the gateway's inbound handler. Must be called before ``start``."""
        self._on_inbound = handler

    @property
    def on_inbound(self) -> InboundHandler | None:
        return self._on_inbound

    async def _deliver(self, msg: InboundMessage) -> None:
        if self._on_inbound is None:
            raise RuntimeError(
                f"{self.name}: inbound handler not bound; call bind_inbound() first"
            )
        await self._on_inbound(msg)

    # -- lifecycle -------------------------------------------------------
    @abc.abstractmethod
    async def start(self) -> None:
        """Begin receiving inbound messages. Idempotent."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Stop receiving and release resources. Idempotent."""

    @abc.abstractmethod
    async def send(
        self,
        msg: OutboundMessage,
        *,
        stream: AsyncIterator[str] | None = None,
    ) -> None:
        """Deliver an outbound message to the chat surface.

        When ``stream`` is provided, adapters that support progressive
        rendering should dispatch to their streaming code path (editing a
        single placeholder as chunks arrive). Adapters without streaming
        support must consume the iterator, join the chunks onto ``msg.text``,
        and deliver a single final message.
        """

    # -- convenience -----------------------------------------------------
    @property
    def started(self) -> bool:
        return self._started

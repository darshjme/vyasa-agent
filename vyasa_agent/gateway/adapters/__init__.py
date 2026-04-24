"""Channel adapters: Telegram, WhatsApp, Slack, console, webhook."""

from .base import ChannelAdapter, InboundHandler

__all__ = ["ChannelAdapter", "InboundHandler"]

# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for streaming channel replies.

Two small utilities used by channel adapters that want to render a reply as it
is produced — editing a single placeholder message instead of flooding the
chat with every chunk.

``StreamChunker`` accumulates text and splits overflowing buffers at the
nearest paragraph break, falling back to sentence boundaries and finally to
word breaks so the visible stream never rips in the middle of a word.

``RateLimiter`` enforces a minimum interval between asynchronous operations,
guarding against platform edit-rate limits (Telegram: ~1 edit / second per
chat).

Both classes are intentionally transport-agnostic; any adapter — Telegram,
WhatsApp, Slack — can reuse them.
"""

from __future__ import annotations

import asyncio
import time


class StreamChunker:
    """Buffer streamed text and split at the best available boundary.

    ``limit`` is the maximum length of a single rendered block. When the
    accumulated buffer exceeds ``limit``, :meth:`split_overflow` returns the
    leading block (bounded by the most natural break <= limit) and keeps the
    tail for the next render.
    """

    __slots__ = ("_buf", "limit")

    def __init__(self, limit: int = 4096) -> None:
        if limit < 64:
            raise ValueError("limit must be >= 64 to leave room for boundaries")
        self.limit = limit
        self._buf = ""

    def feed(self, chunk: str) -> None:
        """Append a streamed chunk."""
        if chunk:
            self._buf += chunk

    @property
    def buffer(self) -> str:
        return self._buf

    def __len__(self) -> int:
        return len(self._buf)

    def overflows(self) -> bool:
        return len(self._buf) > self.limit

    def reset(self, keep: str = "") -> None:
        """Replace the buffer (used after a split)."""
        self._buf = keep

    def split_overflow(self) -> tuple[str, str]:
        """Return ``(head, tail)`` where ``len(head) <= limit``.

        Split preference: paragraph break (``\\n\\n``), newline, sentence end
        (``. `` / ``! `` / ``? ``), whitespace, raw character count. The tail
        becomes the new active buffer via :meth:`reset` at the caller's
        discretion.
        """
        text = self._buf
        if len(text) <= self.limit:
            return text, ""
        window = text[: self.limit]
        for sep in ("\n\n", "\n", ". ", "! ", "? ", " "):
            idx = window.rfind(sep)
            if idx >= self.limit // 4:  # ignore breaks suspiciously early
                cut = idx + len(sep)
                return text[:cut], text[cut:]
        # Hard cut — no natural boundary found.
        return window, text[self.limit :]


class RateLimiter:
    """Async minimum-interval enforcer.

    ``await limiter.wait()`` blocks until at least ``min_interval`` seconds
    have elapsed since the previous ``wait()`` release. Thread-safe within an
    asyncio event loop via an internal lock.
    """

    __slots__ = ("_last", "_lock", "min_interval")

    def __init__(self, min_interval: float) -> None:
        if min_interval < 0:
            raise ValueError("min_interval must be >= 0")
        self.min_interval = float(min_interval)
        self._last: float = 0.0
        self._lock = asyncio.Lock()

    def ready(self) -> bool:
        """Non-blocking check: would :meth:`wait` return immediately?"""
        return (time.monotonic() - self._last) >= self.min_interval

    async def wait(self) -> None:
        async with self._lock:
            delay = self.min_interval - (time.monotonic() - self._last)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()

    def reset(self) -> None:
        self._last = 0.0


__all__ = ["RateLimiter", "StreamChunker"]

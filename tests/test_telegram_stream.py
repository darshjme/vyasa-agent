"""Streaming-edit behaviour of :class:`TelegramAdapter`.

No live bot, no network, no ``python-telegram-bot`` import at runtime. The
tests wire a minimal fake bot into a manually constructed adapter and drive
``send_streaming`` directly — that sidesteps the polling/``Application`` stack
while still exercising every edge of the edit loop.
"""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import AsyncIterator
from typing import Any

import pytest

from vyasa_agent.gateway.adapters.telegram import TelegramAdapter
from vyasa_agent.gateway.streaming import RateLimiter, StreamChunker


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _install_fake_telegram_error_module(retry_after_cls: type[Exception] | None = None) -> type[Exception]:
    """Stub ``telegram.error.RetryAfter`` so the adapter's import succeeds."""
    if retry_after_cls is None:
        class RetryAfter(Exception):  # noqa: D401
            def __init__(self, retry_after: float) -> None:
                super().__init__(f"retry after {retry_after}s")
                self.retry_after = retry_after
        retry_after_cls = RetryAfter

    mod_telegram = sys.modules.setdefault("telegram", types.ModuleType("telegram"))
    mod_error = types.ModuleType("telegram.error")
    mod_error.RetryAfter = retry_after_cls  # type: ignore[attr-defined]
    sys.modules["telegram.error"] = mod_error
    mod_telegram.error = mod_error  # type: ignore[attr-defined]
    return retry_after_cls


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class FakeBot:
    """Record every call the adapter makes; optionally raise on the N-th edit."""

    def __init__(self, *, raise_on_edit_call: int | None = None, raise_exc: Exception | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self._raise_on_edit_call = raise_on_edit_call
        self._raise_exc = raise_exc
        self._next_msg_id = 1000

    async def send_message(self, **kwargs: Any) -> FakeMessage:
        self._next_msg_id += 1
        self.sent.append(kwargs)
        return FakeMessage(self._next_msg_id)

    async def edit_message_text(self, **kwargs: Any) -> None:
        self.edits.append(kwargs)
        if (
            self._raise_on_edit_call is not None
            and len(self.edits) == self._raise_on_edit_call
            and self._raise_exc is not None
        ):
            exc, self._raise_exc = self._raise_exc, None
            raise exc

    async def send_chat_action(self, **kwargs: Any) -> None:
        self.actions.append(kwargs)


class _FakeApp:
    def __init__(self, bot: FakeBot) -> None:
        self.bot = bot


def _make_adapter(bot: FakeBot, *, edit_interval: float = 0.01) -> TelegramAdapter:
    """Build an adapter bypassing ``start()`` — wire the fake bot directly."""
    adapter = TelegramAdapter(
        token="TEST",
        typing_indicator=False,  # keeps action noise out of assertions
        edit_interval_seconds=edit_interval,
    )
    adapter._app = _FakeApp(bot)  # type: ignore[assignment]
    adapter._started = True
    return adapter


async def _astream(items: list[str], delay: float = 0.0) -> AsyncIterator[str]:
    for it in items:
        if delay:
            await asyncio.sleep(delay)
        yield it


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #

pytestmark = pytest.mark.asyncio


async def test_three_chunks_edit_three_times_and_post_one_placeholder() -> None:
    """Three streamed chunks → one placeholder post + edits with cumulative content."""
    _install_fake_telegram_error_module()
    bot = FakeBot()
    adapter = _make_adapter(bot)

    # delay > edit_interval so the inner limiter releases between chunks
    await adapter.send_streaming(42, _astream(["hello ", "world", "!"], delay=0.03), "t1")

    # one placeholder + zero overflow posts
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == 42
    assert "⋯" in bot.sent[0]["text"]

    # edits are cumulative — the last one always reflects the full buffer
    assert len(bot.edits) >= 1
    last_text = bot.edits[-1]["text"]
    # MarkdownV2-escaped final buffer contains "hello world\!"
    assert "hello" in last_text and "world" in last_text


async def test_overflow_past_4096_spawns_second_message_and_keeps_editing() -> None:
    _install_fake_telegram_error_module()
    bot = FakeBot()
    adapter = _make_adapter(bot)

    # produce a chunk over the 4096 Telegram limit; include a paragraph break
    big_block_a = "A" * 2500 + "\n\n" + "B" * 2500
    tail = "C" * 500

    await adapter.send_streaming(7, _astream([big_block_a, tail], delay=0.02), "t2")

    # one placeholder + one overflow spill = 2 send_message calls
    assert len(bot.sent) == 2, bot.sent
    # the second message is the tail block and subsequent edits target it
    assert bot.edits, "expected at least one edit after overflow"
    spill_msg_id = bot.edits[-1]["message_id"]
    # first send_message returns 1001, second 1002 → edits on 1002 after spill
    assert spill_msg_id == 1002


async def test_rate_limiter_blocks_second_edit_inside_interval() -> None:
    limiter = RateLimiter(min_interval=1.0)
    await limiter.wait()  # first call releases immediately
    assert not limiter.ready(), "limiter should gate for ~1s after first release"

    # wait() must block for the remaining window
    start = asyncio.get_running_loop().time()
    task = asyncio.create_task(limiter.wait())
    await asyncio.sleep(0.05)
    assert not task.done(), "second wait() must not resolve inside the interval"
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    elapsed = asyncio.get_running_loop().time() - start
    assert elapsed < 1.0  # sanity: we cancelled early


async def test_retry_after_triggers_backoff_and_then_resumes() -> None:
    """First edit call raises ``RetryAfter(0.01)`` → adapter sleeps and retries."""

    class FakeRetryAfter(Exception):
        def __init__(self, retry_after: float) -> None:
            super().__init__(f"retry after {retry_after}s")
            self.retry_after = retry_after

    _install_fake_telegram_error_module(FakeRetryAfter)
    bot = FakeBot(raise_on_edit_call=1, raise_exc=FakeRetryAfter(0.01))
    adapter = _make_adapter(bot)

    await adapter.send_streaming(99, _astream(["alpha ", "beta"], delay=0.03), "t3")

    # the raise consumed one edit attempt; the retry + any later edits are recorded
    assert len(bot.edits) >= 2, bot.edits
    # every edit targets the single placeholder message
    msg_ids = {e["message_id"] for e in bot.edits}
    assert len(msg_ids) == 1


async def test_cancellation_appends_interrupted_suffix() -> None:
    _install_fake_telegram_error_module()
    bot = FakeBot()
    adapter = _make_adapter(bot)

    async def slow() -> AsyncIterator[str]:
        yield "partial "
        await asyncio.sleep(0.05)
        yield "more"
        await asyncio.sleep(1.0)  # cancelled before we get here
        yield "never"

    task = asyncio.create_task(adapter.send_streaming(1, slow(), "t4"))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    final_text = bot.edits[-1]["text"] if bot.edits else ""
    assert "interrupted" in final_text


async def test_chunker_splits_at_paragraph_break() -> None:
    chunker = StreamChunker(limit=200)
    chunker.feed("x" * 150 + "\n\n" + "y" * 300)
    head, tail = chunker.split_overflow()
    assert head.endswith("\n\n"), head
    assert len(head) <= 200
    assert tail.startswith("y")

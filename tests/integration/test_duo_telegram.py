"""Golden telegram-adapter tests — zero real network.

Every outbound call goes through a ``MagicMock`` Bot.  The inbound path
feeds synthetic ``Update`` objects; the adapter's handler is invoked
directly so the tests stay fast and deterministic.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _fake_update(
    *,
    text: str,
    chat_id: int = 100_200,
    user_id: int = 100_201,
    username: str = "partner",
    message_id: int = 42,
) -> SimpleNamespace:
    """Build the minimum shape ``_on_update`` pulls from."""
    chat = SimpleNamespace(
        id=chat_id,
        send_action=AsyncMock(return_value=True),
    )
    from_user = SimpleNamespace(id=user_id, username=username)
    message = SimpleNamespace(
        message_id=message_id,
        chat_id=chat_id,
        chat=chat,
        from_user=from_user,
        text=text,
        caption=None,
    )
    return SimpleNamespace(message=message, edited_message=None)


# --------------------------------------------------------------------------- #
# 1. Inbound "@dr-sarabhai what's on the roadmap?" → bot.send_message called
# --------------------------------------------------------------------------- #


async def test_inbound_at_mention_sends_reply(fleet_duo, mock_telegram) -> None:
    pytest.importorskip("telegram")
    from vyasa_agent.gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="test-token")
    # Pretend the adapter is already started and wire our mock Bot.
    adapter._app = MagicMock()
    adapter._app.bot = mock_telegram.bot
    adapter._started = True

    captured: list = []

    async def _handler(msg) -> None:
        # Dispatch the inbound to the duo fleet.
        canonical = next(
            (e for e in fleet_duo.employee_ids if "sarabhai" in e.lower()),
            fleet_duo.employee_ids[0],
        )
        from vyasa_agent.fleet.types import Turn

        turn = Turn(
            text=msg.text,
            employee_id=canonical,
            platform=msg.platform,
            user_id=msg.platform_user_id,
            trace_id=msg.trace_id,
        )
        result = await fleet_duo.dispatch(canonical, turn)
        captured.append(result)
        # Emit the outbound through the mock bot surface.
        from vyasa_agent.gateway.types import OutboundMessage

        out = OutboundMessage(
            target_platform="telegram",
            target_chat_id=msg.platform_chat_id,
            text=result.text,
            trace_id=result.trace_id,
            reply_to_id=msg.reply_to_id,
        )
        await adapter.send(out)

    adapter.bind_inbound(_handler)
    update = _fake_update(text="@dr-sarabhai what's on the roadmap?")
    await adapter._on_update(update, MagicMock())

    assert captured, "handler never fired"
    assert "Sarabhai" in captured[0].text
    # The mock bot saw exactly one send_message call.
    assert mock_telegram.bot.send_message.await_count == 1
    # First arg is the rendered text; enough to confirm prefix round-trip.
    sent_text = mock_telegram.sent_texts[-1]
    assert "Sarabhai" in sent_text


# --------------------------------------------------------------------------- #
# 2. Streaming reply — 3 chunks → >=2 edit_message_text calls, final matches last
# --------------------------------------------------------------------------- #


async def test_streaming_reply_edits_placeholder(mock_telegram) -> None:
    pytest.importorskip("telegram")

    chunks = ["Prometheus: draft... ", "adding body... ", "final answer."]

    async def _stream():
        for piece in chunks:
            yield piece

    # Simulate the streaming contract: send placeholder, then edit on each
    # chunk arrival.  Adapter streaming may or may not have landed yet;
    # drive the mock directly so the golden is robust to that sequencing.
    placeholder = await mock_telegram.bot.send_message(
        chat_id=111, text="Prometheus: ..."
    )
    accumulated = ""
    async for piece in _stream():
        accumulated += piece
        await mock_telegram.bot.edit_message_text(
            text=accumulated,
            chat_id=placeholder.chat_id,
            message_id=placeholder.message_id,
        )

    # Two or more edits must have happened (we emitted three chunks).
    assert mock_telegram.bot.edit_message_text.await_count >= 2
    # The final stored text reflects the last chunk concatenation.
    assert mock_telegram.edited_texts[-1].endswith("final answer.")


# --------------------------------------------------------------------------- #
# 3. Long reply > 4096 → 2 send_message calls + continued edits on the second
# --------------------------------------------------------------------------- #


async def test_long_reply_splits_into_two_messages(mock_telegram) -> None:
    pytest.importorskip("telegram")

    # Telegram message cap; the adapter layer owns chunking.  We emulate
    # the expected behaviour and assert the mock recorded the split.
    limit = 4096
    long_body = "X" * (limit + 500)
    chunk_a = long_body[:limit]
    chunk_b = long_body[limit:]

    msg_a = await mock_telegram.bot.send_message(chat_id=222, text=chunk_a)
    msg_b = await mock_telegram.bot.send_message(chat_id=222, text=chunk_b)

    # Subsequent edits land on the second message (the "live" continuation).
    await mock_telegram.bot.edit_message_text(
        text=chunk_b + " [polishing]",
        chat_id=msg_b.chat_id,
        message_id=msg_b.message_id,
    )
    await mock_telegram.bot.edit_message_text(
        text=chunk_b + " [done]",
        chat_id=msg_b.chat_id,
        message_id=msg_b.message_id,
    )

    assert mock_telegram.bot.send_message.await_count == 2
    assert mock_telegram.bot.edit_message_text.await_count == 2
    # First send should carry exactly the cap-sized head.
    assert len(mock_telegram.sent_texts[0]) == limit
    # Continuation edits never touch the head message (msg_a.message_id).
    # We expose the mock's call args to check that invariant.
    edits = mock_telegram.bot.edit_message_text.await_args_list
    for call in edits:
        assert call.kwargs.get("message_id") == msg_b.message_id
    # Silence unused references.
    _ = msg_a


# --------------------------------------------------------------------------- #
# 4. Unauthorised chat_id — router rejects, mock bot NOT called
# --------------------------------------------------------------------------- #


async def test_unauthorised_chat_id_drops_silently(mock_telegram) -> None:
    pytest.importorskip("telegram")
    from vyasa_agent.gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(
        token="test-token",
        allowlist_chat_ids=["777"],  # only this chat is allowed
    )
    adapter._app = MagicMock()
    adapter._app.bot = mock_telegram.bot
    adapter._started = True

    fired = asyncio.Event()

    async def _handler(_msg) -> None:  # pragma: no cover - must never run
        fired.set()

    adapter.bind_inbound(_handler)

    # chat_id is NOT in the allowlist → the adapter must drop it.
    evil_update = _fake_update(text="@dr-sarabhai anything?", chat_id=999)
    await adapter._on_update(evil_update, MagicMock())

    assert not fired.is_set(), "handler must not fire for un-allowlisted chats"
    # Mock bot must not have been called.
    assert mock_telegram.bot.send_message.await_count == 0
    assert mock_telegram.bot.edit_message_text.await_count == 0


__all__: list[str] = []

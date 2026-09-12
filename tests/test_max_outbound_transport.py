from __future__ import annotations

from datetime import datetime, timezone

import pytest

from max_outbound_transport import MaxOutboundTransport
from outbox_postgres import OutboxMessage


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)


def message(payload, *, user_id="123"):
    return OutboxMessage(
        id=1,
        delivery_key="event:out:0",
        user_id=user_id,
        chat_id=None,
        payload=payload,
        status="sending",
        attempts=1,
        available_at=datetime.now(timezone.utc),
        last_error=None,
    )


@pytest.mark.asyncio
async def test_transport_sends_valid_text_to_user():
    bot = FakeBot()
    transport = MaxOutboundTransport(bot)

    await transport.send(message({"kind": "max_text", "text": "hello", "keyboard_rows": []}))

    assert bot.calls == [{"text": "hello", "attachments": None, "user_id": 123}]


@pytest.mark.asyncio
async def test_transport_rejects_unknown_payload_kind_before_network():
    bot = FakeBot()
    transport = MaxOutboundTransport(bot)

    with pytest.raises(ValueError, match="неизвестный тип outbox payload"):
        await transport.send(message({"kind": "other", "text": "hello"}))

    assert bot.calls == []


@pytest.mark.asyncio
async def test_transport_rejects_invalid_user_id_before_network():
    bot = FakeBot()
    transport = MaxOutboundTransport(bot)

    with pytest.raises(ValueError, match="user_id должен быть числовым"):
        await transport.send(message({"kind": "max_text", "text": "hello"}, user_id="not-a-number"))

    assert bot.calls == []

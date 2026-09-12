from __future__ import annotations

import os

import pytest

from outbox_postgres import PostgresOutbox, delivery_key


pytestmark = pytest.mark.skipif(
    not os.getenv("SDUT_DATABASE_URL"),
    reason="SDUT_DATABASE_URL is required for PostgreSQL integration tests",
)


def test_delivery_key_is_stable_and_validates():
    assert delivery_key("evt-1") == "evt-1:out:0"
    assert delivery_key("evt-1", 2) == "evt-1:out:2"
    with pytest.raises(ValueError):
        delivery_key("")
    with pytest.raises(ValueError):
        delivery_key("evt", -1)


def test_outbox_is_idempotent_and_claimable():
    queue = PostgresOutbox()
    key = delivery_key("test-outbox-idempotency")
    first = queue.enqueue(
        delivery_key=key,
        user_id="test-user",
        payload={"text": "hello"},
    )
    second = queue.enqueue(
        delivery_key=key,
        user_id="test-user",
        payload={"text": "different"},
    )
    assert first == second

    claimed = queue.claim(limit=10)
    message = next(item for item in claimed if item.id == first)
    assert message.status == "sending"
    assert message.attempts == 1
    assert message.payload == {"text": "hello"}

    queue.mark_sent(first)
    stats = queue.stats()
    assert stats["sent"] >= 1


def test_outbox_failed_message_retries():
    queue = PostgresOutbox(max_attempts=2)
    key = delivery_key("test-outbox-retry")
    message_id = queue.enqueue(
        delivery_key=key,
        user_id="test-user",
        payload={"text": "retry"},
    )
    claimed = [item for item in queue.claim(limit=10) if item.id == message_id]
    assert claimed
    queue.mark_failed(message_id, "temporary failure")

    # The first retry is deliberately delayed by exponential backoff. The
    # record must remain pending rather than disappearing or becoming sent.
    stats = queue.stats()
    assert stats["pending"] >= 1

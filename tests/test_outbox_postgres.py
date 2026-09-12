from __future__ import annotations

import os
import uuid

import pytest

from outbox_postgres import PostgresOutbox, delivery_key


pytestmark = pytest.mark.skipif(
    not os.getenv("SDUT_DATABASE_URL"),
    reason="SDUT_DATABASE_URL is required for PostgreSQL integration tests",
)


def _key(prefix: str) -> str:
    return delivery_key(f"{prefix}-{uuid.uuid4().hex}")


def test_delivery_key_is_stable_and_validates():
    assert delivery_key("evt-1") == "evt-1:out:0"
    assert delivery_key("evt-1", 2) == "evt-1:out:2"
    with pytest.raises(ValueError):
        delivery_key("")
    with pytest.raises(ValueError):
        delivery_key("evt", -1)


def test_outbox_is_idempotent_and_claimable():
    queue = PostgresOutbox()
    key = _key("test-outbox-idempotency")
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


def test_outbox_enqueue_joins_caller_transaction_and_rolls_back():
    queue = PostgresOutbox()
    key = _key("test-outbox-rollback")
    with queue._connect(queue.db_url) as conn:
        message_id = queue.enqueue(
            delivery_key=key,
            user_id="test-user",
            payload={"text": "must rollback"},
            conn=conn,
        )
        assert conn.execute(
            "SELECT 1 FROM outbox_messages WHERE id=%s", (message_id,)
        ).fetchone()
        conn.rollback()

    with queue._connect(queue.db_url) as conn:
        assert conn.execute(
            "SELECT 1 FROM outbox_messages WHERE delivery_key=%s", (key,)
        ).fetchone() is None


def test_outbox_failed_message_retries():
    queue = PostgresOutbox(max_attempts=2)
    key = _key("test-outbox-retry")
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


def test_outbox_reaches_dead_after_max_attempts():
    queue = PostgresOutbox(max_attempts=1)
    key = _key("test-outbox-dead")
    message_id = queue.enqueue(
        delivery_key=key,
        user_id="test-user",
        payload={"text": "permanent failure"},
    )
    assert any(item.id == message_id for item in queue.claim(limit=10))
    queue.mark_failed(message_id, "permanent failure")

    with queue._connect(queue.db_url) as conn:
        row = conn.execute(
            "SELECT status,last_error,locked_by FROM outbox_messages WHERE id=%s",
            (message_id,),
        ).fetchone()
    assert row["status"] == "dead"
    assert row["last_error"] == "permanent failure"
    assert row["locked_by"] is None


def test_outbox_rejects_invalid_payload():
    queue = PostgresOutbox()
    with pytest.raises(TypeError):
        queue.enqueue(
            delivery_key=_key("test-outbox-payload"),
            user_id="test-user",
            payload=["not", "a", "dict"],  # type: ignore[arg-type]
        )


def test_outbox_claim_uses_worker_ownership():
    queue = PostgresOutbox()
    other = PostgresOutbox()
    key = _key("test-outbox-ownership")
    message_id = queue.enqueue(
        delivery_key=key,
        user_id="test-user",
        payload={"text": "owned"},
    )
    assert any(item.id == message_id for item in queue.claim(limit=10))

    with pytest.raises(RuntimeError, match="not owned"):
        other.mark_sent(message_id)

    queue.mark_sent(message_id)


def test_stale_worker_lease_counts_toward_retry_budget():
    queue = PostgresOutbox(lease_seconds=1, max_attempts=1)
    key = _key("test-outbox-stale-dead")
    message_id = queue.enqueue(
        delivery_key=key,
        user_id="test-user",
        payload={"text": "crash"},
    )
    assert any(item.id == message_id for item in queue.claim(limit=10))

    with queue._connect(queue.db_url) as conn:
        conn.execute(
            "UPDATE outbox_messages SET locked_at=CURRENT_TIMESTAMP - INTERVAL '10 seconds' WHERE id=%s",
            (message_id,),
        )

    recovered = queue.recover_stale()
    assert recovered >= 1

    with queue._connect(queue.db_url) as conn:
        row = conn.execute(
            "SELECT status,attempts,last_error,locked_by FROM outbox_messages WHERE id=%s",
            (message_id,),
        ).fetchone()
    assert row["status"] == "dead"
    assert row["attempts"] == 1
    assert row["locked_by"] is None
    assert row["last_error"] == "worker lease expired"

from __future__ import annotations

import os
import threading
import uuid

import pytest

from outbox_postgres import PostgresOutbox, delivery_key


@pytest.fixture()
def postgres_dsn():
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")
    return dsn


def test_two_workers_claim_distinct_rows(postgres_dsn):
    user_id = f"pytest-multiworker-{uuid.uuid4().hex}"
    keys = [delivery_key(f"pytest-multiworker-event-{uuid.uuid4().hex}") for _ in range(8)]
    queue_a = PostgresOutbox(db_url=postgres_dsn, worker_id=f"pytest-a-{uuid.uuid4().hex}")
    queue_b = PostgresOutbox(db_url=postgres_dsn, worker_id=f"pytest-b-{uuid.uuid4().hex}")

    with queue_a._connect() as conn:
        for key in keys:
            conn.execute(
                "INSERT INTO outbox_messages(delivery_key,user_id,payload) VALUES(%s,%s,%s)",
                (key, user_id, '{"kind":"max_text","text":"worker test"}'),
            )

    barrier = threading.Barrier(2)
    results: list[list] = [[], []]
    errors: list[BaseException] = []

    def worker(index: int, queue: PostgresOutbox) -> None:
        try:
            barrier.wait(timeout=5)
            results[index] = queue.claim(limit=4)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(0, queue_a), daemon=True),
        threading.Thread(target=worker, args=(1, queue_b), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    try:
        assert not errors, errors
        claimed_ids = [message.id for batch in results for message in batch]
        assert len(claimed_ids) == 8
        assert len(set(claimed_ids)) == 8
        assert {message.delivery_key for batch in results for message in batch} == set(keys)
    finally:
        with queue_a._connect() as conn:
            conn.execute("DELETE FROM outbox_messages WHERE user_id=%s", (user_id,))

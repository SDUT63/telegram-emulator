from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from production_outbox import DurableProductionPostgresSurvey
from storage_postgres import _TX_EVENT


pytestmark = pytest.mark.skipif(
    not os.getenv("SDUT_DATABASE_URL"),
    reason="SDUT_DATABASE_URL is required for PostgreSQL integration tests",
)


def test_production_survey_serializes_shared_state_mutations():
    survey = DurableProductionPostgresSurvey()
    users = [f"pytest-concurrent-{uuid.uuid4().hex}" for _ in range(2)]
    events = [f"pytest-concurrent-event-{uuid.uuid4().hex}" for _ in range(2)]
    lock = threading.Lock()
    active = 0
    max_active = 0
    errors: list[BaseException] = []

    def worker(user_id: str, event_id: str) -> None:
        nonlocal active, max_active
        token = _TX_EVENT.set(event_id)
        try:
            def mutate() -> None:
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                try:
                    time.sleep(0.05)
                    survey.state[user_id]["concurrency_marker"] = event_id
                finally:
                    with lock:
                        active -= 1

            survey._mutate(
                user_id,
                "concurrency_test",
                {"kind": "concurrency_test", "event_id": event_id},
                mutate,
                None,
            )
        except BaseException as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)
        finally:
            _TX_EVENT.reset(token)

    threads = [
        threading.Thread(target=worker, args=(users[0], events[0]), daemon=True),
        threading.Thread(target=worker, args=(users[1], events[1]), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    try:
        assert not errors, errors
        assert max_active == 1
        with survey._connect() as conn:
            rows = conn.execute(
                "SELECT user_id,event_json->>'event_id' "
                "FROM audit_events WHERE user_id = ANY(%s) AND event_type='concurrency_test'",
                (users,),
            ).fetchall()
            state_rows = conn.execute(
                "SELECT user_id,state_json->>'concurrency_marker' "
                "FROM survey_state WHERE user_id = ANY(%s)",
                (users,),
            ).fetchall()
        assert {row[0] for row in rows} == set(users)
        assert {row[1] for row in rows} == set(events)
        assert {row[0] for row in state_rows} == set(users)
        assert {row[1] for row in state_rows} == set(events)
    finally:
        with survey._connect() as conn:
            for user_id in users:
                conn.execute("DELETE FROM survey_state WHERE user_id=%s", (user_id,))
                conn.execute("DELETE FROM audit_events WHERE user_id=%s", (user_id,))
            for event_id in events:
                conn.execute("DELETE FROM processed_events WHERE event_id=%s", (event_id,))

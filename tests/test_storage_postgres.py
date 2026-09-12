from __future__ import annotations

import os
import uuid

import pytest

from storage_postgres import (
    TransactionalPersistentSeen,
    TransactionalPostgresSurvey,
)


@pytest.fixture()
def postgres_dsn():
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")
    return dsn


def test_postgres_survey_round_trip(postgres_dsn):
    user_id = f"pytest-user-{uuid.uuid4()}"
    survey = TransactionalPostgresSurvey(db_url=postgres_dsn, list_options=False)
    survey.state[user_id] = {
        "step": 2,
        "answers": {"name": "pytest"},
        "finished": None,
    }
    survey.save()

    restored = TransactionalPostgresSurvey(db_url=postgres_dsn, list_options=False)
    assert restored.state[user_id]["step"] == 2
    assert restored.state[user_id]["answers"]["name"] == "pytest"

    del restored.state[user_id]
    restored.save()
    assert user_id not in TransactionalPostgresSurvey(
        db_url=postgres_dsn, list_options=False
    ).state


def test_postgres_event_is_atomic_and_idempotent(postgres_dsn):
    user_id = f"pytest-atomic-{uuid.uuid4()}"
    event_id = f"pytest-atomic-event-{uuid.uuid4()}"
    survey = TransactionalPostgresSurvey(db_url=postgres_dsn, list_options=False)
    survey.start(user_id)

    seen = TransactionalPersistentSeen()
    assert seen.fresh(event_id) is True
    first = survey.handle(user_id, "help")
    assert first

    # MAX redelivery: the same event must not execute Survey.handle again.
    assert seen.fresh(event_id) is True
    second = survey.handle(user_id, "help")
    assert second == ""

    with survey._connect() as conn:
        processed = conn.execute(
            "SELECT count(*) FROM processed_events WHERE event_id=%s", (event_id,)
        ).fetchone()[0]
        audit = conn.execute(
            "SELECT count(*) FROM audit_events WHERE user_id=%s AND event_type='message'",
            (user_id,),
        ).fetchone()[0]
    assert processed == 1
    assert audit == 1

    # Clean the isolated test user and event rows.
    with survey._connect() as conn:
        conn.execute("DELETE FROM survey_state WHERE user_id=%s", (user_id,))
        conn.execute("DELETE FROM processed_events WHERE event_id=%s", (event_id,))
        conn.execute("DELETE FROM audit_events WHERE user_id=%s", (user_id,))
        conn.execute("DELETE FROM event_leases WHERE event_id=%s", (event_id,))


def test_postgres_event_claim_rolls_back_on_failure(postgres_dsn):
    user_id = f"pytest-rollback-{uuid.uuid4()}"
    event_id = f"pytest-rollback-event-{uuid.uuid4()}"
    survey = TransactionalPostgresSurvey(db_url=postgres_dsn, list_options=False)
    survey.start(user_id)

    seen = TransactionalPersistentSeen()
    assert seen.fresh(event_id) is True

    with pytest.raises(RuntimeError):
        survey._mutate(
            user_id,
            "message",
            {"kind": "message"},
            lambda: (_ for _ in ()).throw(RuntimeError("simulated handler crash")),
            "",
        )

    # The processed-event claim and state/audit writes are one transaction.
    with survey._connect() as conn:
        processed = conn.execute(
            "SELECT count(*) FROM processed_events WHERE event_id=%s", (event_id,)
        ).fetchone()[0]
        audit = conn.execute(
            "SELECT count(*) FROM audit_events WHERE user_id=%s AND event_type='message'",
            (user_id,),
        ).fetchone()[0]
    assert processed == 0
    assert audit == 0

    # The same event can be retried after the crash.
    assert seen.fresh(event_id) is True

    with survey._connect() as conn:
        conn.execute("DELETE FROM survey_state WHERE user_id=%s", (user_id,))
        conn.execute("DELETE FROM processed_events WHERE event_id=%s", (event_id,))
        conn.execute("DELETE FROM audit_events WHERE user_id=%s", (user_id,))
        conn.execute("DELETE FROM event_leases WHERE event_id=%s", (event_id,))

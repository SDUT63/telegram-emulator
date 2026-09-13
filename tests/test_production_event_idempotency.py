from __future__ import annotations

import os
import uuid

import pytest

from production_outbox import DurableProductionPostgresSurvey
from storage_postgres import _TX_EVENT


@pytest.fixture()
def postgres_dsn():
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")
    return dsn


def _cleanup(survey, user_id: str, *event_ids: str) -> None:
    with survey._connect() as conn:
        conn.execute("DELETE FROM outbox_messages WHERE user_id=%s", (user_id,))
        conn.execute("DELETE FROM audit_events WHERE user_id=%s", (user_id,))
        conn.execute("DELETE FROM survey_state WHERE user_id=%s", (user_id,))
        for event_id in event_ids:
            conn.execute("DELETE FROM processed_events WHERE event_id=%s", (event_id,))


def _with_event(event_id: str, fn):
    token = _TX_EVENT.set(event_id)
    try:
        return fn()
    finally:
        _TX_EVENT.reset(token)


def test_provider_event_is_committed_with_state_audit_and_outbox(postgres_dsn):
    user_id = f"pytest-idem-{uuid.uuid4().hex}"
    event_id = f"pytest-provider-event-{uuid.uuid4().hex}"
    survey = DurableProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.start(user_id)
        reply = _with_event(event_id, lambda: survey.handle_message_event(user_id, "help"))
        assert reply

        with survey._connect() as conn:
            processed = conn.execute(
                "SELECT user_id,event_type FROM processed_events WHERE event_id=%s",
                (event_id,),
            ).fetchone()
            audit = conn.execute(
                "SELECT count(*) FROM audit_events WHERE user_id=%s AND event_type='message'",
                (user_id,),
            ).fetchone()[0]
            outbox = conn.execute(
                "SELECT delivery_key,status FROM outbox_messages WHERE delivery_key=%s",
                (f"{event_id}:out:0",),
            ).fetchone()

        assert processed == (user_id, "message")
        assert audit == 1
        assert outbox == (f"{event_id}:out:0", "pending")
    finally:
        _cleanup(survey, user_id, event_id)


def test_same_provider_event_replay_is_noop_and_does_not_create_second_outbox(postgres_dsn):
    user_id = f"pytest-replay-{uuid.uuid4().hex}"
    event_id = f"pytest-replay-event-{uuid.uuid4().hex}"
    survey = DurableProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.start(user_id)
        first = _with_event(event_id, lambda: survey.handle_message_event(user_id, "help"))
        second = _with_event(event_id, lambda: survey.handle_message_event(user_id, "help"))
        assert first
        assert second == ""

        with survey._connect() as conn:
            assert conn.execute(
                "SELECT count(*) FROM processed_events WHERE event_id=%s",
                (event_id,),
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT count(*) FROM audit_events WHERE user_id=%s AND event_type='message'",
                (user_id,),
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT count(*) FROM outbox_messages WHERE delivery_key LIKE %s",
                (f"{event_id}:out:%",),
            ).fetchone()[0] == 1
    finally:
        _cleanup(survey, user_id, event_id)


def test_transaction_failure_rolls_back_processed_event_and_allows_provider_retry(postgres_dsn):
    user_id = f"pytest-retry-after-rollback-{uuid.uuid4().hex}"
    event_id = f"pytest-retry-after-rollback-event-{uuid.uuid4().hex}"
    survey = DurableProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    original_save = survey._save_user
    failures = {"remaining": 1}

    def fail_once(conn, uid):
        if failures["remaining"]:
            failures["remaining"] -= 1
            raise RuntimeError("simulated database commit-path failure")
        return original_save(conn, uid)

    survey._save_user = fail_once
    try:
        survey.start(user_id)
        with pytest.raises(RuntimeError, match="simulated database commit-path failure"):
            _with_event(event_id, lambda: survey.handle_message_event(user_id, "help"))

        with survey._connect() as conn:
            assert conn.execute(
                "SELECT count(*) FROM processed_events WHERE event_id=%s",
                (event_id,),
            ).fetchone()[0] == 0
            assert conn.execute(
                "SELECT count(*) FROM outbox_messages WHERE delivery_key LIKE %s",
                (f"{event_id}:out:%",),
            ).fetchone()[0] == 0

        retry = _with_event(event_id, lambda: survey.handle_message_event(user_id, "help"))
        assert retry

        with survey._connect() as conn:
            assert conn.execute(
                "SELECT count(*) FROM processed_events WHERE event_id=%s",
                (event_id,),
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT count(*) FROM outbox_messages WHERE delivery_key=%s",
                (f"{event_id}:out:0",),
            ).fetchone()[0] == 1
    finally:
        _cleanup(survey, user_id, event_id)


def test_provider_event_id_collision_is_rejected(postgres_dsn):
    user_id = f"pytest-collision-{uuid.uuid4().hex}"
    event_id = f"pytest-collision-event-{uuid.uuid4().hex}"
    survey = DurableProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.start(user_id)
        assert _with_event(event_id, lambda: survey.handle_message_event(user_id, "help"))

        with pytest.raises(RuntimeError, match="event_id collision"):
            _with_event(event_id, lambda: survey.handle_navigation_event(user_id, "map", []))

        with survey._connect() as conn:
            row = conn.execute(
                "SELECT event_type FROM processed_events WHERE event_id=%s",
                (event_id,),
            ).fetchone()
            assert row == ("message",)
    finally:
        _cleanup(survey, user_id, event_id)

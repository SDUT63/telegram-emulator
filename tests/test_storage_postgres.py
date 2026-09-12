from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from production_storage import ProductionPostgresSurvey, TransactionalPersistentSeen
from storage_postgres import _TX_EVENT


@pytest.fixture()
def postgres_dsn():
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")
    return dsn


def _cleanup(survey, user_id, *event_ids):
    with survey._connect() as conn:
        conn.execute("DELETE FROM survey_state WHERE user_id=%s", (user_id,))
        conn.execute("DELETE FROM audit_events WHERE user_id=%s", (user_id,))
        for event_id in event_ids:
            conn.execute("DELETE FROM processed_events WHERE event_id=%s", (event_id,))
            conn.execute("DELETE FROM event_leases WHERE event_id=%s", (event_id,))


def test_postgres_survey_round_trip(postgres_dsn):
    user_id = f"pytest-user-{uuid.uuid4()}"
    survey = ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.state[user_id] = {"step": 2, "answers": {"name": "pytest"}, "finished": None}
        survey.save()
        restored = ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
        assert restored.state[user_id]["step"] == 2
        assert restored.state[user_id]["answers"]["name"] == "pytest"
        del restored.state[user_id]
        restored.save()
        assert user_id not in ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False).state
    finally:
        _cleanup(survey, user_id)


def test_postgres_bot_started_does_not_reset_existing_case(postgres_dsn):
    user_id = f"pytest-start-{uuid.uuid4()}"
    survey = ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        first = survey.start(user_id)
        assert first
        survey.state[user_id]["answers"]["marker"] = "must-survive"
        survey.save()
        second = survey.start(user_id)
        assert "must-survive" in survey.state[user_id]["answers"]
        assert second
    finally:
        _cleanup(survey, user_id)


def test_postgres_bot_started_survives_new_process_instance(postgres_dsn):
    user_id = f"pytest-restart-{uuid.uuid4()}"
    first = ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        first.start(user_id)
        first.state[user_id]["answers"]["restart_marker"] = "persisted"
        first.save()
        second = ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
        response = second.start(user_id)
        assert response
        assert second.state[user_id]["answers"]["restart_marker"] == "persisted"
    finally:
        _cleanup(first, user_id)


def test_postgres_existing_legacy_row_is_not_reset_on_start(postgres_dsn):
    user_id = f"pytest-legacy-start-{uuid.uuid4()}"
    survey = ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.state[user_id] = {
            "step": 3,
            "answers": {"legacy_marker": "must-survive"},
            "finished": None,
            "consent": None,
        }
        survey.save()
        response = survey.start(user_id)
        assert response
        assert survey.state[user_id]["answers"]["legacy_marker"] == "must-survive"
        assert survey.state[user_id]["step"] == 3
        assert survey.state[user_id]["consent"] is None
    finally:
        _cleanup(survey, user_id)


def test_postgres_event_is_atomic_and_idempotent(postgres_dsn):
    user_id = f"pytest-atomic-{uuid.uuid4()}"
    event_id = f"pytest-atomic-event-{uuid.uuid4()}"
    survey = ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.start(user_id)
        seen = TransactionalPersistentSeen()
        assert seen.fresh(event_id) is True
        first = survey.handle(user_id, "help")
        assert first
        assert seen.fresh(event_id) is True
        assert survey.handle(user_id, "help") == ""
        with survey._connect() as conn:
            assert conn.execute("SELECT count(*) FROM processed_events WHERE event_id=%s", (event_id,)).fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM audit_events WHERE user_id=%s AND event_type='message'", (user_id,)).fetchone()[0] == 1
    finally:
        _cleanup(survey, user_id, event_id)


def test_postgres_event_claim_rolls_back_on_failure(postgres_dsn):
    user_id = f"pytest-rollback-{uuid.uuid4()}"
    event_id = f"pytest-rollback-event-{uuid.uuid4()}"
    survey = ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.start(user_id)
        seen = TransactionalPersistentSeen()
        assert seen.fresh(event_id) is True
        with pytest.raises(RuntimeError):
            survey._mutate(user_id, "message", {"kind": "message"}, lambda: (_ for _ in ()).throw(RuntimeError("simulated handler crash")), "")
        with survey._connect() as conn:
            assert conn.execute("SELECT count(*) FROM processed_events WHERE event_id=%s", (event_id,)).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM audit_events WHERE user_id=%s AND event_type='message'", (user_id,)).fetchone()[0] == 0
        assert seen.fresh(event_id) is True
    finally:
        _cleanup(survey, user_id, event_id)


def test_postgres_event_id_collision_is_rejected(postgres_dsn):
    user_id = f"pytest-collision-{uuid.uuid4()}"
    event_id = f"pytest-collision-event-{uuid.uuid4()}"
    survey = ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.start(user_id)
        seen = TransactionalPersistentSeen()
        assert seen.fresh(event_id)
        assert survey.handle(user_id, "help")
        assert seen.fresh(event_id)
        with pytest.raises(RuntimeError, match="event_id collision"):
            survey._mutate(user_id, "callback", {"action": "different"}, lambda: "must-not-run", "")
        with survey._connect() as conn:
            row = conn.execute("SELECT event_type,event_hash FROM processed_events WHERE event_id=%s", (event_id,)).fetchone()
            assert row is not None
            assert row[0] == "message"
    finally:
        _cleanup(survey, user_id, event_id)


def test_postgres_missing_event_id_still_persists_transactionally(postgres_dsn):
    user_id = f"pytest-no-mid-{uuid.uuid4()}"
    survey = ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.start(user_id)
        seen = TransactionalPersistentSeen()
        assert seen.fresh(None) is True
        assert survey.handle(user_id, "help")
        with survey._connect() as conn:
            row = conn.execute("SELECT count(*) FROM processed_events WHERE user_id=%s AND event_type='message'", (user_id,)).fetchone()
            assert row[0] == 1
    finally:
        _cleanup(survey, user_id)


def test_postgres_direct_transactional_survey_handle_uses_atomic_path(postgres_dsn):
    from storage_postgres import TransactionalPostgresSurvey

    user_id = f"pytest-direct-{uuid.uuid4()}"
    event_id = f"pytest-direct-event-{uuid.uuid4()}"
    survey = TransactionalPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.start(user_id)
        seen = TransactionalPersistentSeen()
        assert seen.fresh(event_id)
        assert survey.handle(user_id, "help")
        with survey._connect() as conn:
            assert conn.execute("SELECT count(*) FROM processed_events WHERE event_id=%s", (event_id,)).fetchone()[0] == 1
    finally:
        _cleanup(survey, user_id, event_id)


def test_postgres_follow_up_events_are_distinct_and_idempotent(postgres_dsn):
    user_id = f"pytest-followup-{uuid.uuid4()}"
    event_id = f"pytest-followup-event-{uuid.uuid4()}"
    survey = ProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.start(user_id)
        seen = TransactionalPersistentSeen()
        assert seen.fresh(event_id)
        survey.handle(user_id, "help")
        survey.note_message(user_id, "дополнение")
        survey.note_message(user_id, "дополнение")
        with survey._connect() as conn:
            rows = conn.execute("SELECT event_type, count(*) FROM processed_events WHERE user_id=%s GROUP BY event_type", (user_id,)).fetchall()
            event_types = {row[0]: row[1] for row in rows}
        assert event_types.get("message") == 1
        assert event_types.get("message_attachment") == 1
    finally:
        _cleanup(survey, user_id, event_id, f"{event_id}:message-note")


def test_production_survey_serializes_shared_state_mutations(postgres_dsn):
    from production_outbox import DurableProductionPostgresSurvey

    survey = DurableProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    users = [f"pytest-concurrent-{uuid.uuid4().hex}" for _ in range(2)]
    events = [f"pytest-concurrent-event-{uuid.uuid4().hex}" for _ in range(2)]
    start_barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = 0
    max_active = 0
    errors: list[BaseException] = []

    def worker(user_id: str, event_id: str) -> None:
        nonlocal active, max_active
        token = _TX_EVENT.set(event_id)
        try:
            start_barrier.wait(timeout=5)

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
        except BaseException as exc:  # pragma: no cover
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
                "SELECT user_id,event_json->>'event_id' FROM audit_events "
                "WHERE user_id = ANY(%s) AND event_type='concurrency_test'",
                (users,),
            ).fetchall()
            state_rows = conn.execute(
                "SELECT user_id,state_json->>'concurrency_marker' FROM survey_state WHERE user_id = ANY(%s)",
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

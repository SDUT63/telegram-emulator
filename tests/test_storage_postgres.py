from __future__ import annotations

import os
import uuid

import pytest

from production_storage import ProductionPostgresSurvey, TransactionalPersistentSeen


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

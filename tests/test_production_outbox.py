from __future__ import annotations

import os
import uuid

import pytest

from outbox_postgres import PostgresOutbox, delivery_key
from production_outbox import DurableProductionPostgresSurvey
from storage_postgres import _TX_EVENT


pytestmark = pytest.mark.skipif(
    not os.getenv("SDUT_DATABASE_URL"),
    reason="SDUT_DATABASE_URL is required for PostgreSQL integration tests",
)


def test_survey_reply_and_outbox_commit_together():
    survey = DurableProductionPostgresSurvey()
    user_id = f"outbox-user-{uuid.uuid4().hex}"
    event_id = f"outbox-event-{uuid.uuid4().hex}"
    token = _TX_EVENT.set(event_id)
    try:
        reply = survey.handle(user_id, "hello")
    finally:
        _TX_EVENT.reset(token)

    assert reply
    queue = PostgresOutbox()
    with queue._connect(queue.db_url) as conn:
        row = conn.execute(
            "SELECT delivery_key,user_id,payload,status FROM outbox_messages WHERE delivery_key=%s",
            (delivery_key(event_id),),
        ).fetchone()
        state = conn.execute("SELECT state_json FROM survey_state WHERE user_id=%s", (user_id,)).fetchone()

    assert row is not None
    assert row["user_id"] == user_id
    assert row["payload"]["text"] == reply
    assert isinstance(row["payload"]["keyboard_rows"], list)
    assert row["status"] == "pending"
    assert state is not None


def test_message_with_attachment_is_atomic_and_queues_two_intents():
    survey = DurableProductionPostgresSurvey()
    user_id = f"outbox-atomic-file-{uuid.uuid4().hex}"
    event_id = f"outbox-atomic-file-event-{uuid.uuid4().hex}"
    files = [{"kind": "file", "name": "referral.pdf", "url": "https://max.invalid/file", "size": 1234}]

    token = _TX_EVENT.set(event_id)
    try:
        result = survey.handle_message_event(user_id, "hello", files)
    finally:
        _TX_EVENT.reset(token)

    assert result
    queue = PostgresOutbox()
    with queue._connect(queue.db_url) as conn:
        rows = conn.execute(
            "SELECT delivery_key,payload,status FROM outbox_messages "
            "WHERE delivery_key IN (%s,%s) ORDER BY delivery_key",
            (delivery_key(event_id), delivery_key(event_id, ordinal=1)),
        ).fetchall()
        processed = conn.execute(
            "SELECT event_type,event_hash FROM processed_events WHERE event_id=%s", (event_id,)
        ).fetchone()
        state = conn.execute("SELECT state_json FROM survey_state WHERE user_id=%s", (user_id,)).fetchone()

    assert len(rows) == 2
    by_key = {row["delivery_key"]: row for row in rows}
    assert by_key[delivery_key(event_id)]["payload"]["text"] == result
    assert "Файл получил" in by_key[delivery_key(event_id, ordinal=1)]["payload"]["text"]
    assert all(row["status"] == "pending" for row in rows)
    assert processed is not None
    assert processed["event_type"] == "message"
    assert processed["event_hash"]
    assert state is not None

    with queue._connect(queue.db_url) as conn:
        audit = conn.execute(
            "SELECT event_json FROM audit_events WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user_id,)
        ).fetchone()
    assert audit is not None
    assert "fingerprint" not in audit["event_json"]


def test_message_event_collision_detects_changed_attachment():
    survey = DurableProductionPostgresSurvey()
    user_id = f"outbox-collision-file-{uuid.uuid4().hex}"
    event_id = f"outbox-collision-file-event-{uuid.uuid4().hex}"
    first_files = [{"kind": "file", "name": "one.pdf", "url": "https://max.invalid/one", "size": 10}]
    second_files = [{"kind": "file", "name": "two.pdf", "url": "https://max.invalid/two", "size": 20}]

    token = _TX_EVENT.set(event_id)
    try:
        first = survey.handle_message_event(user_id, "same text", first_files)
        with pytest.raises(RuntimeError, match="event_id collision"):
            survey.handle_message_event(user_id, "same text", second_files)
    finally:
        _TX_EVENT.reset(token)

    assert first


def test_callback_transition_and_reply_are_one_event():
    survey = DurableProductionPostgresSurvey()
    user_id = f"outbox-callback-{uuid.uuid4().hex}"
    start_event = f"outbox-callback-start-{uuid.uuid4().hex}"
    callback_event = f"outbox-callback-event-{uuid.uuid4().hex}"

    token = _TX_EVENT.set(start_event)
    try:
        start_reply = survey.start_event(user_id)
    finally:
        _TX_EVENT.reset(token)
    assert start_reply

    token = _TX_EVENT.set(callback_event)
    try:
        reply = survey.handle_callback_event(user_id, "c", ["y"])
    finally:
        _TX_EVENT.reset(token)

    assert reply
    queue = PostgresOutbox()
    with queue._connect(queue.db_url) as conn:
        processed = conn.execute(
            "SELECT event_type FROM processed_events WHERE event_id=%s", (callback_event,)
        ).fetchone()
        row = conn.execute(
            "SELECT payload,status FROM outbox_messages WHERE delivery_key=%s",
            (delivery_key(callback_event),),
        ).fetchone()
        state = conn.execute("SELECT state_json FROM survey_state WHERE user_id=%s", (user_id,)).fetchone()

    assert processed["event_type"] == "callback"
    assert row["status"] == "pending"
    assert row["payload"]["text"] == reply
    assert state["state_json"]["consent"]["at"]


def test_navigation_event_is_durable_and_does_not_change_answers():
    survey = DurableProductionPostgresSurvey()
    user_id = f"outbox-navigation-{uuid.uuid4().hex}"
    start_event = f"outbox-navigation-start-{uuid.uuid4().hex}"
    nav_event = f"outbox-navigation-event-{uuid.uuid4().hex}"

    token = _TX_EVENT.set(start_event)
    try:
        survey.start_event(user_id)
    finally:
        _TX_EVENT.reset(token)

    before = dict((survey.state.get(user_id) or {}).get("answers") or {})
    token = _TX_EVENT.set(nav_event)
    try:
        reply = survey.handle_navigation_event(user_id, "map", [])
    finally:
        _TX_EVENT.reset(token)

    assert reply
    queue = PostgresOutbox()
    with queue._connect(queue.db_url) as conn:
        row = conn.execute(
            "SELECT payload,status FROM outbox_messages WHERE delivery_key=%s", (delivery_key(nav_event),)
        ).fetchone()
        processed = conn.execute(
            "SELECT event_type FROM processed_events WHERE event_id=%s", (nav_event,)
        ).fetchone()
        state = conn.execute("SELECT state_json FROM survey_state WHERE user_id=%s", (user_id,)).fetchone()

    assert row["status"] == "pending"
    assert row["payload"]["text"] == reply
    assert processed["event_type"] == "navigation"
    assert state["state_json"]["answers"] == before


def test_survey_failure_rolls_back_outbox_and_state():
    survey = DurableProductionPostgresSurvey()
    user_id = f"outbox-failure-{uuid.uuid4().hex}"
    event_id = f"outbox-failure-event-{uuid.uuid4().hex}"
    token = _TX_EVENT.set(event_id)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            survey._mutate(
                user_id,
                "test_failure",
                {"kind": "test_failure"},
                lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                "",
            )
    finally:
        _TX_EVENT.reset(token)

    queue = PostgresOutbox()
    with queue._connect(queue.db_url) as conn:
        outbox = conn.execute("SELECT 1 FROM outbox_messages WHERE delivery_key=%s", (delivery_key(event_id),)).fetchone()
        state = conn.execute("SELECT 1 FROM survey_state WHERE user_id=%s", (user_id,)).fetchone()

    assert outbox is None
    assert state is None


def test_duplicate_event_does_not_create_second_reply():
    survey = DurableProductionPostgresSurvey()
    user_id = f"outbox-duplicate-{uuid.uuid4().hex}"
    event_id = f"outbox-duplicate-event-{uuid.uuid4().hex}"

    token = _TX_EVENT.set(event_id)
    try:
        first = survey.handle(user_id, "hello")
        second = survey.handle(user_id, "hello")
    finally:
        _TX_EVENT.reset(token)

    assert first
    assert second == ""

    queue = PostgresOutbox()
    with queue._connect(queue.db_url) as conn:
        rows = conn.execute("SELECT COUNT(*) AS n FROM outbox_messages WHERE delivery_key=%s", (delivery_key(event_id),)).fetchone()
    assert rows["n"] == 1

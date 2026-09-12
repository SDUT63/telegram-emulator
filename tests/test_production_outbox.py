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
        state = conn.execute(
            "SELECT state_json FROM survey_state WHERE user_id=%s",
            (user_id,),
        ).fetchone()

    assert row is not None
    assert row["user_id"] == user_id
    assert row["payload"]["text"] == reply
    assert isinstance(row["payload"]["keyboard_rows"], list)
    assert row["status"] == "pending"
    assert state is not None


def test_attachment_ack_is_durable_and_has_its_own_delivery_key():
    """Legacy separate attachment path remains covered until dispatcher migration."""
    survey = DurableProductionPostgresSurvey()
    user_id = f"outbox-file-{uuid.uuid4().hex}"
    parent_event = f"outbox-file-event-{uuid.uuid4().hex}"
    child_event = f"{parent_event}:message-note"

    token = _TX_EVENT.set(parent_event)
    try:
        reply = survey.handle(user_id, "hello")
        assert reply
        survey.note_message(
            user_id,
            "hello",
            [{"kind": "file", "name": "referral.pdf", "url": "https://max.invalid/file"}],
        )
    finally:
        _TX_EVENT.reset(token)

    queue = PostgresOutbox()
    child_key = delivery_key(child_event, ordinal=1)
    with queue._connect(queue.db_url) as conn:
        row = conn.execute(
            "SELECT delivery_key,payload,status FROM outbox_messages WHERE delivery_key=%s",
            (child_key,),
        ).fetchone()
        note = conn.execute(
            "SELECT event_id FROM processed_events WHERE event_id=%s",
            (child_event,),
        ).fetchone()

    assert row is not None
    assert row["payload"]["kind"] == "max_text"
    assert "Файл получил" in row["payload"]["text"]
    assert row["status"] == "pending"
    assert note is not None


def test_message_with_attachment_is_atomic_and_queues_two_intents():
    """One inbound event commits state, metadata and both outbound intents."""
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
            "SELECT event_type,event_hash FROM processed_events WHERE event_id=%s",
            (event_id,),
        ).fetchone()
        state = conn.execute(
            "SELECT state_json FROM survey_state WHERE user_id=%s",
            (user_id,),
        ).fetchone()

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
            "SELECT event_json FROM audit_events WHERE user_id=%s ORDER BY id DESC LIMIT 1",
            (user_id,),
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
        outbox = conn.execute(
            "SELECT 1 FROM outbox_messages WHERE delivery_key=%s",
            (delivery_key(event_id),),
        ).fetchone()
        state = conn.execute(
            "SELECT 1 FROM survey_state WHERE user_id=%s",
            (user_id,),
        ).fetchone()

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
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM outbox_messages WHERE delivery_key=%s",
            (delivery_key(event_id),),
        ).fetchone()
    assert rows["n"] == 1

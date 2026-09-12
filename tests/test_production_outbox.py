from __future__ import annotations

import os
import uuid

import pytest

from outbox_postgres import PostgresOutbox, delivery_key
from production_outbox import (
    DurableProductionPostgresSurvey,
    consume_direct_send_suppression,
)
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
        assert consume_direct_send_suppression() is True
        assert consume_direct_send_suppression() is False
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
    survey = DurableProductionPostgresSurvey()
    user_id = f"outbox-file-{uuid.uuid4().hex}"
    parent_event = f"outbox-file-event-{uuid.uuid4().hex}"

    token = _TX_EVENT.set(parent_event)
    try:
        reply = survey.handle(user_id, "hello")
        assert reply
        assert consume_direct_send_suppression() is True

        survey.note_message(
            user_id,
            "hello",
            [{"kind": "file", "name": "referral.pdf", "url": "https://max.invalid/file"}],
        )
        assert consume_direct_send_suppression() is True
        assert consume_direct_send_suppression() is False
    finally:
        _TX_EVENT.reset(token)

    queue = PostgresOutbox()
    child_key = delivery_key(f"{parent_event}:message-note")
    with queue._connect(queue.db_url) as conn:
        row = conn.execute(
            "SELECT delivery_key,payload,status FROM outbox_messages WHERE delivery_key=%s",
            (child_key,),
        ).fetchone()
        note = conn.execute(
            "SELECT event_id FROM processed_events WHERE event_id=%s",
            (f"{parent_event}:message-note",),
        ).fetchone()

    assert row is not None
    assert row["payload"]["kind"] == "max_text"
    assert "Файл получил" in row["payload"]["text"]
    assert row["status"] == "pending"
    assert note is not None


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
        consume_direct_send_suppression()
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

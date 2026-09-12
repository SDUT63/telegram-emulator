from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from durable_outbox_worker import deliver_once
from outbox_postgres import PostgresOutbox
from production_outbox import DurableProductionPostgresSurvey
from storage_postgres import _TX_EVENT


@pytest.fixture()
def postgres_dsn():
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")
    return dsn


class FakeMaxBot:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls: list[dict] = []

    async def send_message(self, **kwargs):
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("simulated MAX network failure")
        self.calls.append(kwargs)


def _cleanup(survey, user_id: str, event_ids: list[str]) -> None:
    with survey._connect() as conn:
        conn.execute("DELETE FROM outbox_messages WHERE user_id=%s", (user_id,))
        conn.execute("DELETE FROM survey_state WHERE user_id=%s", (user_id,))
        conn.execute("DELETE FROM audit_events WHERE user_id=%s", (user_id,))
        for event_id in event_ids:
            conn.execute("DELETE FROM processed_events WHERE event_id=%s", (event_id,))


def test_production_e2e_state_to_max_is_durable(postgres_dsn):
    user_id = f"pytest-e2e-{uuid.uuid4().hex}"
    event_id = f"pytest-e2e-event-{uuid.uuid4().hex}"
    survey = DurableProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.start(user_id)
        token = _TX_EVENT.set(event_id)
        try:
            reply = survey.handle(user_id, "help")
        finally:
            _TX_EVENT.reset(token)

        assert reply
        with survey._connect() as conn:
            row = conn.execute(
                "SELECT status,payload->>'kind',payload->>'text' FROM outbox_messages WHERE delivery_key=%s",
                (f"{event_id}:out:0",),
            ).fetchone()
        assert row is not None
        assert row[0] == "pending"
        assert row[1] == "max_text"
        assert row[2] == reply

        bot = FakeMaxBot()
        assert asyncio.run(deliver_once(bot, queue=PostgresOutbox(db_url=postgres_dsn))) == 1
        assert len(bot.calls) == 1
        assert bot.calls[0]["text"] == reply

        with survey._connect() as conn:
            status = conn.execute(
                "SELECT status FROM outbox_messages WHERE delivery_key=%s",
                (f"{event_id}:out:0",),
            ).fetchone()[0]
        assert status == "sent"
    finally:
        _cleanup(survey, user_id, [event_id])


def test_production_e2e_network_failure_is_retriable(postgres_dsn):
    user_id = f"pytest-e2e-retry-{uuid.uuid4().hex}"
    event_id = f"pytest-e2e-retry-event-{uuid.uuid4().hex}"
    survey = DurableProductionPostgresSurvey(db_url=postgres_dsn, list_options=False)
    try:
        survey.start(user_id)
        token = _TX_EVENT.set(event_id)
        try:
            assert survey.handle(user_id, "help")
        finally:
            _TX_EVENT.reset(token)

        queue = PostgresOutbox(db_url=postgres_dsn)
        failing_bot = FakeMaxBot(fail_first=True)
        assert asyncio.run(deliver_once(failing_bot, queue=queue)) == 1
        assert failing_bot.calls == []

        with survey._connect() as conn:
            row = conn.execute(
                "SELECT status,attempts FROM outbox_messages WHERE delivery_key=%s",
                (f"{event_id}:out:0",),
            ).fetchone()
            assert row[0] == "pending"
            assert row[1] == 1
            conn.execute(
                "UPDATE outbox_messages SET available_at=CURRENT_TIMESTAMP WHERE delivery_key=%s",
                (f"{event_id}:out:0",),
            )

        success_bot = FakeMaxBot()
        assert asyncio.run(deliver_once(success_bot, queue=queue)) == 1
        assert len(success_bot.calls) == 1

        with survey._connect() as conn:
            status = conn.execute(
                "SELECT status FROM outbox_messages WHERE delivery_key=%s",
                (f"{event_id}:out:0",),
            ).fetchone()[0]
        assert status == "sent"
    finally:
        _cleanup(survey, user_id, [event_id])

"""Удаление данных обязано заканчиваться ответом человеку.

Человек попросил стереть данные; по ст. 14 и 21 ФЗ-152 оператор обязан
сообщить, что требование исполнено. При этом подтверждение не должно
становиться лазейкой: всё остальное для удалённого пользователя
по-прежнему отклоняется.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from chatbot_survey import ERASED
from durable_outbox_worker import deliver_once
from outbox_postgres import PostgresOutbox
from production_privacy import ProductionPrivacySurvey
from storage_postgres import _TX_EVENT


@pytest.fixture()
def postgres_dsn():
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")
    return dsn


class FakeMaxBot:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)


def _event(event_id: str, fn):
    token = _TX_EVENT.set(event_id)
    try:
        return fn()
    finally:
        _TX_EVENT.reset(token)


def _purge(survey, user_id: str) -> None:
    with survey._connect() as conn:
        for table in ("outbox_messages", "audit_events", "processed_events", "survey_state", "operator_cases"):
            conn.execute(f"DELETE FROM {table} WHERE user_id=%s", (user_id,))
        conn.execute("DELETE FROM deleted_users WHERE user_id=%s", (user_id,))
        conn.commit()


def _drain(bot, queue) -> int:
    delivered = 0
    while asyncio.run(deliver_once(bot, queue=queue)):
        delivered += 1
    return delivered


def test_deletion_confirms_to_the_user_and_leaves_no_personal_data(postgres_dsn):
    survey = ProductionPrivacySurvey(db_url=postgres_dsn, list_options=False)
    queue = PostgresOutbox(db_url=postgres_dsn)
    user_id = str(abs(hash(uuid.uuid4().hex)) % 10**9)
    try:
        _event(f"conf-start-{uuid.uuid4().hex}", lambda: survey.start_event(user_id))
        _event(f"conf-consent-{uuid.uuid4().hex}", lambda: survey.handle_callback_event(user_id, "c", ["y"]))

        bot = FakeMaxBot()
        _drain(bot, queue)
        before = len(bot.calls)

        _event(
            f"conf-delete-{uuid.uuid4().hex}",
            lambda: survey._mutate(user_id, "message", {"kind": "delete"}, lambda: survey.delete_user(user_id), None),
        )

        # Exactly one message is owed, and it is the confirmation.
        assert _drain(bot, queue) == 1
        assert len(bot.calls) == before + 1
        assert bot.calls[-1]["text"] == ERASED

        with survey._connect() as conn:
            for table in ("survey_state", "audit_events", "processed_events", "operator_cases"):
                assert conn.execute(f"SELECT count(*) FROM {table} WHERE user_id=%s", (user_id,)).fetchone()[0] == 0
            # Delivery redacts identity on the farewell row like any other.
            assert conn.execute("SELECT count(*) FROM outbox_messages WHERE user_id=%s", (user_id,)).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM deleted_users WHERE user_id=%s", (user_id,)).fetchone()[0] == 1
    finally:
        _purge(survey, user_id)


def test_confirmation_is_not_a_bypass_for_ordinary_messages(postgres_dsn):
    survey = ProductionPrivacySurvey(db_url=postgres_dsn, list_options=False)
    queue = PostgresOutbox(db_url=postgres_dsn)
    user_id = str(abs(hash(uuid.uuid4().hex)) % 10**9)
    try:
        _event(f"bypass-start-{uuid.uuid4().hex}", lambda: survey.start_event(user_id))
        _event(
            f"bypass-delete-{uuid.uuid4().hex}",
            lambda: survey._mutate(user_id, "message", {"kind": "delete"}, lambda: survey.delete_user(user_id), None),
        )

        with pytest.raises(RuntimeError, match="deleted user"):
            queue.enqueue(
                delivery_key=f"bypass-probe-{uuid.uuid4().hex}",
                user_id=user_id,
                payload={"kind": "max_text", "text": "должно быть отклонено"},
            )
    finally:
        _purge(survey, user_id)

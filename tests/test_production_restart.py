"""Боевое «начать заново»: тревога первого блока остаётся в карточке.

Боевой restart_after_consent сохраняет имя, телефон и адрес и заново
задаёт только подробную часть. Тревоги он при этом обнулял целиком —
та же ошибка, что была в continue_detailed: «Острое состояние: тяжело
дышит» исчезала из карточки, а CRM поднимает обращения наверх именно
по тревогам.
"""
from __future__ import annotations

import os
import uuid

import pytest

import walk
from production_outbox import DurableProductionPostgresSurvey
from storage_postgres import _TX_EVENT
from survey_questions import CHECKPOINT_ID

pytestmark = pytest.mark.skipif(
    not os.getenv("SDUT_DATABASE_URL"),
    reason="SDUT_DATABASE_URL is required for PostgreSQL integration tests",
)


def _событие(fn):
    token = _TX_EVENT.set(f"restart-{uuid.uuid4().hex}")
    try:
        return fn()
    finally:
        _TX_EVENT.reset(token)


def test_тревога_первого_блока_остаётся_после_начать_заново():
    survey = DurableProductionPostgresSurvey(list_options=False)
    uid = str(900000000000 + uuid.uuid4().int % 999999999)
    try:
        _событие(lambda: survey.start_event(uid))
        _событие(lambda: survey.handle_callback_event(uid, "c", ["y"]))
        for _ in range(walk.ПРЕДЕЛ):
            место = survey.current(uid)
            assert место is not None, "анкета кончилась раньше контрольной точки"
            _, вопрос = место
            if вопрос["id"] == CHECKPOINT_ID:
                break
            ответы = {}
            if вопрос["id"] == "flags":
                ответы["flags"] = [вопрос["options"].index("Тяжело дышит") + 1]
            _событие(lambda: walk.ответить(survey, uid, вопрос, ответы))

        до = list(survey.user_state(uid).get("alerts") or [])
        assert any(t.startswith("Острое состояние") for t in до), до

        _событие(lambda: survey.restart_after_consent(uid))

        assert survey.user_state(uid).get("alerts") == до
    finally:
        with survey._connect() as conn:
            for таблица in ("outbox_messages", "audit_events", "processed_events", "survey_state"):
                conn.execute(f"DELETE FROM {таблица} WHERE user_id=%s", (uid,))
            conn.commit()

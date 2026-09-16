"""Множественный выбор в production: тревожные признаки должны отмечаться.

Клавиатура шлёт t:<шаг>:<вариант>, и до этих проверок канонический диспетчер
такое действие просто отбрасывал. Человек нажимал «Пролежни», «Тяжело дышит»
— и не происходило ничего. Именно этот вопрос говорит координатору, с чем
он едет.
"""
from __future__ import annotations

import os
import uuid

import pytest

import walk
from production_outbox import DurableProductionPostgresSurvey
from storage_postgres import _TX_EVENT

pytestmark = pytest.mark.skipif(
    not os.getenv("SDUT_DATABASE_URL"),
    reason="SDUT_DATABASE_URL is required for PostgreSQL integration tests",
)


def _событие(fn):
    token = _TX_EVENT.set(f"multi-{uuid.uuid4().hex}")
    try:
        return fn()
    finally:
        _TX_EVENT.reset(token)


def _до_множественного(survey, uid):
    for _ in range(40):
        место = survey.current(uid)
        if not место:
            break
        шаг, вопрос = место
        if вопрос.get("multi"):
            return шаг, вопрос
        _событие(lambda: walk.ответить(survey, uid, вопрос))
    raise AssertionError("множественный вопрос не встретился")


def _очистить(survey, uid):
    with survey._connect() as conn:
        for таблица in ("outbox_messages", "audit_events", "processed_events", "survey_state"):
            conn.execute(f"DELETE FROM {таблица} WHERE user_id=%s", (uid,))
        conn.commit()


@pytest.fixture()
def анкета():
    survey = DurableProductionPostgresSurvey(list_options=False)
    uid = str(abs(hash(uuid.uuid4().hex)) % 10**9)
    _событие(lambda: survey.start_event(uid))
    _событие(lambda: survey.handle_callback_event(uid, "c", ["y"]))
    yield survey, uid
    _очистить(survey, uid)


def test_нажатие_варианта_отмечает_его(анкета):
    survey, uid = анкета
    шаг, вопрос = _до_множественного(survey, uid)

    ответ = _событие(lambda: survey.handle_callback_event(uid, "t", [str(шаг), "0"]))

    assert survey.picked(uid, шаг) == [0], "вариант не отметился"
    assert ответ, "человек должен увидеть обновлённый экран, а не тишину"


def test_повторное_нажатие_снимает_отметку(анкета):
    survey, uid = анкета
    шаг, _ = _до_множественного(survey, uid)

    _событие(lambda: survey.handle_callback_event(uid, "t", [str(шаг), "0"]))
    _событие(lambda: survey.handle_callback_event(uid, "t", [str(шаг), "2"]))
    assert set(survey.picked(uid, шаг)) == {0, 2}

    _событие(lambda: survey.handle_callback_event(uid, "t", [str(шаг), "0"]))
    assert survey.picked(uid, шаг) == [2]


def test_готово_записывает_все_отмеченные(анкета):
    survey, uid = анкета
    шаг, вопрос = _до_множественного(survey, uid)

    _событие(lambda: survey.handle_callback_event(uid, "t", [str(шаг), "0"]))
    _событие(lambda: survey.handle_callback_event(uid, "t", [str(шаг), "2"]))
    _событие(lambda: survey.handle_callback_event(uid, "d", [str(шаг)]))

    записано = (survey.state.get(uid) or {}).get("answers", {}).get(вопрос["id"]) or ""
    assert вопрос["options"][0] in записано
    assert вопрос["options"][2] in записано


def test_устаревшее_нажатие_не_меняет_прошлый_вопрос(анкета):
    survey, uid = анкета
    шаг, _ = _до_множественного(survey, uid)

    # Отмечаем и подтверждаем — только тогда анкета уходит вперёд и кнопки
    # этого экрана становятся прошлыми.
    _событие(lambda: survey.handle_callback_event(uid, "t", [str(шаг), "0"]))
    _событие(lambda: survey.handle_callback_event(uid, "d", [str(шаг)]))
    assert survey.current(uid) is None or survey.current(uid)[0] != шаг

    ответ = _событие(lambda: survey.handle_callback_event(uid, "t", [str(шаг), "1"]))

    assert ответ == "", "кнопка с прошлого экрана не должна ничего менять"
    assert survey.picked(uid, шаг) == []


def test_вариант_за_границей_списка_отклоняется(анкета):
    survey, uid = анкета
    шаг, вопрос = _до_множественного(survey, uid)

    ответ = _событие(
        lambda: survey.handle_callback_event(uid, "t", [str(шаг), str(len(вопрос["options"]) + 5)])
    )

    assert ответ == ""
    assert survey.picked(uid, шаг) == []

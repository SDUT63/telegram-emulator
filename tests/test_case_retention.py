"""Срок хранения анкет: часть 7 статьи 5 ФЗ-152.

Хранить персональные данные дольше, чем требует цель обработки, нельзя.
Цель здесь — передать обращение координатору и организовать помощь;
когда обращение закрыто, держать имя, телефон, адрес и сведения
о здоровье больше нечем оправдать.

До этого удалялось только то, о чём человек просил сам. Сами анкеты
лежали бессрочно — то есть срок хранения был «вечно».
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ops"))

import purge_expired_cases as purge
from storage_postgres import _TX_EVENT

pytestmark = pytest.mark.skipif(
    not os.getenv("SDUT_DATABASE_URL"),
    reason="SDUT_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture()
def анкета():
    from production_outbox import DurableProductionPostgresSurvey

    return DurableProductionPostgresSurvey()


def _соединение():
    import psycopg

    return psycopg.connect(os.environ["SDUT_DATABASE_URL"])


def _обращение(анкета, состарить_на_дней: int = 0) -> str:
    u = f"срок-{uuid.uuid4().hex[:8]}"

    def шаг(текст: str):
        токен = _TX_EVENT.set(f"e-{uuid.uuid4().hex}")
        try:
            анкета.handle(u, текст)
        finally:
            _TX_EVENT.reset(токен)

    шаг("здравствуйте")
    токен = _TX_EVENT.set(f"e-{uuid.uuid4().hex}")
    try:
        анкета.grant_consent(u)
    finally:
        _TX_EVENT.reset(токен)
    for фраза in ("мама", "я дочь", "да знает", "Ольга Петровна", "Нина",
                  "89171234567"):
        шаг(фраза)

    if состарить_на_дней:
        with _соединение() as conn:
            conn.execute(
                "UPDATE survey_state SET updated_at = CURRENT_TIMESTAMP "
                "- make_interval(days => %s) WHERE user_id=%s",
                (состарить_на_дней, u),
            )
            conn.commit()
    return u


def _осталось(user_id: str) -> dict[str, int]:
    итог = {}
    with _соединение() as conn:
        for таблица in ("survey_state", "operator_cases", "outbox_messages",
                        "audit_events"):
            итог[таблица] = conn.execute(
                f"SELECT count(*) FROM {таблица} WHERE user_id=%s", (user_id,)
            ).fetchone()[0]
    return итог


def test_без_назначенного_срока_ничего_не_удаляется(анкета, monkeypatch):
    """Срок хранения назначает служба, а не программа.

    Удалить чужие данные по догадке разработчика хуже, чем не удалить.
    """
    monkeypatch.delenv(purge.ПЕРЕМЕННАЯ, raising=False)
    u = _обращение(анкета, состарить_на_дней=5000)

    assert purge.main([]) == 2, "без срока скрипт не должен считать себя успешным"
    assert _осталось(u)["survey_state"] == 1


def test_просроченная_карточка_стирается_целиком(анкета, monkeypatch):
    monkeypatch.setenv(purge.ПЕРЕМЕННАЯ, "365")
    u = _обращение(анкета, состарить_на_дней=400)

    assert purge.main([]) == 0

    осталось = _осталось(u)
    assert осталось == {"survey_state": 0, "operator_cases": 0,
                        "outbox_messages": 0, "audit_events": 0}, осталось


def test_свежая_карточка_не_трогается(анкета, monkeypatch):
    monkeypatch.setenv(purge.ПЕРЕМЕННАЯ, "365")
    u = _обращение(анкета)

    purge.main([])

    assert _осталось(u)["survey_state"] == 1, "удалена карточка в пределах срока"
    _покой(u)


def test_показать_ничего_не_удаляет(анкета, monkeypatch):
    monkeypatch.setenv(purge.ПЕРЕМЕННАЯ, "365")
    u = _обращение(анкета, состарить_на_дней=400)

    assert purge.main(["--показать"]) == 0
    assert _осталось(u)["survey_state"] == 1, "«показать» удалил карточку"

    purge.main([])


def test_человек_может_обратиться_снова_после_истечения_срока(анкета, monkeypatch):
    """Срок вышел — это не отзыв согласия.

    Тот, кто просил стереть данные, получает отметку в `deleted_users`,
    и запоздавшее сообщение не воскресит его карточку. Здесь другое:
    согласие не отзывали. Если человек придёт снова, он должен пройти
    как новый, а не как заблокированный.
    """
    monkeypatch.setenv(purge.ПЕРЕМЕННАЯ, "365")
    u = _обращение(анкета, состарить_на_дней=400)
    purge.main([])

    with _соединение() as conn:
        отметок = conn.execute(
            "SELECT count(*) FROM deleted_users WHERE user_id=%s", (u,)
        ).fetchone()[0]
    assert отметок == 0, "срок хранения не должен блокировать человека"

    токен = _TX_EVENT.set(f"e-{uuid.uuid4().hex}")
    try:
        ответ = анкета.handle(u, "здравствуйте")
    finally:
        _TX_EVENT.reset(токен)
    assert ответ, "человек, вернувшийся после истечения срока, не получил ответа"
    _покой(u)


def _покой(user_id: str) -> None:
    with _соединение() as conn:
        for таблица in ("survey_state", "operator_cases", "outbox_messages",
                        "audit_events"):
            conn.execute(f"DELETE FROM {таблица} WHERE user_id=%s", (user_id,))
        conn.commit()


def test_срок_проверяется_на_разумность(monkeypatch):
    for плохое in ("ноль", "0", "-5", "99999"):
        monkeypatch.setenv(purge.ПЕРЕМЕННАЯ, плохое)
        with pytest.raises(SystemExit):
            purge.срок_хранения()

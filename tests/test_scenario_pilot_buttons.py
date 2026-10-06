"""Кнопка сценария безопасности в ноутбучном пилоте: z:<сценарий>:<шаг>:<номер>.

Через настоящий обработчик нажатий пилота, как в tests/test_stale_buttons.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

import transparent_max_pilot as pilot
from chatbot_survey import QUESTIONS
from storage_sqlite import SQLiteSurvey

UID = "5674119"


class Бот:
    def __init__(self) -> None:
        self.отправки: list[dict] = []

    async def send_message(self, **kwargs):
        self.отправки.append(kwargs)


class Нажатие:
    def __init__(self, bot: Бот, payload: str) -> None:
        self.bot = bot
        self.callback = type("C", (), {"payload": payload})()

    def get_ids(self):
        return (777, int(UID))

    async def ack(self, **kwargs):
        return None


def _обработчик(survey):
    dispatcher = pilot.build_dispatcher(survey)
    for handler in dispatcher.event_handlers:
        if str(getattr(handler, "update_type", "")) == "message_callback":
            return handler.func_event
    raise AssertionError("обработчик message_callback не найден")


@pytest.fixture()
def пилот(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SDUT_SCENARIOS", "all")
    survey = SQLiteSurvey(db_path=str(tmp_path / "pilot.sqlite3"), list_options=False)
    survey.handle(UID, "здравствуйте")
    survey.grant_consent(UID)
    survey.state[UID]["step"] = len(QUESTIONS)
    survey.state[UID]["finished"] = datetime.now().isoformat(timespec="seconds")
    survey.save()
    return survey


def _нажать(survey, payload):
    бот = Бот()
    asyncio.run(_обработчик(survey)(Нажатие(бот, payload)))
    return бот.отправки


def test_кнопка_сценария_ведёт_дальше_и_сохраняется(пилот, tmp_path):
    пилот.handle(UID, "я на пределе, не справляюсь")
    payload = dict(пилот.кнопки_сценария(UID))["Нет"]
    отправки = _нажать(пилот, payload)
    assert "Что сейчас тяжелее всего" in отправки[-1]["text"]

    # Состояние пережило перезапуск: SQLite — источник правды пилота.
    заново = SQLiteSurvey(db_path=str(tmp_path / "pilot.sqlite3"), list_options=False)
    assert заново.ожидание_сценария(UID)[2] == "need_q"


def test_кнопка_с_прошлого_экрана_показывает_где_мы(пилот):
    пилот.handle(UID, "я на пределе, не справляюсь")
    отправки = _нажать(пилот, "z:P2_overwhelmed:need_q:0")
    assert "Это кнопка с прошлого экрана" in отправки[-1]["text"]
    assert пилот.ожидание_сценария(UID)[2] == "safety_q"

"""Присланный файл нельзя терять.

Справка, выписка, фотография направления — обычная часть разговора в службе
долговременного ухода. Если бот отвечает «напишите ответ текстом», человек
считает, что его не услышали, а оператор файла вообще не увидит.
"""
from __future__ import annotations

import asyncio

import pytest

import transparent_max_pilot as pilot
from max_ui import FILES_TAKEN
from storage_sqlite import SQLiteSurvey


class Ссылка:
    url = "https://max.ru/f/9"


class Файл:
    type = "file"
    filename = "справка.pdf"
    size = 2048
    payload = Ссылка()


class Бот:
    def __init__(self) -> None:
        self.отправки: list[dict] = []

    async def send_message(self, **kwargs):
        self.отправки.append(kwargs)


class Событие:
    def __init__(self, bot: Бот, *, text=None, attachments=()) -> None:
        self.bot = bot
        тело = type("Тело", (), {"text": text, "attachments": list(attachments)})()
        self.message = type("Сообщение", (), {"body": тело})()

    def get_ids(self):
        return (777, 5674119)


def _обработчик_сообщений(survey):
    """Настоящий обработчик из диспетчера, а не его копия в тесте."""
    dispatcher = pilot.build_dispatcher(survey)
    for handler in dispatcher.event_handlers:
        if str(getattr(handler, "update_type", "")) == "message_created":
            return handler.func_event
    raise AssertionError("обработчик message_created не найден")


@pytest.fixture()
def анкета(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    survey = SQLiteSurvey(db_path=str(tmp_path / "pilot.sqlite3"), list_options=False)
    survey.handle("5674119", "здравствуйте")
    survey.grant_consent("5674119")
    return survey


def test_файл_без_текста_принят_и_подтверждён(анкета):
    бот = Бот()
    asyncio.run(_обработчик_сообщений(анкета)(Событие(бот, text=None, attachments=[Файл()])))

    тексты = [о["text"] for о in бот.отправки]
    assert FILES_TAKEN in тексты, "человек должен увидеть, что файл получен"
    assert not any("Напишите ответ текстом" in т for т in тексты), (
        "присланный файл — это ответ, а не пустое сообщение"
    )

    сообщения = (анкета.state.get("5674119") or {}).get("messages") or []
    assert any(m.get("files") for m in сообщения), "файл не попал в обращение — оператор его не увидит"


def test_файл_с_текстом_обрабатывает_и_то_и_другое(анкета):
    бот = Бот()
    asyncio.run(_обработчик_сообщений(анкета)(Событие(бот, text="Вот справка", attachments=[Файл()])))

    тексты = [о["text"] for о in бот.отправки]
    assert FILES_TAKEN in тексты
    assert len(тексты) >= 2, "ответ на текст и подтверждение файла — это два разных сообщения"

    сообщения = (анкета.state.get("5674119") or {}).get("messages") or []
    assert any(m.get("files") for m in сообщения)


def test_обычное_сообщение_без_файла_не_подтверждает_файл(анкета):
    бот = Бот()
    asyncio.run(_обработчик_сообщений(анкета)(Событие(бот, text="Иванова Мария")))

    тексты = [о["text"] for о in бот.отправки]
    assert FILES_TAKEN not in тексты
    assert тексты, "на обычное сообщение бот обязан ответить"

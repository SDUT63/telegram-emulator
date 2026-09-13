"""Карта тем в ноутбучном пилоте ведёт себя как вкладки, а не как лента.

Листать карту новыми сообщениями — значит завалить чат: человек
прокручивает десяток одинаковых меню и не понимает, какое живое.
"""
from __future__ import annotations

import asyncio

import pytest

import legacy_max_bot as old
import transparent_max_pilot as pilot
from storage_sqlite import SQLiteSurvey


class Бот:
    def __init__(self) -> None:
        self.отправки: list[dict] = []

    async def send_message(self, **kwargs):
        self.отправки.append(kwargs)


class Событие:
    """Нажатая кнопка, экран которой можно переписать."""

    def __init__(self, bot: Бот) -> None:
        self.bot = bot
        self.правки: list[dict] = []

    async def edit(self, **kwargs):
        self.правки.append(kwargs)


class СобытиеБезЭкрана(Событие):
    """Сообщение удалили или оно слишком старое."""

    async def edit(self, **kwargs):
        raise RuntimeError("сообщение недоступно для правки")


@pytest.fixture()
def пилот(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    survey = SQLiteSurvey(db_path=str(tmp_path / "pilot.sqlite3"), list_options=False)
    survey.handle("5674119", "здравствуйте")
    return survey


def test_листание_карты_переписывает_один_экран(пилот):
    бот = Бот()
    нажатия = [
        old.экран_карты(пилот, "5674119"),
        old.экран_ветви("palliativ", 1, пилот, "5674119"),
        old.экран_ветви("palliativ", 2, пилот, "5674119"),
        old.экран_карты(пилот, "5674119"),
    ]
    правок = 0
    for экран in нажатия:
        событие = Событие(бот)
        asyncio.run(pilot.browse(событие, 777, "5674119", экран, пилот))
        правок += len(событие.правки)

    assert правок == len(нажатия), "каждое нажатие должно переписывать экран"
    assert бот.отправки == [], "листание карты не должно копить сообщения в чате"


def test_если_экран_переписать_нельзя_отправляем_новым(пилот):
    бот = Бот()
    asyncio.run(pilot.browse(СобытиеБезЭкрана(бот), 777, "5674119", old.экран_карты(пилот, "5674119"), пилот))

    assert len(бот.отправки) == 1, "молчания быть не должно"
    assert бот.отправки[0]["text"]


def test_битая_кнопка_ведёт_на_карту_а_не_в_тишину(пилот):
    бот = Бот()
    событие = Событие(бот)
    asyncio.run(pilot.browse(событие, 777, "5674119", old.экран_ветви("нет-такой-ветви", 1, пилот, "5674119"), пилот, "ветвь не найдена"))

    assert len(событие.правки) == 1
    assert "О чём рассказать" in событие.правки[0]["text"]

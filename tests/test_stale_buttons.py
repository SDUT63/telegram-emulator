"""Кнопка с прошлого экрана не должна записывать ответ не в тот вопрос.

Сообщения в чате остаются. Человек листает вверх, видит знакомые кнопки и
нажимает. Если бот применит это к текущему вопросу, координатор получит
чужое «Утром» в графе «когда звонить» — и позвонит не тогда.
"""
from __future__ import annotations

import asyncio

import pytest

import transparent_max_pilot as pilot
import walk
from storage_sqlite import SQLiteSurvey


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
        return (777, 5674119)

    async def ack(self, **kwargs):
        return None


def _обработчик(survey):
    dispatcher = pilot.build_dispatcher(survey)
    for handler in dispatcher.event_handlers:
        if str(getattr(handler, "update_type", "")) == "message_callback":
            return handler.func_event
    raise AssertionError("обработчик message_callback не найден")


@pytest.fixture()
def анкета(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    survey = SQLiteSurvey(db_path=str(tmp_path / "pilot.sqlite3"), list_options=False)
    survey.handle("5674119", "здравствуйте")
    survey.grant_consent("5674119")
    return survey


def _до_второго_выбора(survey):
    """Дойти до вопроса с вариантами, у которого есть предыдущий шаг."""
    предыдущий = None
    for _ in range(40):
        место = survey.current("5674119")
        if not место:
            break
        шаг, вопрос = место
        if вопрос["kind"] == "choice" and предыдущий is not None:
            return предыдущий, шаг, вопрос
        предыдущий = шаг
        walk.ответить(survey, "5674119", вопрос)
    raise AssertionError("не нашли подходящую пару вопросов")


def test_кнопка_прошлого_шага_не_пишет_ответ_в_текущий_вопрос(анкета):
    старый, текущий, вопрос = _до_второго_выбора(анкета)
    до = dict((анкета.state.get("5674119") or {}).get("answers") or {})

    бот = Бот()
    asyncio.run(_обработчик(анкета)(Нажатие(бот, f"a:{старый}:0")))

    после = dict((анкета.state.get("5674119") or {}).get("answers") or {})
    assert после == до, f"ответ записался не в тот вопрос: {set(после) - set(до)}"
    assert анкета.current("5674119")[0] == текущий, "анкета не должна сдвинуться"
    assert бот.отправки, "молчание читается как поломка"
    assert "прошлого экрана" in бот.отправки[0]["text"]


def test_кнопка_текущего_шага_работает_как_раньше(анкета):
    _, текущий, вопрос = _до_второго_выбора(анкета)
    бот = Бот()

    asyncio.run(_обработчик(анкета)(Нажатие(бот, f"a:{текущий}:0")))

    ответы = (анкета.state.get("5674119") or {}).get("answers") or {}
    assert вопрос["id"] in ответы, "обычное нажатие должно записывать ответ"
    assert ответы[вопрос["id"]] == вопрос["options"][0]


def test_номер_варианта_за_границей_списка_отклоняется(анкета):
    _, текущий, вопрос = _до_второго_выбора(анкета)
    до = dict((анкета.state.get("5674119") or {}).get("answers") or {})
    бот = Бот()

    asyncio.run(_обработчик(анкета)(Нажатие(бот, f"a:{текущий}:{len(вопрос['options']) + 5}")))

    после = dict((анкета.state.get("5674119") or {}).get("answers") or {})
    assert после == до
    assert "прошлого экрана" in бот.отправки[0]["text"]


def test_переключатель_с_прошлого_шага_не_меняет_выбор(анкета):
    """Множественный выбор особенно уязвим: toggle берёт шаг прямо из payload."""
    старый, текущий, _ = _до_второго_выбора(анкета)
    бот = Бот()

    asyncio.run(_обработчик(анкета)(Нажатие(бот, f"t:{старый}:0")))

    assert анкета.picked("5674119", старый) == [], "старый шаг не должен меняться"
    assert "прошлого экрана" in бот.отправки[0]["text"]

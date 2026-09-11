"""Кнопки, которые работают.

MAX отклоняет пустое подтверждение нажатия: в запросе обязательно либо
новый текст сообщения, либо всплывающая подсказка. Пустой `ack()` даёт
400 `proto.payload` — и роняет весь обработчик нажатия, так что человек
не получает ни статьи, ни ответа. Кнопка выглядит рабочей и не работает,
а причина видна только в журнале.

Здесь проверяется, что это не может повториться.
"""
import asyncio
import io
import os
import re

import pytest

import max_bot

ИСХОДНИК = io.open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "max_bot.py"), encoding="utf-8").read()


class ФейкMAX:
    """Событие нажатия, которое ведёт себя как настоящий MAX."""

    def __init__(self, строгий: bool = True):
        self.строгий = строгий
        self.подсказки: list[str] = []

    async def ack(self, notification=None):
        if self.строгий and not notification:
            raise RuntimeError(
                "Ошибка от API: code=400 "
                "{'code': 'proto.payload', "
                "'message': 'Invalid request. `message` or `notification` required'}")
        self.подсказки.append(notification)


# ------------------------------------------------------------ подтверждение

def test_подтверждение_всегда_с_подсказкой():
    событие = ФейкMAX()
    asyncio.run(max_bot.подтвердить(событие, "Сейчас пришлю"))
    assert событие.подсказки == ["Сейчас пришлю"]


def test_отказ_подтверждения_ничего_не_роняет():
    """Подтверждение — вежливость, а не работа. Его отказ не должен мешать."""

    class Сломанный(ФейкMAX):
        async def ack(self, notification=None):
            raise RuntimeError("связь отвалилась")

    asyncio.run(max_bot.подтвердить(Сломанный(), "Готово"))   # не бросает


def test_пустое_подтверждение_ловится_фейком():
    """Проверяем сам сторож: он должен ругаться так же, как MAX."""
    with pytest.raises(RuntimeError, match="proto.payload"):
        asyncio.run(ФейкMAX().ack())


# --------------------------------------------------- сторож над исходником

def test_в_коде_нет_пустого_подтверждения():
    """Ровно та строка, из-за которой кнопки перестали работать."""
    assert "event.ack()" not in ИСХОДНИК
    голые = re.findall(r"\.ack\(\s*\)", ИСХОДНИК)
    assert not голые, "пустой ack() MAX отклоняет с кодом 400"


def test_подтверждения_идут_через_помощник():
    """Прямой вызов ack разрешён ровно в одном месте — внутри помощника.

    Везде ещё подтверждать надо через `подтвердить()`: он не даёт
    ни пустой подсказки, ни упавшего обработчика.
    """
    прямые = re.findall(r"await event\.ack\(", ИСХОДНИК)
    assert len(прямые) == 1, f"прямых вызовов ack: {len(прямые)}"
    помощник = ИСХОДНИК.split("async def подтвердить", 1)[1].split("\nasync def")[0]
    assert "await event.ack(notification=подсказка)" in помощник


def test_у_каждого_подтверждения_есть_текст():
    подсказки = re.findall(r"подтвердить\(event,\s*(.*?)\)\n", ИСХОДНИК)
    assert подсказки, "подтверждений в коде не осталось вовсе"
    for п in подсказки:
        текст = п.strip().strip('"').strip("'")
        assert текст and текст != "подсказка", f"пустая подсказка: {п!r}"
        assert len(текст) <= 40, f"подсказка не влезет во всплывашку: {п!r}"


# ------------------------------------------- кнопка не ведёт в пустоту

def test_кнопка_на_исчезнувшую_статью_не_молчит(consented):
    """Статью могли убрать из базы, а кнопка на неё осталась в переписке."""
    from tests.test_dialogue import ФейкБот
    бот = ФейкБот()
    ок = asyncio.run(max_bot.статья(бот, 1, "u1", "Такой статьи никогда не было"))
    assert ок is False
    # Обработчик на этот случай показывает меню тем — проверяем, что оно есть
    бот2 = ФейкБот()
    asyncio.run(max_bot.меню_тем(бот2, 1, "u1", consented))
    assert бот2.ушло and бот2.кнопки[0]


# ------------------------------------------- слова, открывающие меню тем

def test_слова_меню_не_отнимают_ответ_анкеты():
    """«Не знаю» — законный ответ на половину вопросов, а не просьба о меню.

    Слово из ASK_WORDS уводит человека в темы, минуя анкету. Если такое
    слово окажется ещё и вариантом ответа, ответ потеряется молча.
    """
    import survey_questions
    варианты = {в.lower().strip()
                for вопрос in survey_questions.QUESTIONS
                for в in (вопрос.get("options") or [])}
    пересечение = варианты & {с.lower() for с in max_bot.ASK_WORDS}
    assert not пересечение, f"слово и ответ, и команда: {пересечение}"


def test_слова_меню_не_спорят_с_командами_анкеты():
    import chatbot_survey as движок
    команды = set()
    for имя in ("RESTART_WORDS", "BEGIN_WORDS", "CANCEL_WORDS", "SUMMARY_WORDS",
                "HELP_WORDS", "SKIP_WORDS", "BACK_WORDS", "CONTINUE_WORDS",
                "AGREE_WORDS", "REFUSE_WORDS", "ERASE_WORDS", "READ_WORDS"):
        команды |= {с.lower() for с in getattr(движок, имя)}
    пересечение = команды & {с.lower() for с in max_bot.ASK_WORDS}
    assert not пересечение, f"одно слово в двух смыслах: {пересечение}"


def test_меню_тем_открывается_привычными_словами():
    for слово in ("меню", "спросить", "подскажите", "что ты умеешь",
                  "с чего начать", "/ask"):
        assert слово in max_bot.ASK_WORDS

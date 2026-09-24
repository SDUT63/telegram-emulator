"""Строки в правилах маршрутизации — это подписи кнопок анкеты.

Правила сравнивают ответ с подстрокой. Разойдётся подпись на одну
запятую — и правило молча перестаёт срабатывать: так было с
«Не знаю с чего» против кнопки «Не знаю, с чего», и причина
«запрос не сформулирован» не выдавалась ни разу. Эталонные случаи
этого не ловили, потому что были записаны той же ошибочной строкой.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(КОРЕНЬ))

import routing_rules as rr                                      # noqa: E402
from survey_questions import QUESTIONS                          # noqa: E402

ВАРИАНТЫ = {в["id"]: в.get("options") or [] for в in QUESTIONS}


def _есть(ключ: str, строка: str) -> bool:
    return any(строка.lower() in в.lower() for в in ВАРИАНТЫ.get(ключ, []))


def test_признаки_утраты_и_медицинские_совпадают_с_кнопками():
    for таблица in (rr.LOSS, rr.MED):
        for ключ, строки in таблица.items():
            for строка in строки:
                assert _есть(ключ, строка), (ключ, строка)
    for строка in rr.URGENT_FLAGS:
        assert _есть("flags", строка), строка


def test_противоречия_совпадают_с_кнопками():
    for (к1, з1), (к2, з2), _ in rr.CONTRADICTIONS:
        assert _есть(к1, з1), (к1, з1)
        assert _есть(к2, з2), (к2, з2)


def test_строки_внутри_правил_совпадают_с_кнопками():
    исходник = inspect.getsource(rr.route)
    for ключ, значения in re.findall(
            r'has\(a, "(\w+)", ((?:"[^"]+"(?:, )?)+)\)', исходник):
        for строка in re.findall(r'"([^"]+)"', значения):
            assert _есть(ключ, строка), (ключ, строка)


def test_эталонные_случаи_записаны_настоящими_ответами():
    for имя, ответы, _ in rr.CASES:
        for ключ, значение in ответы.items():
            if not ВАРИАНТЫ.get(ключ):
                continue
            части = ([ч.strip() for ч in значение.split(",")]
                     if ключ == "flags" else [значение])
            for часть in части:
                assert any(часть.lower() == в.lower() for в in ВАРИАНТЫ[ключ]), (
                    имя, ключ, часть)


def test_не_знаю_с_чего_даёт_свою_причину():
    маршрут, основание, _ = rr.route({"need": "Не знаю, с чего",
                                      "mobility": "Ходит сам"})
    assert маршрут == "М4" and "не сформулирован" in основание

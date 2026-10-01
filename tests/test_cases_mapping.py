"""Документ соответствия шага 1 не расходится с тестами.

docs/МОДЕЛЬ-ОБРАЩЕНИЯ-ШАГ-1.md обещает: каждое правило контракта
проверено названным тестом, у каждого инварианта И1–И15 есть тест с его
номером в имени. Здесь это обещание сверяется с кодом — иначе таблица
устареет при первой же правке.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parents[1]
КОНТРАКТ = КОРЕНЬ / "docs" / "МОДЕЛЬ-ОБРАЩЕНИЯ.md"
СООТВЕТСТВИЕ = КОРЕНЬ / "docs" / "МОДЕЛЬ-ОБРАЩЕНИЯ-ШАГ-1.md"
ТЕСТЫ = ("test_cases_contract.py", "test_cases_postgres.py")


def _тесты() -> set[str]:
    имена = set()
    for файл in ТЕСТЫ:
        дерево = ast.parse((КОРЕНЬ / "tests" / файл).read_text(encoding="utf-8"))
        имена |= {у.name for у in дерево.body
                  if isinstance(у, ast.FunctionDef) and у.name.startswith("test_")}
    return имена


def test_тесты_из_таблицы_существуют():
    названные = set(re.findall(r"`(test_\w+)`", СООТВЕТСТВИЕ.read_text(encoding="utf-8")))
    assert названные, "в документе соответствия не нашлось ни одного теста"
    assert названные - _тесты() == set()


def test_у_каждого_инварианта_есть_тест():
    инварианты = re.findall(r"^\| (И\d+) \|", КОНТРАКТ.read_text(encoding="utf-8"), re.M)
    assert инварианты == [f"И{n}" for n in range(1, 16)]
    тесты = _тесты()
    без_теста = [и for и in инварианты
                 if not any(т.startswith(f"test_{и.lower()}_") for т in тесты)]
    assert без_теста == []

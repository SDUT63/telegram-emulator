"""Правила отнесения к маршрутам М1–М5.

Случаи заданы службой в routing_rules.py. Здесь они прогоняются как
обычные тесты, чтобы правка правил не сломала разбор молча.
"""
import pytest

import routing_rules as rr


@pytest.mark.parametrize("название,ответы,ждём", rr.CASES,
                         ids=[c[0] for c in rr.CASES])
def test_случай_разбирается_верно(название, ответы, ждём):
    маршрут, основание, _ = rr.route(ответы)
    assert маршрут == ждём, f"{название}: {основание}"


def test_у_каждого_маршрута_есть_основание():
    for _, ответы, _ in rr.CASES:
        _, основание, _ = rr.route(ответы)
        assert основание and len(основание) > 10


def test_экстренные_признаки_обгоняют_всё():
    """Даже при полном наборе паллиативных признаков жизнь важнее маршрута."""
    маршрут, _, _ = rr.route({"flags": "Тяжело дышит", "status": "Паллиативный статус",
                              "pain": "Постоянная", "mobility": "Не встаёт"})
    assert маршрут == "СМП"


def test_правила_ссылаются_на_существующие_варианты():
    """Подписи вариантов уже правились. Правила не должны отстать."""
    from survey_questions import QUESTIONS
    по_ключу = {q["id"]: q.get("options", []) for q in QUESTIONS}
    беда = []
    for источник in (rr.LOSS, rr.MED):
        for ключ, значения in источник.items():
            for v in значения:
                if ключ in по_ключу and not any(
                        v.lower() in o.lower() for o in по_ключу[ключ]):
                    беда.append((ключ, v))
    for v in rr.URGENT_FLAGS:
        if not any(v.lower() in o.lower() for o in по_ключу.get("flags", [])):
            беда.append(("flags", v))
    assert not беда, f"правило ссылается на несуществующий вариант: {беда}"


def test_у_каждого_маршрута_есть_название():
    for _, ответы, _ in rr.CASES:
        маршрут, _, _ = rr.route(ответы)
        assert маршрут in rr.NAMES

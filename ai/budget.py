# -*- coding: utf-8 -*-
"""Бюджет на модель. Без него первая же ошибка в цикле съедает грант.

Лимит месячный и жёсткий: исчерпан — вызовы прекращаются, бот работает
как раньше. Счётчик лежит в файле рядом с состоянием анкеты, пишется
атомарно, переживает перезапуск.
"""
from __future__ import annotations
import json, os, threading
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "..", "ai_budget.json")
LIMIT = float(os.getenv("AI_MONTHLY_LIMIT_RUB", "1500"))
_lock = threading.Lock()

def _read() -> dict:
    try:
        with open(FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}

def _write(data: dict) -> None:
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, FILE)

def month() -> str:
    return date.today().strftime("%Y-%m")

def spent() -> float:
    return float(_read().get(month(), {}).get("rub", 0.0))

def calls() -> int:
    return int(_read().get(month(), {}).get("calls", 0))

def left() -> float:
    return max(LIMIT - spent(), 0.0)

def allowed() -> bool:
    return spent() < LIMIT

def add(rub: float, tokens_in: int = 0, tokens_out: int = 0) -> None:
    with _lock:
        data = _read()
        m = data.setdefault(month(), {"rub": 0.0, "calls": 0, "in": 0, "out": 0})
        m["rub"] = round(m["rub"] + rub, 4)
        m["calls"] += 1
        m["in"] += tokens_in
        m["out"] += tokens_out
        _write(data)

def report() -> str:
    m = _read().get(month(), {})
    return (f"{month()}: потрачено {m.get('rub', 0):.2f} ₽ из {LIMIT:.0f}, "
            f"вызовов {m.get('calls', 0)}, токенов {m.get('in', 0)}→{m.get('out', 0)}")

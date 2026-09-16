#!/usr/bin/env python3
"""Открыть то же хранилище, в котором работает бот.

Зачем отдельно
--------------
Хранилищ три: файл рядом с ботом для разработки, SQLite для пилота
на ноутбуке и PostgreSQL для круглосуточной работы. Бот выбирает
нужное по переменным окружения, а всё, что читает его работу — CRM,
выгрузка в Excel, страница со сводкой, воронка, — выбирало само
и по-своему.

Расходились они молча. Бот пишет в PostgreSQL, CRM читает пустой файл
`responses.json` — и координатор видит ноль обращений при полной базе.
Ни ошибки, ни пустой страницы с объяснением: просто «обращений нет».
Так можно сутки считать, что рекламу никто не увидел.

Поэтому выбор один и здесь. Правило то же, что у бота:

    SDUT_DATABASE_URL задан → PostgreSQL;
    есть файл базы пилота   → SQLite;
    иначе                   → файл рядом с ботом.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Путь к базе пилота — тот же, что у SQLiteSurvey по умолчанию.
ПИЛОТ_ПО_УМОЛЧАНИЮ = "data/sdut_bot.sqlite3"


def адрес_postgres() -> str:
    return (os.getenv("SDUT_DATABASE_URL") or "").strip()


def путь_пилота() -> str:
    return (os.getenv("SDUT_DB_PATH") or ПИЛОТ_ПО_УМОЛЧАНИЮ).strip()


def где_данные() -> str:
    """Имя выбранного хранилища: postgres | sqlite | файл."""
    if адрес_postgres():
        return "postgres"
    if Path(путь_пилота()).is_file():
        return "sqlite"
    return "файл"


def открыть(*, list_options: bool = False) -> Any:
    """Анкета, читающая то же, что пишет бот.

    Для чтения: конструкторы ничего не записывают. Пишет в анкету только
    бот — у него на это своя транзакция с блокировкой по человеку,
    и второй писатель там не предусмотрен.
    """
    выбор = где_данные()

    if выбор == "postgres":
        from storage_postgres import PostgresSurvey

        анкета = PostgresSurvey(list_options=list_options)
        # PostgresSurvey читает состояние в конструкторе, но процесс CRM
        # живёт долго, а бот пишет всё это время. Без перечитывания
        # координатор видел бы срез на момент запуска страницы.
        анкета.load()
        return анкета

    if выбор == "sqlite":
        from storage_sqlite import SQLiteSurvey

        return SQLiteSurvey(list_options=list_options)

    from chatbot_survey import Survey

    return Survey(list_options=list_options)


if __name__ == "__main__":                                  # pragma: no cover
    анкета = открыть()
    начато, закончено = анкета.stats()
    print(f"хранилище: {где_данные()}")
    print(f"обращений: {начато}, заполнено целиком: {закончено}")

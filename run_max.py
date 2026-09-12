#!/usr/bin/env python3
"""Запуск MAX-бота СДУТ на ноутбуке с долговременным хранением."""
from __future__ import annotations

import asyncio

import max_bot
from storage_sqlite import PersistentSeen, SQLiteSurvey


def main() -> None:
    # max_bot.py — проверенный транспорт и сценарий. Здесь меняем только
    # реализации хранения/идемпотентности, не переписывая диспетчер.
    max_bot.Survey = SQLiteSurvey
    max_bot.Seen = PersistentSeen
    asyncio.run(max_bot.main())


if __name__ == "__main__":
    main()

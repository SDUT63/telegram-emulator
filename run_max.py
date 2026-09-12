#!/usr/bin/env python3
"""Запуск legacy SQLite-пилота MAX-бота."""
from __future__ import annotations

import asyncio

import legacy_max_bot as max_bot
from storage_sqlite import PersistentSeen, SQLiteSurvey


def main() -> None:
    max_bot.Survey = SQLiteSurvey
    max_bot.Seen = PersistentSeen
    asyncio.run(max_bot.main())


if __name__ == "__main__":
    main()

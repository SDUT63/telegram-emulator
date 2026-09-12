#!/usr/bin/env python3
"""Production-oriented MAX launcher using PostgreSQL persistence.

This is deliberately separate from run_max.py: the latter remains the
simple laptop/SQLite pilot launcher.
"""
from __future__ import annotations

import asyncio

import max_bot
from storage_postgres import PersistentSeen, PostgresSurvey


def main() -> None:
    max_bot.Survey = PostgresSurvey
    max_bot.Seen = PersistentSeen
    asyncio.run(max_bot.main())


if __name__ == "__main__":
    main()

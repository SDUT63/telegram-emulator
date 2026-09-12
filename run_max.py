#!/usr/bin/env python3
"""Recommended MAX pilot launcher with durable state and idempotency."""
from __future__ import annotations

import asyncio

import max_bot
from storage_sqlite import PersistentSeen, SQLiteSurvey


def main() -> None:
    max_bot.Survey = SQLiteSurvey
    max_bot.Seen = PersistentSeen
    asyncio.run(max_bot.main())


if __name__ == "__main__":
    main()

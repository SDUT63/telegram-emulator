#!/usr/bin/env python3
"""MAX Webhook launcher.

Production deployments should use PostgreSQL; SQLite remains available via
run_max.py for the single-process laptop pilot.
"""
from __future__ import annotations

import asyncio

import max_bot
import max_webhook
from storage_postgres import PersistentSeen, PostgresSurvey


def main() -> None:
    max_bot.Survey = PostgresSurvey
    max_bot.Seen = PersistentSeen
    max_webhook.Survey = PostgresSurvey
    asyncio.run(max_webhook.main())


if __name__ == "__main__":
    main()

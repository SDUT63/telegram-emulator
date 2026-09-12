#!/usr/bin/env python3
"""Recommended launcher for the SDUT MAX bot.

``max_bot.py`` remains the reference transport implementation. This launcher
injects the durable SQLite repository without duplicating the mature handlers.
It is the command to use for the pilot.

Development:
    python run_max.py

Webhook production:
    python run_max_webhook.py
"""
from __future__ import annotations

import asyncio

import max_bot
from storage_sqlite import SQLiteSurvey


def main() -> None:
    # Keep the proven MAX dispatcher and replace only persistence.
    max_bot.Survey = SQLiteSurvey
    asyncio.run(max_bot.main())


if __name__ == "__main__":
    main()

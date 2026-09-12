#!/usr/bin/env python3
"""Production MAX webhook launcher using durable state and idempotency."""
from __future__ import annotations

import asyncio

import max_bot
import max_webhook
from storage_sqlite import PersistentSeen, SQLiteSurvey


def main() -> None:
    max_bot.Seen = PersistentSeen
    max_webhook.Survey = SQLiteSurvey
    asyncio.run(max_webhook.main())


if __name__ == "__main__":
    main()

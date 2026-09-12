#!/usr/bin/env python3
"""MAX webhook launcher with the same durable pilot storage as long polling.

This is the webhook entrypoint, not a production-readiness claim. For a
multi-instance production deployment replace SQLite with the planned shared
PostgreSQL storage and add the production secret/backup/monitoring stack.
"""
from __future__ import annotations

import asyncio

import max_bot
import max_webhook
from storage_sqlite import PersistentSeen, SQLiteSurvey


def main() -> None:
    # max_webhook imports Survey into its module namespace, while
    # max_bot.build_dispatcher resolves Seen from max_bot's global namespace.
    # Replace both before constructing the dispatcher.
    max_bot.Seen = PersistentSeen
    max_webhook.Survey = SQLiteSurvey
    asyncio.run(max_webhook.main())


if __name__ == "__main__":
    main()

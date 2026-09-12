#!/usr/bin/env python3
"""MAX long-polling launcher backed by transactional PostgreSQL.

Long polling is intended for development/testing. Production should use
run_max_webhook.py, but both entrypoints use the same transactional storage
semantics when PostgreSQL is configured.
"""
from __future__ import annotations

import asyncio

import max_bot
from postgres_guard import require_migrations
from production_storage import ProductionPostgresSurvey, TransactionalPersistentSeen


def main() -> None:
    require_migrations()
    max_bot.Survey = ProductionPostgresSurvey
    max_bot.Seen = TransactionalPersistentSeen
    asyncio.run(max_bot.main())


if __name__ == "__main__":
    main()

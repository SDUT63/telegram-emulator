#!/usr/bin/env python3
"""MAX long-polling launcher backed by transactional PostgreSQL.

Long polling is intended for development/testing. Production should use
run_max_webhook.py, but both entrypoints use the same transactional storage
semantics when PostgreSQL is configured.
"""
from __future__ import annotations

import asyncio
import os

import max_bot
from postgres_guard import require_migrations
from scripts.check_outbound_paths import require_clean
from production_outbox import DurableProductionPostgresSurvey
from durable_outbox_worker import run as run_durable_outbox
from storage_postgres import TransactionalPersistentSeen


def _install_combined_outbox_worker() -> None:
    """Keep legacy CRM delivery and durable PostgreSQL delivery together."""
    if getattr(max_bot.outbox_worker, "_sdut_combined", False):
        return

    crm_worker = max_bot.outbox_worker

    async def combined(bot):
        durable = asyncio.create_task(run_durable_outbox(bot))
        crm = asyncio.create_task(crm_worker(bot))
        try:
            await asyncio.gather(durable, crm)
        finally:
            durable.cancel()
            crm.cancel()

    combined._sdut_combined = True
    max_bot.outbox_worker = combined


def main() -> None:
    if not (os.getenv("SDUT_DATABASE_URL") or "").strip():
        raise SystemExit(
            "run_max_postgres.py требует SDUT_DATABASE_URL; "
            "для SQLite-пилота используйте run_max.py"
        )

    # Never start a production PostgreSQL process while the canonical
    # dispatcher still contains an application-level direct MAX send path.
    # This replaces the former Bot.send_message monkeypatch/suppression hack:
    # an unsafe architecture must fail closed, not be made to look safe.
    require_clean()

    os.environ.setdefault("SDUT_REQUIRE_MIGRATIONS", "1")
    require_migrations()

    _install_combined_outbox_worker()
    max_bot.Survey = DurableProductionPostgresSurvey
    max_bot.Seen = TransactionalPersistentSeen
    asyncio.run(max_bot.main())


if __name__ == "__main__":
    main()

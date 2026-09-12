#!/usr/bin/env python3
"""MAX Webhook launcher.

Production deployments use PostgreSQL plus the durable outbound worker.
SQLite remains available only through the local pilot launcher.
"""
from __future__ import annotations

import asyncio
import os

import max_bot
import max_webhook
from production_outbox import DurableProductionPostgresSurvey
from durable_outbox_worker import run as run_durable_outbox
from scripts.check_outbound_paths import require_clean
from storage_postgres import TransactionalPersistentSeen


def _install_production_storage() -> None:
    """Make the webhook's dynamic storage selection use the durable facade."""
    def storage_classes():
        return DurableProductionPostgresSurvey, TransactionalPersistentSeen, "PostgreSQL"

    max_webhook._storage_classes = storage_classes


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
    # A public webhook is a production transport. Running it against the
    # laptop SQLite pilot would make delivery durability depend on one process
    # and could silently bypass the transactional outbox.
    if not (os.getenv("SDUT_DATABASE_URL") or "").strip():
        raise SystemExit(
            "run_max_webhook.py требует SDUT_DATABASE_URL; "
            "для SQLite-пилота используйте run_max.py"
        )

    # Do not hide unsafe application sends behind a Bot.send_message
    # monkeypatch. Until the canonical dispatcher uses the explicit durable
    # transport, production startup must fail closed.
    require_clean()

    os.environ.setdefault("SDUT_REQUIRE_MIGRATIONS", "1")
    _install_production_storage()
    _install_combined_outbox_worker()
    max_bot.Survey = DurableProductionPostgresSurvey
    max_bot.Seen = TransactionalPersistentSeen
    asyncio.run(max_webhook.main())


if __name__ == "__main__":
    main()

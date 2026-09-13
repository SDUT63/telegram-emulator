#!/usr/bin/env python3
"""MAX long-polling launcher backed by transactional PostgreSQL."""
from __future__ import annotations

import asyncio
import os

from durable_outbox_worker import run as run_durable_outbox
from logging_config import configure_logging
from max_config import COMMANDS, read_token
from max_production_dispatcher import build_dispatcher
from postgres_guard import require_migrations
from production_privacy import ProductionPrivacySurvey
from scripts.check_outbound_paths import require_clean
from storage_postgres import TransactionalPersistentSeen


async def _run() -> None:
    from maxapi import Bot
    from maxapi.types import BotCommand
    token = read_token()
    survey = ProductionPrivacySurvey(list_options=False)
    seen = TransactionalPersistentSeen()
    bot = Bot(token)
    dispatcher = build_dispatcher(survey, seen)
    try:
        me = await bot.get_me()
        await bot.delete_webhook()
        try:
            await bot.set_commands(*(BotCommand(name=name, description=text) for name, text in COMMANDS))
        except Exception as error:  # noqa: BLE001
            import logging
            logging.getLogger("сдут-бот").warning("MAX command menu update failed: %s", type(error).__name__)
        worker = asyncio.create_task(run_durable_outbox(bot))
        try:
            import logging
            logging.getLogger("сдут-бот").info("MAX polling service started")
            await dispatcher.start_polling(bot)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
    finally:
        await bot.close_session()


def main() -> None:
    configure_logging()
    if not (os.getenv("SDUT_DATABASE_URL") or "").strip():
        raise SystemExit("run_max_postgres.py требует SDUT_DATABASE_URL; для SQLite-пилота используйте run_max.py")
    os.environ.setdefault("SDUT_REQUIRE_MIGRATIONS", "1")
    require_migrations()
    require_clean()
    asyncio.run(_run())


if __name__ == "__main__":
    main()

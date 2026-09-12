#!/usr/bin/env python3
"""MAX long-polling launcher backed by transactional PostgreSQL.

This is the production-grade long-polling entrypoint used for controlled
operation and integration testing. Public production deployments should use
the equivalent webhook launcher.
"""
from __future__ import annotations

import asyncio
import os

from durable_outbox_worker import run as run_durable_outbox
from max_config import COMMANDS, read_token
from max_production_dispatcher import build_dispatcher
from postgres_guard import require_migrations
from production_outbox import DurableProductionPostgresSurvey
from scripts.check_outbound_paths import require_clean
from storage_postgres import TransactionalPersistentSeen


async def _run() -> None:
    from maxapi import Bot
    from maxapi.types import BotCommand

    token = read_token()
    survey = DurableProductionPostgresSurvey(list_options=False)
    seen = TransactionalPersistentSeen()
    bot = Bot(token)
    dispatcher = build_dispatcher(survey, seen)

    try:
        me = await bot.get_me()
        await bot.delete_webhook()
        try:
            await bot.set_commands(
                *(BotCommand(name=name, description=text) for name, text in COMMANDS)
            )
        except Exception as error:  # noqa: BLE001
            print(f"Предупреждение: меню MAX не обновилось: {error}")

        worker = asyncio.create_task(run_durable_outbox(bot))
        try:
            name = getattr(me, "name", None) or getattr(me, "first_name", "бот")
            print(f"Бот запущен: {name}; PostgreSQL + durable outbox")
            await dispatcher.start_polling(bot)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
    finally:
        await bot.close_session()


def main() -> None:
    if not (os.getenv("SDUT_DATABASE_URL") or "").strip():
        raise SystemExit(
            "run_max_postgres.py требует SDUT_DATABASE_URL; "
            "для SQLite-пилота используйте run_max.py"
        )
    os.environ.setdefault("SDUT_REQUIRE_MIGRATIONS", "1")
    require_migrations()
    require_clean()
    asyncio.run(_run())


if __name__ == "__main__":
    main()

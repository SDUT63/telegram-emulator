#!/usr/bin/env python3
"""MAX Webhook entrypoint for the SDUT bot.

Production deployments use PostgreSQL, the canonical production dispatcher,
and the durable outbound worker. SQLite remains restricted to the local
pilot launcher.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import sys
from urllib.parse import urlsplit

from aiohttp import web

from durable_outbox_worker import run as run_durable_outbox
from max_config import COMMANDS, read_token
from max_production_dispatcher import build_dispatcher
from production_outbox import DurableProductionPostgresSurvey
from storage_postgres import TransactionalPersistentSeen

log = logging.getLogger("сдут-бот")
DEFAULT_PATH = "/max"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{5,256}$")


def read_secret() -> str:
    value = (os.getenv("MAX_WEBHOOK_SECRET") or "").strip()
    if value:
        return value
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".webhook_secret")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            saved = fh.read().strip()
            if saved:
                return saved
    fresh = secrets.token_hex(24)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(fresh)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    log.warning("Секрет вебхука создан и сохранён в .webhook_secret")
    return fresh


def validate_secret(secret: str) -> list[str]:
    if not secret:
        return ["не задан секрет вебхука"]
    if not SECRET_RE.fullmatch(secret):
        return ["MAX_WEBHOOK_SECRET должен содержать 5–256 символов: A-Z, a-z, 0-9, _ или -"]
    return []


def validate_public_url(url: str, expected_path: str) -> list[str]:
    if not url:
        return ["не задан MAX_WEBHOOK_URL — полный публичный HTTPS endpoint"]
    problems: list[str] = []
    try:
        parsed = urlsplit(url)
        explicit_port = parsed.port
    except ValueError:
        return ["MAX_WEBHOOK_URL имеет некорректный формат"]
    if parsed.scheme != "https": problems.append("MAX_WEBHOOK_URL должен использовать https://")
    if not parsed.hostname: problems.append("MAX_WEBHOOK_URL должен содержать имя хоста")
    if parsed.username or parsed.password: problems.append("MAX_WEBHOOK_URL не должен содержать логин или пароль")
    if explicit_port not in (None, 443): problems.append("публичный Webhook MAX должен использовать порт 443")
    if parsed.query or parsed.fragment: problems.append("MAX_WEBHOOK_URL не должен содержать query или fragment")
    path = parsed.path or "/"
    if expected_path and path != expected_path:
        problems.append(f"путь URL ({path}) должен совпадать с MAX_WEBHOOK_PATH ({expected_path})")
    return problems


def validate_settings(url: str, secret: str, path: str) -> list[str]:
    problems = validate_public_url(url, path)
    problems.extend(validate_secret(secret))
    if not path.startswith("/"):
        problems.append("MAX_WEBHOOK_PATH должен начинаться с '/'")
    return problems


async def main() -> None:
    if not (os.getenv("SDUT_DATABASE_URL") or "").strip():
        print("\nMAX Webhook остановлен: SDUT_DATABASE_URL не задан. SQLite разрешён только через run_max.py.\n")
        sys.exit(1)

    token = read_token()
    public_url = (os.getenv("MAX_WEBHOOK_URL") or "").strip().rstrip("/")
    path = (os.getenv("MAX_WEBHOOK_PATH") or DEFAULT_PATH).strip() or DEFAULT_PATH
    host = (os.getenv("MAX_WEBHOOK_HOST") or DEFAULT_HOST).strip()
    try:
        port = int(os.getenv("MAX_WEBHOOK_PORT", str(DEFAULT_PORT)))
    except ValueError:
        print("\nMAX_WEBHOOK_PORT должен быть целым числом.\n")
        sys.exit(1)

    secret = read_secret()
    problems = validate_settings(public_url, secret, path)
    if not (1 <= port <= 65535):
        problems.append("MAX_WEBHOOK_PORT должен быть в диапазоне 1–65535")
    if problems:
        print("\n" + "=" * 70)
        print("  Webhook не настроен:")
        for line in problems: print(f"    — {line}")
        print("\n  Пример production-конфигурации:")
        print("      MAX_WEBHOOK_URL=https://bot.sdut63.ru/max")
        print("      MAX_WEBHOOK_PATH=/max")
        print("      MAX_WEBHOOK_HOST=127.0.0.1")
        print("      MAX_WEBHOOK_PORT=8080")
        print("      SDUT_DATABASE_URL=postgresql://...")
        print("=" * 70 + "\n")
        sys.exit(1)

    from postgres_guard import require_migrations
    try:
        require_migrations()
    except RuntimeError as error:
        print(f"\n  PostgreSQL schema is not ready: {error}\n")
        sys.exit(1)

    from maxapi import Bot
    from maxapi.types import BotCommand
    from maxapi.webhook.aiohttp import AiohttpMaxWebhook

    survey = DurableProductionPostgresSurvey(list_options=False)
    seen = TransactionalPersistentSeen()
    bot = Bot(token)
    dp = build_dispatcher(survey, seen)

    try:
        me = await bot.get_me()
        await bot.subscribe_webhook(url=public_url, secret=secret)
        try:
            await bot.set_commands(*(BotCommand(name=n, description=t) for n, t in COMMANDS))
        except Exception as error:  # noqa: BLE001
            log.warning("Меню команд не обновилось: %s", error)

        webhook = AiohttpMaxWebhook(dp=dp, bot=bot, secret=secret)
        app = webhook.create_app(path=path)

        async def health(_: web.Request) -> web.Response:
            try: storage_ok = bool(survey.health())
            except Exception: storage_ok = False
            return web.json_response({"status": "ok" if storage_ok else "degraded", "storage": "PostgreSQL"}, status=200 if storage_ok else 503)

        async def ready(_: web.Request) -> web.Response:
            try: storage_ok = bool(survey.health())
            except Exception: storage_ok = False
            return web.json_response({"ready": storage_ok, "storage": "PostgreSQL"}, status=200 if storage_ok else 503)

        app.router.add_get("/health", health)
        app.router.add_get("/ready", ready)
        queue = asyncio.create_task(run_durable_outbox(bot))

        name = getattr(me, "name", None) or "бот"
        print("\n" + "=" * 70)
        print(f"  Бот запущен через MAX Webhook: {name}")
        print(f"  Внешний endpoint: {public_url}")
        print(f"  Внутренний listener: {host}:{port}{path}")
        print("  Хранилище: PostgreSQL")
        print("  Outbound: durable PostgreSQL outbox")
        print("  TLS: reverse proxy / сервер с доверенным сертификатом")
        print("=" * 70 + "\n")

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        try:
            await asyncio.Event().wait()
        finally:
            queue.cancel()
            await asyncio.gather(queue, return_exceptions=True)
            await runner.cleanup()
    finally:
        await bot.close_session()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен.\n")

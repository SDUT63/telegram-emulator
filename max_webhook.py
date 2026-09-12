#!/usr/bin/env python3
"""MAX Webhook entrypoint for the SDUT bot.

MAX-facing HTTPS/TLS is expected to terminate at a reverse proxy. This
entrypoint is production-only: PostgreSQL transactional storage and the
durable outbound outbox are mandatory. SQLite is intentionally restricted to
the local pilot launcher (``run_max.py``).
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

import max_bot

log = logging.getLogger("сдут-бот")

DEFAULT_PATH = "/max"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{5,256}$")


def read_secret() -> str:
    """Return a stable webhook secret, creating it once when necessary."""
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
        return [
            "MAX_WEBHOOK_SECRET должен содержать 5–256 символов: "
            "A-Z, a-z, 0-9, _ или -"
        ]
    return []


def validate_public_url(url: str, expected_path: str) -> list[str]:
    """Validate the public URL registered in MAX."""
    if not url:
        return ["не задан MAX_WEBHOOK_URL — полный публичный HTTPS endpoint"]

    problems: list[str] = []
    try:
        parsed = urlsplit(url)
        explicit_port = parsed.port
    except ValueError:
        return ["MAX_WEBHOOK_URL имеет некорректный формат"]

    if parsed.scheme != "https":
        problems.append("MAX_WEBHOOK_URL должен использовать https://")
    if not parsed.hostname:
        problems.append("MAX_WEBHOOK_URL должен содержать имя хоста")
    if parsed.username or parsed.password:
        problems.append("MAX_WEBHOOK_URL не должен содержать логин или пароль")
    if explicit_port not in (None, 443):
        problems.append("публичный Webhook MAX должен использовать порт 443")
    if parsed.query or parsed.fragment:
        problems.append("MAX_WEBHOOK_URL не должен содержать query или fragment")

    path = parsed.path or "/"
    if expected_path and path != expected_path:
        problems.append(
            f"путь URL ({path}) должен совпадать с MAX_WEBHOOK_PATH ({expected_path})"
        )
    return problems


def validate_settings(url: str, secret: str, path: str) -> list[str]:
    problems = validate_public_url(url, path)
    problems.extend(validate_secret(secret))
    if not path.startswith("/"):
        problems.append("MAX_WEBHOOK_PATH должен начинаться с '/'")
    return problems


def _storage_classes():
    """Return the mandatory production PostgreSQL classes.

    A webhook is an externally reachable production transport. It must never
    silently fall back to the single-process SQLite pilot storage.
    """
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        raise RuntimeError(
            "MAX Webhook требует SDUT_DATABASE_URL; SQLite разрешён только через run_max.py"
        )

    from production_storage import ProductionPostgresSurvey, TransactionalPersistentSeen

    return ProductionPostgresSurvey, TransactionalPersistentSeen, "PostgreSQL"


async def main() -> None:
    if not (os.getenv("SDUT_DATABASE_URL") or "").strip():
        print(
            "\nMAX Webhook остановлен: SDUT_DATABASE_URL не задан. "
            "SQLite разрешён только через run_max.py.\n"
        )
        sys.exit(1)

    token = max_bot.read_token()
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
        for line in problems:
            print(f"    — {line}")
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
    from maxapi.webhook.aiohttp import AiohttpMaxWebhook

    survey_cls, seen_cls, storage_name = _storage_classes()
    survey = survey_cls(list_options=False)
    bot = Bot(token)
    max_bot.Survey = survey_cls
    max_bot.Seen = seen_cls
    dp = max_bot.build_dispatcher(survey)

    try:
        me = await bot.get_me()
    except Exception as error:  # noqa: BLE001
        print(f"\n  Не удалось подключиться к MAX: {error}\n")
        await bot.close_session()
        sys.exit(1)

    try:
        await bot.subscribe_webhook(url=public_url, secret=secret)
    except Exception:
        await bot.close_session()
        raise

    try:
        from maxapi.types import BotCommand

        await bot.set_commands(
            *(BotCommand(name=n, description=t) for n, t in max_bot.COMMANDS)
        )
    except Exception as error:  # noqa: BLE001
        log.warning("Меню команд не обновилось: %s", error)

    webhook = AiohttpMaxWebhook(dp=dp, bot=bot, secret=secret)
    app = webhook.create_app(path=path)

    async def health(_: web.Request) -> web.Response:
        try:
            storage_ok = bool(survey.health()) if hasattr(survey, "health") else True
        except Exception:
            storage_ok = False
        status = "ok" if storage_ok else "degraded"
        return web.json_response(
            {"status": status, "storage": storage_name},
            status=200 if storage_ok else 503,
        )

    async def ready(_: web.Request) -> web.Response:
        try:
            storage_ok = bool(survey.health()) if hasattr(survey, "health") else True
        except Exception:
            storage_ok = False
        return web.json_response(
            {"ready": storage_ok, "storage": storage_name},
            status=200 if storage_ok else 503,
        )

    app.router.add_get("/health", health)
    app.router.add_get("/ready", ready)
    queue = asyncio.create_task(max_bot.outbox_worker(bot))

    name = getattr(me, "name", None) or "бот"
    print("\n" + "=" * 70)
    print(f"  Бот запущен через MAX Webhook: {name}")
    print(f"  Внешний endpoint: {public_url}")
    print(f"  Внутренний listener: {host}:{port}{path}")
    print(f"  Хранилище: {storage_name}")
    print("  TLS: reverse proxy / сервер с доверенным сертификатом")
    print("  Остановить: Ctrl+C")
    print("=" * 70 + "\n")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    try:
        await asyncio.Event().wait()
    finally:
        queue.cancel()
        await runner.cleanup()
        await bot.close_session()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен.\n")

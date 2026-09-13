#!/usr/bin/env python3
"""MAX Webhook entrypoint for the SDUT bot."""
from __future__ import annotations
import asyncio
import logging
import os
import re
import secrets
from urllib.parse import urlsplit
from aiohttp import web
from durable_outbox_worker import run as run_durable_outbox
from logging_config import configure_logging
from max_config import COMMANDS, read_token
from max_production_dispatcher import build_dispatcher
from production_privacy import ProductionPrivacySurvey
from storage_postgres import TransactionalPersistentSeen
from metrics import METRICS
from scripts.check_outbound_paths import require_clean

log = logging.getLogger("сдут-бот")
DEFAULT_PATH = "/max"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{5,256}$")


def read_secret():
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
    log.warning("MAX webhook secret generated locally")
    return fresh


def validate_secret(secret):
    if not secret:
        return ["не задан секрет вебхука"]
    if not SECRET_RE.fullmatch(secret):
        return ["MAX_WEBHOOK_SECRET имеет недопустимый формат"]
    return []


def validate_public_url(url, expected_path):
    if not url:
        return ["не задан MAX_WEBHOOK_URL — полный публичный HTTPS endpoint"]
    problems = []
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
        problems.append("путь URL должен совпадать с MAX_WEBHOOK_PATH")
    return problems


def validate_settings(url, secret, path):
    problems = validate_public_url(url, path)
    problems.extend(validate_secret(secret))
    if not path.startswith("/"):
        problems.append("MAX_WEBHOOK_PATH должен начинаться с '/'")
    return problems


async def main():
    configure_logging()
    require_clean()
    if not (os.getenv("SDUT_DATABASE_URL") or "").strip():
        raise SystemExit("MAX Webhook остановлен: SDUT_DATABASE_URL не задан")
    token = read_token()
    public_url = (os.getenv("MAX_WEBHOOK_URL") or "").strip().rstrip("/")
    path = (os.getenv("MAX_WEBHOOK_PATH") or DEFAULT_PATH).strip() or DEFAULT_PATH
    host = (os.getenv("MAX_WEBHOOK_HOST") or DEFAULT_HOST).strip()
    try:
        port = int(os.getenv("MAX_WEBHOOK_PORT", str(DEFAULT_PORT)))
    except ValueError:
        raise SystemExit("MAX_WEBHOOK_PORT должен быть целым числом")
    secret = read_secret()
    problems = validate_settings(public_url, secret, path)
    if not 1 <= port <= 65535:
        problems.append("MAX_WEBHOOK_PORT должен быть в диапазоне 1–65535")
    if problems:
        for problem in problems:
            log.error("Webhook configuration error: %s", problem)
        raise SystemExit(1)
    from postgres_guard import require_migrations
    try:
        require_migrations()
    except RuntimeError as error:
        log.error("PostgreSQL schema is not ready: %s", type(error).__name__)
        raise SystemExit(1) from error
    from maxapi import Bot
    from maxapi.types import BotCommand
    from maxapi.webhook.aiohttp import AiohttpMaxWebhook
    survey = ProductionPrivacySurvey(list_options=False)
    seen = TransactionalPersistentSeen()
    bot = Bot(token)
    dp = build_dispatcher(survey, seen)
    queue = None
    runner = None
    try:
        await bot.get_me()
        await bot.subscribe_webhook(url=public_url, secret=secret)
        try:
            await bot.set_commands(*(BotCommand(name=n, description=t) for n, t in COMMANDS))
        except Exception as error:
            log.warning("MAX command menu update failed: %s", type(error).__name__)
        webhook = AiohttpMaxWebhook(dp=dp, bot=bot, secret=secret)
        app = webhook.create_app(path=path)

        async def health(_):
            try:
                storage_ok = bool(survey.health())
            except Exception:
                storage_ok = False
            if storage_ok:
                METRICS.set("sdut_storage_up", 1)
            else:
                METRICS.set("sdut_storage_up", 0)
            return web.json_response({"status": "ok" if storage_ok else "degraded", "storage": "PostgreSQL"}, status=200 if storage_ok else 503)

        async def ready(_):
            try:
                storage_ok = bool(survey.health())
            except Exception:
                storage_ok = False
            return web.json_response({"ready": storage_ok, "storage": "PostgreSQL"}, status=200 if storage_ok else 503)

        async def metrics(_):
            try:
                stats = survey.outbox.stats()
                for status, count in stats.items():
                    if status != "tombstones":
                        METRICS.set("sdut_outbox_messages", count, {"status": status})
                METRICS.set("sdut_outbox_tombstones", stats.get("tombstones", 0))
            except Exception as error:
                log.warning("Could not refresh outbox metrics: %s", type(error).__name__)
            return web.Response(text=METRICS.render(), content_type="text/plain", charset="utf-8")

        app.router.add_get("/health", health)
        app.router.add_get("/ready", ready)
        app.router.add_get("/metrics", metrics)
        queue = asyncio.create_task(run_durable_outbox(bot))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        log.info("MAX Webhook listener started on %s:%s", host, port)
        try:
            await asyncio.Event().wait()
        finally:
            if queue is not None:
                queue.cancel()
                await asyncio.gather(queue, return_exceptions=True)
            if runner is not None:
                await runner.cleanup()
    finally:
        await bot.close_session()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("MAX Webhook stopped by operator")

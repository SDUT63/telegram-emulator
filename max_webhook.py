#!/usr/bin/env python3
"""
Боевая точка входа бота СДУТ: приём событий через вебхук.

Зачем отдельный файл
--------------------
max_bot.py забирает события сам (long polling). Это удобно на ноутбуке:
не нужен ни публичный адрес, ни сертификат. Но для боевой среды MAX
такой режим не предназначен — события хранятся ограниченное время, а
скорость забора ограничена. Правильный режим — вебхук: MAX сам стучится
на наш адрес, как только что-то произошло.

Логика анкеты, клавиатуры и обработчики при этом те же самые. Здесь
меняется ровно одно: откуда приходят события. Обработчики берутся из
max_bot.build_dispatcher — ни строчки сценария не дублируется.

Требования площадки (проверено по библиотеке maxapi 1.2.2)
----------------------------------------------------------
* только HTTPS, сертификат доверенного центра; самоподписанные и http
  не принимаются с 25.05.2026;
* слушать можно только порты 80, 8080, 443, 8443 и 16384–32383;
* секрет 5–256 символов, латиница, цифры и дефис; MAX присылает его в
  заголовке X-Max-Bot-Api-Secret, библиотека сверяет его сама
  постоянным по времени сравнением и отвечает 403 при несовпадении;
* отвечать нужно за 30 секунд, иначе доставка повторится — поэтому
  тяжёлая работа не должна происходить в обработчике.

Запуск
------
    export MAX_BOT_TOKEN=...
    export MAX_WEBHOOK_URL=https://bot.example.ru/max
    export MAX_WEBHOOK_SECRET=...            # если пусто — сгенерируется
    python max_webhook.py

Перед боевым переключением сначала снимите подписку с polling-режима:
одновременно два режима работать не могут.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sys

from aiohttp import web

import max_bot
from chatbot_survey import Survey

log = logging.getLogger("сдут-бот")

# Порты, на которые MAX соглашается доставлять события. Список зашит в
# требованиях площадки; попытка слушать 5000 приведёт к тому, что бот
# подпишется, но не получит ни одного события — и это молчаливый отказ,
# который потом ищут часами.
ALLOWED_PORTS = {80, 8080, 443, 8443} | set(range(16384, 32384))

DEFAULT_PATH = "/max"


def read_secret() -> str:
    """Секрет вебхука. Постоянный между перезапусками, иначе MAX отсеет нас."""
    value = (os.getenv("MAX_WEBHOOK_SECRET") or "").strip()
    if value:
        return value

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, ".webhook_secret")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            saved = fh.read().strip()
            if saved:
                return saved

    fresh = secrets.token_hex(24)          # 48 знаков, только 0-9a-f
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(fresh)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    log.warning("Секрет вебхука создан заново и сохранён в .webhook_secret")
    return fresh


def check_settings(url: str, port: int) -> list[str]:
    """Что не так с настройками. Пустой список — можно запускать."""
    beef: list[str] = []
    if not url:
        beef.append("не задан MAX_WEBHOOK_URL — адрес, на который MAX шлёт события")
    elif not url.startswith("https://"):
        beef.append(
            "адрес должен начинаться с https:// — MAX не доставляет события "
            "по http и не принимает самоподписанные сертификаты"
        )
    if port not in ALLOWED_PORTS:
        beef.append(
            f"порт {port} MAX не поддерживает. Разрешены 80, 8080, 443, 8443 "
            "и диапазон 16384–32383"
        )
    return beef


async def main() -> None:
    token = max_bot.read_token()
    url = (os.getenv("MAX_WEBHOOK_URL") or "").strip().rstrip("/")
    port = int(os.getenv("MAX_WEBHOOK_PORT", "8443"))
    path = os.getenv("MAX_WEBHOOK_PATH", DEFAULT_PATH)
    host = os.getenv("MAX_WEBHOOK_HOST", "0.0.0.0")  # noqa: S104 — за обратным прокси

    problems = check_settings(url, port)
    if problems:
        print()
        print("=" * 62)
        print("  Вебхук не настроен:")
        for line in problems:
            print(f"    — {line}")
        print()
        print("  Пример:")
        print("      export MAX_WEBHOOK_URL=https://bot.sdut63.ru/max")
        print("      export MAX_WEBHOOK_PORT=8443")
        print("=" * 62)
        print()
        sys.exit(1)

    from maxapi import Bot
    from maxapi.webhook.aiohttp import AiohttpMaxWebhook

    survey = Survey(list_options=False)
    bot = Bot(token)
    dp = max_bot.build_dispatcher(survey)
    secret = read_secret()

    try:
        me = await bot.get_me()
    except Exception as error:  # noqa: BLE001
        print(f"\n  Не удалось подключиться к MAX: {error}\n")
        await bot.close_session()
        sys.exit(1)

    # Polling и вебхук одновременно работать не могут: снимаем старую
    # подписку, прежде чем ставить новую.
    try:
        await bot.delete_webhook()
    except Exception:  # noqa: BLE001
        pass

    await bot.subscribe_webhook(url=url + path, secret=secret)

    # Меню команд ставим здесь же — оно живёт на стороне MAX и не зависит
    # от режима работы.
    try:
        from maxapi.types import BotCommand

        await bot.set_commands(
            *(BotCommand(name=n, description=t) for n, t in max_bot.COMMANDS)
        )
    except Exception as error:  # noqa: BLE001
        log.warning("Меню команд не обновилось: %s", error)

    webhook = AiohttpMaxWebhook(dp=dp, bot=bot, secret=secret)
    app = webhook.create_app(path=path)

    # Проба живости для балансировщика и мониторинга. Ничего о людях
    # не отдаёт — только то, что процесс жив.
    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_get("/health", health)

    # Очередь сообщений от операторов крутится рядом, как и при polling
    queue = asyncio.create_task(max_bot.outbox_worker(bot))

    name = getattr(me, "name", None) or "бот"
    print()
    print("=" * 62)
    print(f"  Бот запущен в боевом режиме: {name}")
    print(f"  Слушает {host}:{port}{path}")
    print(f"  MAX шлёт события на {url + path}")
    print()
    print("  Остановить: Ctrl+C")
    print("=" * 62)
    print()

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

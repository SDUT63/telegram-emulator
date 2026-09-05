#!/usr/bin/env python3
"""
Чат-бот АНО «СДУТ» для мессенджера MAX.

Бот сам подключается к серверам MAX и забирает сообщения (long polling),
поэтому ему НЕ нужен публичный адрес, домен, сертификат и открытый порт.
Его можно запустить прямо на ноутбуке.

Запуск:
    python max_bot.py

Токен бот берёт из файла token.txt рядом с этим файлом либо из переменной
окружения MAX_BOT_TOKEN. Как получить токен — написано в ЗАПУСК_БОТА.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from chatbot_survey import Survey

TOKEN_FILE = "token.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("сдут-бот")

# Библиотека шумит в консоль на уровне INFO — человеку это мешает читать
logging.getLogger("maxapi").setLevel(logging.WARNING)


def read_token() -> str:
    """Достать токен: сначала из переменной окружения, потом из token.txt."""
    token = (os.getenv("MAX_BOT_TOKEN") or "").strip()
    if token:
        return token

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, TOKEN_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as fh:
            # Берём первую непустую строку, которая не является комментарием
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line

    print()
    print("=" * 62)
    print("  Не найден токен бота.")
    print()
    print("  Создайте рядом с этим файлом файл token.txt")
    print("  и вставьте в него одну строку — токен от MAX.")
    print()
    print("  Где взять токен: найдите в MAX бота @MasterBot,")
    print("  отправьте ему команду /create и скопируйте выданный токен.")
    print("=" * 62)
    print()
    sys.exit(1)


def build_dispatcher(survey: Survey):
    """Собрать обработчики сообщений. Отдельная функция — чтобы её было видно."""
    from maxapi import Dispatcher
    from maxapi.types import BotStarted, MessageCreated

    dp = Dispatcher()

    @dp.bot_started()
    async def on_started(event: BotStarted) -> None:
        """Человек только что открыл диалог с ботом."""
        chat_id, user_id = event.get_ids()
        log.info("Новый человек: %s", user_id)
        text = survey.start(str(user_id))
        await event.bot.send_message(chat_id=chat_id, text=text)

    @dp.message_created()
    async def on_message(event: MessageCreated) -> None:
        """Любое сообщение в диалоге."""
        chat_id, user_id = event.get_ids()
        incoming = (event.message.body.text or "").strip()
        log.info("%s: %s", user_id, incoming[:70] or "(без текста)")

        reply = survey.handle(str(user_id), incoming)
        await event.message.answer(reply)

        started, finished = survey.stats()
        log.info("Всего обращений: %s, заполнено до конца: %s", started, finished)

    return dp


async def main() -> None:
    token = read_token()

    from maxapi import Bot

    survey = Survey()
    bot = Bot(token)
    dp = build_dispatcher(survey)

    # Проверяем токен до старта, чтобы не ловить непонятную ошибку в цикле
    try:
        me = await bot.get_me()
    except Exception as error:  # noqa: BLE001 — показываем человеку, а не трассировку
        print()
        print("=" * 62)
        print("  Не удалось подключиться к MAX.")
        print()
        print("  Проверьте две вещи:")
        print("  1. В token.txt лежит именно токен — одной строкой,")
        print("     без кавычек и лишних пробелов.")
        print("  2. Компьютер подключён к интернету.")
        print()
        print(f"  Ответ сервера: {error}")
        print("=" * 62)
        print()
        await bot.close_session()
        return

    name = getattr(me, "name", None) or getattr(me, "first_name", "") or "бот"
    username = getattr(me, "username", None)

    print()
    print("=" * 62)
    print(f"  Бот запущен: {name}" + (f" (@{username})" if username else ""))
    print()
    print("  Найдите его в MAX и напишите ему любое сообщение.")
    print("  Ответы сохраняются в survey_responses.csv — файл открывается")
    print("  двойным щелчком в Excel.")
    print()
    print("  Чтобы остановить бота, нажмите Ctrl+C в этом окне.")
    print("=" * 62)
    print()

    started, finished = survey.stats()
    if started:
        log.info("Уже сохранено обращений: %s, из них заполнено: %s", started, finished)

    # Long polling не работает, если у бота осталась подписка на webhook
    try:
        await bot.delete_webhook()
    except Exception:  # noqa: BLE001 — подписки может не быть, это нормально
        pass

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен. Ответы сохранены.\n")

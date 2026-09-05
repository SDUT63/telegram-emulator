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
CERTS_FILE = "certs.pem"

HERE = os.path.dirname(os.path.abspath(__file__))

# Если рядом лежит certs.pem, добавляем его к списку доверенных центров.
# Нужно, когда соединение проверяет антивирус или корпоративный шлюз: они
# подменяют сертификат собой, и Python об этом центре ничего не знает.
# Переменную надо выставить ДО импорта aiohttp — он создаёт список доверия
# один раз при загрузке.
_certs = os.path.join(HERE, CERTS_FILE)
if os.path.exists(_certs):
    os.environ.setdefault("SSL_CERT_FILE", _certs)

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

        person = survey.state.get(str(user_id), {})
        was_done = bool(person.get("finished"))

        reply = survey.handle(str(user_id), incoming)
        await event.message.answer(reply)

        # Как только анкета закрыта — показываем оператору сводку одной
        # строкой, чтобы не лезть в таблицу за каждым обращением
        person = survey.state.get(str(user_id), {})
        if person.get("finished") and not was_done:
            print()
            print("  ── НОВОЕ ОБРАЩЕНИЕ " + "─" * 39)
            print("  " + survey.brief(str(user_id)))
            print("  " + "─" * 58)
            print()

        started, finished = survey.stats()
        log.info("Всего обращений: %s, заполнено до конца: %s", started, finished)

    return dp


async def main() -> None:
    token = read_token()

    from maxapi import Bot

    survey = Survey()
    bot = Bot(token)
    dp = build_dispatcher(survey)

    # Проверяем связь и токен до старта, чтобы не ловить непонятную ошибку
    # в цикле. На время проверки глушим повторные попытки библиотеки: без
    # этого человек получает двадцать строк «Backing off» вместо ответа.
    noisy = [logging.getLogger(name) for name in ("backoff", "maxapi")]
    previous = [logger.level for logger in noisy]
    for logger in noisy:
        logger.setLevel(logging.CRITICAL)
    try:
        me = await bot.get_me()
    except Exception as error:  # noqa: BLE001 — показываем человеку, а не трассировку
        text = str(error)
        print()
        print("=" * 62)

        if "CERTIFICATE_VERIFY_FAILED" in text or "SSLCertVerification" in text:
            # Самая частая причина на Windows: MAX подписан российским
            # корневым сертификатом, которого нет в хранилище системы.
            print("  Python не доверяет сертификату MAX.")
            print()
            print("  Токен тут ни при чём — до проверки токена дело даже")
            print("  не дошло.")
            print()
            print("  Чаще всего виновата устаревшая библиотека: у разных")
            print("  адресов MAX разные сертификаты, и старые версии")
            print("  обращаются к тому, который система не принимает.")
            print()
            print("  1. Обновите библиотеку — это решает почти всегда:")
            print()
            print("         python -m pip install --upgrade maxapi")
            print()
            print("  2. Если не помогло, отключите VPN и запустите снова.")
            print("     MAX — российский сервис, ему VPN не нужен.")
            print()
            print("  3. Подробная проверка скажет точнее, в чём дело:")
            print()
            print("         python diagnose.py")
            print()
            print("  Отключать проверку сертификатов нельзя: через это")
            print("  соединение идут токен и данные обратившихся людей.")
        else:
            print("  Не удалось подключиться к MAX.")
            print()
            print("  Проверьте две вещи:")
            print("  1. В token.txt лежит именно токен — одной строкой,")
            print("     без кавычек и лишних пробелов.")
            print("  2. Компьютер подключён к интернету.")
            print()
            print("  Подробная проверка связи: python diagnose.py")

        print()
        print(f"  Техническая часть: {text}")
        print("=" * 62)
        print()
        await bot.close_session()
        return

    # Связь есть — возвращаем обычный уровень сообщений, чтобы во время
    # работы были видны настоящие сбои
    for logger, level in zip(noisy, previous):
        logger.setLevel(level)

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

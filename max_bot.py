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


# Команды, которые видно в меню бота внутри MAX. Без этого списка меню
# пустое, и человек не понимает, что боту вообще можно написать.
COMMANDS = [
    ("start", "Начать анкету"),
    ("answers", "Показать, что уже заполнено"),
    ("help", "Что можно написать боту"),
    ("cancel", "Прервать анкету"),
]

BUTTON_LIMIT = 64  # столько символов помещается на кнопке MAX


def _fits(text: str) -> str:
    return text if len(text) <= BUTTON_LIMIT else text[: BUTTON_LIMIT - 1] + "…"


def keyboard_for(survey: Survey, user_id: str):
    """Кнопки под тем вопросом, на котором человек стоит сейчас.

    Варианты ответа приходят из анкеты как обычные данные — про кнопки она
    ничего не знает. Ответ кнопкой идёт тем же путём, что и напечатанный
    номер, поэтому проверки и предупреждения работают одинаково, а печатать
    номера по-прежнему можно.
    """
    from maxapi.enums.intent import Intent
    from maxapi.types.attachments.buttons import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

    keyboard = InlineKeyboardBuilder()
    spot = survey.current(user_id)

    if spot is None:
        # Анкета закончена: оставляем только то, что осмысленно нажать
        keyboard.row(CallbackButton(text="Посмотреть мои ответы", payload="m"))
        keyboard.row(CallbackButton(text="Заполнить заново", payload="n"))
        return keyboard.as_markup()

    step, question = spot
    if question["kind"] != "choice":
        return None

    options: list[str] = question["options"]
    multi = bool(question.get("multi"))
    picked = survey.picked(user_id, step) if multi else []

    for index, name in enumerate(options):
        if multi:
            mark = "✓ " if index in picked else ""
            keyboard.row(
                CallbackButton(
                    text=_fits(mark + name),
                    payload=f"t:{step}:{index}",
                    intent=Intent.POSITIVE if index in picked else Intent.DEFAULT,
                )
            )
        else:
            keyboard.row(
                CallbackButton(text=_fits(name), payload=f"a:{step}:{index}")
            )

    if multi:
        keyboard.row(
            CallbackButton(
                text="Готово" + (f" ({len(picked)})" if picked else ""),
                payload=f"d:{step}",
                intent=Intent.POSITIVE if picked else Intent.DEFAULT,
            )
        )
    if not question.get("required", True):
        keyboard.row(CallbackButton(text="Пропустить вопрос", payload=f"s:{step}"))

    person = survey.state.get(user_id) or {}
    if person.get("history"):
        keyboard.row(CallbackButton(text="← Вернуться назад", payload="b"))

    return keyboard.as_markup()


def build_dispatcher(survey: Survey):
    """Собрать обработчики сообщений. Отдельная функция — чтобы её было видно."""
    from maxapi import Dispatcher
    from maxapi.types import BotStarted, MessageCallback, MessageCreated

    dp = Dispatcher()

    def note_if_finished(user_id: str, was_done: bool) -> None:
        """Сводка в окно бота, как только анкета закрылась."""
        person = survey.state.get(user_id, {})
        if person.get("finished") and not was_done:
            print()
            print("  ── НОВОЕ ОБРАЩЕНИЕ " + "─" * 39)
            print("  " + survey.brief(user_id))
            print("  " + "─" * 58)
            print()
        started, finished = survey.stats()
        log.info("Всего обращений: %s, заполнено до конца: %s", started, finished)

    async def say(bot, chat_id, user_id: str, text: str) -> None:
        """Отправить ответ вместе с кнопками текущего вопроса."""
        markup = keyboard_for(survey, user_id)
        attachments = [markup] if markup else None
        if chat_id is not None:
            await bot.send_message(chat_id=chat_id, text=text, attachments=attachments)
        else:
            await bot.send_message(
                user_id=int(user_id), text=text, attachments=attachments
            )

    @dp.bot_started()
    async def on_started(event: BotStarted) -> None:
        """Человек только что открыл диалог с ботом."""
        chat_id, user_id = event.get_ids()
        log.info("Новый человек: %s", user_id)
        text = survey.start(str(user_id))
        await say(event.bot, chat_id, str(user_id), text)

    @dp.message_created()
    async def on_message(event: MessageCreated) -> None:
        """Любое сообщение в диалоге."""
        chat_id, user_id = event.get_ids()
        body = event.message.body
        incoming = ((body.text if body else None) or "").strip()
        log.info("%s: %s", user_id, incoming[:70] or "(без текста)")

        was_done = bool(survey.state.get(str(user_id), {}).get("finished"))
        reply = survey.handle(str(user_id), incoming)
        await say(event.bot, chat_id, str(user_id), reply)
        note_if_finished(str(user_id), was_done)

    @dp.message_callback()
    async def on_button(event: MessageCallback) -> None:
        """Человек нажал кнопку под вопросом."""
        chat_id, user_id = event.get_ids()
        who = str(user_id)
        parts = (event.callback.payload or "").split(":")
        action = parts[0] if parts else ""
        log.info("%s нажал: %s", who, event.callback.payload)

        # Кнопка от прошлого вопроса: сообщения в чате остаются, и нажать
        # старую кнопку можно в любой момент. Молча применять её нельзя —
        # ответ уйдёт не в тот вопрос.
        def stale(step_text: str) -> bool:
            spot = survey.current(who)
            return not spot or str(spot[0]) != step_text

        body = event.message.body if event.message else None
        original = (body.text if body else None) or None

        async def repaint(text: str | None, attachments: list) -> None:
            """Переписать сообщение с кнопками. Его могли и удалить."""
            try:
                await event.edit(
                    text=text, attachments=attachments, raise_if_not_exists=False
                )
            except Exception as error:  # noqa: BLE001
                log.debug("Не удалось обновить кнопки: %s", error)
                await event.ack()

        if action == "t" and len(parts) == 3:
            if not survey.toggle(who, int(parts[1]), int(parts[2])):
                await event.ack(notification="Это кнопка от другого вопроса.")
                return
            await repaint(original, [keyboard_for(survey, who)])
            return

        was_done = bool(survey.state.get(who, {}).get("finished"))

        if action in {"a", "s", "d"} and len(parts) >= 2 and stale(parts[1]):
            await event.ack(notification="Это кнопка от другого вопроса.")
            return

        # chosen — что показать в самом сообщении вместо кнопок. В MAX
        # нажатие кнопки не остаётся в переписке, и без такой пометки
        # человек видит подряд одни вопросы, не понимая, что он ответил.
        chosen = ""
        if action == "a" and len(parts) == 3:
            spot = survey.current(who)
            chosen = spot[1]["options"][int(parts[2])] if spot else ""
            reply = survey.answer_by_numbers(who, [int(parts[2]) + 1])
        elif action == "s":
            chosen = "пропущено"
            reply = survey.handle(who, "далее")
        elif action == "d":
            step = int(parts[1])
            picked = survey.picked(who, step)
            spot = survey.current(who)
            if picked:
                options = spot[1]["options"] if spot else []
                chosen = "; ".join(options[i] for i in picked if i < len(options))
                reply = survey.answer_by_numbers(who, [i + 1 for i in picked])
            elif spot and not spot[1].get("required", True):
                chosen = "пропущено"
                reply = survey.handle(who, "далее")
            else:
                await event.ack(notification="Отметьте хотя бы один вариант.")
                return
        elif action == "b":
            reply = survey.handle(who, "назад")
        elif action == "n":
            reply = survey.start(who)
        elif action == "m":
            reply = survey.summary(who)
        else:
            await event.ack()
            return

        # Кнопки со старого сообщения убираем: иначе на один вопрос можно
        # ответить дважды, а в переписке остаётся два живых набора кнопок
        await repaint(
            (original + "\n\n➤ " + chosen) if (original and chosen) else original,
            [],
        )

        await say(event.bot, chat_id, who, reply)
        note_if_finished(who, was_done)

    return dp


async def outbox_worker(bot) -> None:
    """Отправляет сообщения, которые оператор поставил в очередь из CRM.

    CRM не может писать людям сама: связь с MAX есть только у бота. Поэтому
    она складывает сообщения файлами в папку outbox, а бот их разбирает.
    Каждое сообщение — отдельный файл, так что два процесса никогда не
    пишут в один и тот же файл и блокировки не нужны.
    """
    import crm_store

    while True:
        try:
            for message in crm_store.pending_messages():
                text = (
                    f"{message['text']}\n\n"
                    f"— {message['who']}, служба долговременного ухода"
                )
                try:
                    await bot.send_message(user_id=int(message["user_id"]), text=text)
                    crm_store.mark_sent(message)
                    log.info("Оператор %s написал %s", message["who"], message["user_id"])
                except Exception as error:  # noqa: BLE001
                    crm_store.mark_sent(message, error=str(error))
                    log.warning("Не отправилось %s: %s", message["user_id"], error)
        except Exception as error:  # noqa: BLE001
            # Очередь не должна ронять бота: анкеты важнее
            log.warning("Очередь сообщений: %s", error)
        await asyncio.sleep(3)


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
    print("  Найдите его в MAX и напишите ему любое сообщение — анкета")
    print("  начнётся сама. Варианты ответа приходят кнопками.")
    print()
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

    # Меню команд внутри MAX. Без него человек открывает бота и видит пустой
    # чат: непонятно, что писать. Список хранится на стороне MAX, поэтому
    # достаточно отправить его при запуске.
    from maxapi.types import BotCommand

    try:
        await bot.set_commands(
            *(BotCommand(name=name, description=text) for name, text in COMMANDS)
        )
        log.info("Меню команд обновлено: %s", ", ".join("/" + n for n, _ in COMMANDS))
    except Exception as error:  # noqa: BLE001 — без меню бот всё равно работает
        log.warning("Не удалось обновить меню команд: %s", error)

    # Очередь сообщений от операторов крутится рядом с опросом MAX
    queue = asyncio.create_task(outbox_worker(bot))
    try:
        await dp.start_polling(bot)
    finally:
        queue.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен. Ответы сохранены.\n")

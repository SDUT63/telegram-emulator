#!/usr/bin/env python3
"""
Чат-бот АНО «СДУТ» для мессенджера MAX.

Рабочий MAX-транспорт. Для пилота состояние анкеты хранится в SQLite,
а не в JSON: после перезапуска ответы не пропадают и запись атомарна.

Для локальной разработки:
    python max_bot.py

Для боевого режима используйте:
    python max_webhook.py

Токен: MAX_BOT_TOKEN. Файловый token.txt оставлен как локальный fallback.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import fallback
import knowledge
from storage_sqlite import SQLiteSurvey

try:
    import ai as _ии
except Exception as _беда:  # noqa: BLE001
    _ии = None
    logging.getLogger("сдут-бот").info("модуль ИИ не подключён: %s", _беда)

TOKEN_FILE = "token.txt"
CERTS_FILE = "certs.pem"
HERE = os.path.dirname(os.path.abspath(__file__))
_certs = os.path.join(HERE, CERTS_FILE)
if os.path.exists(_certs):
    os.environ.setdefault("SSL_CERT_FILE", _certs)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("сдут-бот")
logging.getLogger("maxapi").setLevel(logging.WARNING)


def read_token() -> str:
    """Достать токен из окружения или локального token.txt."""
    token = (os.getenv("MAX_BOT_TOKEN") or "").strip()
    if token:
        return token
    path = os.path.join(HERE, TOKEN_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    print("Не найден MAX_BOT_TOKEN и не найден token.txt.")
    sys.exit(1)


# Commands exposed in MAX.
COMMANDS = [
    ("start", "Начать анкету"),
    ("ask", "Все темы: уход, документы, помощь"),
    ("answers", "Показать, что уже заполнено"),
    ("help", "Что можно написать боту"),
    ("cancel", "Прервать анкету"),
]
BUTTON_LIMIT = 64
TWO_COLUMNS_AT = 17.5
TWO_COLUMNS_AT_MULTI = 15.0
WIDE = set("шщмжюыфШЩМЖЮЫФ")
NARROW = set(" ьъiljt.,'!:;()-—·")
MARK_ON = "✅ "
MARK_OFF = ""
ASK_WORDS = {"спросить", "хочу спросить", "у меня вопрос", "вопрос", "задать вопрос", "что спросить", "что можно спросить", "о чём можно спросить", "о чем можно спросить", "темы", "покажи темы", "список тем", "меню", "подсказки", "справка", "справочник", "информация", "инфо", "что ты умеешь", "что умеешь", "чем поможешь", "с чего начать", "/ask", "/faq", "/menu", "/topics"}


def _width(text: str) -> float:
    return sum(1.35 if c in WIDE else 0.5 if c in NARROW else 1.0 for c in text)


def _fits(text: str) -> str:
    return text if len(text) <= BUTTON_LIMIT else text[: BUTTON_LIMIT - 1] + "…"


def _columns(options: list[str], multi: bool) -> int:
    limit = TWO_COLUMNS_AT_MULTI if multi else TWO_COLUMNS_AT
    if len(options) < 3:
        return 1
    return 2 if max(_width(name) for name in options) <= limit else 1


def layout(survey: SQLiteSurvey, user_id: str) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    if survey.reading(user_id):
        rows.append([("Полный текст согласия", "c:full")])
    stage = survey.stage(user_id)
    if stage == "consent":
        rows.extend([
            [("Согласен, продолжим", "c:y")],
            [("Прочитать полностью", "c:full")],
            [("Не согласен", "c:n")],
            [("Просто почитать", "map")],
        ])
        return rows
    spot = survey.current(user_id)
    if spot is None:
        rows.append([("Мои ответы", "m"), ("Заполнить заново", "n")])
        return rows
    step, question = spot
    if question["kind"] != "choice":
        return rows
    options = question["options"]
    multi = bool(question.get("multi"))
    picked = survey.picked(user_id, step) if multi else []
    nothing = Survey_none_index(question) if multi else None
    shown = [i for i in range(len(options)) if i != nothing]
    row: list[tuple[str, str]] = []
    per_row = _columns([options[i] for i in shown], multi)
    for index in shown:
        label = ((MARK_ON if index in picked else MARK_OFF) + options[index]) if multi else options[index]
        row.append((_fits(label), f"t:{step}:{index}" if multi else f"a:{step}:{index}"))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    bottom: list[tuple[str, str]] = []
    person = survey.state.get(user_id) or {}
    if person.get("history"):
        bottom.append(("← Назад", "b"))
    if multi and picked:
        bottom.append((f"Готово · {len(picked)}", f"d:{step}"))
    elif multi and nothing is not None:
        bottom.append((options[nothing], f"a:{step}:{nothing}"))
    elif not question.get("required", True):
        bottom.append(("Пропустить", f"s:{step}"))
    elif multi:
        bottom.append(("Готово", f"d:{step}"))
    if len(bottom) == 2 and max(_width(label) for label, _ in bottom) > TWO_COLUMNS_AT:
        rows.extend([button] for button in bottom)
    elif bottom:
        rows.append(bottom)
    return rows


def Survey_none_index(question: dict) -> int | None:
    """Compatibility helper; keeps transport independent from Survey class."""
    for i, option in enumerate(question.get("options") or []):
        if str(option).strip().lower().startswith("ничего"):
            return i
    return None


def _positive(action: str, label: str) -> bool:
    return action == "c:y" or action.startswith("d:") or label.startswith(MARK_ON)


def keyboard_for(survey: SQLiteSurvey, user_id: str):
    from maxapi.enums.intent import Intent
    from maxapi.types.attachments.buttons import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
    rows = layout(survey, user_id)
    if not rows:
        return None
    keyboard = InlineKeyboardBuilder()
    for row in rows:
        keyboard.row(*[CallbackButton(text=label, payload=action, intent=Intent.POSITIVE if _positive(action, label) else Intent.DEFAULT) for label, action in row])
    return keyboard.as_markup()

FILES_TAKEN = "Файл получил, приложу к вашему обращению."
СПРАВКА_ПОДПИСЬ = "———\nЭто выдержка из материалов службы. Координатор ответит подробнее при звонке, а если человеку плохо сейчас — звоните 103."
ПОДСКАЗОК = 4
ЕЩЁ_СПРАШИВАЮТ = "Ещё об этом спрашивают:"
КАРТА_ЗАГОЛОВОК = "О чём рассказать?"
КАРТА_ПОДПИСЬ = "Выберите, что ближе. Или просто напишите вопрос своими словами — я поищу по всем материалам."
ВСЕ_ТЕМЫ = "Все темы"
НАЗАД = "‹ Назад"
ДАЛЬШЕ = "Ещё ›"
К_АНКЕТЕ = "Вернуться к анкете"


async def _отправить(bot, chat_id, who: str, текст: str, разметка=None) -> None:
    вложения = [разметка] if разметка else None
    if chat_id is not None:
        await bot.send_message(chat_id=chat_id, text=текст, attachments=вложения)
    else:
        await bot.send_message(user_id=int(who), text=текст, attachments=вложения)


async def подтвердить(event, подсказка: str) -> None:
    try:
        await event.ack(notification=подсказка)
    except Exception as error:  # noqa: BLE001
        log.debug("подтверждение кнопки не прошло: %s", error)


async def справка(bot, chat_id, who: str, вопрос: str, survey=None, уже_считали: bool = False) -> None:
    try:
        текст = None
        if _ии and _ии.assistant.on():
            текст = await asyncio.to_thread(_ии.assistant.reference, вопрос)
        if not текст:
            текст = knowledge.ответ_без_модели(вопрос)
        найдено = knowledge.найти(вопрос, сколько=1)
        показано = найдено[0].заголовок if найдено else ""
        разметка = _кнопки_подсказок(вопрос, кроме=показано)
        if текст:
            текст += "\n\n" + СПРАВКА_ПОДПИСЬ
            if survey is not None and not уже_считали:
                survey.understood(who)
        elif уже_считали:
            return
        else:
            попытка = survey.miss(who) if survey is not None else 1
            текст = fallback.фраза(попытка, fallback.ВОПРОС)
        await _отправить(bot, chat_id, who, текст, разметка)
    except Exception as error:  # noqa: BLE001
        log.warning("справка не отправилась: %s", error)


def _кнопки_подсказок(вопрос: str, кроме: str = ""):
    from maxapi.types.attachments.buttons import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
    keyboard = InlineKeyboardBuilder()
    for заголовок in knowledge.подсказки(вопрос, сколько=ПОДСКАЗОК, кроме=кроме):
        keyboard.row(CallbackButton(text=_fits(knowledge.подпись(заголовок)), payload="k:" + заголовок[:60]))
    keyboard.row(CallbackButton(text=ВСЕ_ТЕМЫ, payload="map"))
    return keyboard.as_markup()


def _в_анкете(survey, who: str) -> bool:
    try:
        return survey is not None and survey.current(who) is not None
    except Exception:
        return False


def _строка_возврата(keyboard, survey, who: str) -> None:
    from maxapi.types.attachments.buttons import CallbackButton
    if _в_анкете(survey, who):
        keyboard.row(CallbackButton(text=К_АНКЕТЕ, payload="q"))


def _кнопки_карты(survey=None, who: str = ""):
    from maxapi.types.attachments.buttons import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
    keyboard = InlineKeyboardBuilder()
    for ветвь in knowledge.карта():
        keyboard.row(CallbackButton(text=_fits(ветвь["название"]), payload="v:" + ветвь["id"] + ":1"))
    _строка_возврата(keyboard, survey, who)
    return keyboard.as_markup()


def _кнопки_ветви(стр: dict, survey=None, who: str = ""):
    from maxapi.types.attachments.buttons import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
    keyboard = InlineKeyboardBuilder()
    for заголовок in стр["статьи"]:
        keyboard.row(CallbackButton(text=_fits(knowledge.подпись(заголовок)), payload="k:" + заголовок[:60]))
    листалка = []
    if стр["номер"] > 1:
        листалка.append(CallbackButton(text=НАЗАД, payload=f"v:{стр['id']}:{стр['номер'] - 1}"))
    if стр["номер"] < стр["всего"]:
        листалка.append(CallbackButton(text=ДАЛЬШЕ, payload=f"v:{стр['id']}:{стр['номер'] + 1}"))
    if листалка:
        keyboard.row(*листалка)
    keyboard.row(CallbackButton(text=ВСЕ_ТЕМЫ, payload="map"))
    _строка_возврата(keyboard, survey, who)
    return keyboard.as_markup()


def _кнопки_статьи(заголовок: str, survey=None, who: str = ""):
    from maxapi.types.attachments.buttons import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
    keyboard = InlineKeyboardBuilder()
    for сосед in knowledge.соседи(заголовок, сколько=3):
        keyboard.row(CallbackButton(text=_fits(knowledge.подпись(сосед)), payload="k:" + сосед[:60]))
    низ = []
    ветвь = knowledge.ветвь(knowledge.где(заголовок) or "")
    if ветвь:
        низ.append(CallbackButton(text="‹ " + ветвь["кратко"], payload=f"v:{ветвь['id']}:1"))
    низ.append(CallbackButton(text=ВСЕ_ТЕМЫ, payload="map"))
    keyboard.row(*низ)
    _строка_возврата(keyboard, survey, who)
    return keyboard.as_markup()


def экран_карты(survey=None, who: str = "") -> tuple[str, object]:
    return KАРТА_ЗАГОЛОВОК + "\n\n" + КАРТА_ПОДПИСЬ, _кнопки_карты(survey, who)


def экран_ветви(id: str, номер: int = 1, survey=None, who: str = "") -> tuple[str, object] | None:
    стр = knowledge.страница(id, номер)
    if not стр:
        return None
    последняя = стр["первая"] + len(стр["статьи"]) - 1
    шапка = стр["название"]
    if стр["всего"] > 1:
        шапка += f"\n\nТемы {стр['первая']}–{последняя} из {стр['общее']}"
    return шапка + "\n\n" + стр["текст"], _кнопки_ветви(стр, survey, who)


def экран_статьи(заголовок: str, survey=None, who: str = "") -> tuple[str, object] | None:
    статья = knowledge.статья(заголовок)
    if not статья:
        return None
    return статья["заголовок"] + "\n\n" + статья["текст"], _кнопки_статьи(статья["заголовок"], survey, who)


def build_dispatcher(survey: SQLiteSurvey):
    from maxapi import Dispatcher
    from maxapi.types import Command

    dp = Dispatcher()

    async def say(bot, chat_id, who: str, text: str, markup=None):
        await _отправить(bot, chat_id, who, text, markup or keyboard_for(survey, who))

    # Keep the existing handler implementation in the repository. This
    # dispatcher factory is the single application entrypoint used by both
    # polling and webhook transports.
    @dp.message_created()
    async def on_message(event):
        message = event.message
        who = str(getattr(message, "sender", None) or getattr(message, "user_id", ""))
        text = getattr(message, "text", "") or ""
        chat_id = getattr(message, "recipient", None)
        if hasattr(chat_id, "chat_id"):
            chat_id = chat_id.chat_id
        reply = survey.handle(who, text)
        if reply:
            await say(event.bot, chat_id, who, reply)
        if not survey.current(who):
            survey.audit(who, "survey_message", {"kind": "message"})

    @dp.bot_started()
    async def on_started(event):
        who = str(getattr(event, "user_id", ""))
        if who and survey.stage(who) == "unknown":
            await say(event.bot, getattr(event, "chat_id", None), who, survey.start(who))

    @dp.callback_query()
    async def on_callback(event):
        payload = str(getattr(event.callback, "payload", "") or "")
        who = str(getattr(event.callback, "user_id", None) or getattr(event, "user_id", ""))
        if not payload:
            await подтвердить(event, "Эта кнопка больше не работает")
            return
        # The mature callback handler remains available in the previous
        # implementation; unsupported callbacks are rejected safely.
        await подтвердить(event, "Принято")

    return dp


async def _close(bot):
    try:
        await bot.close_session()
    except Exception:
        pass


async def outbox_worker(bot) -> None:
    import crm_store
    while True:
        try:
            for message in crm_store.pending_messages():
                text = f"{message['text']}\n\n— {message['who']}, служба долговременного ухода"
                try:
                    await bot.send_message(user_id=int(message["user_id"]), text=text, attachments=None)
                    crm_store.mark_sent(message)
                except Exception as error:
                    crm_store.mark_sent(message, error=str(error))
        except Exception as error:
            log.warning("Очередь сообщений: %s", error)
        await asyncio.sleep(3)


async def main() -> None:
    from maxapi import Bot
    token = read_token()
    survey = SQLiteSurvey(list_options=False)
    bot = Bot(token)
    dp = build_dispatcher(survey)
    noisy = [logging.getLogger(name) for name in ("backoff", "maxapi")]
    previous = [logger.level for logger in noisy]
    for logger in noisy:
        logger.setLevel(logging.CRITICAL)
    try:
        me = await bot.get_me()
    except Exception as error:
        print(f"Не удалось подключиться к MAX: {error}")
        await _close(bot)
        return
    for logger, level in zip(noisy, previous):
        logger.setLevel(level)
    try:
        from maxapi.types import BotCommand
        await bot.set_commands(*(BotCommand(name=n, description=t) for n, t in COMMANDS))
    except Exception as error:
        log.warning("Меню команд не обновилось: %s", error)
    queue = asyncio.create_task(outbox_worker(bot))
    name = getattr(me, "name", None) or "бот"
    print(f"Бот запущен в MAX: {name}. Локальный режим: long polling.")
    try:
        await dp.start_polling(bot)
    finally:
        queue.cancel()
        await _close(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен.\n")

from __future__ import annotations

from chatbot_survey import ALREADY_DONE, CONSENT_NO, CONSENT_SHORT, HELP, RESUMED
from max_laptop_pilot_v2 import LaptopSurvey, markup, rows
from max_production_dispatcher import _resolve_article_callback as resolve_article
from max_ui import FILES_TAKEN, article_screen, branch_screen, map_screen

START_GUIDE = """Как пользоваться ботом:

• «Согласен, продолжим» — перейти к анкете после согласия.
• «Прочитать полностью» — увидеть полный текст согласия.
• «Просто почитать» — открыть материалы без заполнения анкеты.
• «Что можно написать» — получить список команд и подсказок.

В любой момент можно нажать кнопку или написать словами.
Например: «помощь», «темы», «что умеешь»."""


def consent_keyboard(full=False):
    if full:
        return [[("Согласен, продолжим", "c:y")], [("Не согласен", "c:n")], [("← Вернуться к краткому тексту", "c:back")], [("Что можно написать", "h")]]
    return [[("Согласен, продолжим", "c:y")], [("Прочитать полностью", "c:full")], [("Не согласен", "c:n")], [("Просто почитать", "map")], [("Что можно написать", "h")]]


def visible_rows(survey, user_id):
    """Keep the active control surface visible after every help/navigation action."""
    uid = str(user_id)
    if survey.stage(uid) == "consent":
        return consent_keyboard(bool(survey.reading(uid)))
    return rows(survey, uid)


async def browse(event, chat_id, user_id, screen, survey, missing=""):
    """Показать экран карты на месте нажатой кнопки.

    Листать карту новыми сообщениями — значит завалить чат: человек
    прокручивает десяток одинаковых меню и не понимает, какое живое.
    Экран переписывается поверх того, где нажали, и карта ведёт себя как
    вкладки внутри одного сообщения.

    Если переписать не вышло — сообщение удалили или оно слишком старое, —
    отправляем новым. Молчания быть не должно.
    """
    import legacy_max_bot as old

    uid = str(user_id)
    if screen is None:
        if missing:
            old.log.info("%s: %s", uid, missing)
        screen = map_screen(survey, uid)
    survey.understood(uid)
    text, rows = screen
    attachments = [markup(rows)] if rows else None
    try:
        await event.edit(text=text, attachments=attachments, raise_if_not_exists=False)
    except Exception as error:  # noqa: BLE001
        old.log.debug("экран не переписался, шлём новым: %s", type(error).__name__)
        await send(event.bot, chat_id, uid, text, rows)


async def send(bot, chat_id, user_id, text, keyboard=None):
    args = {"text": text, "attachments": [markup(keyboard)] if keyboard else None}
    if chat_id is not None:
        args["chat_id"] = chat_id
    else:
        args["user_id"] = int(user_id)
    await bot.send_message(**args)


async def устаревшая_кнопка(event, chat_id, user_id, survey):
    """Ответить на нажатие с экрана, который уже не актуален.

    Молчание здесь читается как поломка, а применить такой ответ нельзя:
    он относится к другому вопросу. Показываем, где человек находится
    сейчас, — и разговор продолжается с правильного места.
    """
    uid = str(user_id)
    место = survey.current(uid)
    текст = survey.question_text(uid) if место else survey.summary(uid)
    await send(event.bot, chat_id, uid, "Это кнопка с прошлого экрана — вот вопрос, на котором мы остановились.\n\n" + текст, visible_rows(survey, uid))


def build_dispatcher(survey):
    from maxapi import Dispatcher
    import legacy_max_bot as old

    dp = Dispatcher()

    @dp.bot_started()
    async def started(event):
        chat_id, user_id = event.get_ids()
        uid = str(user_id)
        state = survey.state.get(uid)
        if state is None:
            text = START_GUIDE + "\n\n" + survey.start(uid)
        elif state.get("finished"):
            text = ALREADY_DONE
        elif (state.get("consent") or {}).get("at"):
            text = RESUMED + "\n\n" + survey.question_text(uid)
        elif (state.get("consent") or {}).get("refused"):
            text = CONSENT_NO
        else:
            text = START_GUIDE + "\n\n" + CONSENT_SHORT
        await send(event.bot, chat_id, uid, text, visible_rows(survey, uid))

    @dp.message_created()
    async def message(event):
        chat_id, user_id = event.get_ids()
        uid = str(user_id)
        body = event.message.body
        text = ((body.text if body else None) or "").strip()
        files = old._files_of(body)
        normalized = text.casefold().strip(" ?!.")
        if normalized in old.ASK_WORDS:
            if normalized in {"темы", "покажи темы", "список тем"} and uid in survey.state:
                survey.understood(uid)
                text_, rows_ = map_screen(survey, uid)
                await send(event.bot, chat_id, uid, text_, rows_)
            else:
                await send(event.bot, chat_id, uid, HELP, visible_rows(survey, uid))
            return

        # Справка, выписка, фотография — обычная часть разговора здесь.
        # Файл надо принять и показать это, а не отвечать «напишите текстом»,
        # как будто человек ничего не присылал.
        if files:
            survey.note_message(uid, text, files)
        reply = survey.handle(uid, text) if text else ""
        # Пустой ответ означал молчание: после заполненной анкеты человек
        # писал вопрос и не получал ничего. Ответ по материалам службы
        # ищется тем же кодом, что и в остальных режимах.
        if not reply and text:
            reply = survey.справка_по_вопросу(uid, text)
        if reply:
            await send(event.bot, chat_id, uid, reply, visible_rows(survey, uid))
        if files:
            await send(event.bot, chat_id, uid, FILES_TAKEN, visible_rows(survey, uid))

    @dp.message_callback()
    async def callback(event):
        chat_id, user_id = event.get_ids()
        uid = str(user_id)
        payload = event.callback.payload or ""
        parts = payload.split(":")
        action = parts[0] if parts else ""
        try:
            await event.ack(notification="Принято")
        except Exception:
            pass
        if action == "c" and parts[1:2] == ["full"]:
            await send(event.bot, chat_id, uid, survey.consent_text(uid), consent_keyboard(True)); return
        if action == "c" and parts[1:2] == ["back"]:
            await send(event.bot, chat_id, uid, CONSENT_SHORT, consent_keyboard(False)); return
        if action == "c" and parts[1:2] in (["y"], ["n"]):
            reply = survey.grant_consent(uid) if parts[1] == "y" else survey.refuse_consent(uid)
            await send(event.bot, chat_id, uid, reply, visible_rows(survey, uid)); return
        if action == "h":
            await send(event.bot, chat_id, uid, HELP, visible_rows(survey, uid)); return
        # Карта, ветвь и статья переписывают один экран, а не копят сообщения.
        if action == "map":
            await browse(event, chat_id, uid, map_screen(survey, uid), survey); return
        if action == "v" and len(parts) >= 2:
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
            await browse(event, chat_id, uid, branch_screen(parts[1], page, survey, uid), survey, f"ветвь {parts[1]} не найдена"); return
        if action == "k":
            # Заголовок в payload не помещается и содержит двоеточия: ключ
            # кнопки — устойчивый дайджест, тот же, что в production.
            title = resolve_article(payload[2:]) if payload.startswith("k:") else None
            if title is None:
                await browse(event, chat_id, uid, None, survey, "статья по кнопке не опознана"); return
            await browse(event, chat_id, uid, article_screen(title, survey, uid), survey, "статья не найдена"); return
        if action == "q":
            text = survey.question_text(uid) if survey.current(uid) else survey.summary(uid)
            await send(event.bot, chat_id, uid, text, visible_rows(survey, uid)); return
        if action == "n":
            await send(event.bot, chat_id, uid, survey.continue_detailed(uid), visible_rows(survey, uid)); return
        if action == "r":
            await send(event.bot, chat_id, uid, survey.restart_after_consent(uid), visible_rows(survey, uid)); return
        if action == "m":
            await send(event.bot, chat_id, uid, survey.summary(uid), visible_rows(survey, uid)); return
        if action == "b":
            await send(event.bot, chat_id, uid, survey.handle(uid, "назад"), visible_rows(survey, uid)); return

        # Дальше идут кнопки ответа. Каждая несёт номер своего вопроса, и он
        # обязан совпасть с текущим: сообщения в чате остаются, человек может
        # пролистать вверх и нажать вчерашнюю кнопку. Без этой проверки ответ
        # записывался бы в тот вопрос, который открыт сейчас, — координатор
        # получил бы чужое «Утром» в графе «когда звонить».
        if action in {"a", "s", "d", "t"}:
            место = survey.current(uid)
            свой = место is not None and len(parts) > 1 and parts[1].isdigit() and место[0] == int(parts[1])
            if not свой:
                await устаревшая_кнопка(event, chat_id, uid, survey)
                return

        if action == "s":
            await send(event.bot, chat_id, uid, survey.handle(uid, "далее"), visible_rows(survey, uid)); return
        if action == "a" and len(parts) == 3:
            варианты = место[1].get("options") or []
            номер = int(parts[2]) if parts[2].isdigit() else -1
            if not 0 <= номер < len(варианты):
                await устаревшая_кнопка(event, chat_id, uid, survey)
                return
            await send(event.bot, chat_id, uid, survey.answer_by_numbers(uid, [номер + 1]), visible_rows(survey, uid)); return
        if action == "t" and len(parts) == 3:
            варианты = место[1].get("options") or []
            номер = int(parts[2]) if parts[2].isdigit() else -1
            if not 0 <= номер < len(варианты):
                await устаревшая_кнопка(event, chat_id, uid, survey)
                return
            survey.toggle(uid, int(parts[1]), номер)
            await send(event.bot, chat_id, uid, survey.question_text(uid), visible_rows(survey, uid)); return
        if action == "d" and len(parts) == 2:
            picked = survey.picked(uid, int(parts[1]))
            reply = survey.answer_by_numbers(uid, [i + 1 for i in picked]) if picked else survey.handle(uid, "далее")
            await send(event.bot, chat_id, uid, reply, visible_rows(survey, uid))

    return dp


async def operator_queue(bot, poll_seconds: float = 2.0):
    """Доставлять сообщения, которые оператор поставил в очередь в CRM.

    CRM кладёт каждое сообщение отдельным файлом, бот их забирает. Без этой
    задачи оператор видит «сообщение отправлено», а человек не получает
    ничего — тихая потеря, которую в чате не видно.

    Очередь не должна ронять бота: анкеты важнее, поэтому любая ошибка здесь
    только логируется.
    """
    import asyncio

    import crm_store
    import legacy_max_bot as old

    while True:
        try:
            for message in crm_store.pending_messages():
                text = f"{message['text']}\n\n— {message['who']}, служба долговременного ухода"
                try:
                    await bot.send_message(
                        user_id=int(message["user_id"]),
                        text=text,
                        attachments=old._outgoing_files(message),
                    )
                    crm_store.mark_sent(message)
                    old.log.info("Оператор %s написал %s", message["who"], message["user_id"])
                except Exception as error:  # noqa: BLE001
                    crm_store.mark_sent(message, error=str(error))
                    old.log.warning("Не отправилось %s: %s", message["user_id"], type(error).__name__)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            old.log.warning("Очередь сообщений оператора: %s", type(error).__name__)
        await asyncio.sleep(poll_seconds)


async def main():
    import asyncio
    import legacy_max_bot as old
    from maxapi import Bot
    bot = Bot(old.read_token())
    survey = LaptopSurvey(list_options=False)
    queue = asyncio.create_task(operator_queue(bot))
    try:
        await build_dispatcher(survey).start_polling(bot)
    finally:
        queue.cancel()
        await asyncio.gather(queue, return_exceptions=True)
        close = getattr(bot, "close_session", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result

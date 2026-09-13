from __future__ import annotations

from chatbot_survey import ALREADY_DONE, CONSENT_NO, CONSENT_SHORT, HELP, RESUMED
from max_laptop_pilot_v2 import LaptopSurvey, markup, rows

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


async def send(bot, chat_id, user_id, text, keyboard=None):
    args = {"text": text, "attachments": [markup(keyboard)] if keyboard else None}
    if chat_id is not None:
        args["chat_id"] = chat_id
    else:
        args["user_id"] = int(user_id)
    await bot.send_message(**args)


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
        normalized = text.casefold().strip(" ?!.")
        if normalized in old.ASK_WORDS:
            if normalized in {"темы", "покажи темы", "список тем"} and uid in survey.state:
                await old.меню_тем(event.bot, chat_id, uid, survey)
            else:
                await send(event.bot, chat_id, uid, HELP, visible_rows(survey, uid))
            return
        reply = survey.handle(uid, text)
        if reply:
            await send(event.bot, chat_id, uid, reply, visible_rows(survey, uid))

    @dp.message_callback()
    async def callback(event):
        chat_id, user_id = event.get_ids()
        uid = str(user_id)
        parts = (event.callback.payload or "").split(":")
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
        if action == "map":
            await old.меню_тем(event.bot, chat_id, uid, survey); return
        if action == "v" and len(parts) >= 2:
            await old.ветвь(event.bot, chat_id, uid, parts[1], int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1, survey); return
        if action == "k":
            await old.статья(event.bot, chat_id, uid, parts[1] if len(parts) > 1 else "", survey); return
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
        if action == "s":
            await send(event.bot, chat_id, uid, survey.handle(uid, "далее"), visible_rows(survey, uid)); return
        if action == "a" and len(parts) == 3:
            await send(event.bot, chat_id, uid, survey.answer_by_numbers(uid, [int(parts[2]) + 1]), visible_rows(survey, uid)); return
        if action == "t" and len(parts) == 3:
            survey.toggle(uid, int(parts[1]), int(parts[2]))
            await send(event.bot, chat_id, uid, survey.question_text(uid), visible_rows(survey, uid)); return
        if action == "d" and len(parts) == 2:
            picked = survey.picked(uid, int(parts[1]))
            reply = survey.answer_by_numbers(uid, [i + 1 for i in picked]) if picked else survey.handle(uid, "далее")
            await send(event.bot, chat_id, uid, reply, visible_rows(survey, uid))

    return dp


async def main():
    import asyncio
    import legacy_max_bot as old
    from maxapi import Bot
    bot = Bot(old.read_token())
    survey = LaptopSurvey(list_options=False)
    try:
        await build_dispatcher(survey).start_polling(bot)
    finally:
        close = getattr(bot, "close_session", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result

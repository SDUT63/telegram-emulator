#!/usr/bin/env python3
"""Запуск локального SQLite-пилота MAX-бота с прозрачным стартовым экраном."""
from __future__ import annotations

import asyncio

from chatbot_survey import ALREADY_DONE, CONSENT_NO, CONSENT_SHORT, HELP, RESUMED
from max_laptop_pilot_v2 import LaptopSurvey, consent_rows, rows

START_GUIDE = (
    "Как пользоваться ботом:\n\n"
    "• «Согласен, продолжим» — перейти к анкете после согласия.\n"
    "• «Прочитать полностью» — увидеть полный текст согласия.\n"
    "• «Просто почитать» — открыть материалы без заполнения анкеты.\n"
    "• «Что можно написать» — получить список команд и подсказок.\n\n"
    "В любой момент можно нажать кнопку или написать словами. "
    "Например: «помощь», «темы», «что умеешь»."
)


def build_dispatcher(survey):
    from maxapi import Dispatcher
    from maxapi.types import BotStarted, MessageCallback, MessageCreated
    import legacy_max_bot as old

    dp = Dispatcher()

    @dp.bot_started()
    async def started(event):
        chat_id, user_id = event.get_ids()
        user_id = str(user_id)
        state = survey.state.get(user_id)
        if state is None:
            text = START_GUIDE + "\n\n" + survey.start(user_id)
        elif state.get("finished"):
            text = ALREADY_DONE
        elif (state.get("consent") or {}).get("at"):
            text = RESUMED + "\n\n" + survey.question_text(user_id)
        elif (state.get("consent") or {}).get("refused"):
            text = CONSENT_NO
        else:
            text = START_GUIDE + "\n\n" + CONSENT_SHORT
        await _send(event.bot, chat_id, user_id, text, rows(survey, user_id))

    @dp.message_created()
    async def message(event):
        chat_id, user_id = event.get_ids()
        user_id = str(user_id)
        body = event.message.body
        text = ((body.text if body else None) or "").strip()
        normalized = text.casefold().strip(" ?!.")

        # Help is available before consent and before the first questionnaire
        # answer. It never requires a survey record and never changes state.
        if normalized in old.ASK_WORDS:
            if normalized in {"темы", "покажи темы", "список тем"} and user_id in survey.state:
                await old.меню_тем(event.bot, chat_id, user_id, survey)
            else:
                await _send(event.bot, chat_id, user_id, HELP, rows(survey, user_id))
            return

        reply = survey.handle(user_id, text)
        if reply:
            await _send(event.bot, chat_id, user_id, reply, rows(survey, user_id))

    @dp.message_callback()
    async def callback(event):
        chat_id, user_id = event.get_ids()
        user_id = str(user_id)
        payload = (event.callback.payload or "").split(":")
        action = payload[0] if payload else ""
        try:
            await event.ack(notification="Принято")
        except Exception:
            pass

        if action == "c" and payload[1:2] == ["full"]:
            await _send(event.bot, chat_id, user_id, survey.consent_text(user_id), consent_rows(True))
            return
        if action == "c" and payload[1:2] == ["back"]:
            await _send(event.bot, chat_id, user_id, CONSENT_SHORT, consent_rows(False))
            return
        if action == "c" and payload[1:2] in (["y"], ["n"]):
            reply = survey.grant_consent(user_id) if payload[1] == "y" else survey.refuse_consent(user_id)
            await _send(event.bot, chat_id, user_id, reply, rows(survey, user_id))
            return
        if action == "h":
            await _send(event.bot, chat_id, user_id, HELP, rows(survey, user_id))
            return
        if action == "map":
            await old.меню_тем(event.bot, chat_id, user_id, survey)
            return
        if action == "v" and len(payload) >= 2:
            await old.ветвь(event.bot, chat_id, user_id, payload[1], int(payload[2]) if len(payload) > 2 and payload[2].isdigit() else 1, survey)
            return
        if action == "k":
            await old.статья(event.bot, chat_id, user_id, payload[1] if len(payload) > 1 else "", survey)
            return
        if action == "q":
            text = survey.question_text(user_id) if survey.current(user_id) else survey.summary(user_id)
            await _send(event.bot, chat_id, user_id, text, rows(survey, user_id))
            return
        if action == "n":
            await _send(event.bot, chat_id, user_id, survey.continue_detailed(user_id), rows(survey, user_id))
            return
        if action == "r":
            await _send(event.bot, chat_id, user_id, survey.restart_after_consent(user_id), rows(survey, user_id))
            return
        if action == "m":
            await _send(event.bot, chat_id, user_id, survey.summary(user_id), rows(survey, user_id))
            return
        if action == "b":
            await _send(event.bot, chat_id, user_id, survey.handle(user_id, "назад"), rows(survey, user_id))
            return
        if action == "s":
            await _send(event.bot, chat_id, user_id, survey.handle(user_id, "далее"), rows(survey, user_id))
            return
        if action == "a" and len(payload) == 3:
            await _send(event.bot, chat_id, user_id, survey.answer_by_numbers(user_id, [int(payload[2]) + 1]), rows(survey, user_id))
            return
        if action == "t" and len(payload) == 3:
            survey.toggle(user_id, int(payload[1]), int(payload[2]))
            await _send(event.bot, chat_id, user_id, survey.question_text(user_id), rows(survey, user_id))
            return
        if action == "d" and len(payload) == 2:
            picked = survey.picked(user_id, int(payload[1]))
            reply = survey.answer_by_numbers(user_id, [i + 1 for i in picked]) if picked else survey.handle(user_id, "далее")
            await _send(event.bot, chat_id, user_id, reply, rows(survey, user_id))

    return dp


async def _send(bot, chat_id, user_id, text, keyboard_rows=None):
    from max_laptop_pilot_v2 import markup

    args = {"text": text, "attachments": [markup(keyboard_rows)] if keyboard_rows else None}
    if chat_id is not None:
        args["chat_id"] = chat_id
    else:
        args["user_id"] = int(user_id)
    await bot.send_message(**args)


async def main():
    import legacy_max_bot as old
    from maxapi import Bot

    bot = Bot(old.read_token())
    survey = LaptopSurvey(list_options=False)
    dispatcher = build_dispatcher(survey)
    try:
        await dispatcher.start_polling(bot)
    finally:
        close = getattr(bot, "close_session", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

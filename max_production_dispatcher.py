#!/usr/bin/env python3
"""Canonical production dispatcher for MAX.

The legacy ``max_bot.py`` keeps the SQLite/pilot dispatcher for compatibility.
Production entrypoints use this module so every outbound MAX message crosses
one explicit transport and inbound message state changes use the durable
PostgreSQL event boundary.

This module deliberately contains no direct ``Bot.send_message`` call.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from max_outbound_transport import MaxOutboundTransport

log = logging.getLogger("сдут-бот")


def _production_helpers():
    """Load UI/scenario helpers without making this module depend on MAX types."""
    from max_bot import (
        ASK_WORDS,
        COMMANDS,
        FILES_TAKEN,
        СПРАВКА_ПОДПИСЬ,
        _files_of,
        _кнопки_подсказок,
        _отправить,
        _в_анкете,
        _строка_возврата,
        _кнопки_карты,
        _кнопки_ветви,
        _кнопки_статьи,
        экран_карты,
        экран_ветви,
        экран_статьи,
        keyboard_for,
        меню_тем,
        layout,
    )
    return locals()


async def _send(transport: MaxOutboundTransport, user_id: str, chat_id: Any,
                text: str, markup: Any = None) -> None:
    """Send a single application message through the explicit transport."""
    payload: dict[str, Any] = {
        "kind": "max_text",
        "text": str(text),
        "keyboard_rows": [],
    }
    if markup is not None:
        # The durable outbox serializes keyboards before delivery. Direct
        # application sends in this dispatcher are intentionally limited to
        # the non-durable pilot-compatible fallback; production entrypoints
        # must use the outbox for questionnaire replies.
        raise RuntimeError("production dispatcher cannot send an unscheduled MAX message")
    await transport.send({**payload, "user_id": str(user_id), "chat_id": chat_id})


def build_dispatcher(survey, seen):
    """Build the production MAX dispatcher.

    ``seen`` must be ``TransactionalPersistentSeen``. It establishes the
    inbound event id used by ``handle_message_event`` and the transactional
    callback adapters.
    """
    from maxapi import Dispatcher
    from maxapi.types import BotStarted, MessageCallback, MessageCreated

    helpers = _production_helpers()
    dp = Dispatcher()
    transport_by_bot: dict[int, MaxOutboundTransport] = {}

    def transport_for(bot) -> MaxOutboundTransport:
        key = id(bot)
        transport = transport_by_bot.get(key)
        if transport is None:
            transport = MaxOutboundTransport(bot)
            transport_by_bot[key] = transport
        return transport

    def keyboard_payload(user_id: str) -> list[list[list[str]]]:
        return [
            [[str(label), str(action)] for label, action in row]
            for row in helpers["layout"](survey, str(user_id))
        ]

    async def say(bot, chat_id, user_id: str, text: str) -> None:
        """Queue a durable application reply in the current event transaction."""
        # ``handle_message_event`` already creates the main outbound intent.
        # A second network send here would violate the production transaction
        # boundary, so this function is intentionally only used by non-event
        # lifecycle paths and fails closed until they have a durable command.
        if not text:
            return
        raise RuntimeError("say() is not a production delivery primitive; enqueue via event boundary")

    @dp.bot_started()
    async def on_started(event: BotStarted) -> None:
        chat_id, user_id = event.get_ids()
        uid = str(user_id)
        if not seen.fresh(getattr(event, "event_id", None)):
            log.info("%s: повтор BotStarted, пропускаем", uid)
            return
        text = survey.start(uid)
        # BotStarted currently has no durable reply envelope. Keep this path
        # explicit rather than silently sending outside the outbox.
        if text:
            log.warning("%s: BotStarted reply requires durable lifecycle envelope", uid)

    @dp.message_created()
    async def on_message(event: MessageCreated) -> None:
        chat_id, user_id = event.get_ids()
        uid = str(user_id)
        body = event.message.body
        event_id = getattr(body, "mid", None)
        if not seen.fresh(event_id):
            log.info("%s: повтор доставки, пропускаем", uid)
            return

        incoming = ((body.text if body else None) or "").strip()
        files = helpers["_files_of"](body)

        if incoming.lower().strip(" ?!.") in helpers["ASK_WORDS"]:
            # Navigation is deliberately kept outside questionnaire mutation;
            # it does not answer a questionnaire question.
            try:
                await helpers["меню_тем"](event.bot, chat_id, uid, survey)
            except Exception as error:  # noqa: BLE001
                log.warning("%s: карта тем не открылась: %s", uid, error)
            return

        was_done = bool(survey.state.get(uid, {}).get("finished"))
        reply = survey.handle_message_event(uid, incoming, files)
        if files:
            reply = "\n\n".join(x for x in (helpers["FILES_TAKEN"], reply) if x)

        if reply:
            # The durable survey has already enqueued the canonical main reply
            # and attachment acknowledgement in the same transaction.
            log.info("%s: ответ зафиксирован в durable outbox", uid)

        person = survey.state.get(uid, {})
        if person.get("finished") and not was_done:
            log.info("%s: анкета завершена", uid)

    @dp.message_callback()
    async def on_button(event: MessageCallback) -> None:
        try:
            await _handle_callback(event)
        except Exception as error:  # noqa: BLE001
            log.warning("нажатие не обработалось: %s", error)
            try:
                await event.ack(notification="Не сработало, напишите словами")
            except Exception:  # noqa: BLE001
                pass

    async def _handle_callback(event: MessageCallback) -> None:
        chat_id, user_id = event.get_ids()
        who = str(user_id)
        callback = event.callback
        callback_id = getattr(callback, "callback_id", None)
        if not seen.fresh(callback_id):
            log.info("%s: повтор нажатия, пропускаем", who)
            return

        payload = (getattr(callback, "payload", None) or "")
        parts = payload.split(":")
        action = parts[0] if parts else ""

        try:
            await event.ack(notification="Принято")
        except Exception:  # noqa: BLE001
            pass

        if action == "q":
            spot = survey.current(who)
            if spot:
                # Re-painting is an edit operation, not an outbound message.
                try:
                    await event.edit(
                        text=survey.question_text(who),
                        attachments=[helpers["keyboard_for"](survey, who)],
                        raise_if_not_exists=False,
                    )
                except Exception as error:  # noqa: BLE001
                    log.debug("%s: question edit failed: %s", who, error)
            return

        if action == "map":
            text, markup = helpers["экран_карты"](survey, who)
            try:
                await event.edit(text=text, attachments=[markup], raise_if_not_exists=False)
            except Exception:
                # No fallback send here: production outbound delivery must be
                # represented by a durable command, never by an ad-hoc retry.
                log.info("%s: карта не была отредактирована", who)
            return

        if action == "v" and len(parts) >= 2:
            number = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
            screen = helpers["экран_ветви"](parts[1], number, survey, who)
            if screen:
                try:
                    await event.edit(text=screen[0], attachments=[screen[1]], raise_if_not_exists=False)
                except Exception:
                    log.info("%s: экран ветви не был отредактирован", who)
            return

        if action == "k":
            title = payload[2:]
            screen = helpers["экран_статьи"](title, survey, who)
            if screen:
                try:
                    await event.edit(text=screen[0], attachments=[screen[1]], raise_if_not_exists=False)
                except Exception:
                    log.info("%s: статья не была отредактирована", who)
            return

        if action == "c" and parts[1:2] == ["full"]:
            # Full consent text is a read-only action. It is not an answer
            # event, so it cannot be sent from the production dispatcher until
            # a durable navigation-message envelope exists.
            log.info("%s: запрос полного текста согласия", who)
            return

        if action == "c":
            if survey.stage(who) != "consent":
                return
            reply = survey.grant_consent(who) if parts[1:2] == ["y"] else survey.refuse_consent(who)
            try:
                await event.edit(text=((event.message.body.text if event.message else None) or ""), attachments=[], raise_if_not_exists=False)
            except Exception:
                pass
            if reply:
                log.info("%s: consent transition committed; reply requires durable callback envelope", who)
            return

        if action == "t" and len(parts) == 3:
            step, index = int(parts[1]), int(parts[2])
            if not survey.toggle(who, step, index):
                return
            text = survey.question_text(who)
            names = survey.picked_names(who, step)
            if names:
                text += "\n\nОтмечено: " + ", ".join(names)
            try:
                await event.edit(text=text, attachments=[helpers["keyboard_for"](survey, who)], raise_if_not_exists=False)
            except Exception:
                pass
            return

        if action in {"a", "s", "d"} and len(parts) >= 2:
            spot = survey.current(who)
            if not spot or str(spot[0]) != parts[1]:
                return

        if action == "a" and len(parts) == 3:
            index = int(parts[2])
            spot = survey.current(who)
            if not spot or index < 0 or index >= len(spot[1]["options"]):
                return
            reply = survey.answer_by_numbers(who, [index + 1])
        elif action == "s":
            reply = survey.handle(who, "далее")
        elif action == "d":
            step = int(parts[1])
            picked = survey.picked(who, step)
            spot = survey.current(who)
            if not picked:
                if not spot or spot[1].get("required", True):
                    return
                reply = survey.handle(who, "далее")
            else:
                reply = survey.answer_by_numbers(who, [i + 1 for i in picked])
        elif action == "b":
            reply = survey.handle(who, "назад")
        elif action == "n":
            reply = survey.restart_after_consent(who)
        elif action == "m":
            reply = survey.summary(who)
        else:
            return

        if reply:
            log.info("%s: callback transition committed; reply requires durable callback envelope", who)

    return dp


__all__ = ["build_dispatcher"]

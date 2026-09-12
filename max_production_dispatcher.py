#!/usr/bin/env python3
"""Canonical production dispatcher for MAX.

Inbound handlers never call MAX send APIs. They only cross the durable
PostgreSQL event boundary; the outbox worker owns external delivery.
"""
from __future__ import annotations
import logging
from max_ui import files_of, ASK_WORDS
log = logging.getLogger("сдут-бот")

def build_dispatcher(survey, seen):
    from maxapi import Dispatcher
    from maxapi.types import BotStarted, MessageCallback, MessageCreated
    dp = Dispatcher()

    async def acknowledge(event, text: str = "Принято") -> None:
        try:
            await event.ack(notification=text)
        except Exception as error:  # noqa: BLE001
            log.debug("MAX callback acknowledgement failed: %s", error)

    @dp.bot_started()
    async def on_started(event: BotStarted) -> None:
        _, user_id = event.get_ids(); uid = str(user_id)
        event_key = getattr(event, "event_id", None) or getattr(event, "id", None)
        if not seen.fresh(event_key):
            log.info("%s: duplicate BotStarted ignored", uid); return
        if survey.start_event(uid): log.info("%s: BotStarted reply committed to outbox", uid)

    @dp.message_created()
    async def on_message(event: MessageCreated) -> None:
        _, user_id = event.get_ids(); uid = str(user_id)
        body = getattr(getattr(event, "message", None), "body", None)
        event_key = getattr(body, "mid", None)
        if not seen.fresh(event_key):
            log.info("%s: duplicate message ignored", uid); return
        incoming = ((getattr(body, "text", None) or "").strip())
        files = files_of(body)
        if incoming.lower().strip(" ?!.") in ASK_WORDS:
            reply = survey.handle_navigation_event(uid, "map", [])
        else:
            reply = survey.handle_message_event(uid, incoming, files)
        if reply: log.info("%s: message transition committed to outbox", uid)

    @dp.message_callback()
    async def on_button(event: MessageCallback) -> None:
        _, user_id = event.get_ids(); uid = str(user_id)
        callback = event.callback
        callback_id = getattr(callback, "callback_id", None)
        if not seen.fresh(callback_id):
            log.info("%s: duplicate callback ignored", uid); return
        payload = (getattr(callback, "payload", None) or "")
        parts = payload.split(":")
        action = parts[0] if parts else ""; args = parts[1:]
        await acknowledge(event)
        if action == "map": survey.handle_navigation_event(uid, "map", []); return
        if action == "v" and args: survey.handle_navigation_event(uid, "v", args); return
        if action == "k" and payload.startswith("k:"): survey.handle_navigation_event(uid, "k", [payload[2:]]); return
        if action == "q": survey.handle_navigation_event(uid, "q", []); return
        if action == "c" and args[:1] == ["full"]: survey.handle_navigation_event(uid, "cfull", []); return
        if action in {"c", "a", "s", "d", "b", "n", "m"}:
            reply = survey.handle_callback_event(uid, action, args)
            if reply: log.info("%s: callback transition committed to outbox", uid)
            return
        log.info("%s: unknown callback action rejected", uid)
    return dp

__all__ = ["build_dispatcher"]

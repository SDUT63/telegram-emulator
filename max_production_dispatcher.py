#!/usr/bin/env python3
"""Canonical production dispatcher for MAX.

Inbound handlers never call MAX send APIs. They only cross the durable
PostgreSQL event boundary; the outbox worker owns external delivery.
"""
from __future__ import annotations

import contextvars
import hashlib
import logging

from max_ui import ASK_WORDS, files_of
from storage_postgres import _TX_EVENT

log = logging.getLogger("сдут-бот")


def _resolve_article_callback(token: str) -> str | None:
    """Resolve a short article callback against the canonical knowledge base."""
    if not token.startswith("@"):
        return token
    digest = token[1:]
    if len(digest) != 16 or any(ch not in "0123456789abcdef" for ch in digest):
        return None
    import knowledge

    matches = [
        article.заголовок
        for article in knowledge.загрузить()
        if hashlib.sha256(article.заголовок.encode("utf-8")).hexdigest()[:16] == digest
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _event_id(value: object) -> str | None:
    """Normalize a provider identifier; missing values are not accepted."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _fingerprint_event(prefix: str, *parts: object) -> str | None:
    normalized = []
    for part in parts:
        value = _event_id(part)
        if value is None:
            return None
        normalized.append(value)
    raw = "|".join(normalized)
    return f"{prefix}:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _callback_event_id(event, callback_id: str, user_id: str) -> str | None:
    """Build a retry-stable callback event identity.

    MAX's ``callback_id`` identifies the button, not the click event. Reusing
    it as a deduplication key would make the first click permanently suppress
    every later legitimate click on the same button. MAX's public Update model
    exposes the event timestamp rather than a unique callback event ID, so the
    production boundary fingerprints the event's stable fields instead.
    """
    message = getattr(event, "message", None)
    body = getattr(message, "body", None)
    message_id = _event_id(getattr(body, "mid", None) or getattr(message, "mid", None)) or ""
    chat_id = _event_id(getattr(event, "chat_id", None)) or ""
    payload = _event_id(getattr(getattr(event, "callback", None), "payload", None)) or ""
    return _fingerprint_event(
        "callback",
        user_id,
        chat_id,
        message_id,
        callback_id,
        payload,
        getattr(event, "timestamp", None),
    )


def _started_event_id(event, user_id: str) -> str | None:
    """Fingerprint bot_started because MAX does not expose a unique event ID."""
    return _fingerprint_event(
        "bot_started",
        user_id,
        getattr(event, "chat_id", None),
        getattr(event, "timestamp", None),
    )


async def _with_event_id(event_id: str, handler) -> object:
    """Run a DB mutation with the provider event identity bound to the transaction."""
    token = _TX_EVENT.set(event_id)
    try:
        return await handler()
    finally:
        _TX_EVENT.reset(token)


def build_dispatcher(survey, seen=None):
    """Build the canonical MAX inbound dispatcher.

    ``seen`` is retained only for source compatibility with older callers. It
    is deliberately not consulted: PostgreSQL ``processed_events`` is the
    authoritative idempotency boundary and must commit atomically with state,
    audit and outbox intent.
    """
    del seen
    from maxapi import Dispatcher
    from maxapi.types import BotStarted, MessageCallback, MessageCreated

    dp = Dispatcher()

    async def acknowledge(event, text: str = "Принято") -> None:
        try:
            await event.ack(notification=text)
        except Exception as error:  # noqa: BLE001
            log.debug("MAX callback acknowledgement failed: %s", type(error).__name__)

    @dp.bot_started()
    async def on_started(event: BotStarted) -> None:
        _, user_id = event.get_ids()
        uid = str(user_id)
        event_key = _started_event_id(event, uid)
        if event_key is None:
            log.error("%s: BotStarted without stable event fields rejected", uid)
            return

        async def mutate() -> None:
            if survey.start_event(uid):
                log.info("%s: BotStarted transition committed to outbox", uid)

        await _with_event_id(event_key, mutate)

    @dp.message_created()
    async def on_message(event: MessageCreated) -> None:
        _, user_id = event.get_ids()
        uid = str(user_id)
        body = getattr(getattr(event, "message", None), "body", None)
        event_key = _event_id(getattr(body, "mid", None))
        if event_key is None:
            log.error("%s: message without provider mid rejected", uid)
            return
        incoming = (getattr(body, "text", None) or "").strip()
        files = files_of(body)

        async def mutate() -> None:
            if incoming.lower().strip(" ?!.") in ASK_WORDS:
                survey.handle_navigation_event(uid, "map", [])
            else:
                survey.handle_message_event(uid, incoming, files)

        await _with_event_id(event_key, mutate)

    @dp.message_callback()
    async def on_button(event: MessageCallback) -> None:
        _, user_id = event.get_ids()
        uid = str(user_id)
        callback = event.callback
        callback_id = _event_id(getattr(callback, "callback_id", None))
        if callback_id is None:
            log.error("%s: callback without button callback_id rejected", uid)
            return
        event_key = _callback_event_id(event, callback_id, uid)
        if event_key is None:
            log.error("%s: callback without stable event fields rejected", uid)
            return
        payload = (getattr(callback, "payload", None) or "")
        if len(payload) > 512:
            log.warning("%s: oversized callback payload rejected", uid)
            return

        async def mutate() -> None:
            parts = payload.split(":")
            action = parts[0] if parts else ""
            args = parts[1:]
            if action == "map":
                survey.handle_navigation_event(uid, "map", [])
                return
            if action == "v" and args:
                survey.handle_navigation_event(uid, "v", args)
                return
            if action == "k" and payload.startswith("k:"):
                title = _resolve_article_callback(payload[2:])
                if title is None:
                    log.warning("%s: invalid or ambiguous knowledge callback rejected", uid)
                    return
                survey.handle_navigation_event(uid, "k", [title])
                return
            if action == "q":
                survey.handle_navigation_event(uid, "q", [])
                return
            if action == "c" and args[:1] == ["full"]:
                survey.handle_navigation_event(uid, "cfull", [])
                return
            if action in {"c", "a", "s", "d", "b", "n", "m"}:
                survey.handle_callback_event(uid, action, args)
                return
            log.info("%s: unknown callback action rejected", uid)

        # Acknowledgement is deliberately outside the database transaction.
        # A slow/failing acknowledgement must never hold PostgreSQL locks.
        # MAX may retry the same webhook; the event fingerprint is stable for
        # the retry, while later legitimate clicks receive a different key.
        await _with_event_id(event_key, mutate)
        await acknowledge(event)

    return dp


__all__ = ["build_dispatcher", "_resolve_article_callback", "_callback_event_id", "_started_event_id"]

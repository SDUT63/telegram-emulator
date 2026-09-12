#!/usr/bin/env python3
"""MAX network worker for the PostgreSQL durable outbound queue."""
from __future__ import annotations

import asyncio
import logging
import os
import time

from outbox_postgres import (
    DEFAULT_SENT_RETENTION_SECONDS,
    PostgresOutbox,
    OutboxMessage,
)

log = logging.getLogger("сдут-бот")
POLL_SECONDS = 0.5
PRUNE_INTERVAL_SECONDS = 3600


def _retention_seconds() -> int:
    """Read sent-outbox retention from environment, failing closed on bad input."""
    raw = (os.getenv("SDUT_OUTBOX_SENT_RETENTION_DAYS") or "").strip()
    if not raw:
        return DEFAULT_SENT_RETENTION_SECONDS
    try:
        days = int(raw)
    except ValueError as exc:
        raise ValueError("SDUT_OUTBOX_SENT_RETENTION_DAYS должен быть целым числом") from exc
    if days < 1:
        raise ValueError("SDUT_OUTBOX_SENT_RETENTION_DAYS должен быть >= 1")
    return days * 24 * 60 * 60


def _markup(rows):
    if rows is None:
        return None
    if not isinstance(rows, list):
        raise ValueError("keyboard_rows должен быть списком")

    from maxapi.enums.intent import Intent
    from maxapi.types.attachments.buttons import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

    keyboard = InlineKeyboardBuilder()
    for row in rows:
        if not isinstance(row, list):
            raise ValueError("строка keyboard_rows должна быть списком")
        buttons = []
        for item in row:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError("некорректная строка кнопки в outbox payload")
            label, action = item
            if not isinstance(label, str) or not isinstance(action, str):
                raise ValueError("label/action кнопки должны быть строками")
            if not label or not action:
                raise ValueError("label/action кнопки не могут быть пустыми")
            positive = action == "c:y" or action.startswith("d:") or label.startswith("✅ ")
            buttons.append(
                CallbackButton(
                    text=label,
                    payload=action,
                    intent=Intent.POSITIVE if positive else Intent.DEFAULT,
                )
            )
        if buttons:
            keyboard.row(*buttons)
    return keyboard.as_markup() if rows else None


async def _send(bot, message: OutboxMessage) -> None:
    payload = message.payload
    if not isinstance(payload, dict):
        raise ValueError("outbox payload должен быть JSON-объектом")
    if payload.get("kind") != "max_text":
        raise ValueError(f"неизвестный тип outbox payload: {payload.get('kind')!r}")

    text = payload.get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("outbox payload должен содержать непустой text")
    if len(text) > 32000:
        raise ValueError("outbox text слишком длинный")

    attachments = _markup(payload.get("keyboard_rows"))
    kwargs = {"text": text, "attachments": [attachments] if attachments else None}
    if message.chat_id is not None:
        kwargs["chat_id"] = message.chat_id
    else:
        try:
            kwargs["user_id"] = int(message.user_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("outbox user_id должен быть числовым, если chat_id отсутствует") from exc
    await bot.send_message(**kwargs)


async def deliver_once(bot, *, queue: PostgresOutbox | None = None) -> int:
    """Claim and process one batch; returns the number of claimed messages.

    Kept separate from the forever-loop so integration/crash-injection tests
    can exercise the exact claim -> send -> sent/failure boundary without
    running an unbounded background task.
    """
    queue = queue or PostgresOutbox()
    claimed = queue.claim()
    for message in claimed:
        try:
            await _send(bot, message)
        except Exception as error:  # noqa: BLE001
            log.warning(
                "MAX outbox #%s не отправился (попытка %s): %s",
                message.id,
                message.attempts,
                error,
            )
            try:
                queue.mark_failed(message.id, str(error))
            except Exception:
                log.exception("MAX outbox #%s: не удалось зафиксировать failure", message.id)
        else:
            try:
                queue.mark_sent(message.id)
            except Exception:
                # The network request may already have succeeded. Do not send
                # the message a second time merely because the acknowledgement
                # transaction failed; the row remains recoverable as sending.
                log.exception("MAX outbox #%s: не удалось зафиксировать sent", message.id)
    return len(claimed)


async def run(bot, *, poll_seconds: float = POLL_SECONDS, prune_interval_seconds: float = PRUNE_INTERVAL_SECONDS) -> None:
    """Run forever; failures stay in PostgreSQL and are retried with backoff."""
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be > 0")
    if prune_interval_seconds <= 0:
        raise ValueError("prune_interval_seconds must be > 0")
    queue = PostgresOutbox()
    retention_seconds = _retention_seconds()
    next_prune = time.monotonic()
    while True:
        try:
            now = time.monotonic()
            if now >= next_prune:
                try:
                    removed = queue.prune_sent(retention_seconds=retention_seconds)
                    if removed:
                        log.info("MAX durable outbox: очищено sent-записей: %s", removed)
                finally:
                    next_prune = time.monotonic() + prune_interval_seconds

            claimed = await deliver_once(bot, queue=queue)
            if not claimed:
                await asyncio.sleep(poll_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            log.warning("MAX durable outbox: %s", error)
            await asyncio.sleep(poll_seconds)

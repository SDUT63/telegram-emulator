#!/usr/bin/env python3
"""MAX network worker for the PostgreSQL durable outbound queue."""
from __future__ import annotations

import asyncio
import logging

from outbox_postgres import PostgresOutbox, OutboxMessage

log = logging.getLogger("сдут-бот")
POLL_SECONDS = 0.5


def _markup(rows):
    if not rows:
        return None
    from maxapi.enums.intent import Intent
    from maxapi.types.attachments.buttons import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

    keyboard = InlineKeyboardBuilder()
    for row in rows:
        buttons = []
        for item in row:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError("некорректная строка кнопки в outbox payload")
            label, action = str(item[0]), str(item[1])
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
    return keyboard.as_markup()


async def _send(bot, message: OutboxMessage) -> None:
    payload = message.payload
    if payload.get("kind") != "max_text":
        raise ValueError(f"неизвестный тип outbox payload: {payload.get('kind')!r}")

    text = payload.get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("outbox payload должен содержать непустой text")

    attachments = _markup(payload.get("keyboard_rows"))
    kwargs = {"text": text, "attachments": [attachments] if attachments else None}
    if message.chat_id is not None:
        kwargs["chat_id"] = message.chat_id
    else:
        kwargs["user_id"] = int(message.user_id)
    await bot.send_message(**kwargs)


async def run(bot, *, poll_seconds: float = POLL_SECONDS) -> None:
    """Run forever; failures stay in PostgreSQL and are retried with backoff."""
    queue = PostgresOutbox()
    while True:
        try:
            claimed = queue.claim()
            if not claimed:
                await asyncio.sleep(poll_seconds)
                continue

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
                    queue.mark_failed(message.id, str(error))
                else:
                    queue.mark_sent(message.id)
                    log.info("MAX outbox #%s доставлен пользователю %s", message.id, message.user_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            log.warning("MAX durable outbox: %s", error)
            await asyncio.sleep(poll_seconds)

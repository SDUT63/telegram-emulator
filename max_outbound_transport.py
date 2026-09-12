#!/usr/bin/env python3
"""Explicit MAX outbound transport for durable outbox delivery.

Application code persists outbound intent; this adapter is the only component
responsible for turning a validated durable intent into a MAX API request.
Retry state, idempotency and crash recovery remain in the PostgreSQL outbox.
"""
from __future__ import annotations

from typing import Any

from outbox_postgres import OutboxMessage

MAX_TEXT_LIMIT = 32000


def _markup(rows: Any):
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


class MaxOutboundTransport:
    """Send one durable outbound intent through the MAX API."""

    def __init__(self, bot) -> None:
        self._bot = bot

    @staticmethod
    def _validate(message: OutboxMessage) -> dict[str, Any]:
        payload = message.payload
        if not isinstance(payload, dict):
            raise ValueError("outbox payload должен быть JSON-объектом")
        if payload.get("kind") != "max_text":
            raise ValueError(f"неизвестный тип outbox payload: {payload.get('kind')!r}")
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("outbox payload должен содержать непустой text")
        if len(text) > MAX_TEXT_LIMIT:
            raise ValueError("outbox text слишком длинный")
        return payload

    async def send(self, message: OutboxMessage) -> None:
        """Perform exactly one provider request for the claimed intent."""
        payload = self._validate(message)
        attachments = _markup(payload.get("keyboard_rows"))
        kwargs = {"text": payload["text"], "attachments": [attachments] if attachments else None}
        if message.chat_id is not None:
            kwargs["chat_id"] = message.chat_id
        else:
            try:
                kwargs["user_id"] = int(message.user_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("outbox user_id должен быть числовым, если chat_id отсутствует") from exc
        await self._bot.send_message(**kwargs)


__all__ = ["MaxOutboundTransport", "MAX_TEXT_LIMIT"]

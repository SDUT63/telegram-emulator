#!/usr/bin/env python3
"""Explicit MAX outbound transport for durable outbox delivery.

Application code persists outbound intent; this adapter is the only component
responsible for turning a validated durable intent into a MAX API request.
Retry state, idempotency and crash recovery remain in the PostgreSQL outbox.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

from outbox_postgres import OutboxMessage

MAX_TEXT_LIMIT = 32000
DEFAULT_SEND_TIMEOUT_SECONDS = 30


def _send_timeout_seconds() -> float:
    raw = (os.getenv("SDUT_MAX_SEND_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return float(DEFAULT_SEND_TIMEOUT_SECONDS)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("SDUT_MAX_SEND_TIMEOUT_SECONDS должен быть числом") from exc
    if not 1 <= value <= 55:
        raise ValueError("SDUT_MAX_SEND_TIMEOUT_SECONDS должен быть в диапазоне 1..55")
    return value


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



@contextlib.contextmanager
def _materialised(attachments: list[dict[str, Any]]):
    """Выложить вложения во временные файлы на время одной отправки.

    MAX принимает файл путём, а содержимое хранится в базе: у воркера нет
    общего диска с CRM. Временный каталог удаляется в любом случае — копия
    медицинского документа не должна пережить отправку даже при сбое.
    """
    if not attachments:
        yield []
        return
    from maxapi.types.input_media import InputMedia

    with tempfile.TemporaryDirectory(prefix="sdut-outbox-") as directory:
        media = []
        for ordinal, item in enumerate(attachments):
            name = os.path.basename(str(item.get("name") or "")) or f"файл-{ordinal}"
            path = Path(directory) / f"{ordinal:02d}-{name}"
            path.write_bytes(bytes(item.get("content") or b""))
            media.append(InputMedia(str(path)))
        yield media


class MaxOutboundTransport:
    """Send one durable outbound intent through the MAX API."""

    def __init__(self, bot, *, timeout_seconds: float | None = None, attachments_for=None) -> None:
        self._bot = bot
        # Как достать содержимое вложений для одного намерения. Транспорт не
        # ходит в базу сам: очередь остаётся единственным владельцем хранения.
        self._attachments_for = attachments_for
        self.timeout_seconds = (
            _send_timeout_seconds() if timeout_seconds is None else float(timeout_seconds)
        )
        if not 1 <= self.timeout_seconds <= 55:
            raise ValueError("timeout_seconds должен быть в диапазоне 1..55")

    @staticmethod
    def _validate(message: OutboxMessage) -> dict[str, Any]:
        payload = message.payload
        if not isinstance(payload, dict):
            raise ValueError("outbox payload должен быть JSON-объектом")
        if payload.get("kind") not in ("max_text", "max_edit"):
            raise ValueError(f"неизвестный тип outbox payload: {payload.get('kind')!r}")
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("outbox payload должен содержать непустой text")
        if len(text) > MAX_TEXT_LIMIT:
            raise ValueError("outbox text слишком длинный")
        if payload.get("kind") == "max_edit":
            target = payload.get("message_id")
            if not isinstance(target, str) or not target:
                raise ValueError("max_edit payload должен содержать message_id")
        return payload

    async def send(self, message: OutboxMessage) -> None:
        """Perform exactly one bounded provider request for the claimed intent."""
        payload = self._validate(message)
        attachments = _markup(payload.get("keyboard_rows"))
        if payload["kind"] == "max_edit":
            try:
                await asyncio.wait_for(
                    self._bot.edit_message(
                        message_id=payload["message_id"],
                        text=payload["text"],
                        attachments=[attachments] if attachments else None,
                    ),
                    timeout=self.timeout_seconds,
                )
                return
            except asyncio.TimeoutError:
                # The lease bounds the attempt; retrying the edit is safe and
                # idempotent, so let the outbox schedule it rather than sending.
                raise
            except Exception:  # noqa: BLE001
                # The screen is gone, too old, or not editable. Silence would be
                # worse than an extra message: fall through and send a new one.
                pass
        files = list(self._attachments_for(message.delivery_key)) if self._attachments_for else []
        with _materialised(files) as media:
            outgoing = ([attachments] if attachments else []) + media
            kwargs = {"text": payload["text"], "attachments": outgoing or None}
            if message.chat_id is not None:
                kwargs["chat_id"] = message.chat_id
            else:
                try:
                    kwargs["user_id"] = int(message.user_id)
                except (TypeError, ValueError) as exc:
                    raise ValueError("outbox user_id должен быть числовым, если chat_id отсутствует") from exc
            await asyncio.wait_for(
                self._bot.send_message(**kwargs),
                timeout=self.timeout_seconds,
            )


__all__ = ["MaxOutboundTransport", "MAX_TEXT_LIMIT", "DEFAULT_SEND_TIMEOUT_SECONDS"]

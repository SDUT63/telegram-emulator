#!/usr/bin/env python3
"""Deliver operator messages through the same explicit MAX transport."""
from __future__ import annotations

import asyncio
import logging

from max_outbound_transport import MaxOutboundTransport

log = logging.getLogger("сдут-бот")


def _files(message: dict) -> list | None:
    from max_bot import _outgoing_files
    return _outgoing_files(message)


def _payload(message: dict) -> dict:
    return {
        "kind": "max_text",
        "text": f"{message['text']}\n\n— {message['who']}, служба долговременного ухода",
        "user_id": str(message["user_id"]),
        "chat_id": None,
        "keyboard_rows": [],
    }


async def run(bot) -> None:
    """Poll the legacy CRM file queue and deliver via MaxOutboundTransport.

    The CRM queue is a compatibility integration, not the questionnaire
    transaction source of truth. It therefore has its own explicit retry
    state while still sharing the single network transport boundary.
    """
    import crm_store

    transport = MaxOutboundTransport(bot)
    while True:
        try:
            for message in crm_store.pending_messages():
                try:
                    payload = _payload(message)
                    attachments = _files(message)
                    if attachments:
                        payload["attachments"] = attachments
                    await transport.send(payload)
                    crm_store.mark_sent(message)
                    log.info("CRM outbound message delivered")
                except Exception as error:  # noqa: BLE001
                    crm_store.mark_sent(message, error=str(error))
                    log.warning("CRM outbound delivery failed: %s", error)
        except Exception as error:  # noqa: BLE001
            log.warning("CRM queue polling failed: %s", error)
        await asyncio.sleep(3)


__all__ = ["run"]

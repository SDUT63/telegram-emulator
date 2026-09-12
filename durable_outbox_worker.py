#!/usr/bin/env python3
"""MAX network worker for the PostgreSQL durable outbound queue."""
from __future__ import annotations

import asyncio
import logging
import os
import time

from max_outbound_transport import MaxOutboundTransport
from outbox_postgres import (
    DEFAULT_SENT_RETENTION_SECONDS,
    PostgresOutbox,
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


async def deliver_once(bot, *, queue: PostgresOutbox | None = None) -> int:
    """Claim and process one batch through the explicit MAX transport."""
    queue = queue or PostgresOutbox()
    transport = MaxOutboundTransport(bot)
    claimed = queue.claim()
    for message in claimed:
        try:
            await transport.send(message)
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
                # The provider request may already have succeeded. Do not send
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

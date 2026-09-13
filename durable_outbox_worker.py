#!/usr/bin/env python3
"""MAX network worker for the PostgreSQL durable outbound queue."""
from __future__ import annotations

import asyncio
import logging
import os
import time

import psycopg
from psycopg.rows import dict_row

from max_outbound_transport import MaxOutboundTransport
from metrics import METRICS
from outbox_postgres import DEFAULT_SENT_RETENTION_SECONDS, PostgresOutbox

log = logging.getLogger("сдут-бот")
POLL_SECONDS = 0.5
PRUNE_INTERVAL_SECONDS = 3600
LEASE_SAFETY_MARGIN_SECONDS = 5


def _retention_seconds() -> int:
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


def _validate_lease_budget(queue: PostgresOutbox, transport: MaxOutboundTransport) -> None:
    required = transport.timeout_seconds + LEASE_SAFETY_MARGIN_SECONDS
    if queue.lease_seconds < required:
        raise ValueError(
            "outbox lease_seconds must be at least send timeout plus "
            f"{LEASE_SAFETY_MARGIN_SECONDS}s safety margin ({required}s required, "
            f"{queue.lease_seconds}s configured)"
        )


def _claim_still_deliverable(queue: PostgresOutbox, message_id: int, user_id: str) -> bool:
    """Revalidate a claim after acquiring the user lock.

    A deletion committed between claim and send invalidates the claim — except
    for the farewell row, which is the deletion confirmation itself and is the
    one message a purged user is still owed.
    """
    with psycopg.connect(queue.db_url, row_factory=dict_row) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM outbox_messages o
            LEFT JOIN deleted_users d ON d.user_id=o.user_id
            WHERE o.id=%s AND o.status='sending' AND o.locked_by=%s
              AND o.user_id=%s AND (d.user_id IS NULL OR o.farewell)
            """,
            (message_id, queue.worker_id, str(user_id)),
        ).fetchone()
    return row is not None


async def deliver_once(bot, *, queue: PostgresOutbox | None = None) -> int:
    queue = queue or PostgresOutbox()
    transport = MaxOutboundTransport(bot)
    _validate_lease_budget(queue, transport)
    claimed = queue.claim(limit=1)
    for message in claimed:
        if not message.user_id:
            log.error("MAX outbox #%s: claimed message has no recipient", message.id)
            METRICS.inc("sdut_outbox_invalid_claims_total")
            continue
        with queue.user_delivery_lock(message.user_id):
            if not _claim_still_deliverable(queue, message.id, message.user_id):
                log.info("MAX outbox #%s: claim invalidated by user deletion", message.id)
                METRICS.inc("sdut_outbox_claims_invalidated_total")
                continue
            try:
                await transport.send(message)
            except Exception as error:  # noqa: BLE001
                error_class = type(error).__name__
                METRICS.inc("sdut_outbox_delivery_failures_total", {"error_class": error_class})
                log.warning(
                    "MAX outbox #%s delivery failed (attempt %s): %s",
                    message.id,
                    message.attempts,
                    error_class,
                )
                try:
                    queue.mark_failed(message.id, error)
                except Exception:  # noqa: BLE001
                    METRICS.inc("sdut_outbox_persistence_failures_total")
                    log.exception("MAX outbox #%s: failure state could not be persisted", message.id)
            else:
                try:
                    queue.mark_sent(message.id)
                except Exception:  # noqa: BLE001
                    METRICS.inc("sdut_outbox_persistence_failures_total")
                    log.exception("MAX outbox #%s: sent state could not be persisted", message.id)
                else:
                    METRICS.inc("sdut_outbox_deliveries_total")
    return len(claimed)


async def run(
    bot,
    *,
    poll_seconds: float = POLL_SECONDS,
    prune_interval_seconds: float = PRUNE_INTERVAL_SECONDS,
) -> None:
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
            METRICS.inc("sdut_outbox_worker_failures_total", {"error_class": type(error).__name__})
            log.warning("MAX outbox worker failure: %s", type(error).__name__)
            await asyncio.sleep(poll_seconds)

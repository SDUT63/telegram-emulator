#!/usr/bin/env python3
"""Durable PostgreSQL outbound queue for MAX messages.

The queue deliberately provides at-least-once external delivery. A process
crash after MAX accepts a message but before the local `sent` commit can still
produce a duplicate on retry because MAX does not expose a provider-side
idempotency key for ordinary bot messages.
"""
from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


DEFAULT_LEASE_SECONDS = 60
DEFAULT_MAX_ATTEMPTS = 12
DEFAULT_BATCH_SIZE = 20


def database_url() -> str:
    value = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not value:
        raise RuntimeError("SDUT_DATABASE_URL не задан")
    return value


def _connect(db_url: str | None = None):
    return psycopg.connect(db_url or database_url(), row_factory=dict_row)


def delivery_key(event_id: str, ordinal: int = 0) -> str:
    """Stable key for one logical outgoing message of an incoming event."""
    if not event_id or not str(event_id).strip():
        raise ValueError("event_id must not be empty")
    if ordinal < 0:
        raise ValueError("ordinal must be >= 0")
    return f"{str(event_id).strip()}:out:{ordinal}"


@dataclass(frozen=True)
class OutboxMessage:
    id: int
    delivery_key: str
    user_id: str
    chat_id: str | None
    payload: dict[str, Any]
    status: str
    attempts: int
    available_at: datetime
    last_error: str | None


class PostgresOutbox:
    """Insert, claim, acknowledge and retry outbound messages."""

    def __init__(
        self,
        db_url: str | None = None,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        worker_id: str | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.db_url = db_url or database_url()
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def enqueue(
        self,
        *,
        delivery_key: str,
        user_id: str,
        payload: dict[str, Any],
        chat_id: str | None = None,
        conn=None,
    ) -> int:
        if not delivery_key.strip():
            raise ValueError("delivery_key must not be empty")
        if not str(user_id).strip():
            raise ValueError("user_id must not be empty")
        own = conn is None
        connection = conn or _connect(self.db_url)
        try:
            row = connection.execute(
                """
                INSERT INTO outbox_messages(delivery_key,user_id,chat_id,payload)
                VALUES(%s,%s,%s,%s)
                ON CONFLICT(delivery_key) DO NOTHING
                RETURNING id
                """,
                (delivery_key, str(user_id), chat_id, Jsonb(payload)),
            ).fetchone()
            if own:
                connection.commit()
            if row:
                return int(row["id"])
            existing = connection.execute(
                "SELECT id FROM outbox_messages WHERE delivery_key=%s",
                (delivery_key,),
            ).fetchone()
            if existing is None:
                raise RuntimeError("outbox insert disappeared unexpectedly")
            return int(existing["id"])
        except Exception:
            if own:
                connection.rollback()
            raise
        finally:
            if own:
                connection.close()

    def claim(self, *, limit: int = DEFAULT_BATCH_SIZE) -> list[OutboxMessage]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        now = datetime.now(timezone.utc)
        with _connect(self.db_url) as conn:
            conn.execute(
                """
                UPDATE outbox_messages
                   SET status='pending', locked_at=NULL, locked_by=NULL
                 WHERE status='sending'
                   AND locked_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                """,
                (self.lease_seconds,),
            )
            rows = conn.execute(
                """
                WITH picked AS (
                    SELECT id
                      FROM outbox_messages
                     WHERE status='pending'
                       AND available_at <= CURRENT_TIMESTAMP
                     ORDER BY id
                     FOR UPDATE SKIP LOCKED
                     LIMIT %s
                )
                UPDATE outbox_messages AS o
                   SET status='sending', locked_at=%s, locked_by=%s,
                       attempts=o.attempts+1
                  FROM picked
                 WHERE o.id=picked.id
                RETURNING o.*
                """,
                (limit, now, self.worker_id),
            ).fetchall()
            return [self._row(row) for row in rows]

    def mark_sent(self, message_id: int) -> None:
        with _connect(self.db_url) as conn:
            updated = conn.execute(
                """
                UPDATE outbox_messages
                   SET status='sent', sent_at=CURRENT_TIMESTAMP,
                       locked_at=NULL, locked_by=NULL, last_error=NULL
                 WHERE id=%s AND status='sending' AND locked_by=%s
                """,
                (message_id, self.worker_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError(f"outbox message {message_id} is not owned by this worker")

    def mark_failed(self, message_id: int, error: str) -> None:
        safe_error = str(error)[:4000]
        with _connect(self.db_url) as conn:
            row = conn.execute(
                "SELECT attempts FROM outbox_messages WHERE id=%s AND status='sending' AND locked_by=%s FOR UPDATE",
                (message_id, self.worker_id),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"outbox message {message_id} is not owned by this worker")
            attempts = int(row["attempts"])
            if attempts >= self.max_attempts:
                status = "dead"
                available_sql = "available_at"
                params = (safe_error, message_id, self.worker_id)
                conn.execute(
                    f"""UPDATE outbox_messages SET status=%s,last_error=%s,locked_at=NULL,locked_by=NULL WHERE id=%s AND locked_by=%s""",
                    (status, safe_error, message_id, self.worker_id),
                )
            else:
                delay = min(3600, 2 ** min(attempts, 10))
                conn.execute(
                    """
                    UPDATE outbox_messages
                       SET status='pending', available_at=CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                           last_error=%s, locked_at=NULL, locked_by=NULL
                     WHERE id=%s AND locked_by=%s
                    """,
                    (delay, safe_error, message_id, self.worker_id),
                )

    def recover_stale(self) -> int:
        with _connect(self.db_url) as conn:
            result = conn.execute(
                """
                UPDATE outbox_messages
                   SET status='pending', locked_at=NULL, locked_by=NULL
                 WHERE status='sending'
                   AND locked_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                """,
                (self.lease_seconds,),
            )
            return result.rowcount

    def stats(self) -> dict[str, int]:
        with _connect(self.db_url) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM outbox_messages GROUP BY status"
            ).fetchall()
        result = {"pending": 0, "sending": 0, "sent": 0, "dead": 0}
        result.update({str(row["status"]): int(row["n"]) for row in rows})
        return result

    @staticmethod
    def _row(row: dict[str, Any]) -> OutboxMessage:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return OutboxMessage(
            id=int(row["id"]),
            delivery_key=str(row["delivery_key"]),
            user_id=str(row["user_id"]),
            chat_id=str(row["chat_id"]) if row["chat_id"] is not None else None,
            payload=dict(payload),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            available_at=row["available_at"],
            last_error=row["last_error"],
        )

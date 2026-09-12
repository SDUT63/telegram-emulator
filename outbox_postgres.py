#!/usr/bin/env python3
"""Durable PostgreSQL outbound queue for MAX messages.

The queue deliberately provides at-least-once external delivery. A process
crash after MAX accepts a message but before the local `sent` commit can still
produce a duplicate on retry because MAX does not expose a provider-side
idempotency key for ordinary bot messages.

Successful and permanently failed deliveries are redacted after their
terminal state is recorded. A permanent tombstone keeps the delivery key and
payload digest, so retention cleanup cannot make an old event capable of
creating a new outbound delivery.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
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
DEFAULT_SENT_RETENTION_SECONDS = 30 * 24 * 60 * 60

_REDACTED_PAYLOAD = {"kind": "redacted"}


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


def payload_sha256(payload: dict[str, Any]) -> str:
    """Hash canonical JSON without storing another copy of its contents."""
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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
    """Insert, claim, acknowledge, retry and retain outbound messages."""

    def __init__(self, db_url: str | None = None, *, lease_seconds: int = DEFAULT_LEASE_SECONDS, max_attempts: int = DEFAULT_MAX_ATTEMPTS, worker_id: str | None = None) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.db_url = db_url or database_url()
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def enqueue(self, *, delivery_key: str, user_id: str, payload: dict[str, Any], chat_id: str | None = None, conn=None) -> int:
        """Persist one outbound intent, idempotently and collision-safely.

        Reusing a delivery key for a different logical message is a programming
        error and fails loudly. A matching retained tombstone is a historical
        duplicate and is deliberately treated as a no-op.
        """
        key = str(delivery_key).strip()
        uid = str(user_id).strip()
        if not key:
            raise ValueError("delivery_key must not be empty")
        if not uid:
            raise ValueError("user_id must not be empty")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")
        digest = payload_sha256(payload)

        own = conn is None
        connection = conn or _connect(self.db_url)
        try:
            tombstone = connection.execute(
                "SELECT payload_sha256 FROM outbox_delivery_tombstones WHERE delivery_key=%s",
                (key,),
            ).fetchone()
            if tombstone is not None:
                if str(tombstone["payload_sha256"]) != digest:
                    raise ValueError(f"delivery_key collision for {key!r}: retained outbound intent differs")
                if own:
                    connection.commit()
                return 0

            row = connection.execute(
                """
                INSERT INTO outbox_messages(delivery_key,user_id,chat_id,payload,payload_sha256)
                VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(delivery_key) DO NOTHING
                RETURNING id
                """,
                (key, uid, chat_id, Jsonb(payload), digest),
            ).fetchone()
            if row:
                message_id = int(row["id"])
            else:
                existing = connection.execute(
                    "SELECT id,user_id,chat_id,payload,payload_sha256 FROM outbox_messages WHERE delivery_key=%s",
                    (key,),
                ).fetchone()
                if existing is None:
                    raise RuntimeError("outbox insert disappeared unexpectedly")
                existing_payload = existing["payload"]
                if isinstance(existing_payload, str):
                    existing_payload = json.loads(existing_payload)
                existing_chat = str(existing["chat_id"]) if existing["chat_id"] is not None else None
                existing_digest = existing["payload_sha256"]
                same_payload = (
                    str(existing["user_id"]) == uid
                    and existing_chat == chat_id
                    and (str(existing_digest) == digest if existing_digest else dict(existing_payload) == payload)
                )
                if not same_payload:
                    raise ValueError(f"delivery_key collision for {key!r}: existing outbound intent differs")
                message_id = int(existing["id"])
            if own:
                connection.commit()
            return message_id
        except Exception:
            if own:
                connection.rollback()
            raise
        finally:
            if own:
                connection.close()

    def claim(self, *, limit: int = DEFAULT_BATCH_SIZE) -> list[OutboxMessage]:
        """Atomically claim ready rows, allowing multiple workers safely."""
        if limit < 1:
            raise ValueError("limit must be >= 1")
        now = datetime.now(timezone.utc)
        with _connect(self.db_url) as conn:
            stale = conn.execute(
                """
                SELECT id,payload,payload_sha256,attempts
                  FROM outbox_messages
                 WHERE status='sending'
                   AND locked_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                 FOR UPDATE SKIP LOCKED
                """,
                (self.lease_seconds,),
            ).fetchall()
            for row in stale:
                attempts = int(row["attempts"])
                if attempts >= self.max_attempts:
                    # A stale delivery that exhausted its budget is terminal.
                    # Redact its potentially sensitive response before making
                    # it visible as dead; the digest remains the integrity
                    # identity of the original outbound intent.
                    digest = str(row["payload_sha256"] or payload_sha256(dict(row["payload"])))
                    conn.execute(
                        """
                        UPDATE outbox_messages
                           SET status='dead', payload=%s, payload_sha256=%s,
                               locked_at=NULL, locked_by=NULL,
                               last_error=COALESCE(last_error, 'worker lease expired')
                         WHERE id=%s AND status='sending'
                        """,
                        (Jsonb(_REDACTED_PAYLOAD), digest, int(row["id"])),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE outbox_messages
                           SET status='pending', locked_at=NULL, locked_by=NULL
                         WHERE id=%s AND status='sending'
                        """,
                        (int(row["id"]),),
                    )

            rows = conn.execute(
                """
                WITH picked AS (
                    SELECT id FROM outbox_messages
                     WHERE status='pending' AND available_at <= CURRENT_TIMESTAMP
                     ORDER BY id FOR UPDATE SKIP LOCKED LIMIT %s
                )
                UPDATE outbox_messages AS o
                   SET status='sending', locked_at=%s, locked_by=%s, attempts=o.attempts+1
                  FROM picked WHERE o.id=picked.id
                RETURNING o.*
                """,
                (limit, now, self.worker_id),
            ).fetchall()
            return [self._row(row) for row in rows]

    def mark_sent(self, message_id: int) -> None:
        """Acknowledge a delivery and immediately redact its payload."""
        with _connect(self.db_url) as conn:
            row = conn.execute(
                "SELECT payload,payload_sha256 FROM outbox_messages WHERE id=%s AND status='sending' AND locked_by=%s FOR UPDATE",
                (message_id, self.worker_id),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"outbox message {message_id} is not owned by this worker")
            digest = str(row["payload_sha256"] or payload_sha256(dict(row["payload"])))
            updated = conn.execute(
                """
                UPDATE outbox_messages
                   SET status='sent', sent_at=CURRENT_TIMESTAMP,
                       locked_at=NULL, locked_by=NULL, last_error=NULL,
                       payload=%s, payload_sha256=%s
                 WHERE id=%s AND status='sending' AND locked_by=%s
                """,
                (Jsonb(_REDACTED_PAYLOAD), digest, message_id, self.worker_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError(f"outbox message {message_id} is not owned by this worker")

    def mark_failed(self, message_id: int, error: str) -> None:
        """Return a failed message for retry or quarantine it as dead."""
        safe_error = str(error)[:4000]
        with _connect(self.db_url) as conn:
            row = conn.execute(
                "SELECT payload,payload_sha256,attempts FROM outbox_messages WHERE id=%s AND status='sending' AND locked_by=%s FOR UPDATE",
                (message_id, self.worker_id),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"outbox message {message_id} is not owned by this worker")
            attempts = int(row["attempts"])
            if attempts >= self.max_attempts:
                digest = str(row["payload_sha256"] or payload_sha256(dict(row["payload"])))
                conn.execute(
                    """
                    UPDATE outbox_messages
                       SET status='dead', payload=%s, payload_sha256=%s,
                           last_error=%s, locked_at=NULL, locked_by=NULL
                     WHERE id=%s AND status='sending' AND locked_by=%s
                    """,
                    (Jsonb(_REDACTED_PAYLOAD), digest, safe_error, message_id, self.worker_id),
                )
            else:
                delay = min(3600, 2 ** min(attempts, 10))
                conn.execute(
                    "UPDATE outbox_messages SET status='pending',available_at=CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),last_error=%s,locked_at=NULL,locked_by=NULL WHERE id=%s AND status='sending' AND locked_by=%s",
                    (delay, safe_error, message_id, self.worker_id),
                )

    def recover_stale(self) -> int:
        """Release expired leases, enforcing the retry budget."""
        with _connect(self.db_url) as conn:
            rows = conn.execute(
                """
                SELECT id,payload,payload_sha256,attempts
                  FROM outbox_messages
                 WHERE status='sending'
                   AND locked_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                 FOR UPDATE SKIP LOCKED
                """,
                (self.lease_seconds,),
            ).fetchall()
            for row in rows:
                attempts = int(row["attempts"])
                if attempts >= self.max_attempts:
                    digest = str(row["payload_sha256"] or payload_sha256(dict(row["payload"])))
                    conn.execute(
                        """
                        UPDATE outbox_messages
                           SET status='dead', payload=%s, payload_sha256=%s,
                               locked_at=NULL, locked_by=NULL,
                               last_error=COALESCE(last_error, 'worker lease expired')
                         WHERE id=%s AND status='sending'
                        """,
                        (Jsonb(_REDACTED_PAYLOAD), digest, int(row["id"])),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE outbox_messages
                           SET status='pending', locked_at=NULL, locked_by=NULL
                         WHERE id=%s AND status='sending'
                        """,
                        (int(row["id"]),),
                    )
            return len(rows)

    def prune_sent(self, *, retention_seconds: int = DEFAULT_SENT_RETENTION_SECONDS, limit: int = 500) -> int:
        """Redact-and-tombstone old sent rows without reopening delivery keys.

        Tombstones are intentionally retained indefinitely: deleting them would
        re-enable an old MAX event to create a new outbound delivery after a
        long-retention cleanup. Their footprint is only the delivery key and
        a 64-character digest.
        """
        if retention_seconds < 0:
            raise ValueError("retention_seconds must be >= 0")
        if limit < 1:
            raise ValueError("limit must be >= 1")
        with _connect(self.db_url) as conn:
            rows = conn.execute(
                """
                SELECT id,delivery_key,payload,payload_sha256
                  FROM outbox_messages
                 WHERE status='sent'
                   AND sent_at IS NOT NULL
                   AND sent_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
                 ORDER BY id
                 FOR UPDATE SKIP LOCKED
                 LIMIT %s
                """,
                (retention_seconds, limit),
            ).fetchall()
            if not rows:
                return 0
            ids: list[int] = []
            for row in rows:
                digest = str(row["payload_sha256"] or payload_sha256(dict(row["payload"])))
                existing = conn.execute(
                    "SELECT payload_sha256 FROM outbox_delivery_tombstones WHERE delivery_key=%s FOR UPDATE",
                    (str(row["delivery_key"]),),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_sha256"]) != digest:
                        raise ValueError(
                            f"delivery_key collision for {row['delivery_key']!r}: tombstone digest differs"
                        )
                else:
                    conn.execute(
                        "INSERT INTO outbox_delivery_tombstones(delivery_key,payload_sha256) VALUES(%s,%s)",
                        (str(row["delivery_key"]), digest),
                    )
                ids.append(int(row["id"]))
            conn.execute("DELETE FROM outbox_messages WHERE id = ANY(%s)", (ids,))
            return len(ids)

    def stats(self) -> dict[str, int]:
        with _connect(self.db_url) as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS n FROM outbox_messages GROUP BY status").fetchall()
            tombstones = conn.execute("SELECT COUNT(*) AS n FROM outbox_delivery_tombstones").fetchone()
        result = {"pending": 0, "sending": 0, "sent": 0, "dead": 0, "tombstones": 0}
        result.update({str(row["status"]): int(row["n"]) for row in rows})
        result["tombstones"] = int(tombstones["n"])
        return result

    @staticmethod
    def _row(row: dict[str, Any]) -> OutboxMessage:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return OutboxMessage(
            id=int(row["id"]), delivery_key=str(row["delivery_key"]), user_id=str(row["user_id"]),
            chat_id=str(row["chat_id"]) if row["chat_id"] is not None else None,
            payload=dict(payload), status=str(row["status"]), attempts=int(row["attempts"]),
            available_at=row["available_at"], last_error=row["last_error"],
        )

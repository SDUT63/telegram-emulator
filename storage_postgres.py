#!/usr/bin/env python3
"""Durable PostgreSQL storage for the SDUT MAX bot.

Two storage modes are intentionally kept:

* :class:`PostgresSurvey` preserves the original Survey interface and is used
  by generic callers/tests.
* :class:`TransactionalPostgresSurvey` adds request-level atomicity for the
  MAX dispatcher without requiring the legacy dispatcher to be rewritten.

The transactional path is the production path. One incoming event is handled
under a PostgreSQL transaction which:

1. serializes the affected user with ``pg_advisory_xact_lock``;
2. loads the current user state while holding the row lock;
3. claims the event in ``processed_events``;
4. runs the existing Survey mutation code;
5. persists the changed user state;
6. writes an audit record;
7. commits all of the above together.

If the process dies before commit, both the event claim and state mutation are
rolled back and MAX can safely redeliver the event. If commit succeeds, a
redelivery sees ``processed_events`` and is ignored.

This gives exactly-once *database state transition* semantics. External side
effects such as an already-sent MAX response are deliberately not described as
exactly-once; those require an outbox/idempotency key at the transport layer.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

import psycopg
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from chatbot_survey import Survey

DEFAULT_LIMIT = 5000
DEFAULT_LEASE_SECONDS = 45.0
T = TypeVar("T")

# Context is local to the current asyncio task/thread. The dispatcher calls
# Survey methods synchronously before awaiting MAX I/O, so this is safe for the
# transaction wrapper and does not leak event IDs between concurrent requests.
_TX_CONNECTION: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "sdut_tx_connection", default=None
)
_TX_USER: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "sdut_tx_user", default=None
)
_TX_EVENT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "sdut_tx_event", default=None
)
_TX_ACCEPTED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "sdut_tx_accepted", default=False
)


def database_url() -> str:
    value = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not value:
        raise RuntimeError(
            "SDUT_DATABASE_URL не задан. Для PostgreSQL укажите "
            "postgresql://user:password@host:5432/database"
        )
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _event_hash(event_type: str, payload: dict[str, Any] | None) -> str:
    raw = _canonical({"type": event_type, "payload": payload or {}}).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class PostgresSurvey(Survey):
    """Survey state persisted in PostgreSQL with safe per-user reconciliation."""

    def __init__(self, db_url: str | None = None, *, list_options: bool = False) -> None:
        self.db_url = db_url or database_url()
        self._db_lock = threading.RLock()
        self._loaded_snapshot: dict[str, dict[str, Any]] = {}
        self._init_db()
        super().__init__(storage_path=":memory:", list_options=list_options)

    def _connect(self):
        return psycopg.connect(self.db_url, row_factory=tuple_row)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS survey_state (
                    user_id TEXT PRIMARY KEY,
                    state_json JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS event_leases (
                    event_id TEXT PRIMARY KEY,
                    claimed_at DOUBLE PRECISION NOT NULL,
                    last_seen_at DOUBLE PRECISION NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    event_type TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    processed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id BIGSERIAL PRIMARY KEY,
                    user_id TEXT,
                    event_type TEXT NOT NULL,
                    event_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_user_created "
                "ON audit_events(user_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_leases_seen "
                "ON event_leases(last_seen_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_processed_events_user_time "
                "ON processed_events(user_id, processed_at)"
            )

    def load(self) -> None:
        with self._db_lock, self._connect() as conn:
            rows = conn.execute("SELECT user_id, state_json FROM survey_state").fetchall()
        state: dict[str, dict[str, Any]] = {}
        for user_id, value in rows:
            if isinstance(value, dict):
                state[str(user_id)] = value
        self.state = state
        self._loaded_snapshot = copy.deepcopy(state)

    def save(self) -> None:
        """Persist changed users without deleting another worker's state."""
        active = _TX_CONNECTION.get()
        active_user = _TX_USER.get()
        if active is not None and active_user:
            self._save_user(active, active_user)
            return

        with self._db_lock, self._connect() as conn:
            current_ids = {str(user_id) for user_id in self.state}
            loaded_ids = set(self._loaded_snapshot)
            changed_ids = set()
            for user_id in current_ids | loaded_ids:
                before = self._loaded_snapshot.get(user_id)
                after = self.state.get(user_id)
                if _canonical(before) != _canonical(after):
                    changed_ids.add(user_id)

            for user_id in sorted(changed_ids):
                self._save_user(conn, user_id)
            self._loaded_snapshot = copy.deepcopy(self.state)

    def _save_user(self, conn, user_id: str) -> None:
        after = self.state.get(user_id)
        before = self._loaded_snapshot.get(user_id)
        row = conn.execute(
            "SELECT state_json FROM survey_state WHERE user_id=%s FOR UPDATE",
            (user_id,),
        ).fetchone()

        if after is None:
            if row is None:
                return
            db_state = row[0] if isinstance(row[0], dict) else {}
            if before is None or _canonical(db_state) == _canonical(before):
                conn.execute("DELETE FROM survey_state WHERE user_id=%s", (user_id,))
            return

        if row is None:
            merged = copy.deepcopy(after)
        else:
            db_state = row[0] if isinstance(row[0], dict) else {}
            merged = copy.deepcopy(db_state)
            for key, value in after.items():
                old_value = before.get(key) if before else None
                if _canonical(value) != _canonical(old_value):
                    merged[key] = value
            if before:
                for key in before:
                    if key not in after and key in merged:
                        if _canonical(merged[key]) == _canonical(before[key]):
                            merged.pop(key, None)

        conn.execute(
            """
            INSERT INTO survey_state(user_id,state_json,updated_at)
            VALUES(%s,%s,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                state_json=EXCLUDED.state_json,
                updated_at=EXCLUDED.updated_at
            """,
            (user_id, Jsonb(merged)),
        )
        self.state[user_id] = merged
        self._loaded_snapshot[user_id] = copy.deepcopy(merged)

    def mark_event_once(self, event_id: str) -> bool:
        return PersistentSeen(db_url=self.db_url).fresh(event_id)

    def audit(self, user_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        active = _TX_CONNECTION.get()
        if active is not None:
            active.execute(
                "INSERT INTO audit_events(user_id,event_type,event_json) VALUES(%s,%s,%s)",
                (user_id, event_type, Jsonb(payload)),
            )
            return
        with self._db_lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(user_id,event_type,event_json) VALUES(%s,%s,%s)",
                (user_id, event_type, Jsonb(payload)),
            )

    def health(self) -> bool:
        with self._db_lock, self._connect() as conn:
            return conn.execute("SELECT 1").fetchone() == (1,)


class TransactionalPostgresSurvey(PostgresSurvey):
    """PostgresSurvey with atomic event/state/audit handling for MAX.

    The legacy dispatcher is intentionally left unchanged. Its ``Seen`` hook
    records the current event in a context variable; this class wraps the
    mutating Survey calls and commits the database transition before the
    dispatcher performs network I/O.
    """

    def _begin_event(self, user_id: str, event_type: str, payload: dict[str, Any] | None = None):
        event_id = _TX_EVENT.get()
        if not event_id:
            return None, False

        conn = self._connect()
        try:
            # Transaction-level lock serializes every mutation for one user
            # across all bot workers. PostgreSQL releases it automatically at
            # commit/rollback.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (str(user_id),),
            )
            row = conn.execute(
                "SELECT state_json FROM survey_state WHERE user_id=%s FOR UPDATE",
                (str(user_id),),
            ).fetchone()
            current = row[0] if row and isinstance(row[0], dict) else self._blank()
            self.state[str(user_id)] = copy.deepcopy(current)
            self._loaded_snapshot[str(user_id)] = copy.deepcopy(current)

            event_hash = _event_hash(event_type, payload)
            inserted = conn.execute(
                """
                INSERT INTO processed_events(event_id,user_id,event_type,event_hash)
                VALUES(%s,%s,%s,%s)
                ON CONFLICT(event_id) DO NOTHING
                RETURNING event_id
                """,
                (event_id, str(user_id), event_type, event_hash),
            ).fetchone()
            if inserted is None:
                conn.rollback()
                conn.close()
                _TX_ACCEPTED.set(False)
                return None, False

            token_conn = _TX_CONNECTION.set(conn)
            token_user = _TX_USER.set(str(user_id))
            token_accepted = _TX_ACCEPTED.set(True)
            return (conn, token_conn, token_user, token_accepted, event_type, payload or {}), True
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def _finish_event(self, ctx, user_id: str, event_type: str, payload: dict[str, Any] | None, error: BaseException | None) -> None:
        if not ctx:
            return
        conn, token_conn, token_user, token_accepted, _, _ = ctx
        try:
            if error is None:
                self._save_user(conn, str(user_id))
                self.audit(str(user_id), event_type, payload or {})
                conn.commit()
            else:
                conn.rollback()
        finally:
            _TX_CONNECTION.reset(token_conn)
            _TX_USER.reset(token_user)
            _TX_ACCEPTED.reset(token_accepted)
            conn.close()

    @contextmanager
    def _atomic(self, user_id: str, event_type: str, payload: dict[str, Any] | None = None) -> Iterator[bool]:
        ctx, accepted = self._begin_event(user_id, event_type, payload)
        if not accepted:
            yield False
            return
        error: BaseException | None = None
        try:
            yield True
        except BaseException as exc:
            error = exc
            raise
        finally:
            self._finish_event(ctx, user_id, event_type, payload, error)

    def _mutate(self, user_id: str, event_type: str, payload: dict[str, Any], fn: Callable[[], T], duplicate: T) -> T:
        # Nested mutations inside Survey.handle reuse the outer transaction.
        if _TX_CONNECTION.get() is not None and _TX_USER.get() == str(user_id):
            return fn()
        with self._atomic(str(user_id), event_type, payload) as accepted:
            if not accepted:
                return duplicate
            return fn()

    def handle(self, user_id: str, text: str) -> str:
        return self._mutate(
            user_id,
            "message",
            {"kind": "message"},
            lambda: super().handle(user_id, text),
            "",
        )

    def grant_consent(self, user_id: str) -> str:
        return self._mutate(
            user_id, "callback", {"action": "grant_consent"},
            lambda: super().grant_consent(user_id), "",
        )

    def refuse_consent(self, user_id: str) -> str:
        return self._mutate(
            user_id, "callback", {"action": "refuse_consent"},
            lambda: super().refuse_consent(user_id), "",
        )

    def toggle(self, user_id: str, step: int, index: int) -> bool:
        return self._mutate(
            user_id, "callback", {"action": "toggle", "step": step, "index": index},
            lambda: super().toggle(user_id, step, index), False,
        )

    def answer_by_numbers(self, user_id: str, numbers: list[int]) -> str:
        return self._mutate(
            user_id, "callback", {"action": "answer", "numbers": numbers},
            lambda: super().answer_by_numbers(user_id, numbers), "",
        )

    def restart_after_consent(self, user_id: str) -> str:
        return self._mutate(
            user_id, "callback", {"action": "restart"},
            lambda: super().restart_after_consent(user_id), "",
        )

    def start(self, user_id: str) -> str:
        # BotStarted has no dispatcher Seen key in the legacy handler. Keep it
        # durable and serialized even though it is not part of MAX retry
        # de-duplication.
        if _TX_CONNECTION.get() is not None:
            return super().start(user_id)
        event_id = f"start:{user_id}:{time.time_ns()}"
        token = _TX_EVENT.set(event_id)
        try:
            return self._mutate(
                user_id, "bot_started", {"kind": "bot_started"},
                lambda: super().start(user_id), "",
            )
        finally:
            _TX_EVENT.reset(token)

    def note_message(self, user_id: str, text: str, files: list | None = None) -> None:
        # A file is attached by the legacy dispatcher after handle() has
        # already committed. Treat it as a separate idempotent follow-up.
        if _TX_CONNECTION.get() is not None:
            super().note_message(user_id, text, files)
            return
        event_id = _TX_EVENT.get()
        if event_id:
            followup = f"{event_id}:message-note"
            token = _TX_EVENT.set(followup)
            try:
                self._mutate(
                    user_id,
                    "message_attachment",
                    {"kind": "message_attachment", "has_files": bool(files)},
                    lambda: super().note_message(user_id, text, files),
                    None,
                )
            finally:
                _TX_EVENT.reset(token)
            return
        super().note_message(user_id, text, files)


class TransactionalPersistentSeen:
    """Dispatcher-compatible Seen that delegates the atomic claim to Survey."""

    def fresh(self, key: str | None) -> bool:
        # The transaction wrapper performs the real INSERT ... ON CONFLICT
        # inside the same DB transaction as state mutation. Returning True here
        # keeps the untouched dispatcher moving to the Survey mutation layer.
        _TX_EVENT.set((key or "").strip() or None)
        return True


class PersistentSeen:
    """Legacy persistent event leases backed by PostgreSQL."""

    def __init__(
        self,
        limit: int = DEFAULT_LIMIT,
        db_url: str | None = None,
        *,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        self.limit = limit
        self.lease_seconds = float(lease_seconds)
        self.db_url = db_url or database_url()
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS event_leases (
                    event_id TEXT PRIMARY KEY,
                    claimed_at DOUBLE PRECISION NOT NULL,
                    last_seen_at DOUBLE PRECISION NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_leases_seen "
                "ON event_leases(last_seen_at)"
            )

    def _connect(self):
        return psycopg.connect(self.db_url, row_factory=tuple_row)

    def fresh(self, key: str | None) -> bool:
        key = (key or "").strip()
        if not key:
            return True
        now = time.time()
        cutoff = now - self.lease_seconds
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT claimed_at FROM event_leases WHERE event_id=%s FOR UPDATE",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO event_leases(event_id,claimed_at,last_seen_at) VALUES(%s,%s,%s)",
                    (key, now, now),
                )
                accepted = True
            elif float(row[0]) <= cutoff:
                conn.execute(
                    "UPDATE event_leases SET claimed_at=%s,last_seen_at=%s WHERE event_id=%s",
                    (now, now, key),
                )
                accepted = True
            else:
                conn.execute(
                    "UPDATE event_leases SET last_seen_at=%s WHERE event_id=%s",
                    (now, key),
                )
                accepted = False
            conn.execute(
                """
                DELETE FROM event_leases
                WHERE event_id IN (
                    SELECT event_id FROM event_leases
                    ORDER BY last_seen_at DESC
                    OFFSET %s
                )
                """,
                (self.limit,),
            )
        return accepted

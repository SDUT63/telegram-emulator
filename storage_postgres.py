#!/usr/bin/env python3
"""PostgreSQL persistence for the SDUT survey.

Use this backend for production/multi-instance deployments. It keeps the
same Survey/PersistentSeen interface as the SQLite pilot backend, but uses
row-level locking and transactional writes suitable for several workers.

Environment:
    SDUT_DATABASE_URL=postgresql://user:password@host:5432/database
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import psycopg
from psycopg.rows import tuple_row

from chatbot_survey import Survey


DEFAULT_LIMIT = 5000
DEFAULT_LEASE_SECONDS = 45.0


def database_url() -> str:
    value = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not value:
        raise RuntimeError(
            "SDUT_DATABASE_URL не задан. Для PostgreSQL укажите "
            "postgresql://user:password@host:5432/database"
        )
    return value


class PostgresSurvey(Survey):
    """Survey persisted in PostgreSQL."""

    def __init__(self, db_url: str | None = None, *, list_options: bool = False) -> None:
        self.db_url = db_url or database_url()
        self._db_lock = threading.RLock()
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

    def load(self) -> None:
        with self._db_lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, state_json FROM survey_state"
            ).fetchall()
        self.state = {}
        for user_id, value in rows:
            if isinstance(value, dict):
                self.state[str(user_id)] = value
            elif isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    self.state[str(user_id)] = parsed

    def save(self) -> None:
        """Reconcile the in-memory survey state in one PostgreSQL transaction."""
        with self._db_lock, self._connect() as conn:
            current_ids = {str(user_id) for user_id in self.state}
            if current_ids:
                conn.execute(
                    "DELETE FROM survey_state WHERE NOT (user_id = ANY(%s))",
                    (list(current_ids),),
                )
            else:
                conn.execute("DELETE FROM survey_state")
            for user_id, state in self.state.items():
                conn.execute(
                    """
                    INSERT INTO survey_state(user_id, state_json, updated_at)
                    VALUES(%s, %s::jsonb, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        state_json = EXCLUDED.state_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        str(user_id),
                        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                    ),
                )

    def mark_event_once(self, event_id: str) -> bool:
        return PersistentSeen(db_url=self.db_url).fresh(event_id)

    def audit(self, user_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        with self._db_lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(user_id, event_type, event_json) VALUES(%s,%s,%s::jsonb)",
                (
                    user_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def health(self) -> bool:
        with self._db_lock, self._connect() as conn:
            return conn.execute("SELECT 1").fetchone() == (1,)


class PersistentSeen:
    """Multi-instance-safe persistent event leases backed by PostgreSQL."""

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
                "SELECT claimed_at FROM event_leases WHERE event_id = %s FOR UPDATE",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO event_leases(event_id, claimed_at, last_seen_at) VALUES(%s,%s,%s)",
                    (key, now, now),
                )
                accepted = True
            elif float(row[0]) <= cutoff:
                conn.execute(
                    "UPDATE event_leases SET claimed_at=%s, last_seen_at=%s WHERE event_id=%s",
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

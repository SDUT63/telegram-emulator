#!/usr/bin/env python3
"""PostgreSQL persistence for the SDUT survey.

The adapter keeps the existing Survey interface while avoiding the dangerous
"delete every row not present in this worker's snapshot" pattern. State is
reconciled per user: unchanged users are never touched, and concurrent
workers merge top-level fields under a row lock.

This is durable storage for a controlled deployment. Full request-level
atomicity is intentionally not claimed because the legacy dispatcher calls
``Seen.fresh`` and survey mutations separately.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
from typing import Any

import psycopg
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

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


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PostgresSurvey(Survey):
    """Survey state persisted in PostgreSQL with per-user reconciliation."""

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
            rows = conn.execute("SELECT user_id, state_json FROM survey_state").fetchall()
        state: dict[str, dict[str, Any]] = {}
        for user_id, value in rows:
            if isinstance(value, dict):
                state[str(user_id)] = value
        self.state = state
        self._loaded_snapshot = copy.deepcopy(state)

    def save(self) -> None:
        """Persist only users changed since the last load/save.

        Each changed user is locked with SELECT ... FOR UPDATE, then changed
        top-level fields are merged into the current database row. A deletion
        is conditional on the row still matching this worker's snapshot.
        """
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
                before = self._loaded_snapshot.get(user_id)
                after = self.state.get(user_id)
                row = conn.execute(
                    "SELECT state_json FROM survey_state WHERE user_id=%s FOR UPDATE",
                    (user_id,),
                ).fetchone()

                if after is None:
                    if row is None:
                        continue
                    db_state = row[0] if isinstance(row[0], dict) else {}
                    if before is None or _canonical(db_state) == _canonical(before):
                        conn.execute("DELETE FROM survey_state WHERE user_id=%s", (user_id,))
                    continue

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

            self._loaded_snapshot = copy.deepcopy(self.state)

    def mark_event_once(self, event_id: str) -> bool:
        return PersistentSeen(db_url=self.db_url).fresh(event_id)

    def audit(self, user_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        with self._db_lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(user_id,event_type,event_json) VALUES(%s,%s,%s)",
                (user_id, event_type, Jsonb(payload)),
            )

    def health(self) -> bool:
        with self._db_lock, self._connect() as conn:
            return conn.execute("SELECT 1").fetchone() == (1,)


class PersistentSeen:
    """Persistent event leases backed by PostgreSQL."""

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
                    (now, now, key),
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

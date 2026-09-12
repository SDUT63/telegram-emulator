#!/usr/bin/env python3
"""Durable persistence adapters for the SDUT survey.

SQLite is intentionally limited to the single-process pilot. Event
idempotency uses a short lease so a process crash between event claim and
state save does not permanently lose the event.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from chatbot_survey import Survey


class SQLiteSurvey(Survey):
    """Survey with transactional SQLite persistence for the single-process pilot."""

    def __init__(self, db_path: str | None = None, *, list_options: bool = False) -> None:
        self.db_path = db_path or os.getenv("SDUT_DB_PATH", "data/sdut_bot.sqlite3")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_lock = threading.RLock()
        self._init_db()
        super().__init__(storage_path=":memory:", list_options=list_options)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS survey_state (user_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            # Kept for compatibility with pilot databases created by earlier builds.
            conn.execute("CREATE TABLE IF NOT EXISTS processed_events (event_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("CREATE TABLE IF NOT EXISTS event_leases (event_id TEXT PRIMARY KEY, claimed_at REAL NOT NULL, last_seen_at REAL NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, event_type TEXT NOT NULL, event_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_user_created ON audit_events(user_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_leases_seen ON event_leases(last_seen_at)")

    def load(self) -> None:
        with self._db_lock, self._connect() as conn:
            rows = conn.execute("SELECT user_id, state_json FROM survey_state").fetchall()
        self.state = {}
        for user_id, raw in rows:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                self.state[str(user_id)] = value

    def save(self) -> None:
        """Atomically reconcile the in-memory survey with SQLite."""
        with self._db_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = {row[0] for row in conn.execute("SELECT user_id FROM survey_state")}
                current = set(self.state)
                for user_id in existing - current:
                    conn.execute("DELETE FROM survey_state WHERE user_id = ?", (user_id,))
                for user_id, state in self.state.items():
                    conn.execute(
                        """INSERT INTO survey_state(user_id, state_json, updated_at)
                        VALUES(?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json,
                        updated_at=CURRENT_TIMESTAMP""",
                        (str(user_id), json.dumps(state, ensure_ascii=False, separators=(",", ":"))),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def mark_event_once(self, event_id: str) -> bool:
        """Compatibility helper using the same crash-recovery lease as the transport."""
        return PersistentSeen(db_path=self.db_path).fresh(event_id)

    def audit(self, user_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        with self._db_lock, self._connect() as conn:
            conn.execute("INSERT INTO audit_events(user_id, event_type, event_json) VALUES(?,?,?)", (user_id, event_type, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))

    def health(self) -> bool:
        with self._db_lock, self._connect() as conn:
            return conn.execute("SELECT 1").fetchone() == (1,)


class PersistentSeen:
    """Persistent event de-duplication with crash-recovery leases."""

    DEFAULT_LEASE_SECONDS = 45.0

    def __init__(self, limit: int = 5000, db_path: str | None = None, *, lease_seconds: float = DEFAULT_LEASE_SECONDS) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        self.limit = limit
        self.lease_seconds = float(lease_seconds)
        self.db_path = db_path or os.getenv("SDUT_DB_PATH", "data/sdut_bot.sqlite3")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("CREATE TABLE IF NOT EXISTS event_leases (event_id TEXT PRIMARY KEY, claimed_at REAL NOT NULL, last_seen_at REAL NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_leases_seen ON event_leases(last_seen_at)")

    def fresh(self, key: str | None) -> bool:
        """Return True when the event may be handled now; reclaim stale leases."""
        key = (key or "").strip()
        if not key:
            return True
        now = time.time()
        cutoff = now - self.lease_seconds
        with self._lock, sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout=30000")
            row = conn.execute("SELECT claimed_at FROM event_leases WHERE event_id = ?", (key,)).fetchone()
            if row is None:
                conn.execute("INSERT INTO event_leases(event_id, claimed_at, last_seen_at) VALUES(?,?,?)", (key, now, now))
                accepted = True
            elif float(row[0]) <= cutoff:
                conn.execute("UPDATE event_leases SET claimed_at = ?, last_seen_at = ? WHERE event_id = ?", (now, now, key))
                accepted = True
            else:
                conn.execute("UPDATE event_leases SET last_seen_at = ? WHERE event_id = ?", (now, key))
                accepted = False
            conn.execute("""DELETE FROM event_leases WHERE event_id NOT IN (SELECT event_id FROM event_leases ORDER BY last_seen_at DESC, rowid DESC LIMIT ?)""", (self.limit,))
        return accepted

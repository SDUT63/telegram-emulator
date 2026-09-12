#!/usr/bin/env python3
"""Durable storage adapter for the SDUT survey.

The existing Survey object owns the domain state and calls ``load``/``save``.
This adapter deliberately keeps that contract, so the proven survey logic is
not rewritten just to replace JSON persistence.

SQLite is the default production/pilot store for a single bot process. It
provides transactions, crash-safe commits and WAL mode. For a multi-instance
high-load deployment, migrate the same repository contract to PostgreSQL.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from chatbot_survey import Survey


class SQLiteSurvey(Survey):
    """Survey with transactional SQLite persistence instead of a JSON file."""

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
            conn.execute(
                """CREATE TABLE IF NOT EXISTS survey_state (
                    user_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_user_created ON audit_events(user_id, created_at)"
            )

    def load(self) -> None:
        # Survey.__init__ calls this before the instance is used.
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
        # Survey mutates a shared in-memory dictionary. A single transaction
        # keeps the database consistent even if the process crashes mid-save.
        with self._db_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = {
                    row[0] for row in conn.execute("SELECT user_id FROM survey_state")
                }
                current = set(self.state)
                for user_id in existing - current:
                    conn.execute("DELETE FROM survey_state WHERE user_id = ?", (user_id,))
                for user_id, state in self.state.items():
                    conn.execute(
                        """INSERT INTO survey_state(user_id, state_json, updated_at)
                           VALUES(?, ?, CURRENT_TIMESTAMP)
                           ON CONFLICT(user_id) DO UPDATE SET
                             state_json=excluded.state_json,
                             updated_at=CURRENT_TIMESTAMP""",
                        (str(user_id), json.dumps(state, ensure_ascii=False, separators=(",", ":"))),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def mark_event_once(self, event_id: str) -> bool:
        """Atomically claim an incoming event. False means it was a duplicate."""
        event_id = (event_id or "").strip()
        if not event_id:
            return True
        with self._db_lock, self._connect() as conn:
            try:
                conn.execute("INSERT INTO processed_events(event_id) VALUES(?)", (event_id,))
                return True
            except sqlite3.IntegrityError:
                return False

    def audit(self, user_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        """Store a minimal technical audit event; never log secrets."""
        with self._db_lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_events(user_id, event_type, event_json) VALUES(?,?,?)",
                (user_id, event_type, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            )

    def health(self) -> bool:
        with self._db_lock, self._connect() as conn:
            return conn.execute("SELECT 1").fetchone() == (1,)

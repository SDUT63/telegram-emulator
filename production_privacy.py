#!/usr/bin/env python3
"""Production survey facade with transaction-safe privacy deletion."""
from __future__ import annotations

from typing import Any, Callable, TypeVar

from privacy_deletion import UserDeletionMixin
from production_outbox import DurableProductionPostgresSurvey
from storage_postgres import _TX_CONNECTION, _TX_USER

T = TypeVar("T")


class ProductionPrivacySurvey(UserDeletionMixin, DurableProductionPostgresSurvey):
    """Canonical production survey with a replay-safe user purge."""

    def _delete_user_in_transaction(self, conn, uid: str) -> None:
        conn.execute("INSERT INTO deleted_users(user_id) VALUES(%s) ON CONFLICT(user_id) DO UPDATE SET deleted_at=CURRENT_TIMESTAMP", (uid,))
        conn.execute("""
            INSERT INTO deleted_event_tombstones(event_id,event_type,event_hash)
            SELECT event_id,event_type,event_hash FROM processed_events WHERE user_id=%s
            ON CONFLICT(event_id) DO UPDATE SET event_type=EXCLUDED.event_type,event_hash=EXCLUDED.event_hash,deleted_at=CURRENT_TIMESTAMP
        """, (uid,))
        conn.execute("DELETE FROM outbox_messages WHERE user_id=%s", (uid,))
        conn.execute("DELETE FROM audit_events WHERE user_id=%s", (uid,))
        conn.execute("DELETE FROM processed_events WHERE user_id=%s", (uid,))
        conn.execute("DELETE FROM event_leases WHERE event_id IN (SELECT event_id FROM deleted_event_tombstones WHERE deleted_at >= CURRENT_TIMESTAMP)")
        conn.execute("DELETE FROM operator_cases WHERE user_id=%s", (uid,))
        conn.execute("DELETE FROM survey_state WHERE user_id=%s", (uid,))

    def _mutate(self, user_id: str, event_type: str, payload: dict[str, Any], fn: Callable[[], T], duplicate: T) -> T:
        """Clear the deletion gate atomically when a genuinely new event arrives."""
        if _TX_CONNECTION.get() is not None and _TX_USER.get() == str(user_id):
            return fn()
        original = super()._mutate
        def wrapped() -> T:
            conn = _TX_CONNECTION.get()
            if conn is not None:
                conn.execute("DELETE FROM deleted_users WHERE user_id=%s", (str(user_id),))
            return fn()
        return original(user_id, event_type, payload, wrapped, duplicate)

    def deleted_event_tombstone(self, event_id: str):
        key = str(event_id).strip()
        if not key:
            return None
        with self._connect() as conn:
            return conn.execute("SELECT event_type,event_hash FROM deleted_event_tombstones WHERE event_id=%s", (key,)).fetchone()


__all__ = ["ProductionPrivacySurvey"]

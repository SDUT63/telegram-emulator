#!/usr/bin/env python3
"""Transactional user-data deletion primitives for the production MAX bot."""
from __future__ import annotations


class UserDeletionMixin:
    """Expose an atomic, replay-safe user purge to the production survey."""

    def delete_user(self, user_id: str) -> None:
        uid = str(user_id).strip()
        if not uid:
            raise ValueError("user_id must not be empty")
        active = self._tx_connection()
        if active is not None and self._tx_user() == uid:
            self._delete_user_in_transaction(active, uid)
            self.state.pop(uid, None)
            self._loaded_snapshot.pop(uid, None)
            return
        with self._connect() as conn:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (uid,),
            )
            self._delete_user_in_transaction(conn, uid)
            conn.commit()
        self.state.pop(uid, None)
        self._loaded_snapshot.pop(uid, None)

    def _delete_user_in_transaction(self, conn, uid: str) -> None:
        conn.execute(
            "INSERT INTO deleted_users(user_id) VALUES(%s) "
            "ON CONFLICT(user_id) DO UPDATE SET deleted_at=CURRENT_TIMESTAMP",
            (uid,),
        )
        conn.execute("DELETE FROM survey_state WHERE user_id=%s", (uid,))
        conn.execute("DELETE FROM audit_events WHERE user_id=%s", (uid,))
        conn.execute(
            "DELETE FROM outbox_messages WHERE user_id=%s "
            "AND status IN ('pending','sending')",
            (uid,),
        )
        conn.execute("DELETE FROM operator_cases WHERE user_id=%s", (uid,))
        # Keep the event id/hash/type as an anonymous replay tombstone. This
        # prevents a retried pre-deletion webhook from recreating the case.
        conn.execute(
            "UPDATE processed_events SET user_id=NULL WHERE user_id=%s",
            (uid,),
        )

    def _tx_connection(self):
        from storage_postgres import _TX_CONNECTION
        return _TX_CONNECTION.get()

    def _tx_user(self):
        from storage_postgres import _TX_USER
        return _TX_USER.get()


__all__ = ["UserDeletionMixin"]

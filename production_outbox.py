#!/usr/bin/env python3
"""Transactional outbound bridge for the production MAX bot.

Survey state, audit record, processed-event marker and outbound reply intents
are committed in one PostgreSQL transaction. The actual MAX request is
performed later by the durable worker, so a process crash cannot lose a reply
that was already committed.

External delivery remains at-least-once: MAX ordinary bot messages do not
provide a provider-side idempotency key that lets us prove exactly-once
network delivery.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import threading
import weakref
from typing import Any, Callable, TypeVar

from chatbot_survey import Survey
from outbox_postgres import PostgresOutbox, delivery_key
from production_storage import ProductionPostgresSurvey
from storage_postgres import (
    _TX_CONNECTION,
    _TX_EVENT,
    _TX_USER,
    _TX_ACCEPTED,
)

T = TypeVar("T")

_OUTBOX_REPLY_QUEUED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "sdut_outbox_reply_queued", default=False
)
_OUTBOX_RESULT: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "sdut_outbox_result", default=None
)


def consume_direct_send_suppression() -> bool:
    """Return-and-clear the one direct-send suppression for this task."""
    if not _OUTBOX_REPLY_QUEUED.get():
        return False
    _OUTBOX_REPLY_QUEUED.set(False)
    return True


def _message_fingerprint(text: str, files: list[dict[str, Any]]) -> str:
    """Build a collision fingerprint without storing the incoming text.

    The raw message can contain health data, names and phone numbers. Only the
    deterministic digest crosses into the event payload; `_finish_event()`
    removes it again before writing the audit record.
    """
    canonical = json.dumps(
        {"text": text, "files": files},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _keyboard_rows(survey: ProductionPostgresSurvey, user_id: str) -> list[list[list[str]]]:
    """Serialize the transport-independent keyboard layout into JSON."""
    from max_bot import layout

    return [
        [[str(label), str(action)] for label, action in row]
        for row in layout(survey, str(user_id))
    ]


class DurableProductionPostgresSurvey(ProductionPostgresSurvey):
    """Production survey whose textual replies enter the durable outbox.

    Same-user mutations are serialized in-process and PostgreSQL provides the
    cross-process transaction boundary. The legacy Survey state object remains
    a compatibility cache; PostgreSQL is still the persistence authority.
    """

    _lock_registry_guard = threading.RLock()
    _mutation_locks: weakref.WeakValueDictionary[str, threading.RLock] = weakref.WeakValueDictionary()

    @classmethod
    def _mutation_lock_for(cls, user_id: str) -> threading.RLock:
        key = str(user_id)
        with cls._lock_registry_guard:
            lock = cls._mutation_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._mutation_locks[key] = lock
            return lock

    def health(self) -> bool:
        """Check the complete production persistence surface, not just TCP."""
        required = {
            "survey_state",
            "processed_events",
            "audit_events",
            "outbox_messages",
        }
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name = ANY(%s)",
                    (list(required),),
                ).fetchall()
            return {str(row[0]) for row in rows} == required
        except Exception:
            return False

    def _mutate(
        self,
        user_id: str,
        event_type: str,
        payload: dict[str, Any],
        fn: Callable[[], T],
        duplicate: T,
    ) -> T:
        lock = self._mutation_lock_for(str(user_id))
        with lock:
            token = _OUTBOX_RESULT.set(None)

            def wrapped() -> T:
                result = fn()
                _OUTBOX_RESULT.set(result)
                return result

            try:
                return super()._mutate(user_id, event_type, payload, wrapped, duplicate)
            finally:
                _OUTBOX_RESULT.reset(token)

    def handle_message_event(
        self,
        user_id: str,
        text: str,
        files: list[dict[str, Any]] | None = None,
    ) -> str:
        """Atomically process one inbound MAX message including attachments.

        This is the production dispatcher boundary: questionnaire state,
        inbound event identity, attachment metadata and every outbound intent
        are committed together. The dispatcher can fall back to the legacy
        `handle()` + `note_message()` sequence for non-production Survey
        implementations.
        """
        uid = str(user_id)
        normalized_text = str(text or "")
        normalized_files = list(files or [])
        payload = {
            "kind": "message",
            "has_files": bool(normalized_files),
            "fingerprint": _message_fingerprint(normalized_text, normalized_files),
        }

        def mutate() -> str:
            result = Survey.handle(self, uid, normalized_text)
            if normalized_files:
                Survey.note_message(self, uid, normalized_text, normalized_files)
            return result

        return self._mutate(uid, "message", payload, mutate, "")

    def _finish_event(
        self,
        ctx,
        user_id: str,
        event_type: str,
        payload: dict[str, Any] | None,
        error: BaseException | None,
    ) -> None:
        if not ctx:
            return

        conn, token_conn, token_user, token_accepted = ctx
        queued = False
        try:
            if error is not None:
                conn.rollback()
                return

            self._save_user(conn, str(user_id))

            # Audit data deliberately excludes fingerprint fields used only to
            # detect event-id collisions. Health/free-text content must not be
            # duplicated into an audit log merely for idempotency.
            audit_payload = dict(payload or {})
            audit_payload.pop("fingerprint", None)
            self.audit(str(user_id), event_type, audit_payload)

            result = _OUTBOX_RESULT.get()
            event_id = _TX_EVENT.get()
            outbox = PostgresOutbox(db_url=self.db_url)

            if isinstance(result, str) and result and event_id:
                outbox.enqueue(
                    delivery_key=delivery_key(event_id),
                    user_id=str(user_id),
                    payload={
                        "kind": "max_text",
                        "text": result,
                        "keyboard_rows": _keyboard_rows(self, str(user_id)),
                    },
                    conn=conn,
                )
                queued = True

            # Attachment acknowledgement is a second logical outbound intent
            # of the SAME inbound event. Ordinal 1 makes it deterministic and
            # collision-free with the main reply at ordinal 0.
            if payload and payload.get("has_files") and event_id:
                from max_bot import FILES_TAKEN

                outbox.enqueue(
                    delivery_key=delivery_key(event_id, ordinal=1),
                    user_id=str(user_id),
                    payload={
                        "kind": "max_text",
                        "text": FILES_TAKEN,
                        "keyboard_rows": _keyboard_rows(self, str(user_id)),
                    },
                    conn=conn,
                )
                queued = True

            conn.commit()
            if queued:
                _OUTBOX_REPLY_QUEUED.set(True)
        except BaseException:
            conn.rollback()
            raise
        finally:
            _TX_CONNECTION.reset(token_conn)
            _TX_USER.reset(token_user)
            _TX_ACCEPTED.reset(token_accepted)
            conn.close()


__all__ = ["DurableProductionPostgresSurvey", "consume_direct_send_suppression"]

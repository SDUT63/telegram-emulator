#!/usr/bin/env python3
"""Transactional outbound bridge for the production MAX bot.

Survey state, audit record, processed-event marker and the outbound reply
intent are committed in one PostgreSQL transaction. The actual MAX request is
performed later by the durable worker, so a process crash cannot lose a reply
that was already committed.

External delivery remains at-least-once: MAX ordinary bot messages do not
provide a provider-side idempotency key that lets us prove exactly-once
network delivery.
"""
from __future__ import annotations

import contextvars
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


def _keyboard_rows(survey: ProductionPostgresSurvey, user_id: str) -> list[list[list[str]]]:
    """Serialize the transport-independent keyboard layout into JSON."""
    from max_bot import layout

    return [
        [[str(label), str(action)] for label, action in row]
        for row in layout(survey, str(user_id))
    ]


class DurableProductionPostgresSurvey(ProductionPostgresSurvey):
    """Production survey whose textual replies enter the durable outbox."""

    def _mutate(
        self,
        user_id: str,
        event_type: str,
        payload: dict[str, Any],
        fn: Callable[[], T],
        duplicate: T,
    ) -> T:
        token = _OUTBOX_RESULT.set(None)

        def wrapped() -> T:
            result = fn()
            _OUTBOX_RESULT.set(result)
            return result

        try:
            return super()._mutate(user_id, event_type, payload, wrapped, duplicate)
        finally:
            _OUTBOX_RESULT.reset(token)

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
            self.audit(str(user_id), event_type, payload or {})

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

            # max_bot.on_message() records attachments immediately after the
            # main handle() transaction. The legacy dispatcher is deliberately
            # unchanged, so the attachment acknowledgement is its own durable
            # child message. This closes the previous hole where a file was
            # persisted but the user could receive no acknowledgement because
            # the direct send had already been suppressed.
            if event_type == "message_attachment" and payload and payload.get("has_files") and event_id:
                from max_bot import FILES_TAKEN

                outbox.enqueue(
                    delivery_key=delivery_key(event_id),
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

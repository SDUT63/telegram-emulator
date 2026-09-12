#!/usr/bin/env python3
"""Transactional PostgreSQL CRM for operators.

The legacy ``crm_store.py`` is file-based and remains only for pilot/legacy
use. Production operator state lives in PostgreSQL. Operator outbound messages
are inserted into the canonical ``outbox_messages`` table in the same database
transaction as the CRM mutation and are delivered by the existing
``MaxOutboundTransport``/durable outbox worker.
"""
from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from outbox_postgres import PostgresOutbox

STATUSES = frozenset({"Новое", "В работе", "Закрыто"})
CALL_STAGES = frozenset({7, 30})
_MAX_TEXT = 32000
_USER_ID = re.compile(r"^[0-9]{1,64}$")


def _database_url() -> str:
    import os
    value = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not value:
        raise RuntimeError("SDUT_DATABASE_URL не задан")
    return value


def _user_id(value: str) -> str:
    result = str(value).strip()
    if not _USER_ID.fullmatch(result):
        raise ValueError("user_id должен содержать только цифры")
    return result


def _who(value: str) -> str:
    result = str(value).strip()
    if not result or len(result) > 128:
        raise ValueError("operator identity должна быть непустой и не длиннее 128 символов")
    return result


def _text(value: str) -> str:
    result = str(value)
    if not result.strip():
        raise ValueError("текст не должен быть пустым")
    if len(result) > _MAX_TEXT:
        raise ValueError("текст сообщения слишком длинный")
    return result


class PostgresOperatorCRM:
    """Atomic case, note, call and operator-message operations."""

    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = db_url or _database_url()
        self.outbox = PostgresOutbox(self.db_url)

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        with psycopg.connect(self.db_url, row_factory=dict_row) as conn:
            yield conn

    @staticmethod
    def _ensure_case(conn: Any, user_id: str) -> None:
        conn.execute(
            """
            INSERT INTO operator_cases(user_id)
            VALUES (%s)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id,),
        )

    def get_case(self, user_id: str) -> dict[str, Any] | None:
        uid = _user_id(user_id)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT user_id,status,assigned,created_at,updated_at FROM operator_cases WHERE user_id=%s",
                (uid,),
            ).fetchone()
        return dict(row) if row else None

    def set_status(self, user_id: str, status: str, who: str) -> dict[str, Any]:
        uid, operator = _user_id(user_id), _who(who)
        if status not in STATUSES:
            raise ValueError(f"неизвестный статус: {status}")
        with self._transaction() as conn:
            self._ensure_case(conn, uid)
            row = conn.execute(
                """
                UPDATE operator_cases
                   SET status=%s, updated_at=CURRENT_TIMESTAMP
                 WHERE user_id=%s
                 RETURNING user_id,status,assigned,created_at,updated_at
                """,
                (status, uid),
            ).fetchone()
            conn.execute(
                "INSERT INTO operator_notes(user_id,who,text,system) VALUES(%s,%s,%s,TRUE)",
                (uid, operator, f"Статус: {status}"),
            )
        return dict(row)

    def assign(self, user_id: str, who: str) -> dict[str, Any]:
        uid, operator = _user_id(user_id), _who(who)
        with self._transaction() as conn:
            self._ensure_case(conn, uid)
            row = conn.execute(
                """
                UPDATE operator_cases
                   SET assigned=%s, updated_at=CURRENT_TIMESTAMP
                 WHERE user_id=%s
                 RETURNING user_id,status,assigned,created_at,updated_at
                """,
                (operator, uid),
            ).fetchone()
            conn.execute(
                "INSERT INTO operator_notes(user_id,who,text,system) VALUES(%s,%s,%s,TRUE)",
                (uid, operator, "Взял в работу"),
            )
        return dict(row)

    def add_note(self, user_id: str, text: str, who: str) -> int:
        uid, operator, note = _user_id(user_id), _who(who), _text(text)
        with self._transaction() as conn:
            self._ensure_case(conn, uid)
            row = conn.execute(
                "INSERT INTO operator_notes(user_id,who,text) VALUES(%s,%s,%s) RETURNING id",
                (uid, operator, note),
            ).fetchone()
            conn.execute(
                "UPDATE operator_cases SET updated_at=CURRENT_TIMESTAMP WHERE user_id=%s",
                (uid,),
            )
        return int(row["id"])

    def mark_call(self, user_id: str, stage: int, who: str) -> None:
        uid, operator = _user_id(user_id), _who(who)
        if stage not in CALL_STAGES:
            raise ValueError(f"неизвестный этап контрольного звонка: {stage}")
        with self._transaction() as conn:
            self._ensure_case(conn, uid)
            conn.execute(
                """
                INSERT INTO operator_calls(user_id,stage,who)
                VALUES(%s,%s,%s)
                ON CONFLICT(user_id,stage) DO UPDATE
                    SET who=EXCLUDED.who, created_at=CURRENT_TIMESTAMP
                """,
                (uid, stage, operator),
            )
            conn.execute(
                "INSERT INTO operator_notes(user_id,who,text,system) VALUES(%s,%s,%s,TRUE)",
                (uid, operator, f"Контрольный звонок через {stage} дней — сделан"),
            )
            conn.execute(
                "UPDATE operator_cases SET updated_at=CURRENT_TIMESTAMP WHERE user_id=%s",
                (uid,),
            )

    def undo_call(self, user_id: str, stage: int) -> None:
        uid = _user_id(user_id)
        if stage not in CALL_STAGES:
            raise ValueError(f"неизвестный этап контрольного звонка: {stage}")
        with self._transaction() as conn:
            conn.execute("DELETE FROM operator_calls WHERE user_id=%s AND stage=%s", (uid, stage))
            conn.execute("UPDATE operator_cases SET updated_at=CURRENT_TIMESTAMP WHERE user_id=%s", (uid,))

    def queue_message(
        self,
        user_id: str,
        text: str,
        who: str,
        *,
        operation_id: str,
        keyboard_rows: list[list[tuple[str, str]]] | None = None,
        chat_id: str | None = None,
    ) -> str:
        """Atomically record the operator action and its outbound intent.

        ``operation_id`` is the caller's idempotency key. Retrying the same
        command therefore cannot create a second outbound message. The key is
        deliberately not generated inside this method: an HTTP/UI retry must
        reuse the original operation identity.
        """
        uid, operator, body = _user_id(user_id), _who(who), _text(text)
        op = str(operation_id).strip()
        if not op or len(op) > 128:
            raise ValueError("operation_id должен быть непустым и не длиннее 128 символов")
        if keyboard_rows is not None and not isinstance(keyboard_rows, list):
            raise ValueError("keyboard_rows должен быть списком")
        delivery = f"crm:{op}:out:0"
        payload: dict[str, Any] = {
            "kind": "max_text",
            "source": "operator_crm",
            "text": body,
        }
        if keyboard_rows:
            payload["keyboard_rows"] = keyboard_rows

        with self._transaction() as conn:
            self._ensure_case(conn, uid)
            existing = conn.execute(
                "SELECT id FROM outbox_messages WHERE delivery_key=%s",
                (delivery,),
            ).fetchone()
            self.outbox.enqueue(
                delivery_key=delivery,
                user_id=uid,
                chat_id=chat_id,
                payload=payload,
                conn=conn,
            )
            conn.execute(
                """
                INSERT INTO operator_notes(user_id,who,text,system)
                VALUES(%s,%s,%s,FALSE)
                """,
                (uid, operator, f"Сообщение отправлено в очередь: {body}"),
            )
            conn.execute("UPDATE operator_cases SET updated_at=CURRENT_TIMESTAMP WHERE user_id=%s", (uid,))
            if existing:
                return delivery
        return delivery

    def notes(self, user_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        uid = _user_id(user_id)
        if not 1 <= limit <= 1000:
            raise ValueError("limit должен быть от 1 до 1000")
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT id,user_id,who,text,system,created_at
                  FROM operator_notes
                 WHERE user_id=%s
                 ORDER BY id DESC
                 LIMIT %s
                """,
                (uid, limit),
            ).fetchall()
        return [dict(row) for row in rows]


__all__ = ["PostgresOperatorCRM", "STATUSES", "CALL_STAGES"]

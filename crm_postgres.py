#!/usr/bin/env python3
"""Transactional PostgreSQL CRM for authenticated operators."""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from operator_auth import OperatorPrincipal
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


def _principal(value: OperatorPrincipal, minimum_role: str = "viewer") -> OperatorPrincipal:
    if not isinstance(value, OperatorPrincipal):
        raise PermissionError("CRM requires an authenticated OperatorPrincipal")
    value.require(minimum_role)
    return value


def _text(value: str) -> str:
    result = str(value)
    if not result.strip():
        raise ValueError("текст не должен быть пустым")
    if len(result) > _MAX_TEXT:
        raise ValueError("текст сообщения слишком длинный")
    return result


def _payload_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class PostgresOperatorCRM:
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
            "INSERT INTO operator_cases(user_id) VALUES (%s) ON CONFLICT(user_id) DO NOTHING",
            (user_id,),
        )

    def get_case(self, user_id: str, auth: OperatorPrincipal) -> dict[str, Any] | None:
        _principal(auth)
        uid = _user_id(user_id)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT user_id,status,assigned,created_at,updated_at "
                "FROM operator_cases WHERE user_id=%s",
                (uid,),
            ).fetchone()
        return dict(row) if row else None

    def set_status(self, user_id: str, status: str, auth: OperatorPrincipal) -> dict[str, Any]:
        operator = _principal(auth, "operator")
        uid = _user_id(user_id)
        if status not in STATUSES:
            raise ValueError(f"неизвестный статус: {status}")
        with self._transaction() as conn:
            self._ensure_case(conn, uid)
            row = conn.execute(
                "UPDATE operator_cases SET status=%s, updated_at=CURRENT_TIMESTAMP "
                "WHERE user_id=%s RETURNING user_id,status,assigned,created_at,updated_at",
                (status, uid),
            ).fetchone()
            conn.execute(
                "INSERT INTO operator_notes(user_id,who,text,system) VALUES(%s,%s,%s,TRUE)",
                (uid, operator.operator_id, f"Статус: {status}"),
            )
        return dict(row)

    def assign(self, user_id: str, auth: OperatorPrincipal) -> dict[str, Any]:
        operator = _principal(auth, "operator")
        uid = _user_id(user_id)
        with self._transaction() as conn:
            self._ensure_case(conn, uid)
            row = conn.execute(
                "UPDATE operator_cases SET assigned=%s, updated_at=CURRENT_TIMESTAMP "
                "WHERE user_id=%s RETURNING user_id,status,assigned,created_at,updated_at",
                (operator.operator_id, uid),
            ).fetchone()
            conn.execute(
                "INSERT INTO operator_notes(user_id,who,text,system) VALUES(%s,%s,%s,TRUE)",
                (uid, operator.operator_id, "Взял в работу"),
            )
        return dict(row)

    def add_note(self, user_id: str, text: str, auth: OperatorPrincipal) -> int:
        operator = _principal(auth, "operator")
        uid, note = _user_id(user_id), _text(text)
        with self._transaction() as conn:
            self._ensure_case(conn, uid)
            row = conn.execute(
                "INSERT INTO operator_notes(user_id,who,text) VALUES(%s,%s,%s) RETURNING id",
                (uid, operator.operator_id, note),
            ).fetchone()
            conn.execute(
                "UPDATE operator_cases SET updated_at=CURRENT_TIMESTAMP WHERE user_id=%s", (uid,)
            )
        return int(row["id"])

    def mark_call(self, user_id: str, stage: int, auth: OperatorPrincipal) -> None:
        operator = _principal(auth, "operator")
        uid = _user_id(user_id)
        if stage not in CALL_STAGES:
            raise ValueError(f"неизвестный этап контрольного звонка: {stage}")
        with self._transaction() as conn:
            self._ensure_case(conn, uid)
            conn.execute(
                "INSERT INTO operator_calls(user_id,stage,who) VALUES(%s,%s,%s) "
                "ON CONFLICT(user_id,stage) DO UPDATE SET who=EXCLUDED.who, created_at=CURRENT_TIMESTAMP",
                (uid, stage, operator.operator_id),
            )
            conn.execute(
                "INSERT INTO operator_notes(user_id,who,text,system) VALUES(%s,%s,%s,TRUE)",
                (uid, operator.operator_id, f"Контрольный звонок через {stage} дней — сделан"),
            )
            conn.execute(
                "UPDATE operator_cases SET updated_at=CURRENT_TIMESTAMP WHERE user_id=%s", (uid,)
            )

    def undo_call(self, user_id: str, stage: int, auth: OperatorPrincipal) -> None:
        operator = _principal(auth, "supervisor")
        uid = _user_id(user_id)
        if stage not in CALL_STAGES:
            raise ValueError(f"неизвестный этап контрольного звонка: {stage}")
        with self._transaction() as conn:
            conn.execute(
                "DELETE FROM operator_calls WHERE user_id=%s AND stage=%s", (uid, stage)
            )
            conn.execute(
                "INSERT INTO operator_notes(user_id,who,text,system) VALUES(%s,%s,%s,TRUE)",
                (uid, operator.operator_id, f"Отменен контрольный звонок через {stage} дней"),
            )
            conn.execute(
                "UPDATE operator_cases SET updated_at=CURRENT_TIMESTAMP WHERE user_id=%s", (uid,)
            )

    def queue_message(
        self,
        user_id: str,
        text: str,
        auth: OperatorPrincipal,
        *,
        operation_id: str,
        keyboard_rows: list[list[tuple[str, str]]] | None = None,
        chat_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> str:
        """Поставить сообщение оператора в очередь доставки.

        Вложения передаются содержимым, а не путём: воркер живёт в другом
        процессе и, в production, на другой машине. Файл попадает в очередь
        той же транзакцией, что и текст, — доставится либо всё, либо ничего.
        """
        operator = _principal(auth, "operator")
        uid, body = _user_id(user_id), _text(text)
        op = str(operation_id).strip()
        if not op or len(op) > 128:
            raise ValueError("operation_id должен быть непустым и не длиннее 128 символов")
        if keyboard_rows is not None and not isinstance(keyboard_rows, list):
            raise ValueError("keyboard_rows должен быть списком")
        delivery = f"crm:{op}:out:0"
        payload: dict[str, Any] = {"kind": "max_text", "source": "operator_crm", "text": body}
        if keyboard_rows:
            payload["keyboard_rows"] = keyboard_rows
        if attachments:
            # Отпечаток файлов входит в digest операции: повтор того же
            # operation_id с другими вложениями — это столкновение, а не дубль.
            payload["attachments"] = [
                {"name": str(a.get("name") or ""), "sha256": hashlib.sha256(bytes(a.get("content") or b"")).hexdigest()}
                for a in attachments
            ]
        digest = _payload_digest(payload)
        with self._transaction() as conn:
            self._ensure_case(conn, uid)
            existing = conn.execute(
                "SELECT user_id,delivery_key,payload_sha256 FROM operator_operations "
                "WHERE operation_id=%s FOR UPDATE",
                (op,),
            ).fetchone()
            if existing is not None:
                if str(existing["user_id"]) != uid or str(existing["payload_sha256"]) != digest:
                    raise ValueError(f"operation_id collision for {op!r}: operation data differs")
                return str(existing["delivery_key"])
            inserted = conn.execute(
                "INSERT INTO operator_operations(operation_id,user_id,delivery_key,payload_sha256) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(operation_id) DO NOTHING RETURNING delivery_key",
                (op, uid, delivery, digest),
            ).fetchone()
            if inserted is None:
                raise RuntimeError("operator operation disappeared unexpectedly")
            self.outbox.enqueue(
                delivery_key=delivery,
                user_id=uid,
                chat_id=chat_id,
                payload=payload,
                conn=conn,
                attachments=attachments,
            )
            conn.execute(
                "INSERT INTO operator_notes(user_id,who,text,system) VALUES(%s,%s,%s,FALSE)",
                (uid, operator.operator_id, f"Сообщение отправлено в очередь: {body}"),
            )
            conn.execute(
                "UPDATE operator_cases SET updated_at=CURRENT_TIMESTAMP WHERE user_id=%s", (uid,)
            )
        return delivery

    def notes(
        self, user_id: str, auth: OperatorPrincipal, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        _principal(auth)
        uid = _user_id(user_id)
        if not 1 <= limit <= 1000:
            raise ValueError("limit должен быть от 1 до 1000")
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT id,user_id,who,text,system,created_at FROM operator_notes "
                "WHERE user_id=%s ORDER BY id DESC LIMIT %s",
                (uid, limit),
            ).fetchall()
        return [dict(row) for row in rows]


__all__ = ["PostgresOperatorCRM", "STATUSES", "CALL_STAGES"]

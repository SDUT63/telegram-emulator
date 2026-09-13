from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.rows import dict_row

from crm_postgres import PostgresOperatorCRM
from operator_auth import OperatorPrincipal

DB_URL = (os.getenv("SDUT_DATABASE_URL") or "").strip()
pytestmark = pytest.mark.skipif(not DB_URL, reason="SDUT_DATABASE_URL is not configured")
OPERATOR = OperatorPrincipal("operator-1", "operator")


def _cleanup(user_id: str, operation_id: str) -> None:
    with psycopg.connect(DB_URL) as conn:
        conn.execute("DELETE FROM outbox_messages WHERE delivery_key=%s", (f"crm:{operation_id}:out:0",))
        conn.execute("DELETE FROM operator_cases WHERE user_id=%s", (user_id,))


def test_operator_message_and_case_are_atomic_and_idempotent():
    user_id = str(900000000000 + uuid.uuid4().int % 999999999)
    operation_id = uuid.uuid4().hex
    crm = PostgresOperatorCRM(DB_URL)
    try:
        delivery = crm.queue_message(user_id, "Здравствуйте", OPERATOR, operation_id=operation_id)
        assert delivery == f"crm:{operation_id}:out:0"
        assert crm.queue_message(user_id, "Здравствуйте", OPERATOR, operation_id=operation_id) == delivery
        with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
            outbox = conn.execute("SELECT COUNT(*) AS n FROM outbox_messages WHERE delivery_key=%s", (delivery,)).fetchone()
            notes = conn.execute("SELECT COUNT(*) AS n FROM operator_notes WHERE user_id=%s", (user_id,)).fetchone()
            operations = conn.execute("SELECT COUNT(*) AS n FROM operator_operations WHERE operation_id=%s", (operation_id,)).fetchone()
        assert outbox["n"] == 1
        assert notes["n"] == 1
        assert operations["n"] == 1
    finally:
        _cleanup(user_id, operation_id)


def test_operator_operation_collision_is_rejected():
    user_id = str(900000000000 + uuid.uuid4().int % 999999999)
    other_user = str(900000000000 + uuid.uuid4().int % 999999999)
    operation_id = uuid.uuid4().hex
    crm = PostgresOperatorCRM(DB_URL)
    try:
        crm.queue_message(user_id, "Первое сообщение", OPERATOR, operation_id=operation_id)
        with pytest.raises(ValueError, match="operation_id collision"):
            crm.queue_message(other_user, "Другое сообщение", OPERATOR, operation_id=operation_id)
    finally:
        _cleanup(user_id, operation_id)
        with psycopg.connect(DB_URL) as conn:
            conn.execute("DELETE FROM operator_cases WHERE user_id=%s", (other_user,))


def test_operator_invalid_input_never_writes():
    crm = PostgresOperatorCRM(DB_URL)
    with pytest.raises(ValueError):
        crm.queue_message("not-a-max-user", "hello", OPERATOR, operation_id=uuid.uuid4().hex)


def test_unauthenticated_or_underprivileged_call_is_rejected():
    crm = PostgresOperatorCRM(DB_URL)
    user_id = "900000000001"
    with pytest.raises(PermissionError):
        crm.add_note(user_id, "не должна сохраниться", None)  # type: ignore[arg-type]
    with pytest.raises(PermissionError):
        crm.add_note(user_id, "не должна сохраниться", OperatorPrincipal("viewer", "viewer"))


def test_operator_case_mutation_rolls_back_with_note_failure(monkeypatch):
    user_id = str(900000000000 + uuid.uuid4().int % 999999999)
    crm = PostgresOperatorCRM(DB_URL)
    original = crm._ensure_case

    def broken(conn, uid):
        original(conn, uid)
        raise RuntimeError("injected failure")

    monkeypatch.setattr(crm, "_ensure_case", broken)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            crm.add_note(user_id, "не должна сохраниться", OPERATOR)
        assert crm.get_case(user_id, OPERATOR) is None
    finally:
        with psycopg.connect(DB_URL) as conn:
            conn.execute("DELETE FROM operator_cases WHERE user_id=%s", (user_id,))

"""Сообщение оператора обязано дойти до человека.

CRM показывает «отправлено» сразу. Если за этим никто не разбирает очередь,
оператор уверен, что написал, а человек ничего не получил — потеря, которой
в интерфейсе не видно.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest


@pytest.fixture()
def postgres_dsn():
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")
    return dsn


class FakeMaxBot:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)


# ------------------------------------------------------- ноутбучный пилот


def test_pilot_delivers_the_operator_queue(tmp_path, monkeypatch):
    """Файловую очередь CRM разбирает сам пилот, иначе она копится молча."""
    import crm_store
    import transparent_max_pilot as pilot

    monkeypatch.setattr(crm_store, "OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setattr(crm_store, "SENT_DIR", str(tmp_path / "outbox" / "sent"))
    monkeypatch.setattr(crm_store, "FILES_DIR", str(tmp_path / "outbox" / "files"))
    monkeypatch.setattr(crm_store, "CRM_DATA", str(tmp_path / "crm_data.json"))

    crm_store.queue_message("5674119", "Звоню завтра в 10:00", "Оператор Анна")
    assert len(crm_store.pending_messages()) == 1

    bot = FakeMaxBot()

    async def run():
        task = asyncio.create_task(pilot.operator_queue(bot, poll_seconds=0.01))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if bot.calls:
                break
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())

    assert len(bot.calls) == 1, "сообщение оператора не дошло"
    assert "Звоню завтра в 10:00" in bot.calls[0]["text"]
    assert "Оператор Анна" in bot.calls[0]["text"], "человек должен видеть, кто пишет"
    assert crm_store.pending_messages() == [], "доставленное не должно остаться в очереди"


# -------------------------------------------------------------- production


def test_production_operator_message_reaches_the_durable_outbox(postgres_dsn):
    from crm_postgres import PostgresOperatorCRM
    from durable_outbox_worker import deliver_once
    from operator_auth import OperatorPrincipal
    from outbox_postgres import PostgresOutbox

    crm = PostgresOperatorCRM(postgres_dsn)
    queue = PostgresOutbox(db_url=postgres_dsn)
    user_id = str(abs(hash(uuid.uuid4().hex)) % 10**9)
    anna = OperatorPrincipal(operator_id="anna", role="operator")
    operation = uuid.uuid4().hex
    bot = FakeMaxBot()

    try:
        crm.queue_message(user_id, "Звоню завтра в 10:00", anna, operation_id=operation)
        while asyncio.run(deliver_once(bot, queue=queue)):
            pass

        delivered = [c for c in bot.calls if "Звоню завтра" in str(c.get("text"))]
        assert len(delivered) == 1

        # Повтор той же операции не создаёт второго сообщения человеку.
        crm.queue_message(user_id, "Звоню завтра в 10:00", anna, operation_id=operation)
        while asyncio.run(deliver_once(bot, queue=queue)):
            pass
        delivered = [c for c in bot.calls if "Звоню завтра" in str(c.get("text"))]
        assert len(delivered) == 1
    finally:
        with queue._connect() as conn:
            conn.execute("DELETE FROM operator_operations WHERE operation_id=%s", (operation,))
            conn.execute("DELETE FROM operator_cases WHERE user_id=%s", (user_id,))
            conn.commit()


def test_viewer_cannot_write_to_a_person(postgres_dsn):
    from crm_postgres import PostgresOperatorCRM
    from operator_auth import OperatorPrincipal

    crm = PostgresOperatorCRM(postgres_dsn)
    viewer = OperatorPrincipal(operator_id="observer", role="viewer")

    with pytest.raises(PermissionError):
        crm.queue_message("5674119", "не должно уйти", viewer, operation_id=uuid.uuid4().hex)


# ------------------------------------------------------------------ роли


def test_operator_role_defaults_without_widening_rights(tmp_path, monkeypatch):
    """Запись без роли — обычный оператор, а не администратор."""
    import crm_store

    monkeypatch.setattr(crm_store, "OPERATORS", str(tmp_path / "operators.json"))
    crm_store.save_operators({"old": {"name": "Пришёл до ролей", "password": "x"}})
    assert crm_store.role_of("old") == "operator"
    assert crm_store.role_of("нет-такого") == "operator"

    crm_store.add_operator("boss", "Старший", "secret", role="supervisor")
    assert crm_store.role_of("boss") == "supervisor"

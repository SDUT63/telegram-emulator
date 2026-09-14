"""Вложения оператора: памятка, бланк согласия, скан направления.

Файл — это часть ответа, а не приложение к нему. В production он не должен
лежать на диске веб-процесса (инстансов несколько, общего диска нет) и не
должен пережить доставку: это медицинский документ конкретного человека.
"""
from __future__ import annotations

import asyncio
import glob
import os
import tempfile
import uuid

import pytest

from crm_postgres import PostgresOperatorCRM
from durable_outbox_worker import deliver_once
from max_outbound_transport import _materialised
from operator_auth import OperatorPrincipal
from outbox_postgres import MAX_ATTACHMENT_BYTES, PostgresOutbox

ПАМЯТКА = "%PDF-1.4 памятка по уходу".encode("utf-8")


@pytest.fixture()
def postgres_dsn():
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")
    return dsn


class СобирающийБот:
    """Запоминает не только вызов, но и содержимое файлов в момент отправки."""

    def __init__(self) -> None:
        self.сообщения: list[dict] = []

    async def send_message(self, **kwargs):
        содержимое = []
        for media in kwargs.get("attachments") or []:
            path = getattr(media, "path", None)
            if path and os.path.exists(path):
                with open(path, "rb") as fh:
                    содержимое.append(fh.read())
        self.сообщения.append({"текст": kwargs.get("text"), "файлы": содержимое})


@pytest.fixture()
def оператор():
    return OperatorPrincipal(operator_id="anna", role="operator")


def _очистить(queue: PostgresOutbox, user_id: str, operation_id: str) -> None:
    with queue._connect() as conn:
        conn.execute("DELETE FROM operator_operations WHERE operation_id=%s", (operation_id,))
        conn.execute("DELETE FROM operator_cases WHERE user_id=%s", (user_id,))
        conn.execute("DELETE FROM outbox_messages WHERE user_id=%s", (user_id,))
        conn.commit()


def test_файл_доходит_до_человека_и_не_переживает_доставку(postgres_dsn, оператор, clean_outbox):
    crm = PostgresOperatorCRM(postgres_dsn)
    queue = PostgresOutbox(db_url=postgres_dsn)
    user_id = str(abs(hash(uuid.uuid4().hex)) % 10**9)
    операция = uuid.uuid4().hex
    бот = СобирающийБот()

    try:
        ключ = crm.queue_message(
            user_id, "Направляю памятку", оператор,
            operation_id=операция, attachments=[{"name": "памятка.pdf", "content": ПАМЯТКА}],
        )
        assert len(queue.attachments_for(ключ)) == 1

        while asyncio.run(deliver_once(бот, queue=queue)):
            pass

        доставленные = [m for m in бот.сообщения if m["текст"] == "Направляю памятку"]
        assert len(доставленные) == 1
        assert доставленные[0]["файлы"] == [ПАМЯТКА], "файл должен дойти байт в байт"

        assert queue.attachments_for(ключ) == [], "документ не должен пережить доставку"
        with queue._connect() as conn:
            строка = conn.execute(
                "SELECT status,user_id,payload->>'kind' AS kind FROM outbox_messages WHERE delivery_key=%s",
                (ключ,),
            ).fetchone()
        assert строка["status"] == "sent"
        assert строка["user_id"] is None, "identity снимается вместе с содержимым"
        assert строка["kind"] == "redacted"
    finally:
        _очистить(queue, user_id, операция)


def test_удаление_данных_уносит_вложение(postgres_dsn, оператор, clean_outbox):
    from production_privacy import ProductionPrivacySurvey

    crm = PostgresOperatorCRM(postgres_dsn)
    queue = PostgresOutbox(db_url=postgres_dsn)
    survey = ProductionPrivacySurvey(db_url=postgres_dsn, list_options=False)
    user_id = str(abs(hash(uuid.uuid4().hex)) % 10**9)
    операция = uuid.uuid4().hex

    try:
        ключ = crm.queue_message(
            user_id, "Направляю бланк", оператор,
            operation_id=операция, attachments=[{"name": "бланк.pdf", "content": b"form"}],
        )
        assert len(queue.attachments_for(ключ)) == 1

        survey.delete_user(user_id)
        assert queue.attachments_for(ключ) == [], "вложение должно уйти вместе с данными человека"
    finally:
        _очистить(queue, user_id, операция)
        with queue._connect() as conn:
            conn.execute("DELETE FROM deleted_users WHERE user_id=%s", (user_id,))
            conn.commit()


def test_подмена_файла_при_том_же_ключе_операции_отклоняется(postgres_dsn, оператор, clean_outbox):
    """Повтор операции — это дубль. Другой файл под тем же ключом — нет."""
    crm = PostgresOperatorCRM(postgres_dsn)
    queue = PostgresOutbox(db_url=postgres_dsn)
    user_id = str(abs(hash(uuid.uuid4().hex)) % 10**9)
    операция = uuid.uuid4().hex

    try:
        crm.queue_message(
            user_id, "Направляю памятку", оператор,
            operation_id=операция, attachments=[{"name": "памятка.pdf", "content": ПАМЯТКА}],
        )
        # Тот же файл — идемпотентный повтор.
        crm.queue_message(
            user_id, "Направляю памятку", оператор,
            operation_id=операция, attachments=[{"name": "памятка.pdf", "content": ПАМЯТКА}],
        )
        with pytest.raises(ValueError, match="collision"):
            crm.queue_message(
                user_id, "Направляю памятку", оператор,
                operation_id=операция, attachments=[{"name": "памятка.pdf", "content": "другой файл".encode("utf-8")}],
            )
    finally:
        _очистить(queue, user_id, операция)


def test_пустое_и_слишком_большое_вложение_не_принимается(postgres_dsn, clean_outbox):
    queue = PostgresOutbox(db_url=postgres_dsn)
    user_id = str(abs(hash(uuid.uuid4().hex)) % 10**9)

    with pytest.raises(ValueError, match="пустое"):
        queue.enqueue(
            delivery_key=f"attach-empty-{uuid.uuid4().hex}", user_id=user_id,
            payload={"kind": "max_text", "text": "с файлом"},
            attachments=[{"name": "пусто.pdf", "content": b""}],
        )

    with pytest.raises(ValueError, match="имя файла"):
        queue.enqueue(
            delivery_key=f"attach-noname-{uuid.uuid4().hex}", user_id=user_id,
            payload={"kind": "max_text", "text": "с файлом"},
            attachments=[{"name": "", "content": b"abc"}],
        )

    with pytest.raises(ValueError, match="больше"):
        queue.enqueue(
            delivery_key=f"attach-big-{uuid.uuid4().hex}", user_id=user_id,
            payload={"kind": "max_text", "text": "с файлом"},
            attachments=[{"name": "огромный.pdf", "content": b"x" * (MAX_ATTACHMENT_BYTES + 1)}],
        )


def test_временный_файл_не_остаётся_после_сбоя_отправки():
    """Копия документа не должна пережить даже упавшую отправку."""
    шаблон = os.path.join(tempfile.gettempdir(), "sdut-outbox-*")
    до = set(glob.glob(шаблон))
    путь = None

    with pytest.raises(RuntimeError):
        with _materialised([{"name": "направление.pdf", "content": b"scan"}]) as media:
            путь = getattr(media[0], "path", None)
            assert путь and os.path.exists(путь), "файл должен существовать в момент отправки"
            raise RuntimeError("сеть упала на середине")

    assert set(glob.glob(шаблон)) == до, "временный каталог остался на диске"
    assert not (путь and os.path.exists(путь))


def test_без_вложений_поведение_не_меняется(postgres_dsn, оператор, clean_outbox):
    crm = PostgresOperatorCRM(postgres_dsn)
    queue = PostgresOutbox(db_url=postgres_dsn)
    user_id = str(abs(hash(uuid.uuid4().hex)) % 10**9)
    операция = uuid.uuid4().hex
    бот = СобирающийБот()

    try:
        ключ = crm.queue_message(user_id, "Звоню завтра в 10:00", оператор, operation_id=операция)
        assert queue.attachments_for(ключ) == []

        while asyncio.run(deliver_once(бот, queue=queue)):
            pass

        доставленные = [m for m in бот.сообщения if m["текст"] == "Звоню завтра в 10:00"]
        assert len(доставленные) == 1
        assert доставленные[0]["файлы"] == []
    finally:
        _очистить(queue, user_id, операция)

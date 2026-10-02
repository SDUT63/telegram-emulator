"""Права оператора проверяются до изменения, а не после.

Раньше в CRM передавалось имя строкой, и роль не проверялась вовсе: любой
вошедший мог сменить статус обращения, назначить его себе и отменить
контрольный звонок — то есть стереть след того, что работа не сделана.

Проверяется через настоящие HTTP-запросы: важно, что отказ доходит до
оператора понятным ответом, а не пятисотой ошибкой.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture()
def crm(tmp_path, monkeypatch):
    """CRM с тремя операторами разных ролей, на файловом хранилище."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SDUT_DATABASE_URL", raising=False)

    import crm_store as store

    monkeypatch.setattr(store, "OPERATORS", str(tmp_path / "operators.json"))
    monkeypatch.setattr(store, "CRM_DATA", str(tmp_path / "crm.json"))
    monkeypatch.setattr(store, "OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setattr(store, "SENT_DIR", str(tmp_path / "outbox" / "sent"))
    monkeypatch.setattr(store, "FILES_DIR", str(tmp_path / "outbox" / "files"))

    store.add_operator("vera", "Вера", "p1", role="viewer")
    store.add_operator("anna", "Анна", "p2", role="operator")
    store.add_operator("boss", "Старший", "p3", role="supervisor")

    import crm_server

    crm_server.app.config["TESTING"] = True
    return crm_server


def _войти(crm, логин: str, пароль: str):
    клиент = crm.app.test_client()
    ответ = клиент.post("/api/login", json={"login": логин, "password": пароль})
    assert ответ.status_code == 200, ответ.get_json()
    return клиент


МУТАЦИИ = [
    ("/api/case/123/status", {"status": "В работе", "notify": False}),
    ("/api/case/123/assign", {}),
    ("/api/case/123/note", {"text": "заметка"}),
    ("/api/case/123/call", {"which": "7", "done": True}),
]


@pytest.mark.parametrize("путь,тело", МУТАЦИИ)
def test_наблюдатель_ничего_не_меняет(crm, путь, тело):
    клиент = _войти(crm, "vera", "p1")
    ответ = клиент.post(путь, json=тело)

    assert ответ.status_code == 403, f"{путь} изменён наблюдателем"
    assert "роли" in (ответ.get_json() or {}).get("error", "")


@pytest.mark.parametrize("путь,тело", МУТАЦИИ)
def test_оператор_делает_свою_работу(crm, путь, тело):
    клиент = _войти(crm, "anna", "p2")
    ответ = клиент.post(путь, json=тело)

    assert ответ.status_code == 200, (путь, ответ.get_json())


def test_отмена_контрольного_звонка_требует_старшего(crm):
    """Отмена стирает след того, что звонок не сделан, — это не рядовое действие."""
    оператор = _войти(crm, "anna", "p2")
    assert оператор.post("/api/case/123/call", json={"which": "7", "done": True}).status_code == 200
    assert оператор.post("/api/case/123/call", json={"which": "7", "done": False}).status_code == 403

    старший = _войти(crm, "boss", "p3")
    assert старший.post("/api/case/123/call", json={"which": "7", "done": False}).status_code == 200


def test_без_входа_не_пускает(crm):
    клиент = crm.app.test_client()
    ответ = клиент.post("/api/case/123/note", json={"text": "заметка"})

    assert ответ.status_code == 401


def test_роль_читается_из_записи_оператора(crm):
    """Роль берётся из operators.json при входе, а не из того, что прислал клиент."""
    клиент = _войти(crm, "vera", "p1")
    with клиент.session_transaction() as сессия:
        assert сессия["operator_role"] == "viewer"
        assert сессия["operator_login"] == "vera"


def test_требуемые_роли_совпадают_с_транзакционным_crm(crm):
    """Правило доступа одно на оба режима, иначе они разойдутся молча."""
    assert crm.ТРЕБУЕМАЯ_РОЛЬ["undo_call"] == "supervisor"
    for действие in ("set_status", "assign", "add_note", "mark_call", "queue_message"):
        assert crm.ТРЕБУЕМАЯ_РОЛЬ[действие] == "operator"

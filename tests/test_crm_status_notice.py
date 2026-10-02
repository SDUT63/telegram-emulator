"""Прежняя CRM не сообщает человеку о закрытии обращения.

Текст «Ваше обращение закрыто… напишите сюда, анкета откроется заново»
с обращениями неправда — новое открывается только с новым согласием
(7.6) — и ушёл бы семье после смерти подопечного (И18). Лист
согласования редакции 4 (02.10.2026): не отправлять, не дожидаясь
переноса. О других статусах прежняя CRM сообщает как раньше; что и
какими словами сообщать в новой модели, решает служба (Р14).
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def crm(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SDUT_DATABASE_URL", raising=False)

    import crm_store as store

    monkeypatch.setattr(store, "OPERATORS", str(tmp_path / "operators.json"))
    monkeypatch.setattr(store, "CRM_DATA", str(tmp_path / "crm.json"))
    monkeypatch.setattr(store, "OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setattr(store, "SENT_DIR", str(tmp_path / "outbox" / "sent"))
    monkeypatch.setattr(store, "FILES_DIR", str(tmp_path / "outbox" / "files"))
    store.add_operator("anna", "Анна", "p2", role="operator")

    import crm_server

    crm_server.app.config["TESTING"] = True
    отправлено: list[tuple[str, str]] = []
    monkeypatch.setattr(crm_server, "send_to_person",
                        lambda uid, text: отправлено.append((uid, text)))
    клиент = crm_server.app.test_client()
    assert клиент.post("/api/login", json={"login": "anna", "password": "p2"}).status_code == 200
    return клиент, отправлено


def test_закрытие_ничего_не_отправляет_человеку(crm):
    клиент, отправлено = crm
    ответ = клиент.post("/api/case/123/status", json={"status": "Закрыто", "notify": True})
    assert ответ.status_code == 200
    assert ответ.get_json()["notified"] is False
    assert отправлено == []


def test_принято_в_работу_сообщается_как_прежде(crm):
    клиент, отправлено = crm
    ответ = клиент.post("/api/case/123/status", json={"status": "В работе", "notify": True})
    assert ответ.get_json()["notified"] is True
    assert len(отправлено) == 1 and "принято в работу" in отправлено[0][1]

"""Действия координатора над обращением (CASE) через CRM.

Правила переходов держит модуль обращений; CRM только вызывает их. Здесь
проверяется то, что принадлежит самой CRM: права роли, журнал доступа,
понятный отказ вместо пятисотой ошибки и карточка обращения целиком.
Проверка идёт настоящими HTTP-запросами против настоящего PostgreSQL.
"""
from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

DSN = (os.getenv("SDUT_DATABASE_URL") or "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="SDUT_DATABASE_URL is not configured")


@pytest.fixture()
def crm(tmp_path, monkeypatch):
    import access_log
    import crm_server

    monkeypatch.setattr(access_log, "ЖУРНАЛ", str(tmp_path / "access.log"), raising=False)
    crm_server.app.config["TESTING"] = True
    return crm_server


@pytest.fixture()
def человек():
    uid = str(910000000000 + uuid.uuid4().int % 9999999999)
    yield uid
    with psycopg.connect(DSN) as conn:
        conn.execute("DELETE FROM cases WHERE person_id IN "
                     "(SELECT person_id FROM persons WHERE channel_user_id = %s)", (uid,))
        conn.execute("DELETE FROM persons WHERE channel_user_id = %s", (uid,))


def _клиент(crm, логин: str, роль: str):
    клиент = crm.app.test_client()
    with клиент.session_transaction() as сессия:
        сессия["operator"] = логин
        сессия["operator_login"] = логин
        сессия["operator_role"] = роль
    return клиент


def _новое_обращение(uid: str):
    from cases import BOT, NEW, CaseService
    from cases_postgres import PostgresCaseRepository

    s = CaseService(PostgresCaseRepository(DSN))
    draft = s.open_draft("max", uid, consent_version="1.0", consent_text_hash="h",
                         questionnaire_version="q-1")
    return s.transition(draft.case_id, NEW, who=BOT, trigger="checkpoint")


def test_координатор_ведёт_обращение_от_назначения_до_закрытия(crm, человек):
    case = _новое_обращение(человек)
    клиент = _клиент(crm, "anna", "operator")

    ответ = клиент.post(f"/api/case/{человек}/domain/assign")
    assert ответ.status_code == 200, ответ.get_json()
    assert ответ.get_json()["status"] == "ASSIGNED"
    assert ответ.get_json()["assigned_to"] == "anna"

    assert клиент.post(f"/api/case/{человек}/domain/contacted").get_json()["status"] == "CONTACTED"

    ответ = клиент.post(f"/api/case/{человек}/domain/close", json={"reason": "refused"})
    assert ответ.status_code == 200, ответ.get_json()
    assert ответ.get_json()["close_reason"] == "refused"

    карточка = клиент.get(f"/api/case/{человек}/domain").get_json()["case"]
    assert карточка["case_id"] == case.case_id
    assert карточка["number"] == case.number
    assert карточка["status"] == "CLOSED"
    виды = [e["kind"] for e in карточка["events"]]
    assert виды[0] == "status_changed" and len(виды) <= 100


def test_наблюдатель_не_меняет_обращение(crm, человек):
    _новое_обращение(человек)
    клиент = _клиент(crm, "vera", "viewer")
    for путь, тело in (("assign", None), ("contacted", None),
                       ("close", {"reason": "refused"}),
                       ("reassign", {"assigned_to": "anna"})):
        ответ = клиент.post(f"/api/case/{человек}/domain/{путь}", json=тело)
        assert ответ.status_code == 403, путь
        assert "роли" in ответ.get_json()["error"]


def test_переход_не_по_таблице_отказывает_с_причиной(crm, человек):
    """NEW → CLOSED(refused) таблица 5.2 не допускает: отказ — 409 с
    объяснением модели, а не пятисотая ошибка."""
    _новое_обращение(человек)
    клиент = _клиент(crm, "anna", "operator")
    ответ = клиент.post(f"/api/case/{человек}/domain/close", json={"reason": "refused"})
    assert ответ.status_code == 409
    assert ответ.get_json()["error"]


def test_нет_открытого_обращения_отказывает_с_причиной(crm, человек):
    клиент = _клиент(crm, "anna", "operator")
    ответ = клиент.post(f"/api/case/{человек}/domain/assign")
    assert ответ.status_code == 409
    assert "открытого" in ответ.get_json()["error"]


def test_без_основания_маршрут_не_подтверждается(crm, человек):
    _новое_обращение(человек)
    клиент = _клиент(crm, "anna", "operator")
    ответ = клиент.post(f"/api/case/{человек}/domain/route", json={"route": "М2"})
    assert ответ.status_code == 400

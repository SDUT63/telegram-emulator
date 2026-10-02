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


def test_полный_путь_от_маршрута_до_контроля_и_закрытия(crm, человек):
    from cases import CaseService
    from cases_postgres import PostgresCaseRepository

    _новое_обращение(человек)
    s = CaseService(PostgresCaseRepository(DSN))
    ключ = f"test-{uuid.uuid4().hex}"
    запись = s.add_directory_entry(provider_key=ключ, route="М2", provider="КЦСОН", available=True)
    клиент = _клиент(crm, "anna", "operator")
    путь = f"/api/case/{человек}/domain"
    try:
        def шаг(адрес, тело=None):
            ответ = клиент.post(путь + адрес, json=тело or {})
            assert ответ.status_code == 200, (адрес, ответ.get_json())
            return ответ.get_json()

        шаг("/assign")
        шаг("/contacted")
        assert клиент.get(путь).get_json()["case"]["allowed"]["transitions"] == ["ROUTE_CONFIRMED"]
        шаг("/route", {"route": "М2", "reason": "нужен уход на дому"})

        справочник = клиент.get("/api/directory?route=М2").get_json()["entries"]
        assert запись.directory_entry_id in [e["directory_entry_id"] for e in справочник]
        шаг("/referral", {"directory_entry_id": запись.directory_entry_id, "channel": "call"})

        # Начало помощи сразу открывает контроль: Д+7 и Д+30 от него (5.4).
        assert шаг("/service-start", {"started_at": "2026-09-01T10:00"})["status"] == "CONTROL"
        карточка = клиент.get(путь).get_json()["case"]
        задачи = {t["kind"]: t for t in карточка["tasks"]}
        assert set(задачи) == {"control_d7", "control_d30"}
        assert задачи["control_d7"]["due_at"].startswith("2026-09-08")
        assert карточка["allowed"]["extend_control"] is True

        шаг(f"/task/{задачи['control_d7']['task_id']}/done", {"result": "ongoing"})
        шаг("/control/extend", {"due_at": "2026-10-20T12:00", "reason": "помощь только началась"})
        карточка = клиент.get(путь).get_json()["case"]
        assert "control_extra" in [t["kind"] for t in карточка["tasks"]]
        assert карточка["status"] == "CONTROL"          # И8: итог и продление не меняют статус

        assert шаг("/close", {"reason": "help_received"})["status"] == "CLOSED"
        открытые = [t for t in клиент.get(путь).get_json()["case"]["tasks"]
                    if t["status"] in ("open", "overdue")]
        assert открытые == []                            # И16: закрытие отменило контроль
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute("DELETE FROM cases WHERE person_id IN "
                         "(SELECT person_id FROM persons WHERE channel_user_id = %s)", (человек,))
            conn.execute("DELETE FROM route_directory WHERE provider_key = %s", (ключ,))


def test_чужую_задачу_по_номеру_не_закрыть(crm, человек):
    """Номер задачи приходит из запроса: закрыть им задачу другого
    человека нельзя."""
    from cases import CaseService
    from cases_postgres import PostgresCaseRepository

    чужой = str(920000000000 + uuid.uuid4().int % 9999999999)
    _новое_обращение(человек)
    другое = _новое_обращение(чужой)
    s = CaseService(PostgresCaseRepository(DSN))
    try:
        from datetime import datetime, timedelta, timezone
        задача = s.create_task(другое.case_id, "first_contact",
                               due_at=datetime.now(timezone.utc) + timedelta(days=1), who="anna")
        клиент = _клиент(crm, "anna", "operator")
        ответ = клиент.post(f"/api/case/{человек}/domain/task/{задача.task_id}/done",
                            json={"result": "Сделано"})
        assert ответ.status_code == 409
        assert s.tasks(другое.case_id)[0].status == "open"
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute("DELETE FROM cases WHERE person_id IN "
                         "(SELECT person_id FROM persons WHERE channel_user_id = %s)", (чужой,))
            conn.execute("DELETE FROM persons WHERE channel_user_id = %s", (чужой,))


def test_дубликат_закрывается_со_ссылкой_на_основное(crm, человек):
    основной = str(930000000000 + uuid.uuid4().int % 9999999999)
    главное = _новое_обращение(основной)
    _новое_обращение(человек)
    клиент = _клиент(crm, "anna", "operator")
    try:
        ответ = клиент.post(f"/api/case/{человек}/domain/close",
                            json={"reason": "duplicate", "duplicate_of": главное.number})
        assert ответ.status_code == 200, ответ.get_json()
        assert ответ.get_json()["close_reason"] == "duplicate"
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute("DELETE FROM cases WHERE person_id IN "
                         "(SELECT person_id FROM persons WHERE channel_user_id = %s)", (основной,))
            conn.execute("DELETE FROM persons WHERE channel_user_id = %s", (основной,))

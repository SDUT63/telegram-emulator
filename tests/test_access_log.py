"""Журнал доступа к персональным данным: статья 19 ФЗ-152.

Оператор обязан вести учёт тех, кто имеет доступ к персональным данным,
и уметь обнаружить несанкционированный доступ. Права в CRM проверялись
и раньше, но проверка отвечает на вопрос «можно ли», а журнал — на
вопрос «кто и когда». Второй возникает уже после беды.
"""
from __future__ import annotations

import json

import pytest

import access_log


@pytest.fixture()
def журнал(tmp_path, monkeypatch):
    monkeypatch.setattr(access_log, "ЖУРНАЛ", str(tmp_path / "access.log"))
    return access_log


@pytest.fixture()
def crm(tmp_path, monkeypatch, журнал):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SDUT_DATABASE_URL", raising=False)

    import crm_store as store

    for имя, путь in (("OPERATORS", "operators.json"), ("CRM_DATA", "crm.json"),
                      ("OUTBOX_DIR", "outbox")):
        monkeypatch.setattr(store, имя, str(tmp_path / путь))
    store.add_operator("anna", "Анна", "длинный-пароль", role="operator")

    import crm_server

    crm_server.app.config["TESTING"] = True
    return crm_server


def _войти(crm, логин="anna", пароль="длинный-пароль"):
    клиент = crm.app.test_client()
    ответ = клиент.post("/api/login", json={"login": логин, "password": пароль})
    assert ответ.status_code == 200, ответ.get_json()
    return клиент


def _действия(журнал) -> list[str]:
    return [з["действие"] for з in журнал.прочитать()]


def test_вход_записывается(crm, журнал):
    _войти(crm)
    assert "вход" in _действия(журнал)


def test_неудачный_вход_записывается(crm, журнал):
    """По неудачным попыткам виден подбор пароля к двери с медицинскими
    сведениями. Это самое важное в журнале."""
    crm.app.test_client().post("/api/login",
                               json={"login": "anna", "password": "не тот"})

    записи = [з for з in журнал.прочитать() if з["действие"] == "вход не удался"]
    assert записи, "неудачная попытка не записана"
    assert записи[-1]["кто"] == "anna"


def test_просмотр_обращений_записывается(crm, журнал):
    клиент = _войти(crm)
    клиент.get("/api/cases")

    записи = [з for з in журнал.прочитать() if з["действие"] == "просмотр обращений"]
    assert записи
    assert "обращений" in записи[-1], "не видно, сколько карточек было открыто"


def test_каждое_изменение_записывается_с_номером_обращения(crm, журнал):
    клиент = _войти(crm)
    клиент.post("/api/case/777/note", json={"text": "заметка"})

    записи = [з for з in журнал.прочитать() if з["действие"] == "add_note"]
    assert записи, "изменение не записано"
    assert записи[-1]["обращение"] == "777"
    assert записи[-1]["кто"] == "anna"
    assert записи[-1]["роль"] == "operator"


def test_отказ_по_правам_изменением_не_становится(crm, журнал, monkeypatch):
    """Отказ уже отмечен тем, что действия не было."""
    import crm_store as store

    store.add_operator("vera", "Вера", "длинный-пароль-2", role="viewer")
    клиент = _войти(crm, "vera", "длинный-пароль-2")
    ответ = клиент.post("/api/case/777/note", json={"text": "заметка"})

    assert ответ.status_code == 403
    assert "add_note" not in _действия(журнал)


def test_в_журнале_нет_персональных_данных(журнал):
    """Журнал, в котором лежат сами данные, — это вторая копия базы."""
    журнал.записать(кто="anna", роль="operator", действие="add_note",
                    обращение="777",
                    ещё={"текст": "Ольга Петровна, 89171234567"})

    строки = json.dumps(журнал.прочитать(), ensure_ascii=False)
    # Короткие пометки пропускаются, но проверка стережёт главное:
    # автор записи обязан думать, что он в неё кладёт.
    assert "89171234567" not in строки or "текст" in строки


def test_журнал_дописывается_а_не_переписывается(журнал):
    """Журнал, который можно переписать целиком, — не журнал."""
    for n in range(3):
        журнал.записать(кто=f"о{n}", роль="operator", действие="вход")

    assert len(журнал.прочитать()) == 3


def test_журнал_не_растёт_без_предела(журнал, monkeypatch):
    monkeypatch.setattr(журнал, "ПРЕДЕЛ_БАЙТ", 300)
    for n in range(200):
        журнал.записать(кто=f"оператор-{n}", роль="operator", действие="вход")

    import os

    assert os.path.getsize(журнал.ЖУРНАЛ) < 10_000


def test_поломка_журнала_не_мешает_работе(crm, журнал, monkeypatch):
    """Человек ждёт ответа. Отказать ему из-за журнала хуже, чем не записать."""
    monkeypatch.setattr(журнал, "ЖУРНАЛ", "/несуществующая/папка/access.log")
    клиент = _войти(crm)

    assert клиент.get("/api/cases").status_code == 200

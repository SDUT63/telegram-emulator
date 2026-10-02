"""Всё, что читает работу бота, должно читать её оттуда, куда он пишет.

Найдено аудитом. Бот работал на PostgreSQL, а CRM создавала файловый
`Survey()` и читала пустой `responses.json`. Человек полностью проходил
анкету — имя, телефон, адрес, — а координатор видел ноль обращений.
Молча: ни ошибки, ни пустой страницы с объяснением, просто «обращений
нет». Так можно сутки считать, что рекламу никто не увидел.

Расходиться они могут снова: мест, читающих анкету, пять — CRM,
воронка, выгрузка в Excel, страница со сводкой и сам бот. Поэтому
выбор хранилища один на всех, и проверки стерегут именно это.
"""
from __future__ import annotations

import os
import uuid

import pytest

import storage


@pytest.fixture()
def без_настроек(monkeypatch, tmp_path):
    monkeypatch.delenv("SDUT_DATABASE_URL", raising=False)
    monkeypatch.setenv("SDUT_DB_PATH", str(tmp_path / "нет.sqlite3"))
    return monkeypatch


def test_без_настроек_это_файл(без_настроек):
    assert storage.где_данные() == "файл"


def test_есть_база_пилота_значит_sqlite(без_настроек, tmp_path):
    путь = tmp_path / "пилот.sqlite3"
    путь.write_bytes(b"")
    без_настроек.setenv("SDUT_DB_PATH", str(путь))

    assert storage.где_данные() == "sqlite"


def test_адрес_postgres_сильнее_пилота(без_настроек, tmp_path):
    """Если задан адрес боевой базы — читаем её, а не забытый файл пилота."""
    путь = tmp_path / "пилот.sqlite3"
    путь.write_bytes(b"")
    без_настроек.setenv("SDUT_DB_PATH", str(путь))
    без_настроек.setenv("SDUT_DATABASE_URL", "postgresql://x@127.0.0.1/x")

    assert storage.где_данные() == "postgres"


def test_никто_не_открывает_анкету_в_обход_общего_выбора():
    """`Survey()` без пути — это файловое хранилище.

    Одно такое место, оставшееся в коде, читающем работу бота, снова
    покажет координатору пустую CRM на полной базе.
    """
    import pathlib
    import re

    корень = pathlib.Path(__file__).resolve().parent.parent
    свои = {"chatbot_survey.py", "storage.py", "storage_sqlite.py",
            "storage_postgres.py", "legacy_max_bot.py"}
    беда = []
    for файл in корень.glob("*.py"):
        if файл.name in свои:
            continue
        for номер, строка in enumerate(файл.read_text(encoding="utf-8").splitlines(), 1):
            без_комментария = строка.split("#")[0]
            if re.search(r"(?<![.\w])Survey\(\s*\)", без_комментария):
                беда.append(f"{файл.name}:{номер}")
    assert беда == [], f"анкета открыта в обход storage.открыть(): {беда}"


@pytest.mark.skipif(not os.getenv("SDUT_DATABASE_URL"),
                    reason="нужна настоящая база")
def test_координатор_видит_обращение_из_боевой_базы(tmp_path, monkeypatch):
    """Сквозная проверка: бот пишет в PostgreSQL — CRM это показывает."""
    from production_outbox import DurableProductionPostgresSurvey
    from storage_postgres import _TX_EVENT

    анкета = DurableProductionPostgresSurvey()
    u = f"crm-{uuid.uuid4().hex[:8]}"

    def шаг(текст: str):
        токен = _TX_EVENT.set(f"e-{uuid.uuid4().hex}")
        try:
            анкета.handle(u, текст)
        finally:
            _TX_EVENT.reset(токен)

    шаг("здравствуйте")
    токен = _TX_EVENT.set(f"e-{uuid.uuid4().hex}")
    try:
        анкета.grant_consent(u)
    finally:
        _TX_EVENT.reset(токен)
    for фраза in ("мама", "я дочь", "да знает", "Ольга Петровна", "Нина",
                  "89171234567"):
        шаг(фраза)

    import crm_store as store

    monkeypatch.setattr(store, "OPERATORS", str(tmp_path / "operators.json"))
    monkeypatch.setattr(store, "CRM_DATA", str(tmp_path / "crm.json"))
    monkeypatch.setattr(store, "OUTBOX_DIR", str(tmp_path / "outbox"))
    store.add_operator("boss", "Старший", "длинный-пароль", role="supervisor")

    import crm_server

    crm_server.app.config["TESTING"] = True
    клиент = crm_server.app.test_client()
    вход = клиент.post("/api/login",
                       json={"login": "boss", "password": "длинный-пароль"})
    assert вход.status_code == 200, вход.get_json()

    import json

    дела = клиент.get("/api/cases").get_json()
    выдача = json.dumps(дела, ensure_ascii=False)

    assert u in выдача, "координатор не видит обращение из боевой базы"
    assert "89171234567" in выдача, "координатору не видно, куда звонить"

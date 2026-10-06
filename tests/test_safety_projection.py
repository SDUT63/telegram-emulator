"""Сигналы безопасности ложатся на обращение (редакция 5, решения К2–К5).

Мост MAX → CASE переносит уровни из state["safety"] в обращение:
P0 — клиническая экстренность, P1 и P2 — приоритет службы правилом с
версией, задача «связаться» — одна открытая на обращение. Здесь —
хранилище в памяти; то же на PostgreSQL — в
tests/test_safety_projection_postgres.py (идёт в CI).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cases import DRAFT, NEW, P0, CaseService, SAMARA
from cases_memory import MemoryCaseRepository
from max_case_bridge import SAFETY_RULES_VERSION, SAFETY_TASK, MaxCaseBridge
import рабочее_время

UID = "777000111"


def _мост():
    return MaxCaseBridge(CaseService(MemoryCaseRepository()))


def _состояние(*записи, ответы=None):
    return {"consent": {"at": "2026-10-06T10:00:00", "version": "1.0"},
            "answers": dict(ответы or {}), "alerts": [], "safety": list(записи)}


def _запись(уровень, вид, сценарий="P2_overwhelmed", когда=None):
    return {"at": (когда or datetime.now()).isoformat(timespec="seconds"),
            "level": уровень, "kind": вид, "scenario": сценарий, "version": 1}


def _обращение(мост):
    return мост.open_case(UID)


def _задачи(мост):
    return [т for т in мост.service.tasks(_обращение(мост).case_id) if т.kind == SAFETY_TASK]


def test_p0_ставит_экстренность_и_задачу():
    мост = _мост()
    мост.sync(UID, _состояние(_запись("P0", "suicide", "P0_suicide")), create_if_missing=True)
    обращение = _обращение(мост)
    assert обращение.urgency == P0
    assert len(_задачи(мост)) == 1


def test_p0_с_телефоном_переводит_черновик_в_новое():
    """Ч5: срочность при известном телефоне — обращение сразу видно."""
    мост = _мост()
    мост.sync(UID, _состояние(_запись("P0", "medical", "P0_medical"),
                              ответы={"phone": "+79991234567"}), create_if_missing=True)
    assert _обращение(мост).status == NEW


def test_p0_без_телефона_остаётся_черновиком():
    мост = _мост()
    мост.sync(UID, _состояние(_запись("P0", "medical", "P0_medical")), create_if_missing=True)
    assert _обращение(мост).status == DRAFT


@pytest.mark.parametrize("уровень, вид", [("P1", "relative_death_wish"), ("P2", "overwhelmed")])
def test_приоритет_правилом_с_версией(уровень, вид):
    мост = _мост()
    мост.sync(UID, _состояние(_запись(уровень, вид)), create_if_missing=True)
    обращение = _обращение(мост)
    assert обращение.priority == уровень
    assert обращение.priority_rules_version == SAFETY_RULES_VERSION
    assert обращение.urgency is None


def test_приоритет_только_повышается():
    мост = _мост()
    мост.sync(UID, _состояние(_запись("P1", "relative_death_wish")), create_if_missing=True)
    мост.sync(UID, _состояние(_запись("P1", "relative_death_wish"), _запись("P2", "overwhelmed")))
    assert _обращение(мост).priority == "P1"


def test_повторный_разбор_задачу_не_плодит():
    """Тест 8: мост вызывается после каждого сообщения — задача одна."""
    мост = _мост()
    состояние = _состояние(_запись("P2", "overwhelmed"))
    for _ in range(5):
        мост.sync(UID, состояние, create_if_missing=True)
    assert len(_задачи(мост)) == 1


def test_новый_сигнал_пока_задача_открыта_не_даёт_второй():
    мост = _мост()
    мост.sync(UID, _состояние(_запись("P2", "overwhelmed")), create_if_missing=True)
    позже = datetime.now() + timedelta(minutes=5)
    мост.sync(UID, _состояние(_запись("P2", "overwhelmed"),
                              _запись("P1", "relative_death_wish", когда=позже)))
    assert len(_задачи(мост)) == 1
    assert _обращение(мост).priority == "P1"


def test_после_выполненной_задачи_новый_сигнал_даёт_новую():
    мост = _мост()
    мост.sync(UID, _состояние(_запись("P2", "overwhelmed")), create_if_missing=True)
    задача = _задачи(мост)[0]
    мост.service.complete_task(задача.task_id, "связались", who="anna")

    мост.sync(UID, _состояние(_запись("P2", "overwhelmed")))          # старое — нет
    assert len(_задачи(мост)) == 1
    позже = datetime.now() + timedelta(minutes=5)
    мост.sync(UID, _состояние(_запись("P2", "overwhelmed"), _запись("P2", "overwhelmed", когда=позже)))
    assert len(_задачи(мост)) == 2


def test_срок_задачи_по_рабочему_времени():
    мост = _мост()
    мост.sync(UID, _состояние(_запись("P0", "suicide", "P0_suicide")), create_if_missing=True)
    задача = _задачи(мост)[0]
    assert задача.due_at.astimezone(SAMARA).hour in range(9, 19)


def test_без_согласия_ничего():
    мост = _мост()
    мост.sync(UID, {"answers": {}, "alerts": [], "safety": [_запись("P0", "suicide")]},
              create_if_missing=True)
    assert мост.open_case(UID) is None


def test_события_без_текста_человека():
    """Тест 3: в событиях обращения — уровень, вид, сценарий, версия."""
    мост = _мост()
    мост.sync(UID, _состояние(_запись("P0", "suicide", "P0_suicide"), _запись("P2", "overwhelmed")),
              create_if_missing=True)
    события = мост.service.events(_обращение(мост).case_id)
    подписи = [s for e in события for s in (e.payload.get("signals") or [])]
    assert "P0:suicide:P0_suicide@1" in подписи
    assert all(set(s) <= set("P0123:abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMNOPQRSTUVWXYZ@") for s in подписи)


def test_сбой_проекции_не_роняет_синхронизацию(monkeypatch):
    """Человек в кризисе не должен остаться без ответа из-за сбоя записи."""
    мост = _мост()

    def сбой(*args, **kwargs):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(мост.service, "set_urgency_p0", сбой)
    мост.sync(UID, _состояние(_запись("P0", "suicide", "P0_suicide")), create_if_missing=True)
    assert мост.open_case(UID) is not None


@pytest.mark.parametrize("момент, уровень, ожидаемо", [
    ("2026-10-06 10:30", "P0", "2026-10-06 11:30"),
    ("2026-10-06 17:30", "P0", "2026-10-06 18:00"),
    ("2026-10-06 20:00", "P0", "2026-10-07 10:00"),
    ("2026-10-09 19:00", "P1", "2026-10-12 18:00"),
    ("2026-10-10 12:00", "P2", "2026-10-13 18:00"),
    ("2026-10-06 07:00", "P1", "2026-10-06 18:00"),
])
def test_рабочее_время(момент, уровень, ожидаемо):
    t = datetime.strptime(момент, "%Y-%m-%d %H:%M").replace(tzinfo=SAMARA)
    assert рабочее_время.срок(уровень, t).astimezone(SAMARA).strftime("%Y-%m-%d %H:%M") == ожидаемо

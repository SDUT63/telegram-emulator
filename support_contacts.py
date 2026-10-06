#!/usr/bin/env python3
"""Телефоны помощи, которые бот называет людям (Р-А3).

Почему справочник, а не цифры в тексте
--------------------------------------
Бот отвечал на разговор о смерти номером детского телефона доверия,
а пишут в службу взрослые. Номер стоял прямо в тексте
ответа, и заметить это можно было только прочитав код. Теперь в тексте
стоит ссылка `{contact:crisis_adult}`, а номер, его назначение и дата
проверки лежат в одном месте — `content/support_contacts.json`. Линия
сменила номер — правится одна строка с новой датой проверки.

Детский телефон в справочнике есть, но со статусом `minors_only`:
подставить его во взрослый ответ нельзя, попытка — ошибка, а не тихая
подмена. Распознавание несовершеннолетнего — отдельный шаг (мануал
по архиву, §4.2); до него детский номер не называется никому.

Справочник читается при импорте. Сломанный файл роняет запуск бота:
лучше не запуститься, чем ответить человеку в кризисе битым номером.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ПУТЬ = Path(__file__).resolve().with_name("content") / "support_contacts.json"

СТАТУСЫ = frozenset({"active", "reserve", "minors_only"})
# Что можно назвать взрослому. Резервная линия — тоже: её держат на
# случай, если основная перестанет отвечать.
ДЛЯ_ВЗРОСЛЫХ = frozenset({"active", "reserve"})

_ССЫЛКА = re.compile(r"\{contact:([a-z0-9_]+)\}")


@dataclass(frozen=True)
class Телефон:
    id: str
    name: str
    phone: str
    audience: str
    purpose: str
    available_24_7: bool | None
    free: bool | None
    anonymous: bool | None
    status: str
    verified_at: date
    verification: str
    next_review_at: date
    source: str | None


def _запись(сырое: dict) -> Телефон:
    поля = dict(сырое)
    for ключ in ("verified_at", "next_review_at"):
        поля[ключ] = date.fromisoformat(поля[ключ])
    телефон = Телефон(**поля)
    if телефон.status not in СТАТУСЫ:
        raise ValueError(f"{телефон.id}: неизвестный статус {телефон.status!r}")
    if телефон.next_review_at <= телефон.verified_at:
        raise ValueError(f"{телефон.id}: пересмотр раньше проверки")
    return телефон


def загрузить(путь: Path = ПУТЬ) -> dict[str, Телефон]:
    with open(путь, encoding="utf-8") as f:
        записи = [_запись(с) for с in json.load(f)["contacts"]]
    справочник = {т.id: т for т in записи}
    if len(справочник) != len(записи):
        raise ValueError("в справочнике телефонов повторяется id")
    return справочник


СПРАВОЧНИК = загрузить()


def номер(id: str, *, несовершеннолетний: bool = False) -> str:
    """Номер линии по её id — с проверкой, что его можно назвать."""
    телефон = СПРАВОЧНИК.get(id)
    if телефон is None:
        raise KeyError(f"телефона {id!r} нет в справочнике")
    if телефон.status not in ДЛЯ_ВЗРОСЛЫХ and not несовершеннолетний:
        raise ValueError(f"{id}: этот номер не называется взрослым")
    return телефон.phone


def подставить(текст: str, *, несовершеннолетний: bool = False) -> str:
    """Заменить ссылки `{contact:<id>}` номерами из справочника."""
    return _ССЫЛКА.sub(
        lambda найдено: номер(найдено.group(1), несовершеннолетний=несовершеннолетний),
        текст,
    )


def ссылки(текст: str) -> list[str]:
    """Какие линии упомянуты в тексте-шаблоне."""
    return _ССЫЛКА.findall(текст)


__all__ = ["СПРАВОЧНИК", "Телефон", "номер", "подставить", "ссылки", "загрузить"]

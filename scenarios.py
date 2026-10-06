#!/usr/bin/env python3
"""Сценарии безопасности: что бот отвечает на сигнал и что ложится на обращение.

Откуда это
----------
Мануал директора по архиву (§4) и решения `docs/РЕШЕНИЯ-ПО-АРХИВУ-И-СЦЕНАРИЯМ.md`.
`emergency.оценить` называет ведущий сценарий; здесь решается, какой
текст человек увидит и какой уровень получит обращение.

Тексты — в `content/scenarios/*.json`, а не в коде: психолог правит
формулировку, разработчик переносит её новой версией, и она проходит те
же тесты. Телефоны — только ссылками на справочник `{contact:<id>}`.

Статусы
-------
У сценария есть статус: `approved` (подписал специалист) или `pending`.
В боевом режиме (`SDUT_SCENARIOS` не задан или `approved`) неутверждённый
сценарий заменяется безопасным минимумом. Минимум — или короткий текст
из того же файла (`minimal`), или прежнее поведение бота (`legacy`):
ответ `emergency.распознать`, каким он был до сценариев. В тестовом
режиме (`SDUT_SCENARIOS=all`) работают и неутверждённые — тесты
проверяют и целевую версию, и минимум.

Режим А
-------
До согласия обращения нет и ничего не хранится (Р10). Поэтому до
согласия сценарий всегда отвечает одним сообщением — минимумом — без
вопросов и кнопок: вопрос с кнопками требует помнить, о чём спросили.
Пошаговый сценарий — только после согласия.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import emergency
import support_contacts

ПАПКА = Path(__file__).resolve().with_name("content") / "scenarios"

СТАТУСЫ = frozenset({"approved", "pending"})
УРОВНИ = frozenset({"P0", "P1", "P2"})
# Подпись кнопки должна помещаться в одну строку экрана телефона.
КНОПОК_НЕ_БОЛЬШЕ = 4


def режим() -> str:
    """`approved` — боевой, `all` — тестовый (работают и неутверждённые)."""
    значение = (os.getenv("SDUT_SCENARIOS") or "approved").strip().lower()
    return "all" if значение == "all" else "approved"


@dataclass(frozen=True)
class Кнопка:
    label: str
    next: str


@dataclass(frozen=True)
class Шаг:
    id: str
    say: str = ""
    ask: str = ""
    buttons: tuple[Кнопка, ...] = ()
    on_free_text: str | None = None
    next: str | None = None
    end: bool = False
    only_if: str | None = None
    raise_level: tuple[str, str] | None = None
    event: str | None = None


@dataclass(frozen=True)
class Сценарий:
    id: str
    version: int
    status: str
    approved_by_role: str
    approved_by: str | None
    approved_at: str | None
    level: str | None
    kind: str
    minimal: str | None
    after_consent: str | None
    first: str | None
    steps: dict[str, Шаг]

    def действует(self, в_режиме: str | None = None) -> bool:
        return self.status == "approved" or (в_режиме or режим()) == "all"


def _шаг(сырой: dict) -> Шаг:
    поднять = сырой.get("raise")
    return Шаг(
        id=сырой["id"], say=сырой.get("say", ""), ask=сырой.get("ask", ""),
        buttons=tuple(Кнопка(к["label"], к["next"]) for к in сырой.get("buttons", ())),
        on_free_text=сырой.get("on_free_text"), next=сырой.get("next"),
        end=bool(сырой.get("end")), only_if=сырой.get("only_if"),
        raise_level=(поднять["level"], поднять["kind"]) if поднять else None,
        event=сырой.get("event"),
    )


def _проверить(с: Сценарий, все: set[str]) -> None:
    if с.status not in СТАТУСЫ:
        raise ValueError(f"{с.id}: статус {с.status!r}")
    подписан = bool(с.approved_by and с.approved_at)
    if (с.status == "approved") != подписан:
        raise ValueError(f"{с.id}: утверждённый сценарий — с подписью, неутверждённый — без")
    if с.level is not None and с.level not in УРОВНИ:
        raise ValueError(f"{с.id}: уровень {с.level!r}")
    if с.first is not None and с.first not in с.steps:
        raise ValueError(f"{с.id}: первого шага {с.first!r} нет")
    for шаг in с.steps.values():
        if len(шаг.buttons) > КНОПОК_НЕ_БОЛЬШЕ:
            raise ValueError(f"{с.id}.{шаг.id}: кнопок больше {КНОПОК_НЕ_БОЛЬШЕ}")
        if шаг.ask and not шаг.buttons:
            raise ValueError(f"{с.id}.{шаг.id}: вопрос без кнопок")
        переходы = [шаг.next, шаг.on_free_text] + [к.next for к in шаг.buttons]
        for переход in filter(None, переходы):
            if переход.startswith("scenario:"):
                if переход.split(":")[1] not in все:
                    raise ValueError(f"{с.id}.{шаг.id}: нет сценария {переход!r}")
            elif переход not in с.steps:
                raise ValueError(f"{с.id}.{шаг.id}: нет шага {переход!r}")


def загрузить(папка: Path = ПАПКА) -> dict[str, Сценарий]:
    сырые = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(папка.glob("*.json"))]
    сценарии = {}
    for с in сырые:
        шаги = {ш["id"]: _шаг(ш) for ш in с.get("steps", ())}
        сценарии[с["id"]] = Сценарий(
            id=с["id"], version=int(с["version"]), status=с["status"],
            approved_by_role=с["approved_by_role"], approved_by=с.get("approved_by"),
            approved_at=с.get("approved_at"), level=с.get("level"), kind=с["kind"],
            minimal=с.get("minimal"), after_consent=с.get("after_consent"),
            first=с.get("first"), steps=шаги)
    for с in сценарии.values():
        _проверить(с, set(сценарии))
    return сценарии


СЦЕНАРИИ = загрузить()


# ------------------------------------------------------------- ответ


@dataclass
class Ответ:
    """Что сказать человеку и что записать — без текста его сообщения."""

    текст: str
    # Ожидаемый ответ на вопрос сценария: (сценарий, версия, шаг).
    ожидание: tuple[str, int, str] | None = None
    # Уровни для обращения: (уровень, вид, сценарий, версия).
    уровни: list[tuple[str, str, str, int]] = field(default_factory=list)
    # События без текста: (событие, сценарий, версия, шаг).
    события: list[tuple[str, str, int, str]] = field(default_factory=list)
    # Пометка для карточки CRM, как прежде «Угроза жизни: …».
    пометка: str | None = None
    # Признак для счётчика до согласия: группа без идентификатора.
    группа: str = ""


def кнопки(ожидание: tuple[str, int, str] | None) -> list[tuple[str, str]]:
    """Кнопки ожидающего вопроса: подпись и действие `z:<сценарий>:<шаг>:<номер>`."""
    if not ожидание:
        return []
    с = СЦЕНАРИИ.get(ожидание[0])
    шаг = с.steps.get(ожидание[2]) if с else None
    if шаг is None:
        return []
    return [(к.label, f"z:{с.id}:{шаг.id}:{номер}") for номер, к in enumerate(шаг.buttons)]


def _подставить(текст: str, несовершеннолетний: bool) -> str:
    # {contact:crisis} — телефон доверия по возрасту: детский только тому,
    # кто сам назвал себя несовершеннолетним (мануал, §4.2).
    линия = "children_line" if несовершеннолетний else "crisis_adult"
    текст = текст.replace("{contact:crisis}", "{contact:" + линия + "}")
    return support_contacts.подставить(текст, несовершеннолетний=несовершеннолетний)


ПОМЕТКИ = {
    "suicide": "Угроза жизни: разговор о смерти",
    "medical": "Угроза жизни: острое состояние",
    "relative_death_wish": "Близкий просит дать ему умереть",
    "overwhelmed": "Тот, кто ухаживает: на пределе",
}


def _уровень(с: Сценарий, ответ: Ответ) -> None:
    if с.level:
        ответ.уровни.append((с.level, с.kind, с.id, с.version))
        ответ.пометка = ответ.пометка or ПОМЕТКИ.get(с.kind)


def _пройти(с: Сценарий, шаг_id: str, ответ: Ответ, *, согласие: bool,
            эвтаназия: bool, части: list[str]) -> None:
    """Сказать шаги подряд, пока не встретится вопрос или конец."""
    увидено = set()
    while шаг_id and шаг_id not in увидено:
        увидено.add(шаг_id)
        if шаг_id.startswith("scenario:"):
            _войти(шаг_id, ответ, согласие=согласие, эвтаназия=эвтаназия, части=части)
            return
        шаг = с.steps[шаг_id]
        if шаг.only_if == "euthanasia" and not эвтаназия:
            шаг_id = шаг.next
            continue
        if шаг.say:
            части.append(шаг.say)
        if шаг.raise_level:
            уровень, вид = шаг.raise_level
            ответ.уровни.append((уровень, вид, с.id, с.version))
            ответ.события.append(("level_raised", с.id, с.version, шаг.id))
        if шаг.event:
            ответ.события.append((шаг.event, с.id, с.version, шаг.id))
        if шаг.ask:
            части.append(шаг.ask)
            ответ.ожидание = (с.id, с.version, шаг.id)
            return
        if шаг.end:
            if с.after_consent:
                части.append(с.after_consent)
            return
        шаг_id = шаг.next


def _войти(переход: str, ответ: Ответ, *, согласие: bool, эвтаназия: bool,
           части: list[str]) -> None:
    """Переход в другой сценарий: «Да» на вопрос о безопасности → кризис."""
    _, id, *шаг = переход.split(":")
    с = СЦЕНАРИИ[id]
    _уровень(с, ответ)
    ответ.события.append(("level_raised", с.id, с.version, шаг[0] if шаг else "start"))
    if шаг and с.действует():
        _пройти(с, шаг[0], ответ, согласие=согласие, эвтаназия=эвтаназия, части=части)
    else:
        части.append(_первое_сообщение(с, согласие=согласие, несовершеннолетний=False,
                                       текст_человека="", эвтаназия=эвтаназия, ответ=ответ))


def _прежнее(текст_человека: str, ответ: Ответ) -> str:
    """Прежнее поведение — ответ `распознать`, каким он был до сценариев."""
    тревога = emergency.распознать(текст_человека) if текст_человека else None
    if тревога is None:
        return ""
    ответ.пометка = тревога.пометка
    ответ.группа = тревога.вид
    вид = "suicide" if тревога.вид == "поддержка" else "medical"
    ответ.уровни.append(("P0", вид, "legacy", 0))
    return тревога.ответ


def _первое_сообщение(с: Сценарий, *, согласие: bool, несовершеннолетний: bool,
                      текст_человека: str, эвтаназия: bool, ответ: Ответ) -> str:
    """Минимум одним сообщением: до согласия и для неутверждённого сценария."""
    if с.id == emergency.P0_SUICIDE and not с.действует():
        if несовершеннолетний:
            # Р-А3: детский телефон доверия — тому, кто сам назвал себя
            # несовершеннолетним; остальной текст прежний.
            return _подставить(emergency.ПОДДЕРЖКА_ШАБЛОН.replace(
                "{contact:crisis_adult}", "{contact:children_line}"), True)
        return emergency.ПОДДЕРЖКА
    if с.id == emergency.P0_MEDICAL:
        return emergency.СКОРАЯ
    if с.minimal == "legacy":
        if с.действует() and с.first:
            части: list[str] = []
            _пройти(с, с.first, ответ, согласие=False, эвтаназия=эвтаназия, части=части)
            ответ.ожидание = None
            return "\n\n".join(_подставить(ч, несовершеннолетний) for ч in части)
        return _прежнее(текст_человека, ответ)
    текст = _подставить(с.minimal or "", несовершеннолетний)
    if согласие and с.after_consent:
        текст += "\n\n" + с.after_consent
    return текст


# Группа для счётчика до согласия — прежние значения для P0, чтобы ряды
# метрики `sdut_alerts_before_consent_total` не разорвались.
ГРУППЫ = {"suicide": "поддержка", "medical": "скорая"}


def ответ(оценка: "emergency.Оценка", текст_человека: str, *, согласие: bool) -> Ответ | None:
    """Ответ на сигнал: ведущий сценарий и уровни попутных."""
    с = СЦЕНАРИИ[оценка.ведущий]
    результат = Ответ(текст="", группа=ГРУППЫ.get(с.kind, с.kind))
    if с.level:
        _уровень(с, результат)
        результат.события.append(("scenario_entered", с.id, с.version, с.first or "minimal"))
    if с.level == "P0":
        # Пометка в карточке — как прежде: «Угроза жизни: <признак>».
        # Признак — слово из перечня, а не из сообщения человека.
        результат.пометка = f"Угроза жизни: {оценка.признак}"

    пошагово = согласие and с.действует() and с.first and с.minimal != "legacy"
    if с.id == emergency.P0_SUICIDE and с.действует():
        пошагово = True
    if пошагово:
        части: list[str] = []
        _пройти(с, с.first, результат, согласие=согласие, эвтаназия=оценка.эвтаназия,
                части=части)
        текст = "\n\n".join(_подставить(ч, оценка.несовершеннолетний) for ч in части)
        if not согласие:
            результат.ожидание = None
    else:
        текст = _первое_сообщение(с, согласие=согласие, несовершеннолетний=оценка.несовершеннолетний,
                                  текст_человека=текст_человека, эвтаназия=оценка.эвтаназия,
                                  ответ=результат)
    if not текст:
        return None

    for попутный in оценка.попутно:
        п = СЦЕНАРИИ[попутный]
        if п.level:
            результат.уровни.append((п.level, п.kind, п.id, п.version))
        # Попутный Р-А4 после кризиса: близкому тоже нужна паллиативная
        # служба (мануал, §6.1). Его текст — после ответа на кризис.
        if попутный == emergency.RA4 and с.level == "P0":
            if согласие and п.действует():
                текст += "\n\n" + _подставить(п.steps["m1"].say, False)
            else:
                текст += "\n\n" + _подставить(п.minimal or "", False)
    результат.текст = текст
    return результат


def продолжить(ожидание: tuple[str, int, str], выбор: int | None, *, согласие: bool) -> Ответ | None:
    """Ответ на вопрос сценария: кнопкой (`выбор`) или свободным текстом (None)."""
    id, версия, шаг_id = ожидание
    с = СЦЕНАРИИ.get(id)
    if с is None or с.version != версия or шаг_id not in с.steps:
        return None
    шаг = с.steps[шаг_id]
    if выбор is None:
        следующий = шаг.on_free_text
    elif 0 <= выбор < len(шаг.buttons):
        следующий = шаг.buttons[выбор].next
    else:
        return None
    результат = Ответ(текст="")
    результат.события.append(("branch_taken", с.id, с.version, шаг_id))
    if not следующий:
        return результат
    части: list[str] = []
    _пройти(с, следующий, результат, согласие=согласие, эвтаназия=False, части=части)
    результат.текст = "\n\n".join(_подставить(ч, False) for ч in части)
    return результат


__all__ = ["СЦЕНАРИИ", "Сценарий", "Ответ", "ответ", "продолжить", "кнопки", "режим", "загрузить"]

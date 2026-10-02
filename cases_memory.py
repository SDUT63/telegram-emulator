#!/usr/bin/env python3
"""Хранилище обращений в памяти — для модульных тестов.

Бизнес-логики здесь нет: правила живут в cases.py. Здесь только то, что
в PostgreSQL держат ограничения и триггеры миграции 014, — чтобы тесты
контракта на этом хранилище ловили то же, что и на настоящей базе:
одно открытое обращение на человека, неизменяемый номер, предложенный
маршрут, снимок согласия и версия справочника, обращение на согласии
только со снимком согласия; с миграцией 015 — причины закрытия
редакции 3 и судьба открытых задач (И16).

Транзакция — общий замок и копия данных на входе: исключение внутри
возвращает всё как было.
"""
from __future__ import annotations

import copy
import itertools
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from typing import Iterator

from cases import (
    CLOSED, CONSENT_BASIS, CONTROL_KINDS, OPEN_TASK_STATUSES, PROCESSING, Case,
    CaseError, CaseEvent, Consent, DirectoryEntry, ImmutableRecord, Intake,
    OpenCaseExists, Outcome, Person, Referral, Task,
)

# Таблицы с данными человека — для проверки И14.
PERSON_DATA_TABLES = (
    "persons", "cases", "case_consents", "intakes", "case_events",
    "tasks", "referrals", "outcomes",
)


class MemoryCaseRepository:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._depth = 0
        self._ids = itertools.count(1)
        self._t: dict[str, dict] = {
            "persons": {}, "cases": {}, "case_consents": {}, "intakes": {},
            "case_events": {}, "tasks": {}, "referrals": {}, "outcomes": {},
            "route_directory": {}, "case_number_counters": {},
        }

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            снимок = copy.deepcopy(self._t)
            self._depth = 1
            try:
                yield
                self._при_фиксации()
            except BaseException:
                self._t = снимок
                raise
            finally:
                self._depth = 0

    def _при_фиксации(self) -> None:
        # То же, что отложенный триггер cases_require_consent в 014 (И11).
        со_снимком = {c.case_id for c in self._t["case_consents"].values() if c.kind == PROCESSING}
        for case in self._t["cases"].values():
            if case.legal_basis == CONSENT_BASIS and case.case_id not in со_снимком:
                raise CaseError("И11: обращение на основании согласия без снимка согласия")
        # То же, что отложенные триггеры И16 в 015.
        for задача in self._t["tasks"].values():
            if задача.status not in OPEN_TASK_STATUSES:
                continue
            case = self._t["cases"][задача.case_id]
            if case.status == CLOSED:
                raise CaseError("И16: у закрытого обращения не может быть открытых задач")
            в_контроле = case.status == "CONTROL" or (
                case.status == "ESCALATED" and case.status_before_escalation == "CONTROL")
            if задача.kind in CONTROL_KINDS and not в_контроле:
                raise CaseError("И16: открытые контрольные задачи есть только у обращения в контроле")

    def _в_транзакции(self) -> None:
        if not self._depth:
            raise RuntimeError("операция хранилища вне транзакции")

    @staticmethod
    def _проверить_строку(case: Case) -> None:
        # То же, что CHECK-ограничения таблицы cases в 014.
        правила = (
            ((case.status == CLOSED) == (case.close_reason is not None), "закрыто ⇔ есть причина (И7)"),
            ((case.number is None) == (case.opened_at is None), "номер ⇔ выходило из черновика (И2)"),
            (case.status != "DRAFT" or case.number is None, "у черновика номера нет (И2)"),
            (case.status in ("DRAFT", CLOSED) or case.number is not None, "у рабочего обращения есть номер (И2)"),
            ((case.status == "ESCALATED") == (case.status_before_escalation is not None),
             "эскалированное помнит, откуда пришло"),
            (case.duplicate_of is None or case.duplicate_of != case.case_id, "дубликат самого себя"),
            (case.close_reason != "consent_not_given" or case.legal_basis == "vital_interest",
             "consent_not_given — только при vital_interest (И7)"),
        )
        for верно, что in правила:
            if not верно:
                raise CaseError(f"нарушено ограничение таблицы cases: {что}")

    @staticmethod
    def _копия(value):
        return copy.deepcopy(value)

    def _id(self) -> int:
        return next(self._ids)

    # --- persons ------------------------------------------------------------

    def lock_person(self, channel: str, channel_user_id: str, first_seen_at: datetime) -> Person:
        self._в_транзакции()
        найден = self.find_person(channel, channel_user_id)
        if найден is not None:
            return найден
        person = Person(self._id(), channel, channel_user_id, first_seen_at)
        self._t["persons"][person.person_id] = person
        return self._копия(person)

    def find_person(self, channel: str, channel_user_id: str) -> Person | None:
        self._в_транзакции()
        for person in self._t["persons"].values():
            if person.channel == channel and person.channel_user_id == channel_user_id:
                return self._копия(person)
        return None

    def update_person(self, person: Person) -> None:
        self._в_транзакции()
        self._t["persons"][person.person_id] = self._копия(person)

    # --- cases --------------------------------------------------------------

    def get_case(self, case_id: int, *, lock: bool = False) -> Case | None:
        self._в_транзакции()
        return self._копия(self._t["cases"].get(case_id))

    def open_case_of(self, person_id: int) -> Case | None:
        self._в_транзакции()
        for case in self._t["cases"].values():
            if case.person_id == person_id and case.status != CLOSED:
                return self._копия(case)
        return None

    def cases_of(self, person_id: int) -> list[Case]:
        self._в_транзакции()
        return [self._копия(c) for c in sorted(self._t["cases"].values(), key=lambda c: c.case_id)
                if c.person_id == person_id]

    def drafts_created_before(self, moment: datetime) -> list[Case]:
        self._в_транзакции()
        return [self._копия(c) for c in self._t["cases"].values()
                if c.status == "DRAFT" and c.created_at < moment]

    def insert_case(self, case: Case) -> Case:
        self._в_транзакции()
        self._проверить_строку(case)
        if case.close_reason == "consent_not_given":
            raise CaseError("И7: consent_not_given — только переходом из CONTACTED")
        if case.status != CLOSED and self.open_case_of(case.person_id) is not None:
            raise OpenCaseExists("И1: у человека уже есть открытое обращение")
        if case.number is not None and any(c.number == case.number for c in self._t["cases"].values()):
            raise ImmutableRecord("И2: номер обращения уже выдан")
        новое = replace(case, case_id=self._id())
        self._t["cases"][новое.case_id] = self._копия(новое)
        return новое

    def update_case(self, case: Case) -> None:
        self._в_транзакции()
        self._проверить_строку(case)
        было = self._t["cases"][case.case_id]
        if было.number is not None and case.number != было.number:
            raise ImmutableRecord("И2: номер обращения не меняется")
        if было.suggested_route is not None and case.suggested_route != было.suggested_route:
            raise ImmutableRecord("И5: предложенный маршрут записывается один раз")
        # То же, что триггер cases_r3_close_rules (015, усилен в 016).
        if было.close_reason is not None and case.close_reason != было.close_reason:
            raise CaseError("И7: причина закрытия не меняется, закрытое обращение "
                            "не переоткрывается — из CLOSED переходов нет (5.2)")
        if было.close_reason is None and case.close_reason is not None:
            if было.status == "DRAFT" and case.close_reason != "abandoned_draft":
                raise CaseError("Г5: черновик закрывается только как abandoned_draft")
            if case.close_reason == "consent_not_given" and было.status != "CONTACTED":
                raise CaseError("И7: consent_not_given — только из CONTACTED")
        if case.number is not None and any(
                c.number == case.number and c.case_id != case.case_id for c in self._t["cases"].values()):
            raise ImmutableRecord("И2: номер обращения уже выдан")
        self._t["cases"][case.case_id] = self._копия(case)

    def delete_cases_of(self, person_id: int) -> None:
        self._в_транзакции()
        удаляемые = {c.case_id for c in self._t["cases"].values() if c.person_id == person_id}
        for таблица in PERSON_DATA_TABLES:
            if таблица in ("persons", "cases"):
                continue
            self._t[таблица] = {k: v for k, v in self._t[таблица].items()
                                if v.case_id not in удаляемые}
        self._t["cases"] = {k: (replace(v, duplicate_of=None) if v.duplicate_of in удаляемые else v)
                            for k, v in self._t["cases"].items() if k not in удаляемые}

    def next_number(self, year: int) -> int:
        self._в_транзакции()
        счётчики = self._t["case_number_counters"]
        счётчики[year] = счётчики.get(year, 0) + 1
        return счётчики[year]

    # --- consents -----------------------------------------------------------

    def insert_consent(self, consent: Consent) -> Consent:
        self._в_транзакции()
        новое = replace(consent, consent_id=self._id())
        self._t["case_consents"][новое.consent_id] = self._копия(новое)
        return новое

    def consents(self, case_id: int) -> list[Consent]:
        self._в_транзакции()
        return [self._копия(c) for c in self._t["case_consents"].values() if c.case_id == case_id]

    def set_consent_withdrawn(self, consent_id: int, at: datetime) -> None:
        self._в_транзакции()
        было = self._t["case_consents"][consent_id]
        if было.withdrawn_at is not None:
            raise ImmutableRecord("И11: отзыв отмечается один раз")
        self._t["case_consents"][consent_id] = replace(было, withdrawn_at=at)

    # --- intakes ------------------------------------------------------------

    def insert_intake(self, intake: Intake) -> None:
        self._в_транзакции()
        ключ = (intake.case_id, intake.version)
        if ключ in self._t["intakes"]:
            raise ImmutableRecord("версия анкеты уже есть")
        self._t["intakes"][ключ] = self._копия(intake)

    def intakes(self, case_id: int) -> list[Intake]:
        self._в_транзакции()
        return [self._копия(i) for _, i in sorted(self._t["intakes"].items()) if i.case_id == case_id]

    def update_intake(self, intake: Intake) -> None:
        self._в_транзакции()
        self._t["intakes"][(intake.case_id, intake.version)] = self._копия(intake)

    # --- events -------------------------------------------------------------

    def insert_event(self, event: CaseEvent) -> CaseEvent:
        self._в_транзакции()
        новое = replace(event, event_id=self._id())
        self._t["case_events"][новое.event_id] = self._копия(новое)
        return новое

    def events(self, case_id: int) -> list[CaseEvent]:
        self._в_транзакции()
        return [self._копия(e) for _, e in sorted(self._t["case_events"].items()) if e.case_id == case_id]

    # --- tasks --------------------------------------------------------------

    def insert_task(self, task: Task) -> Task:
        self._в_транзакции()
        новая = replace(task, task_id=self._id())
        self._t["tasks"][новая.task_id] = self._копия(новая)
        return новая

    def get_task(self, task_id: int, *, lock: bool = False) -> Task | None:
        self._в_транзакции()
        return self._копия(self._t["tasks"].get(task_id))

    def update_task(self, task: Task) -> None:
        self._в_транзакции()
        self._t["tasks"][task.task_id] = self._копия(task)

    def tasks(self, case_id: int) -> list[Task]:
        self._в_транзакции()
        return [self._копия(t) for _, t in sorted(self._t["tasks"].items()) if t.case_id == case_id]

    # --- referrals, outcomes --------------------------------------------------

    def insert_referral(self, referral: Referral) -> Referral:
        self._в_транзакции()
        if referral.directory_entry_id not in self._t["route_directory"]:
            raise CaseError("направление ссылается на несуществующую запись справочника")
        новое = replace(referral, referral_id=self._id())
        self._t["referrals"][новое.referral_id] = self._копия(новое)
        return новое

    def get_referral(self, referral_id: int, *, lock: bool = False) -> Referral | None:
        self._в_транзакции()
        return self._копия(self._t["referrals"].get(referral_id))

    def update_referral(self, referral: Referral) -> None:
        self._в_транзакции()
        self._t["referrals"][referral.referral_id] = self._копия(referral)

    def referrals(self, case_id: int) -> list[Referral]:
        self._в_транзакции()
        return [self._копия(r) for _, r in sorted(self._t["referrals"].items()) if r.case_id == case_id]

    def insert_outcome(self, outcome: Outcome) -> Outcome:
        self._в_транзакции()
        новый = replace(outcome, outcome_id=self._id())
        self._t["outcomes"][новый.outcome_id] = self._копия(новый)
        return новый

    def outcomes(self, case_id: int) -> list[Outcome]:
        self._в_транзакции()
        return [self._копия(o) for _, o in sorted(self._t["outcomes"].items()) if o.case_id == case_id]

    # --- route_directory ----------------------------------------------------

    def insert_directory_entry(self, entry: DirectoryEntry) -> DirectoryEntry:
        self._в_транзакции()
        if entry.valid_to is None and self.current_directory_entry(entry.provider_key) is not None:
            raise ImmutableRecord("у организации уже есть действующая запись")
        новая = replace(entry, directory_entry_id=self._id())
        self._t["route_directory"][новая.directory_entry_id] = self._копия(новая)
        return новая

    def get_directory_entry(self, directory_entry_id: int) -> DirectoryEntry | None:
        self._в_транзакции()
        return self._копия(self._t["route_directory"].get(directory_entry_id))

    def current_directory_entry(self, provider_key: str) -> DirectoryEntry | None:
        self._в_транзакции()
        for entry in self._t["route_directory"].values():
            if entry.provider_key == provider_key and entry.valid_to is None:
                return self._копия(entry)
        return None

    def set_directory_valid_to(self, directory_entry_id: int, at: datetime) -> None:
        self._в_транзакции()
        было = self._t["route_directory"][directory_entry_id]
        if было.valid_to is not None:
            raise ImmutableRecord("И12: версия справочника закрывается один раз")
        self._t["route_directory"][directory_entry_id] = replace(было, valid_to=at)

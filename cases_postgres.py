#!/usr/bin/env python3
"""Хранилище обращений в PostgreSQL — адаптер для круглосуточной работы.

Бизнес-логики здесь нет: правила живут в cases.py, ограничения —
в migrations/postgres/014_cases.sql. Таблицы создаёт миграция, а не
этот модуль.

Транзакция. Если обращение меняется во время обработки события MAX,
транзакция события уже открыта (storage_postgres._TX_CONNECTION), и
адаптер работает внутри неё: учёт события и изменение обращения
фиксируются или откатываются вместе, а повторная доставка того же
события до модуля обращений не доходит (И15). Вне события адаптер
открывает свою транзакцию.
"""
from __future__ import annotations

import contextvars
import dataclasses
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, TypeVar

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from cases import (
    Case, CaseError, CaseEvent, Consent, DirectoryEntry, ImmutableRecord, Intake,
    OpenCaseExists, Outcome, Person, Referral, Task,
)
from storage_postgres import _TX_CONNECTION, database_url

# Таблицы с данными человека — для проверки И14. Новая таблица со
# столбцом case_id или person_id, не внесённая сюда, роняет тест.
PERSON_DATA_TABLES = (
    "persons", "cases", "case_consents", "intakes", "case_events",
    "tasks", "referrals", "outcomes",
)

_JSON = {
    "cases": {"suggested_route"},
    "intakes": {"answers", "alerts", "story_hints"},
    "case_events": {"payload"},
}

T = TypeVar("T")


class PostgresCaseRepository:
    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = db_url or database_url()
        self._conn: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
            f"sdut_cases_conn_{id(self)}", default=None)

    # --- транзакция ---------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._conn.get() is not None:
            yield
            return
        событие = _TX_CONNECTION.get()
        if событие is not None:
            # Фиксирует или откатывает транзакция события (И15).
            token = self._conn.set(событие)
            try:
                with _ошибки_базы():
                    yield
            finally:
                self._conn.reset(token)
            return
        conn = psycopg.connect(self.db_url)
        token = self._conn.set(conn)
        try:
            with _ошибки_базы():
                yield
                conn.commit()      # здесь срабатывают отложенные проверки (И11)
        except BaseException:
            conn.rollback()
            raise
        finally:
            self._conn.reset(token)
            conn.close()

    def _cur(self):
        conn = self._conn.get()
        if conn is None:
            raise RuntimeError("операция хранилища вне транзакции")
        return conn.cursor(row_factory=dict_row)

    def _one(self, cls: type[T], query: str, params: tuple = ()) -> T | None:
        row = self._cur().execute(query, params).fetchone()
        return None if row is None else cls(**row)

    def _many(self, cls: type[T], query: str, params: tuple = ()) -> list[T]:
        return [cls(**row) for row in self._cur().execute(query, params).fetchall()]

    def _insert(self, table: str, obj: Any, key: str | None) -> Any:
        данные = {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
        if key is not None and данные.get(key) is None:
            данные.pop(key)
        json_cols = _JSON.get(table, set())
        значения = [Jsonb(v) if k in json_cols and v is not None else v for k, v in данные.items()]
        запрос = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, данные)),
            sql.SQL(", ").join(sql.Placeholder() * len(данные)),
        )
        row = self._cur().execute(запрос, значения).fetchone()
        return type(obj)(**row)

    def _update(self, table: str, obj: Any, keys: tuple[str, ...]) -> None:
        данные = {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
        json_cols = _JSON.get(table, set())
        поля = [k for k in данные if k not in keys]
        запрос = sql.SQL("UPDATE {} SET {} WHERE {}").format(
            sql.Identifier(table),
            sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in поля),
            sql.SQL(" AND ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in keys),
        )
        значения = [Jsonb(данные[k]) if k in json_cols and данные[k] is not None else данные[k]
                    for k in поля] + [данные[k] for k in keys]
        self._cur().execute(запрос, значения)

    # --- persons ------------------------------------------------------------

    def lock_person(self, channel: str, channel_user_id: str, first_seen_at: datetime) -> Person:
        cur = self._cur()
        cur.execute(
            "INSERT INTO persons(channel, channel_user_id, first_seen_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (channel, channel_user_id) DO NOTHING",
            (channel, channel_user_id, first_seen_at))
        return self._one(Person, "SELECT * FROM persons WHERE channel = %s AND channel_user_id = %s "
                                 "FOR UPDATE", (channel, channel_user_id))

    def find_person(self, channel: str, channel_user_id: str) -> Person | None:
        return self._one(Person, "SELECT * FROM persons WHERE channel = %s AND channel_user_id = %s",
                         (channel, channel_user_id))

    def update_person(self, person: Person) -> None:
        self._update("persons", person, ("person_id",))

    # --- cases --------------------------------------------------------------

    def get_case(self, case_id: int, *, lock: bool = False) -> Case | None:
        return self._one(Case, "SELECT * FROM cases WHERE case_id = %s" + (" FOR UPDATE" if lock else ""),
                         (case_id,))

    def open_case_of(self, person_id: int) -> Case | None:
        return self._one(Case, "SELECT * FROM cases WHERE person_id = %s AND status <> 'CLOSED'",
                         (person_id,))

    def cases_of(self, person_id: int) -> list[Case]:
        return self._many(Case, "SELECT * FROM cases WHERE person_id = %s ORDER BY case_id", (person_id,))

    def drafts_created_before(self, moment: datetime) -> list[Case]:
        return self._many(Case, "SELECT * FROM cases WHERE status = 'DRAFT' AND created_at < %s "
                                "ORDER BY case_id", (moment,))

    def insert_case(self, case: Case) -> Case:
        return self._insert("cases", case, "case_id")

    def update_case(self, case: Case) -> None:
        self._update("cases", case, ("case_id",))

    def delete_cases_of(self, person_id: int) -> None:
        # Каскад удаляет согласия, анкеты, события, задачи, направления, исходы.
        self._cur().execute("DELETE FROM cases WHERE person_id = %s", (person_id,))

    def next_number(self, year: int) -> int:
        # Строка года блокируется до конца транзакции: номера выдаются по
        # очереди, откат возвращает номер (4.9).
        row = self._cur().execute(
            "INSERT INTO case_number_counters(year, last_value) VALUES (%s, 1) "
            "ON CONFLICT (year) DO UPDATE SET last_value = case_number_counters.last_value + 1 "
            "RETURNING last_value", (year,)).fetchone()
        return int(row["last_value"])

    # --- consents -----------------------------------------------------------

    def insert_consent(self, consent: Consent) -> Consent:
        return self._insert("case_consents", consent, "consent_id")

    def consents(self, case_id: int) -> list[Consent]:
        return self._many(Consent, "SELECT * FROM case_consents WHERE case_id = %s ORDER BY consent_id",
                          (case_id,))

    def set_consent_withdrawn(self, consent_id: int, at: datetime) -> None:
        self._cur().execute("UPDATE case_consents SET withdrawn_at = %s WHERE consent_id = %s",
                            (at, consent_id))

    # --- intakes ------------------------------------------------------------

    def insert_intake(self, intake: Intake) -> None:
        self._insert("intakes", intake, None)

    def intakes(self, case_id: int) -> list[Intake]:
        return self._many(Intake, "SELECT * FROM intakes WHERE case_id = %s ORDER BY version", (case_id,))

    def update_intake(self, intake: Intake) -> None:
        self._update("intakes", intake, ("case_id", "version"))

    # --- events -------------------------------------------------------------

    def insert_event(self, event: CaseEvent) -> CaseEvent:
        return self._insert("case_events", event, "event_id")

    def events(self, case_id: int) -> list[CaseEvent]:
        return self._many(CaseEvent, "SELECT * FROM case_events WHERE case_id = %s ORDER BY event_id",
                          (case_id,))

    # --- tasks --------------------------------------------------------------

    def insert_task(self, task: Task) -> Task:
        return self._insert("tasks", task, "task_id")

    def get_task(self, task_id: int, *, lock: bool = False) -> Task | None:
        return self._one(Task, "SELECT * FROM tasks WHERE task_id = %s" + (" FOR UPDATE" if lock else ""),
                         (task_id,))

    def update_task(self, task: Task) -> None:
        self._update("tasks", task, ("task_id",))

    def tasks(self, case_id: int) -> list[Task]:
        return self._many(Task, "SELECT * FROM tasks WHERE case_id = %s ORDER BY task_id", (case_id,))

    # --- referrals, outcomes --------------------------------------------------

    def insert_referral(self, referral: Referral) -> Referral:
        return self._insert("referrals", referral, "referral_id")

    def get_referral(self, referral_id: int, *, lock: bool = False) -> Referral | None:
        return self._one(Referral, "SELECT * FROM referrals WHERE referral_id = %s"
                         + (" FOR UPDATE" if lock else ""), (referral_id,))

    def update_referral(self, referral: Referral) -> None:
        self._update("referrals", referral, ("referral_id",))

    def referrals(self, case_id: int) -> list[Referral]:
        return self._many(Referral, "SELECT * FROM referrals WHERE case_id = %s ORDER BY referral_id",
                          (case_id,))

    def insert_outcome(self, outcome: Outcome) -> Outcome:
        return self._insert("outcomes", outcome, "outcome_id")

    def outcomes(self, case_id: int) -> list[Outcome]:
        return self._many(Outcome, "SELECT * FROM outcomes WHERE case_id = %s ORDER BY outcome_id",
                          (case_id,))

    # --- route_directory ----------------------------------------------------

    def insert_directory_entry(self, entry: DirectoryEntry) -> DirectoryEntry:
        return self._insert("route_directory", entry, "directory_entry_id")

    def get_directory_entry(self, directory_entry_id: int) -> DirectoryEntry | None:
        return self._one(DirectoryEntry, "SELECT * FROM route_directory WHERE directory_entry_id = %s",
                         (directory_entry_id,))

    def current_directory_entry(self, provider_key: str) -> DirectoryEntry | None:
        return self._one(DirectoryEntry, "SELECT * FROM route_directory "
                                         "WHERE provider_key = %s AND valid_to IS NULL", (provider_key,))

    def set_directory_valid_to(self, directory_entry_id: int, at: datetime) -> None:
        self._cur().execute("UPDATE route_directory SET valid_to = %s WHERE directory_entry_id = %s",
                            (at, directory_entry_id))


_НЕИЗМЕНЯЕМОЕ = ("И2:", "И3:", "И5:", "И12:", "И11: снимок согласия")


@contextmanager
def _ошибки_базы() -> Iterator[None]:
    """Ошибки базы — в ошибки контракта: вызывающему не нужно знать psycopg."""
    try:
        yield
    except psycopg.errors.UniqueViolation as exc:
        if "cases_one_open_per_person" in str(exc):
            raise OpenCaseExists("И1: у человека уже есть открытое обращение") from exc
        raise ImmutableRecord(str(exc).splitlines()[0]) from exc
    except psycopg.errors.RaiseException as exc:
        # Сообщения триггеров 014 и 015 начинаются с номера инварианта или
        # пробела. Попытка изменить неизменяемое — ImmutableRecord; прочие
        # нарушения правил (И11 без снимка, И7, И16, Г5) — CaseError.
        сообщение = str(exc).splitlines()[0]
        if сообщение.startswith(_НЕИЗМЕНЯЕМОЕ):
            raise ImmutableRecord(сообщение) from exc
        raise CaseError(сообщение) from exc
    except psycopg.errors.IntegrityError as exc:
        raise CaseError(str(exc).splitlines()[0]) from exc

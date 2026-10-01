#!/usr/bin/env python3
"""Модуль обращений — единственное место правил модели обращения СДУТ.

Контракт: docs/МОДЕЛЬ-ОБРАЩЕНИЯ.md, редакция 2 (доменный контракт v1).
Соответствие правил коду и тестам — docs/МОДЕЛЬ-ОБРАЩЕНИЯ-ШАГ-1.md;
там же прочтения (Ч1–Ч12) и пробелы (Г1–Г5), которые ждут редакции 3.
Ссылки вида «5.2», «И4», «Ч3», «Г1» в этом файле ведут туда.

Модуль не знает, где лежат данные: чтение и запись идут через
CaseRepository. Адаптеры (cases_memory.py, cases_postgres.py) только
хранят и сами ничего не решают. Поэтому правило, нарушенное здесь,
нарушено во всех хранилищах сразу, — и тесты контракта гоняются на
каждом из них.

Бот, CRM и перенос старых данных к модулю пока не подключены: это
шаги 2–4 раздела 14 контракта.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Protocol

# --- 5.1 Статусы -----------------------------------------------------------

DRAFT = "DRAFT"
NEW = "NEW"
ASSIGNED = "ASSIGNED"
CONTACTED = "CONTACTED"
ROUTE_CONFIRMED = "ROUTE_CONFIRMED"
REFERRED = "REFERRED"
SERVICE_STARTED = "SERVICE_STARTED"
CONTROL = "CONTROL"
CLOSED = "CLOSED"
NO_CONTACT = "NO_CONTACT"
WAITING_EXTERNAL = "WAITING_EXTERNAL"
ESCALATED = "ESCALATED"

STATUSES = (
    DRAFT, NEW, ASSIGNED, CONTACTED, ROUTE_CONFIRMED, REFERRED,
    SERVICE_STARTED, CONTROL, CLOSED, NO_CONTACT, WAITING_EXTERNAL, ESCALATED,
)
# Открытый — любой статус, кроме CLOSED, включая черновик (Ч5, И1).
OPEN_STATUSES = frozenset(STATUSES) - {CLOSED}

# --- 5.2 Переходы ----------------------------------------------------------
#
# Строки таблицы 5.2, кроме трёх общих правил, которые проверяет allowed():
# любой открытый → ESCALATED; ESCALATED → статус, из которого пришли;
# любой открытый → CLOSED(duplicate).

TRANSITIONS: dict[str, frozenset[str]] = {
    DRAFT: frozenset({NEW, CLOSED}),
    NEW: frozenset({ASSIGNED}),
    ASSIGNED: frozenset({CONTACTED, NO_CONTACT}),
    NO_CONTACT: frozenset({CONTACTED, CLOSED}),
    CONTACTED: frozenset({ROUTE_CONFIRMED}),
    ROUTE_CONFIRMED: frozenset({REFERRED, CLOSED}),
    REFERRED: frozenset({SERVICE_STARTED, WAITING_EXTERNAL, CLOSED}),
    WAITING_EXTERNAL: frozenset({SERVICE_STARTED, CLOSED}),
    SERVICE_STARTED: frozenset({CONTROL}),
    CONTROL: frozenset({CLOSED, REFERRED, ROUTE_CONFIRMED, WAITING_EXTERNAL, CONTROL}),
}

# Основания перехода DRAFT → NEW (Ч7).
NEW_TRIGGERS = ("checkpoint", "p0_alert_with_phone", "callback_request")

# --- 5.3 Причины закрытия --------------------------------------------------

CLOSE_REASONS = (
    "help_received", "help_partial", "solved_otherwise", "consultation_enough",
    "refused", "not_eligible", "unable_to_contact", "duplicate",
    "abandoned_draft", "migrated",
)
# Закрывает не человек: брошенный черновик — по сроку, перенесённое — перенос.
SYSTEM_CLOSE_REASONS = frozenset({"abandoned_draft", "migrated"})
_ПО_РЕШЕНИЮ = frozenset(CLOSE_REASONS) - SYSTEM_CLOSE_REASONS - {"duplicate"}

# Из какого статуса какими причинами закрывают. Где таблица 5.2 называет
# причины — только они; где пишет «CLOSED» без перечня — любая причина
# координатора (Ч6). duplicate — по общему правилу, из любого открытого.
CLOSE_REASONS_FROM: dict[str, frozenset[str]] = {
    DRAFT: frozenset({"abandoned_draft"}),
    NO_CONTACT: frozenset({"unable_to_contact"}),
    ROUTE_CONFIRMED: frozenset({"consultation_enough"}),
    REFERRED: frozenset({"refused", "not_eligible"}),
    WAITING_EXTERNAL: _ПО_РЕШЕНИЮ,
    CONTROL: _ПО_РЕШЕНИЮ,
}

# --- Остальные словари контракта -------------------------------------------

BOT = "bot"
SYSTEM = "system"

CONSENT_BASIS = "consent"
VITAL_INTEREST = "vital_interest"          # режим Б, п. 3 ч. 2 ст. 10 ФЗ-152
LEGAL_BASES = (CONSENT_BASIS, VITAL_INTEREST)

PROCESSING = "processing"
MEDICAL_TRANSFER = "medical_transfer"
CONSENT_KINDS = (PROCESSING, MEDICAL_TRANSFER)
GIVEN_VIA = ("bot", "paper")               # Ч9

ROUTES = ("М1", "М2", "М3", "М4", "М5", "СМП")
MIGRATION_RULES_VERSION = "migration"      # метка реконструкции (раздел 11)
PRIORITIES = ("P1", "P2", "P3")
P0 = "P0"

TASK_KINDS = (
    "first_contact", "confirm_route", "referral_followup",
    "control_d7", "control_d30", "escalation",
)
CONTROL_KINDS = frozenset({"control_d7", "control_d30"})
FOLLOW_UP_KINDS = frozenset({"referral_followup", "escalation"})
OPEN_TASK_STATUSES = frozenset({"open", "overdue"})

# Итоги контрольного звонка (4.6): помощь получена, получена частично,
# помощь идёт, решилось иначе, ждём начала, исполнитель не пришёл, отказ,
# нет связи.
CONTROL_RESULTS = (
    "received", "partially", "ongoing", "solved_otherwise",
    "waiting_start", "provider_no_show", "refused", "no_contact",
)
# Эти итоги на Д+7 не ждут Д+30: задача-последствие создаётся сразу (5.2).
FOLLOW_UP_RESULTS = frozenset({"waiting_start", "provider_no_show"})

REFERRAL_CHANNELS = ("call", "letter", "in_person")

OUTCOME_NEEDS = (
    "palliative", "home_social_service", "ltc_care", "assistive_devices",
    "family_training", "needs_assessment", "documents", "caregiver_support", "other",
)
OUTCOME_ACTIONS = (
    "consultation", "referral", "accompaniment", "visit",
    "assistive_devices_issue", "training",
)
OUTCOME_RESULTS = (
    "received", "partially", "waiting", "refused", "not_eligible",
    "unable_to_contact", "solved_otherwise",
)

STORY_LIMIT = 4000
ABANDONED_DRAFT_AFTER = timedelta(days=30)   # 7.7; в текст согласия — после Р5

# Самара живёт по UTC+4 без перевода часов с 2011 года. Фиксированный сдвиг
# вместо zoneinfo: на ноутбуке с Windows базы часовых поясов может не быть.
SAMARA = timezone(timedelta(hours=4), "Europe/Samara")


# --- Ошибки -----------------------------------------------------------------

class CaseError(Exception):
    """Операция нарушает контракт и отклонена; ничего не записано."""


class ForbiddenTransition(CaseError):
    """Перехода нет в таблице 5.2."""


class ContractGap(CaseError):
    """Правило не определено редакцией 2 контракта — ждёт редакции 3."""


class NotFound(CaseError):
    """Нет такой записи."""


class OpenCaseExists(CaseError):
    """Второе открытое обращение того же человека (И1). Бросает хранилище."""


class ImmutableRecord(CaseError):
    """Попытка изменить то, что контракт запрещает менять. Бросает хранилище."""


# --- 4. Сущности ------------------------------------------------------------

@dataclass(frozen=True)
class Person:
    person_id: int | None
    channel: str
    channel_user_id: str
    first_seen_at: datetime | None
    deleted_at: datetime | None = None


@dataclass(frozen=True)
class Case:
    case_id: int | None
    person_id: int
    source: str
    legal_basis: str
    status: str
    created_at: datetime
    number: str | None = None
    status_before_escalation: str | None = None
    requester_kind: str | None = None
    district: str | None = None
    urgency: str | None = None
    priority: str | None = None
    priority_reason: str | None = None
    priority_rules_version: str | None = None
    suggested_route: dict | None = None
    final_route: str | None = None
    route_changed_by: str | None = None
    route_change_reason: str | None = None
    assigned_to: str | None = None
    due_at: datetime | None = None
    opened_at: datetime | None = None
    contacted_at: datetime | None = None
    route_confirmed_at: datetime | None = None
    referred_at: datetime | None = None
    service_started_at: datetime | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None
    duplicate_of: int | None = None


@dataclass(frozen=True)
class Consent:
    consent_id: int | None
    case_id: int
    kind: str
    version: str
    text_hash: str | None
    given_at: datetime
    given_via: str
    withdrawn_at: datetime | None = None


@dataclass(frozen=True)
class Intake:
    case_id: int
    version: int
    questionnaire_version: str
    created_at: datetime
    answers: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)
    story: str | None = None
    story_hints: list = field(default_factory=list)
    checkpoint_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class DirectoryEntry:
    directory_entry_id: int | None
    provider_key: str
    route: str
    provider: str
    available: bool
    valid_from: datetime
    fallback: str | None = None
    phone: str | None = None
    hours: str | None = None
    address: str | None = None
    conditions: str | None = None
    documents: str | None = None
    valid_to: datetime | None = None


@dataclass(frozen=True)
class Task:
    task_id: int | None
    case_id: int
    kind: str
    due_at: datetime
    status: str
    created_at: datetime
    assigned_to: str | None = None
    done_at: datetime | None = None
    done_by: str | None = None
    result: str | None = None
    escalated_at: datetime | None = None
    escalated_to: str | None = None


@dataclass(frozen=True)
class Referral:
    referral_id: int | None
    case_id: int
    directory_entry_id: int
    referred_at: datetime
    channel: str
    response: str | None = None
    response_at: datetime | None = None


@dataclass(frozen=True)
class Outcome:
    outcome_id: int | None
    case_id: int
    need: str
    action: str
    result: str
    recorded_at: datetime
    recorded_by: str
    directory_entry_id: int | None = None
    service_started_at: datetime | None = None
    service_received_at: datetime | None = None


@dataclass(frozen=True)
class CaseEvent:
    event_id: int | None
    case_id: int
    at: datetime
    who: str
    kind: str
    payload: dict = field(default_factory=dict)


# --- Интерфейс хранилища ------------------------------------------------------

class CaseRepository(Protocol):
    """Что модуль обращений требует от хранилища.

    Только чтение и запись. Ограничения, которые хранилище обязано держать
    само (последний рубеж на случай ошибки в модуле): одно открытое
    обращение на человека (И1, OpenCaseExists), номер и предложенный
    маршрут не перезаписываются (И2, И5), снимок согласия и версия
    справочника не меняются, кроме однократной отметки (И11, И12),
    обращение на основании согласия не фиксируется без снимка согласия
    (И11). Изменения событий в интерфейсе нет вовсе (И3).
    """

    def transaction(self) -> AbstractContextManager[None]: ...

    def lock_person(self, channel: str, channel_user_id: str, first_seen_at: datetime) -> Person: ...
    def find_person(self, channel: str, channel_user_id: str) -> Person | None: ...
    def update_person(self, person: Person) -> None: ...

    def get_case(self, case_id: int, *, lock: bool = False) -> Case | None: ...
    def open_case_of(self, person_id: int) -> Case | None: ...
    def cases_of(self, person_id: int) -> list[Case]: ...
    def drafts_created_before(self, moment: datetime) -> list[Case]: ...
    def insert_case(self, case: Case) -> Case: ...
    def update_case(self, case: Case) -> None: ...
    def delete_cases_of(self, person_id: int) -> None: ...
    def next_number(self, year: int) -> int: ...

    def insert_consent(self, consent: Consent) -> Consent: ...
    def consents(self, case_id: int) -> list[Consent]: ...
    def set_consent_withdrawn(self, consent_id: int, at: datetime) -> None: ...

    def insert_intake(self, intake: Intake) -> None: ...
    def intakes(self, case_id: int) -> list[Intake]: ...
    def update_intake(self, intake: Intake) -> None: ...

    def insert_event(self, event: CaseEvent) -> CaseEvent: ...
    def events(self, case_id: int) -> list[CaseEvent]: ...

    def insert_task(self, task: Task) -> Task: ...
    def get_task(self, task_id: int, *, lock: bool = False) -> Task | None: ...
    def update_task(self, task: Task) -> None: ...
    def tasks(self, case_id: int) -> list[Task]: ...

    def insert_referral(self, referral: Referral) -> Referral: ...
    def get_referral(self, referral_id: int, *, lock: bool = False) -> Referral | None: ...
    def update_referral(self, referral: Referral) -> None: ...
    def referrals(self, case_id: int) -> list[Referral]: ...

    def insert_outcome(self, outcome: Outcome) -> Outcome: ...
    def outcomes(self, case_id: int) -> list[Outcome]: ...

    def insert_directory_entry(self, entry: DirectoryEntry) -> DirectoryEntry: ...
    def get_directory_entry(self, directory_entry_id: int) -> DirectoryEntry | None: ...
    def current_directory_entry(self, provider_key: str) -> DirectoryEntry | None: ...
    def set_directory_valid_to(self, directory_entry_id: int, at: datetime) -> None: ...


def allowed(frm: str, to: str, reason: str | None = None, *, back_to: str | None = None) -> bool:
    """Есть ли переход в таблице 5.2. Данные перехода здесь не проверяются."""
    if frm == CLOSED:
        return False
    if to == CLOSED and reason == "duplicate":
        return True
    if to == ESCALATED:
        return frm != ESCALATED          # ESCALATED → ESCALATED — не переход (Ч5)
    if frm == ESCALATED:
        return back_to is not None and to == back_to
    if to not in TRANSITIONS.get(frm, ()):
        return False
    if to == CLOSED:
        return reason in CLOSE_REASONS_FROM.get(frm, frozenset())
    return True


def case_number(year: int, sequence: int) -> str:
    """Номер обращения: SDUT-{год}-{номер:05d} (4.9)."""
    return f"SDUT-{year}-{sequence:05d}"


def _нужно(условие: bool, сообщение: str) -> None:
    if not условие:
        raise CaseError(сообщение)


def _текст(значение: Any, что: str) -> str:
    _нужно(isinstance(значение, str) and значение.strip() != "", f"{что}: нужен непустой текст")
    return значение


class CaseService:
    """Все операции над обращениями. Каждая — одна транзакция хранилища."""

    def __init__(self, repo: CaseRepository, clock: Callable[[], datetime] | None = None) -> None:
        self.repo = repo
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # --- служебное ---------------------------------------------------------

    def _now(self) -> datetime:
        сейчас = self._clock()
        if сейчас.tzinfo is None:
            raise CaseError("часы модуля обращений должны отдавать время с часовым поясом")
        return сейчас

    def _case(self, case_id: int, *, lock: bool = False) -> Case:
        case = self.repo.get_case(case_id, lock=lock)
        if case is None:
            raise NotFound(f"обращение {case_id} не найдено")
        return case

    def _open(self, case_id: int) -> Case:
        case = self._case(case_id, lock=True)
        if case.status == CLOSED:
            raise CaseError(f"обращение {case.number or case_id} закрыто")
        return case

    def _event(self, case_id: int, who: str, what: str, at: datetime, /, **payload: Any) -> None:
        # В событии — что произошло, а не что человек рассказал (4.8):
        # ответов, рассказа, имён здесь не бывает.
        self.repo.insert_event(CaseEvent(None, case_id, at, who, what,
                                         {k: v for k, v in payload.items() if v is not None}))

    def _assign_number(self, now: datetime) -> str:
        """Номер — по году Самары в момент перехода, из счётчика года (4.9)."""
        год = now.astimezone(SAMARA).year
        return case_number(год, self.repo.next_number(год))

    def _add_consent(self, case_id: int, kind: str, version: str, text_hash: str | None,
                     given_via: str, now: datetime, who: str) -> Consent:
        _нужно(kind in CONSENT_KINDS, f"вид согласия: {kind!r}")
        _текст(version, "версия согласия")
        _нужно(given_via in GIVEN_VIA, f"как дано согласие: {given_via!r}")
        consent = self.repo.insert_consent(
            Consent(None, case_id, kind, version, text_hash, now, given_via))
        self._event(case_id, who, "consent_given", now, kind=kind, version=version)
        return consent

    def _create_task(self, case_id: int, kind: str, due_at: datetime, who: str,
                     assigned_to: str | None, now: datetime) -> Task:
        _нужно(kind in TASK_KINDS, f"вид задачи: {kind!r}")
        _нужно(isinstance(due_at, datetime) and due_at.tzinfo is not None,
               "у задачи должен быть срок с часовым поясом (И9)")
        task = self.repo.insert_task(Task(None, case_id, kind, due_at, "open", now, assigned_to))
        self._event(case_id, who, "task_created", now, task_id=task.task_id, kind=kind,
                    due_at=due_at.isoformat())
        return task

    # --- 7. Создание обращения ----------------------------------------------

    def open_draft(self, channel: str, channel_user_id: str, *, consent_version: str,
                   consent_text_hash: str | None, questionnaire_version: str,
                   given_via: str = "bot", source: str = "bot", who: str = BOT) -> Case:
        """Согласие дано → обращение DRAFT, снимок согласия, intake v1 (7.2).

        Открытое обращение уже есть — возвращается оно: второго не бывает
        (И1), «заново» — это новая версия анкеты, а не новое обращение
        (7.5). Если открытое обращение создано в режиме Б, согласие
        добавляется к нему и основанием становится согласие (7.1).
        Новое обращение после закрытого — с новым согласием (7.6): снимок
        берётся тот, что передан сейчас.
        """
        _текст(channel, "канал")
        _текст(channel_user_id, "идентификатор в канале")
        _текст(questionnaire_version, "версия анкеты")
        with self.repo.transaction():
            now = self._now()
            person = self.repo.lock_person(channel, channel_user_id, now)
            if person.deleted_at is not None:
                # Вернулся после удаления — проходит как новый (Ч1).
                person = replace(person, deleted_at=None, first_seen_at=now)
                self.repo.update_person(person)
            открытое = self.repo.open_case_of(person.person_id)
            if открытое is not None:
                if открытое.legal_basis == VITAL_INTEREST:
                    self._add_consent(открытое.case_id, PROCESSING, consent_version,
                                      consent_text_hash, given_via, now, who)
                    открытое = replace(открытое, legal_basis=CONSENT_BASIS)
                    self.repo.update_case(открытое)
                    self._event(открытое.case_id, who, "legal_basis_changed", now,
                                legal_basis=CONSENT_BASIS)
                return открытое
            case = self.repo.insert_case(Case(
                None, person.person_id, source, CONSENT_BASIS, DRAFT, now))
            self._add_consent(case.case_id, PROCESSING, consent_version, consent_text_hash,
                              given_via, now, who)
            self.repo.insert_intake(Intake(case.case_id, 1, questionnaire_version, now))
            self._event(case.case_id, who, "created", now, status=DRAFT,
                        legal_basis=CONSENT_BASIS, source=source)
            return case

    def open_emergency(self, channel: str, channel_user_id: str, *, sign_group: str,
                       questionnaire_version: str, source: str = "bot", who: str = BOT) -> Case:
        """Режим Б (7.1): угроза жизни до согласия.

        Вызывать только если служба утвердила режим Б (Р10). Обращение
        сразу NEW с номером, P0, основание — защита жизни. Хранится
        минимум: канал, идентификатор, группа признака (в alerts intake
        версии 1, Ч8), время. Текста сообщения нет.
        Если открытое обращение уже есть — человек не «до согласия»;
        возвращается оно, без изменений.
        """
        _текст(sign_group, "группа признака")
        with self.repo.transaction():
            now = self._now()
            person = self.repo.lock_person(channel, channel_user_id, now)
            if person.deleted_at is not None:
                person = replace(person, deleted_at=None, first_seen_at=now)
                self.repo.update_person(person)
            открытое = self.repo.open_case_of(person.person_id)
            if открытое is not None:
                return открытое
            case = self.repo.insert_case(Case(
                None, person.person_id, source, VITAL_INTEREST, NEW, now,
                number=self._assign_number(now), urgency=P0, opened_at=now))
            self.repo.insert_intake(Intake(case.case_id, 1, questionnaire_version, now,
                                           alerts=[sign_group]))
            self._event(case.case_id, who, "created", now, status=NEW,
                        legal_basis=VITAL_INTEREST, urgency=P0, number=case.number,
                        source=source)
            return case

    def open_by_operator(self, channel: str, channel_user_id: str, *, who: str,
                         consent_version: str, consent_text_hash: str | None,
                         questionnaire_version: str, given_via: str = "paper",
                         source: str = "operator") -> Case:
        """Обращение, заведённое оператором: сразу NEW с номером (4.9)."""
        _текст(who, "кто заводит обращение")
        with self.repo.transaction():
            now = self._now()
            person = self.repo.lock_person(channel, channel_user_id, now)
            if person.deleted_at is not None:
                person = replace(person, deleted_at=None, first_seen_at=now)
                self.repo.update_person(person)
            if self.repo.open_case_of(person.person_id) is not None:
                raise OpenCaseExists("у человека уже есть открытое обращение (И1)")
            case = self.repo.insert_case(Case(
                None, person.person_id, source, CONSENT_BASIS, NEW, now,
                number=self._assign_number(now), opened_at=now))
            self._add_consent(case.case_id, PROCESSING, consent_version, consent_text_hash,
                              given_via, now, who)
            self.repo.insert_intake(Intake(case.case_id, 1, questionnaire_version, now))
            self._event(case.case_id, who, "created", now, status=NEW,
                        legal_basis=CONSENT_BASIS, number=case.number, source=source)
            return case

    def close_abandoned_drafts(self, *, older_than: timedelta = ABANDONED_DRAFT_AFTER) -> list[int]:
        """Черновики, не ставшие NEW за срок, закрываются системой (7.7, И7).

        Единственное автоматическое закрытие. Удаление закрытых — дело
        сроков хранения (раздел 8, после Р5).
        """
        граница = self._now() - older_than
        with self.repo.transaction():
            черновики = self.repo.drafts_created_before(граница)
        закрытые = []
        for case in черновики:
            self.transition(case.case_id, CLOSED, who=SYSTEM, reason="abandoned_draft")
            закрытые.append(case.case_id)
        return закрытые

    # --- 4.4 Анкета ---------------------------------------------------------

    def new_intake_version(self, case_id: int, *, who: str, questionnaire_version: str,
                           answers: dict | None = None, alerts: list | None = None) -> Intake:
        """«Заново», пока обращение открыто: новая версия анкеты (7.5)."""
        with self.repo.transaction():
            now = self._now()
            self._open(case_id)
            версия = max((i.version for i in self.repo.intakes(case_id)), default=0) + 1
            intake = Intake(case_id, версия, _текст(questionnaire_version, "версия анкеты"), now,
                            answers=dict(answers or {}), alerts=list(alerts or []))
            self.repo.insert_intake(intake)
            self._event(case_id, who, "intake_version", now, version=версия)
            return intake

    def update_intake(self, case_id: int, *, who: str, answers: dict | None = None,
                      alerts: list | None = None, story: str | None = None,
                      story_hints: list | None = None, checkpoint_at: datetime | None = None,
                      completed_at: datetime | None = None) -> Intake:
        """Дописать текущую версию анкеты. Прежние версии не меняются."""
        if story is not None:
            _нужно(len(story) <= STORY_LIMIT, f"рассказ длиннее {STORY_LIMIT} знаков")
        with self.repo.transaction():
            now = self._now()
            self._open(case_id)
            версии = self.repo.intakes(case_id)
            _нужно(bool(версии), "у обращения нет анкеты")
            текущая = max(версии, key=lambda i: i.version)
            изменения: dict[str, Any] = {}
            for имя, значение in (("answers", answers), ("alerts", alerts), ("story", story),
                                  ("story_hints", story_hints), ("checkpoint_at", checkpoint_at),
                                  ("completed_at", completed_at)):
                if значение is not None:
                    изменения[имя] = значение
            if not изменения:
                return текущая
            обновлённая = replace(текущая, **изменения)
            self.repo.update_intake(обновлённая)
            self._event(case_id, who, "intake_updated", now, version=текущая.version,
                        fields=sorted(изменения))
            return обновлённая

    # --- 4.3 Согласия -------------------------------------------------------

    def add_consent(self, case_id: int, *, kind: str, version: str, text_hash: str | None,
                    given_via: str, who: str) -> Consent:
        """Новый снимок согласия. Существующие снимки не меняются (И11)."""
        with self.repo.transaction():
            now = self._now()
            case = self._open(case_id)
            consent = self._add_consent(case_id, kind, version, text_hash, given_via, now, who)
            if kind == PROCESSING and case.legal_basis == VITAL_INTEREST:
                self.repo.update_case(replace(case, legal_basis=CONSENT_BASIS))
                self._event(case_id, who, "legal_basis_changed", now, legal_basis=CONSENT_BASIS)
            return consent

    def withdraw_medical_consent(self, case_id: int, *, who: str) -> None:
        """Отзыв согласия на передачу в медицинскую организацию (4.3).

        Запрещает только передачу; обращение продолжается.
        """
        with self.repo.transaction():
            now = self._now()
            self._case(case_id, lock=True)
            действующие = [c for c in self.repo.consents(case_id)
                           if c.kind == MEDICAL_TRANSFER and c.withdrawn_at is None]
            _нужно(bool(действующие), "действующего согласия на передачу нет")
            for consent in действующие:
                self.repo.set_consent_withdrawn(consent.consent_id, now)
            self._event(case_id, who, "consent_withdrawn", now, kind=MEDICAL_TRANSFER)

    def withdraw_processing_consent(self, channel: str, channel_user_id: str, *, who: str) -> bool:
        """Отзыв согласия на обработку — это «удалить» (4.3)."""
        return self.delete_person(channel, channel_user_id, who=who)

    def delete_person(self, channel: str, channel_user_id: str, *, who: str) -> bool:
        """Удаление по просьбе: все обращения человека стираются целиком (И14).

        Остаётся только отметка удаления у человека (Ч1). Удаление — не
        закрытие: события о нём негде хранить, обращения больше нет.
        """
        with self.repo.transaction():
            now = self._now()
            person = self.repo.find_person(channel, channel_user_id)
            if person is None:
                return False
            person = self.repo.lock_person(channel, channel_user_id, now)
            self.repo.delete_cases_of(person.person_id)
            self.repo.update_person(replace(person, first_seen_at=None, deleted_at=now))
            return True

    # --- 6. Экстренность и приоритет ----------------------------------------

    def set_urgency_p0(self, case_id: int, *, who: str, signals: Iterable[str]) -> Case:
        """Клиническая экстренность P0 (раздел 6)."""
        сигналы = [_текст(s, "сигнал") for s in signals]
        _нужно(bool(сигналы), "P0 ставится по сигналам — передайте их")
        with self.repo.transaction():
            now = self._now()
            case = replace(self._open(case_id), urgency=P0)
            self.repo.update_case(case)
            self._event(case_id, who, "urgency_set", now, urgency=P0, signals=сигналы)
            return case

    def set_priority(self, case_id: int, priority: str, *, who: str, reason: str,
                     rules_version: str | None = None, signals: Iterable[str] | None = None) -> Case:
        """Приоритет службы P1–P3 (раздел 6, И6, Ч10)."""
        _нужно(priority in PRIORITIES, f"приоритет: {priority!r}")
        _текст(reason, "причина приоритета")
        по_правилам = who in (BOT, SYSTEM)
        if по_правилам:
            _текст(rules_version, "версия правил приоритета (И6)")
            _нужно(signals is not None, "сигналы, по которым посчитан приоритет (И6)")
        else:
            _нужно(rules_version is None, "приоритет, выставленный вручную, не имеет версии правил")
        сигналы = [str(s) for s in signals] if signals is not None else None
        with self.repo.transaction():
            now = self._now()
            case = replace(self._open(case_id), priority=priority, priority_reason=reason,
                           priority_rules_version=rules_version)
            self.repo.update_case(case)
            self._event(case_id, who, "priority_set", now, priority=priority,
                        rules_version=rules_version, signals=сигналы)
            return case

    # --- Маршрут ------------------------------------------------------------

    def suggest_route(self, case_id: int, *, route: str, reason: str, signals: Iterable[str],
                      rules_version: str, also: Iterable[str] = (), who: str = BOT) -> Case:
        """Снимок маршрута, предложенного ботом. Записывается один раз (И5, И6)."""
        _нужно(route in ROUTES, f"маршрут: {route!r}")
        _текст(reason, "основание маршрута")
        _текст(rules_version, "версия правил маршрута (И6)")
        _нужно(rules_version != MIGRATION_RULES_VERSION,
               "реконструкция при переносе не записывается как предложенный маршрут (И13)")
        сигналы = [_текст(s, "сигнал") for s in signals]
        with self.repo.transaction():
            now = self._now()
            case = self._open(case_id)
            _нужно(case.suggested_route is None, "предложенный маршрут уже записан (И5)")
            снимок = {"route": route, "also": list(also), "reason": reason, "signals": сигналы,
                      "rules_version": rules_version, "calculated_at": now.isoformat()}
            case = replace(case, suggested_route=снимок)
            self.repo.update_case(case)
            self._event(case_id, who, "route_suggested", now, route=route,
                        rules_version=rules_version)
            return case

    def record_route_reconstruction(self, case_id: int, *, route: str, reason: str,
                                    signals: Iterable[str], rules_version: str,
                                    also: Iterable[str] = (), who: str = SYSTEM) -> None:
        """Маршрут, восстановленный при переносе (раздел 11).

        Только событие: предложенным маршрутом он не становится (И13).
        """
        _нужно(route in ROUTES, f"маршрут: {route!r}")
        _текст(rules_version, "версия правил реконструкции")
        with self.repo.transaction():
            now = self._now()
            self._case(case_id, lock=True)
            self._event(case_id, who, "route_reconstructed", now, route=route, also=list(also),
                        reason=reason, signals=[str(s) for s in signals],
                        rules_version=rules_version, calculated_at=now.isoformat())

    # --- 5. Жизненный цикл --------------------------------------------------

    def transition(self, case_id: int, to: str, *, who: str, reason: str | None = None,
                   trigger: str | None = None, assigned_to: str | None = None,
                   final_route: str | None = None, directory_entry_id: int | None = None,
                   referral_channel: str | None = None,
                   service_started_at: datetime | None = None,
                   duplicate_of: int | None = None) -> Case:
        """Переход по таблице 5.2 со всеми его последствиями — или ничего (И4)."""
        _текст(who, "кто выполняет переход")
        _нужно(to in STATUSES, f"статус: {to!r}")
        with self.repo.transaction():
            now = self._now()
            case = self._case(case_id, lock=True)
            frm = case.status
            if frm == CONTROL and to == CONTROL:
                raise ContractGap(
                    "продление контроля: вид и срок новой контрольной задачи не определены "
                    "редакцией 2 — пробел Г1, ждёт редакции 3")
            if frm == DRAFT and to == ESCALATED:
                raise ContractGap(
                    "эскалация черновика: 5.2 разрешает её из любого открытого статуса, "
                    "а И2 и 4.9 требуют номер у каждого рабочего обращения, которого у "
                    "черновика нет — пробел Г5, ждёт редакции 3")
            if not allowed(frm, to, reason, back_to=case.status_before_escalation):
                raise ForbiddenTransition(
                    f"{case.number or case_id}: перехода {frm} → {to}"
                    + (f" ({reason})" if reason else "") + " нет в таблице 5.2")

            изменения: dict[str, Any] = {"status": to}
            после: list[Callable[[], None]] = []

            if frm == ESCALATED:
                изменения["status_before_escalation"] = None
            # Возврат от старшего — только статус: номер, направление, задачи
            # контроля уже были созданы, когда обращение впервые туда пришло.
            возврат = frm == ESCALATED and to != CLOSED

            if возврат:
                pass
            elif to == ESCALATED:
                _текст(reason, "причина эскалации")
                изменения["status_before_escalation"] = frm
            elif to == CLOSED:
                системная = reason in SYSTEM_CLOSE_REASONS
                _нужно(системная == (who == SYSTEM),
                       "автоматически закрывается только черновик по сроку; "
                       "остальное закрывает человек (И7)")
                if reason == "duplicate":
                    _нужно(duplicate_of is not None and duplicate_of != case_id,
                           "дубликат закрывается ссылкой на основное обращение")
                    self._case(duplicate_of)
                    изменения["duplicate_of"] = duplicate_of
                изменения["close_reason"] = reason
                изменения["closed_at"] = now
            elif to == NEW:
                _нужно(trigger in NEW_TRIGGERS, f"основание перехода в NEW: {trigger!r} (Ч7)")
                изменения["number"] = self._assign_number(now)
                изменения["opened_at"] = now
            elif to == ASSIGNED:
                изменения["assigned_to"] = _текст(assigned_to, "ответственный")
            elif to == CONTACTED:
                изменения["contacted_at"] = now
            elif to == ROUTE_CONFIRMED:
                _нужно(final_route in ROUTES, f"итоговый маршрут: {final_route!r}")
                предложенный = (case.suggested_route or {}).get("route")
                if final_route != предложенный or frm == CONTROL:
                    # И5; пустой предложенный ≠ любому маршруту (Ч11); из
                    # контроля — «новый итоговый маршрут с причиной» (5.2).
                    _текст(reason, "причина: итоговый маршрут расходится с предложенным (И5)")
                    изменения["route_changed_by"] = who
                    изменения["route_change_reason"] = reason
                изменения["final_route"] = final_route
                изменения["route_confirmed_at"] = now
            elif to == REFERRED:
                _нужно(referral_channel in REFERRAL_CHANNELS,
                       f"как передано исполнителю: {referral_channel!r}")
                _нужно(directory_entry_id is not None, "куда направлено: запись справочника")
                запись = self.repo.get_directory_entry(directory_entry_id)
                if запись is None:
                    raise NotFound(f"записи справочника {directory_entry_id} нет")
                _нужно(запись.valid_to is None, "направлять можно только по действующей записи")
                изменения["referred_at"] = now

                def направить() -> None:
                    r = self.repo.insert_referral(
                        Referral(None, case_id, directory_entry_id, now, referral_channel))
                    self._event(case_id, who, "referred", now, referral_id=r.referral_id,
                                directory_entry_id=directory_entry_id, channel=referral_channel)
                после.append(направить)
            elif to == SERVICE_STARTED:
                начало = service_started_at or now
                _нужно(начало.tzinfo is not None and начало <= now,
                       "дата начала помощи — не в будущем и с часовым поясом")
                изменения["service_started_at"] = начало
            elif to == CONTROL:
                начало = case.service_started_at
                _нужно(начало is not None, "контроль считается от начала помощи (5.4)")

                def контроль() -> None:
                    # Д+7 и Д+30 — от начала помощи (5.4, И8); до Р2 —
                    # календарные дни (Ч3); исполнитель — ответственный (Ч12).
                    for вид, дней in (("control_d7", 7), ("control_d30", 30)):
                        self._create_task(case_id, вид, начало + timedelta(days=дней), who,
                                          case.assigned_to, now)
                после.append(контроль)

            обновлённое = replace(case, **изменения)
            self.repo.update_case(обновлённое)
            self._event(case_id, who, "status_changed", now, **{
                "from": frm, "to": to, "reason": reason, "trigger": trigger,
                "number": изменения.get("number"),
            })
            for шаг in после:
                шаг()
            return обновлённое

    def reassign(self, case_id: int, assigned_to: str, *, who: str) -> Case:
        """Сменить ответственного, не меняя статус."""
        with self.repo.transaction():
            now = self._now()
            case = replace(self._open(case_id), assigned_to=_текст(assigned_to, "ответственный"))
            self.repo.update_case(case)
            self._event(case_id, who, "assigned", now, assigned_to=assigned_to)
            return case

    # --- 4.6 Задачи ---------------------------------------------------------

    def create_task(self, case_id: int, kind: str, *, due_at: datetime, who: str,
                    assigned_to: str | None = None) -> Task:
        """Задача со сроком (И9). Контрольные создаёт только вход в CONTROL (И8)."""
        _нужно(kind not in CONTROL_KINDS,
               "задачи Д+7 и Д+30 создаются только при входе в CONTROL (И8)")
        with self.repo.transaction():
            now = self._now()
            case = self._open(case_id)
            return self._create_task(case_id, kind, due_at, who,
                                     assigned_to or case.assigned_to, now)

    def complete_task(self, task_id: int, result: str, *, who: str,
                      follow_up_kind: str | None = None,
                      follow_up_due_at: datetime | None = None) -> Task:
        """Итог задачи. Статус обращения не меняется (И8, 5.2).

        Итог Д+7 «ждём начала» или «исполнитель не пришёл» не ждёт Д+30:
        задача-последствие создаётся здесь же; её вид и срок до Р1/Р2
        называет координатор (Ч4).
        """
        _текст(who, "кто выполнил задачу")
        with self.repo.transaction():
            now = self._now()
            task = self.repo.get_task(task_id, lock=True)
            if task is None:
                raise NotFound(f"задачи {task_id} нет")
            _нужно(task.status in OPEN_TASK_STATUSES, "задача уже закрыта")
            if task.kind in CONTROL_KINDS:
                _нужно(result in CONTROL_RESULTS, f"итог контрольного звонка: {result!r}")
            else:
                _текст(result, "итог задачи")
            нужно_последствие = task.kind == "control_d7" and result in FOLLOW_UP_RESULTS
            if нужно_последствие:
                _нужно(follow_up_kind in FOLLOW_UP_KINDS,
                       "итог Д+7 требует задачи-последствия: referral_followup или escalation")
                _нужно(follow_up_due_at is not None, "срок задачи-последствия (Ч4)")
            else:
                _нужно(follow_up_kind is None and follow_up_due_at is None,
                       "задача-последствие создаётся только по итогу Д+7 (5.2)")
            сделано = replace(task, status="done", done_at=now, done_by=who, result=result)
            self.repo.update_task(сделано)
            self._event(task.case_id, who, "task_done", now, task_id=task_id, kind=task.kind,
                        result=result)
            if нужно_последствие:
                self._create_task(task.case_id, follow_up_kind, follow_up_due_at, who,
                                  task.assigned_to, now)
            return сделано

    def cancel_task(self, task_id: int, *, who: str, reason: str) -> Task:
        """Отменить задачу вручную (до решения Г4 — единственный способ)."""
        _текст(reason, "причина отмены")
        with self.repo.transaction():
            now = self._now()
            task = self.repo.get_task(task_id, lock=True)
            if task is None:
                raise NotFound(f"задачи {task_id} нет")
            _нужно(task.status in OPEN_TASK_STATUSES, "задача уже закрыта")
            отменена = replace(task, status="cancelled")
            self.repo.update_task(отменена)
            self._event(task.case_id, who, "task_cancelled", now, task_id=task_id,
                        kind=task.kind, reason=reason)
            return отменена

    # --- 4.7 Направление и исход --------------------------------------------

    def record_referral_response(self, referral_id: int, response: str, *, who: str) -> Referral:
        with self.repo.transaction():
            now = self._now()
            referral = self.repo.get_referral(referral_id, lock=True)
            if referral is None:
                raise NotFound(f"направления {referral_id} нет")
            ответ = replace(referral, response=_текст(response, "ответ исполнителя"),
                            response_at=now)
            self.repo.update_referral(ответ)
            self._event(referral.case_id, who, "referral_response", now, referral_id=referral_id)
            return ответ

    def record_outcome(self, case_id: int, *, need: str, action: str, result: str, who: str,
                       directory_entry_id: int | None = None,
                       service_started_at: datetime | None = None,
                       service_received_at: datetime | None = None) -> Outcome:
        _нужно(need in OUTCOME_NEEDS, f"потребность: {need!r}")
        _нужно(action in OUTCOME_ACTIONS, f"действие: {action!r}")
        _нужно(result in OUTCOME_RESULTS, f"результат: {result!r}")
        with self.repo.transaction():
            now = self._now()
            self._case(case_id, lock=True)
            if directory_entry_id is not None and self.repo.get_directory_entry(directory_entry_id) is None:
                raise NotFound(f"записи справочника {directory_entry_id} нет")
            outcome = self.repo.insert_outcome(Outcome(
                None, case_id, need, action, result, now, who, directory_entry_id,
                service_started_at, service_received_at))
            self._event(case_id, who, "outcome", now, outcome_id=outcome.outcome_id,
                        need=need, action=action, result=result)
            return outcome

    # --- 4.5 Справочник исполнителей ----------------------------------------

    _ПОЛЯ_СПРАВОЧНИКА = frozenset({
        "route", "provider", "available", "fallback", "phone", "hours",
        "address", "conditions", "documents",
    })

    def add_directory_entry(self, *, provider_key: str, route: str, provider: str,
                            available: bool, fallback: str | None = None,
                            phone: str | None = None, hours: str | None = None,
                            address: str | None = None, conditions: str | None = None,
                            documents: str | None = None) -> DirectoryEntry:
        """Первая версия записи об исполнителе."""
        запись = self._проверить_запись(DirectoryEntry(
            None, _текст(provider_key, "ключ организации"), route, provider, available,
            self._now(), fallback, phone, hours, address, conditions, documents))
        with self.repo.transaction():
            _нужно(self.repo.current_directory_entry(provider_key) is None,
                   "у организации уже есть действующая запись — меняйте через новую версию")
            return self.repo.insert_directory_entry(запись)

    def replace_directory_entry(self, directory_entry_id: int, **changes: Any) -> DirectoryEntry:
        """Новая версия записи; старая закрывается и остаётся как была (И12)."""
        лишние = set(changes) - self._ПОЛЯ_СПРАВОЧНИКА
        _нужно(not лишние, f"эти поля записи не меняются: {sorted(лишние)}")
        with self.repo.transaction():
            now = self._now()
            старая = self.repo.get_directory_entry(directory_entry_id)
            if старая is None:
                raise NotFound(f"записи справочника {directory_entry_id} нет")
            _нужно(старая.valid_to is None, "запись уже заменена — меняйте действующую")
            новая = self._проверить_запись(replace(
                старая, directory_entry_id=None, valid_from=now, valid_to=None, **changes))
            self.repo.set_directory_valid_to(directory_entry_id, now)
            return self.repo.insert_directory_entry(новая)

    @staticmethod
    def _проверить_запись(запись: DirectoryEntry) -> DirectoryEntry:
        _нужно(запись.route in ROUTES, f"маршрут: {запись.route!r}")
        _текст(запись.provider, "организация")
        _нужно(isinstance(запись.available, bool), "есть ли исполнитель — да или нет")
        _нужно(запись.available or bool(запись.fallback),
               "если исполнителя нет, нужно сказать, что делать вместо (4.5)")
        return запись

    # --- Чтение -------------------------------------------------------------

    def case(self, case_id: int) -> Case:
        with self.repo.transaction():
            return self._case(case_id)

    def open_case(self, channel: str, channel_user_id: str) -> Case | None:
        with self.repo.transaction():
            person = self.repo.find_person(channel, channel_user_id)
            return None if person is None else self.repo.open_case_of(person.person_id)

    def cases_of(self, channel: str, channel_user_id: str) -> list[Case]:
        with self.repo.transaction():
            person = self.repo.find_person(channel, channel_user_id)
            return [] if person is None else self.repo.cases_of(person.person_id)

    def events(self, case_id: int) -> list[CaseEvent]:
        with self.repo.transaction():
            return self.repo.events(case_id)

    def tasks(self, case_id: int) -> list[Task]:
        with self.repo.transaction():
            return self.repo.tasks(case_id)

    def consents(self, case_id: int) -> list[Consent]:
        with self.repo.transaction():
            return self.repo.consents(case_id)

    def intakes(self, case_id: int) -> list[Intake]:
        with self.repo.transaction():
            return self.repo.intakes(case_id)

    def referrals(self, case_id: int) -> list[Referral]:
        with self.repo.transaction():
            return self.repo.referrals(case_id)

    def outcomes(self, case_id: int) -> list[Outcome]:
        with self.repo.transaction():
            return self.repo.outcomes(case_id)

    def directory_entry(self, directory_entry_id: int) -> DirectoryEntry:
        with self.repo.transaction():
            запись = self.repo.get_directory_entry(directory_entry_id)
        if запись is None:
            raise NotFound(f"записи справочника {directory_entry_id} нет")
        return запись

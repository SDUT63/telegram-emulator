"""Тесты контракта модели обращения (docs/МОДЕЛЬ-ОБРАЩЕНИЯ.md, ред. 2).

Каждый тест идёт на обоих хранилищах: в памяти и в PostgreSQL (если
задан SDUT_DATABASE_URL). Правило, нарушенное в одном хранилище, —
дефект модуля, а не хранилища: у адаптеров своей логики нет.

Номер инварианта — в имени теста; соответствие правил и тестов —
docs/МОДЕЛЬ-ОБРАЩЕНИЯ-ШАГ-1.md, его сверяет test_cases_mapping.py.
"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

import pytest

from cases import (
    ASSIGNED, BOT, CLOSE_REASONS, CLOSED, CONTACTED, CONTROL, DRAFT, ESCALATED, MEDICAL_TRANSFER,
    NEW, NO_CONTACT, P0, PROCESSING, REFERRED, ROUTE_CONFIRMED, SERVICE_STARTED,
    STATUSES, SYSTEM, VITAL_INTEREST, WAITING_EXTERNAL, Case, CaseError, CaseService,
    ContractGap, ForbiddenTransition, ImmutableRecord, OpenCaseExists, allowed,
)
from cases_memory import MemoryCaseRepository

DSN = (os.getenv("SDUT_DATABASE_URL") or "").strip()
ОПЕРАТОР = "coordinator-1"

# Таблица 5.2, переписанная из контракта руками, — независимая сверка
# с cases.TRANSITIONS. ПО_РЕШЕНИЮ — причины 5.3 без системных и duplicate (Ч6).
ПО_РЕШЕНИЮ = ("help_received", "help_partial", "solved_otherwise", "consultation_enough",
              "refused", "not_eligible", "unable_to_contact")
ОТКРЫТЫЕ = [s for s in STATUSES if s != CLOSED]
ТАБЛИЦА_5_2 = {
    (DRAFT, NEW, None), (DRAFT, CLOSED, "abandoned_draft"),
    (NEW, ASSIGNED, None),
    (ASSIGNED, CONTACTED, None), (ASSIGNED, NO_CONTACT, None),
    (NO_CONTACT, CONTACTED, None), (NO_CONTACT, CLOSED, "unable_to_contact"),
    (CONTACTED, ROUTE_CONFIRMED, None),
    (ROUTE_CONFIRMED, REFERRED, None), (ROUTE_CONFIRMED, CLOSED, "consultation_enough"),
    (REFERRED, SERVICE_STARTED, None), (REFERRED, WAITING_EXTERNAL, None),
    (REFERRED, CLOSED, "refused"), (REFERRED, CLOSED, "not_eligible"),
    (WAITING_EXTERNAL, SERVICE_STARTED, None),
    *((WAITING_EXTERNAL, CLOSED, r) for r in ПО_РЕШЕНИЮ),
    (SERVICE_STARTED, CONTROL, None),
    *((CONTROL, CLOSED, r) for r in ПО_РЕШЕНИЮ),
    (CONTROL, REFERRED, None), (CONTROL, ROUTE_CONFIRMED, None), (CONTROL, WAITING_EXTERNAL, None),
    # общие правила
    *((s, ESCALATED, None) for s in ОТКРЫТЫЕ if s != ESCALATED),
    *((s, CLOSED, "duplicate") for s in ОТКРЫТЫЕ),
    (ESCALATED, NEW, None),          # эскалированное из NEW возвращается в NEW
}
# В таблице контракта есть, но правило не определено — пробелы Г1 и Г5
# (docs/МОДЕЛЬ-ОБРАЩЕНИЯ-ШАГ-1.md). Модуль отклоняет их с ContractGap.
ПРОБЕЛЫ = {(CONTROL, CONTROL, None): "Г1", (DRAFT, ESCALATED, None): "Г5"}

# Как довести обращение до статуса — каноническим путём.
ПУТИ = {
    DRAFT: [],
    NEW: [NEW],
    ASSIGNED: [NEW, ASSIGNED],
    CONTACTED: [NEW, ASSIGNED, CONTACTED],
    NO_CONTACT: [NEW, ASSIGNED, NO_CONTACT],
    ROUTE_CONFIRMED: [NEW, ASSIGNED, CONTACTED, ROUTE_CONFIRMED],
    REFERRED: [NEW, ASSIGNED, CONTACTED, ROUTE_CONFIRMED, REFERRED],
    WAITING_EXTERNAL: [NEW, ASSIGNED, CONTACTED, ROUTE_CONFIRMED, REFERRED, WAITING_EXTERNAL],
    SERVICE_STARTED: [NEW, ASSIGNED, CONTACTED, ROUTE_CONFIRMED, REFERRED, SERVICE_STARTED],
    CONTROL: [NEW, ASSIGNED, CONTACTED, ROUTE_CONFIRMED, REFERRED, SERVICE_STARTED, CONTROL],
    ESCALATED: [NEW, ESCALATED],
    CLOSED: [NEW, ASSIGNED, NO_CONTACT, CLOSED],
}


class Часы:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def сдвинуть(self, **сколько) -> None:
        self.now += timedelta(**сколько)


@dataclass
class Среда:
    s: CaseService
    часы: Часы
    люди: list = field(default_factory=list)
    ключи: list = field(default_factory=list)

    def человек(self) -> str:
        uid = str(900000000000 + uuid.uuid4().int % 99999999999)
        self.люди.append(uid)
        return uid

    def черновик(self, uid: str | None = None, версия: str = "1.0") -> Case:
        return self.s.open_draft("max", uid or self.человек(), consent_version=версия,
                                 consent_text_hash=f"hash-{версия}", questionnaire_version="q-1")

    def запись(self, route: str = "М2", **поля):
        ключ = f"test-{uuid.uuid4().hex}"
        self.ключи.append(ключ)
        return self.s.add_directory_entry(provider_key=ключ, route=route,
                                          provider=поля.pop("provider", "КЦСОН"),
                                          available=поля.pop("available", True), **поля)

    def данные(self, case: Case, to: str, reason: str | None = None) -> dict:
        """Всё, что нужно переходу, — чтобы отказ мог быть только по таблице."""
        d: dict = {"who": ОПЕРАТОР, "reason": reason}
        if to == NEW:
            d.update(trigger="checkpoint", who=BOT)
        elif to == ASSIGNED:
            d["assigned_to"] = ОПЕРАТОР
        elif to == ROUTE_CONFIRMED:
            d.update(final_route="М2", reason=reason or "по разговору с семьёй")
        elif to == REFERRED:
            d.update(directory_entry_id=self.запись().directory_entry_id, referral_channel="call")
        elif to == ESCALATED:
            d["reason"] = reason or "просрочка"
        elif to == CLOSED:
            if reason in ("abandoned_draft", "migrated"):
                d["who"] = SYSTEM
            if reason == "duplicate":
                d["duplicate_of"] = self.черновик().case_id
        return d

    def довести(self, case: Case, статус: str) -> Case:
        for to in ПУТИ[статус]:
            причина = "unable_to_contact" if to == CLOSED else None
            case = self.s.transition(case.case_id, to, **self.данные(case, to, причина))
        assert case.status == статус
        return case


@pytest.fixture(params=["память", "postgres"])
def среда(request):
    if request.param == "память":
        repo = MemoryCaseRepository()
    else:
        if not DSN:
            pytest.skip("SDUT_DATABASE_URL is not configured")
        from cases_postgres import PostgresCaseRepository
        repo = PostgresCaseRepository(DSN)
    часы = Часы()
    с = Среда(CaseService(repo, clock=часы), часы)
    yield с
    if request.param == "postgres":
        убрать_из_базы(с.люди, с.ключи)


def убрать_из_базы(люди: list, ключи: list) -> None:
    import psycopg
    with psycopg.connect(DSN) as conn:
        conn.execute("DELETE FROM cases WHERE person_id IN "
                     "(SELECT person_id FROM persons WHERE channel_user_id = ANY(%s))", (люди,))
        conn.execute("DELETE FROM persons WHERE channel_user_id = ANY(%s)", (люди,))
        conn.execute("DELETE FROM outcomes WHERE directory_entry_id IN "
                     "(SELECT directory_entry_id FROM route_directory WHERE provider_key = ANY(%s))", (ключи,))
        conn.execute("DELETE FROM route_directory WHERE provider_key = ANY(%s)", (ключи,))


def _события(среда: Среда, case: Case) -> list[str]:
    return [e.kind for e in среда.s.events(case.case_id)]


# --- И1 ---------------------------------------------------------------------

def test_и1_второе_открытое_обращение_не_создаётся(среда):
    uid = среда.человек()
    первое = среда.черновик(uid)
    второе = среда.черновик(uid)
    assert второе.case_id == первое.case_id
    with pytest.raises(OpenCaseExists):
        среда.s.open_by_operator("max", uid, who=ОПЕРАТОР, consent_version="1.0",
                                 consent_text_hash="h", questionnaire_version="q-1")
    assert len(среда.s.cases_of("max", uid)) == 1


def test_и1_новое_обращение_после_закрытия(среда):
    uid = среда.человек()
    первое = среда.довести(среда.черновик(uid, "1.0"), CLOSED)
    второе = среда.черновик(uid, "1.1")
    assert второе.case_id != первое.case_id
    assert второе.status == DRAFT
    # 7.6: новое обращение — с новым согласием; старое осталось при своём.
    assert [c.version for c in среда.s.consents(второе.case_id)] == ["1.1"]
    assert [c.version for c in среда.s.consents(первое.case_id)] == ["1.0"]


# --- И2 ---------------------------------------------------------------------

НОМЕР = re.compile(r"^SDUT-(\d{4})-(\d{5,})$")


def test_и2_номер_выдаётся_при_переходе_в_new(среда):
    case = среда.черновик()
    assert case.number is None and case.opened_at is None
    case = среда.s.transition(case.case_id, NEW, who=BOT, trigger="checkpoint")
    assert НОМЕР.match(case.number)
    assert case.opened_at == среда.часы.now
    переход = [e for e in среда.s.events(case.case_id) if e.kind == "status_changed"][-1]
    assert переход.payload["number"] == case.number
    # Ч2: черновик, закрытый без перехода в NEW, номера не получает.
    брошенный = среда.черновик()
    среда.часы.сдвинуть(days=31)
    assert брошенный.case_id in среда.s.close_abandoned_drafts()
    assert среда.s.case(брошенный.case_id).number is None


def test_и2_номера_идут_подряд_и_не_меняются(среда):
    номера = []
    for _ in range(3):
        case = среда.s.transition(среда.черновик().case_id, NEW, who=BOT, trigger="checkpoint")
        номера.append(int(НОМЕР.match(case.number).group(2)))
    assert номера == list(range(номера[0], номера[0] + 3))
    case = среда.s.transition(case.case_id, ASSIGNED, who=ОПЕРАТОР, assigned_to=ОПЕРАТОР)
    assert int(НОМЕР.match(case.number).group(2)) == номера[-1]
    with pytest.raises(ImmutableRecord):
        with среда.s.repo.transaction():
            среда.s.repo.update_case(replace(case, number="SDUT-2026-99999"))
    assert среда.s.case(case.case_id).number == case.number


def test_и2_год_номера_по_времени_самары(среда):
    среда.часы.now = datetime(2026, 12, 31, 19, 30, tzinfo=timezone.utc)   # 23:30 в Самаре
    case = среда.s.transition(среда.черновик().case_id, NEW, who=BOT, trigger="checkpoint")
    assert case.number.startswith("SDUT-2026-")
    среда.часы.now = datetime(2026, 12, 31, 20, 30, tzinfo=timezone.utc)   # 00:30, уже 2027
    case = среда.s.transition(среда.черновик().case_id, NEW, who=BOT, trigger="checkpoint")
    assert case.number.startswith("SDUT-2027-")


# --- И3 ---------------------------------------------------------------------

def test_и3_события_только_дописываются(среда):
    case = среда.черновик()
    было = среда.s.events(case.case_id)
    среда.довести(case, CONTACTED)
    стало = среда.s.events(case.case_id)
    assert стало[:len(было)] == было
    assert len(стало) > len(было)
    for имя in ("update_event", "delete_event", "delete_events"):
        assert not hasattr(среда.s.repo, имя), f"у хранилища не должно быть {имя} (И3)"


# --- И4 ---------------------------------------------------------------------

def test_и4_таблица_модуля_совпадает_с_контрактом():
    for frm in STATUSES:
        for to in STATUSES:
            for reason in ([None] if to != CLOSED else [None, *CLOSE_REASONS]):
                ожидается = (frm, to, reason) in ТАБЛИЦА_5_2 or (frm, to, reason) in ПРОБЕЛЫ
                назад = NEW if frm == ESCALATED else None
                assert allowed(frm, to, reason, back_to=назад) == ожидается, (frm, to, reason)


@pytest.mark.parametrize("frm,to,reason", sorted(ТАБЛИЦА_5_2 - set(ПРОБЕЛЫ), key=str))
def test_и4_разрешённые_переходы_выполняются(среда, frm, to, reason):
    case = среда.довести(среда.черновик(), frm)
    после = среда.s.transition(case.case_id, to, **среда.данные(case, to, reason))
    assert после.status == to
    assert после.close_reason == (reason if to == CLOSED else None)
    последнее = среда.s.events(case.case_id)
    переходы = [e for e in последнее if e.kind == "status_changed"]
    assert переходы[-1].payload["from"] == frm and переходы[-1].payload["to"] == to


@pytest.mark.parametrize("frm", STATUSES)
def test_и4_запрещённые_переходы_ничего_не_меняют(среда, frm):
    case = среда.довести(среда.черновик(), frm)
    было_событий = len(среда.s.events(case.case_id))
    for to in STATUSES:
        for reason in ([None] if to != CLOSED else [None, *CLOSE_REASONS]):
            if (frm, to, reason) in ТАБЛИЦА_5_2 or (frm, to, reason) in ПРОБЕЛЫ:
                continue
            данные = среда.данные(case, to, reason)
            with pytest.raises(ForbiddenTransition):
                среда.s.transition(case.case_id, to, **данные)
            assert среда.s.case(case.case_id) == case, (frm, to, reason)
    assert len(среда.s.events(case.case_id)) == было_событий


# --- И5 ---------------------------------------------------------------------

def _к_разговору(среда: Среда) -> Case:
    return среда.довести(среда.черновик(), CONTACTED)


def test_и5_предложенный_маршрут_записывается_один_раз(среда):
    case = _к_разговору(среда)
    case = среда.s.suggest_route(case.case_id, route="М2", reason="две сферы", signals=["self_care"],
                                 rules_version="routing-1")
    with pytest.raises(CaseError):
        среда.s.suggest_route(case.case_id, route="М1", reason="иначе", signals=["x"],
                              rules_version="routing-2")
    with pytest.raises(ImmutableRecord):
        with среда.s.repo.transaction():
            среда.s.repo.update_case(replace(case, suggested_route={"route": "М1"}))
    assert среда.s.case(case.case_id).suggested_route["route"] == "М2"


def test_и5_расхождение_с_ботом_требует_причины(среда):
    case = _к_разговору(среда)
    среда.s.suggest_route(case.case_id, route="М2", reason="две сферы", signals=["self_care"],
                          rules_version="routing-1")
    with pytest.raises(CaseError):
        среда.s.transition(case.case_id, ROUTE_CONFIRMED, who=ОПЕРАТОР, final_route="М1")
    assert среда.s.case(case.case_id).status == CONTACTED
    подтверждено = среда.s.transition(case.case_id, ROUTE_CONFIRMED, who=ОПЕРАТОР, final_route="М1",
                                      reason="после звонка: паллиативный статус")
    assert подтверждено.route_changed_by == ОПЕРАТОР
    assert подтверждено.route_change_reason == "после звонка: паллиативный статус"

    согласен = _к_разговору(среда)
    среда.s.suggest_route(согласен.case_id, route="М2", reason="две сферы", signals=["self_care"],
                          rules_version="routing-1")
    согласен = среда.s.transition(согласен.case_id, ROUTE_CONFIRMED, who=ОПЕРАТОР, final_route="М2")
    assert согласен.route_changed_by is None and согласен.route_change_reason is None

    # Ч11: бот ничего не предлагал — причина всё равно нужна.
    без_предложения = _к_разговору(среда)
    with pytest.raises(CaseError):
        среда.s.transition(без_предложения.case_id, ROUTE_CONFIRMED, who=ОПЕРАТОР, final_route="М2")


# --- И6 ---------------------------------------------------------------------

def test_и6_расчёт_по_правилам_хранит_версию_и_сигналы(среда):
    case = среда.черновик()
    with pytest.raises(CaseError):
        среда.s.suggest_route(case.case_id, route="М2", reason="r", signals=["a"], rules_version="")
    case = среда.s.suggest_route(case.case_id, route="М2", reason="две сферы",
                                 signals=["self_care", "mobility"], rules_version="routing-1",
                                 also=["М4"])
    снимок = case.suggested_route
    assert снимок["rules_version"] == "routing-1"
    assert снимок["signals"] == ["self_care", "mobility"]
    assert снимок["also"] == ["М4"]
    assert снимок["calculated_at"]

    with pytest.raises(CaseError):
        среда.s.set_priority(case.case_id, "P2", who=BOT, reason="М5")
    case = среда.s.set_priority(case.case_id, "P2", who=BOT, reason="М5",
                                rules_version="priority-draft", signals=["route:М5"])
    assert case.priority_rules_version == "priority-draft"
    событие = [e for e in среда.s.events(case.case_id) if e.kind == "priority_set"][-1]
    assert событие.payload["signals"] == ["route:М5"]
    # Ч10: вручную — с причиной и без версии правил.
    with pytest.raises(CaseError):
        среда.s.set_priority(case.case_id, "P1", who=ОПЕРАТОР, reason="r", rules_version="x")
    case = среда.s.set_priority(case.case_id, "P1", who=ОПЕРАТОР, reason="открытая рана со слов дочери")
    assert case.priority == "P1" and case.priority_rules_version is None


# --- И7 ---------------------------------------------------------------------

def test_и7_закрытие_только_с_допустимой_причиной(среда):
    case = среда.довести(среда.черновик(), NO_CONTACT)
    with pytest.raises(ForbiddenTransition):
        среда.s.transition(case.case_id, CLOSED, who=ОПЕРАТОР)
    with pytest.raises(ForbiddenTransition):
        среда.s.transition(case.case_id, CLOSED, who=ОПЕРАТОР, reason="help_received")
    with pytest.raises(CaseError):
        среда.s.transition(case.case_id, CLOSED, who=SYSTEM, reason="unable_to_contact")
    закрыто = среда.s.transition(case.case_id, CLOSED, who=ОПЕРАТОР, reason="unable_to_contact")
    assert закрыто.close_reason == "unable_to_contact" and закрыто.closed_at == среда.часы.now

    черновик = среда.черновик()
    with pytest.raises(CaseError):
        среда.s.transition(черновик.case_id, CLOSED, who=ОПЕРАТОР, reason="abandoned_draft")


def test_и7_автоматически_закрывается_только_черновик(среда):
    старый = среда.черновик()
    рабочее = среда.s.transition(среда.черновик().case_id, NEW, who=BOT, trigger="checkpoint")
    среда.часы.сдвинуть(days=2)
    молодой = среда.черновик()
    среда.часы.сдвинуть(days=29)          # старому 31 день, молодому 29
    with pytest.raises(CaseError):
        среда.s.transition(рабочее.case_id, CLOSED, who=SYSTEM, reason="abandoned_draft")
    закрытые = среда.s.close_abandoned_drafts()
    assert старый.case_id in закрытые
    assert молодой.case_id not in закрытые and рабочее.case_id not in закрытые
    assert среда.s.case(старый.case_id).close_reason == "abandoned_draft"
    assert среда.s.case(молодой.case_id).status == DRAFT
    assert среда.s.case(рабочее.case_id).status == NEW


# --- И8 ---------------------------------------------------------------------

def _в_контроле(среда: Среда, начало_дней_назад: int = 0) -> Case:
    case = среда.довести(среда.черновик(), REFERRED)
    начало = среда.часы.now - timedelta(days=начало_дней_назад)
    среда.s.transition(case.case_id, SERVICE_STARTED, who=ОПЕРАТОР, service_started_at=начало)
    return среда.s.transition(case.case_id, CONTROL, who=ОПЕРАТОР)


def _задача(среда: Среда, case: Case, kind: str):
    return next(t for t in среда.s.tasks(case.case_id) if t.kind == kind)


def test_и8_контрольные_задачи_от_начала_помощи(среда):
    case = _в_контроле(среда, начало_дней_назад=3)
    начало = case.service_started_at
    сроки = {t.kind: t.due_at for t in среда.s.tasks(case.case_id)}
    assert сроки == {"control_d7": начало + timedelta(days=7), "control_d30": начало + timedelta(days=30)}
    assert all(t.assigned_to == ОПЕРАТОР for t in среда.s.tasks(case.case_id))   # Ч12
    for вид in ("control_d7", "control_d30"):
        with pytest.raises(CaseError):
            среда.s.create_task(case.case_id, вид, due_at=среда.часы.now, who=ОПЕРАТОР)


def test_и8_итог_контроля_не_меняет_статус(среда):
    case = _в_контроле(среда)
    среда.s.complete_task(_задача(среда, case, "control_d7").task_id, "ongoing", who=ОПЕРАТОР)
    assert среда.s.case(case.case_id).status == CONTROL
    среда.s.complete_task(_задача(среда, case, "control_d30").task_id, "received", who=ОПЕРАТОР)
    assert среда.s.case(case.case_id).status == CONTROL
    закрыто = среда.s.transition(case.case_id, CLOSED, who=ОПЕРАТОР, reason="help_received")
    assert закрыто.status == CLOSED


def test_и8_д7_не_пришёл_сразу_создаёт_задачу(среда):
    case = _в_контроле(среда)
    д7 = _задача(среда, case, "control_d7")
    with pytest.raises(CaseError):
        среда.s.complete_task(д7.task_id, "provider_no_show", who=ОПЕРАТОР)
    assert _задача(среда, case, "control_d7").status == "open"
    срок = среда.часы.now + timedelta(days=1)
    сделано = среда.s.complete_task(д7.task_id, "provider_no_show", who=ОПЕРАТОР,
                                    follow_up_kind="referral_followup", follow_up_due_at=срок)
    assert сделано.status == "done" and сделано.result == "provider_no_show"
    последствие = _задача(среда, case, "referral_followup")
    assert последствие.status == "open" and последствие.due_at == срок
    assert среда.s.case(case.case_id).status == CONTROL
    # Последствие — только по итогу Д+7 «ждём начала» / «не пришёл».
    with pytest.raises(CaseError):
        среда.s.complete_task(_задача(среда, case, "control_d30").task_id, "received", who=ОПЕРАТОР,
                              follow_up_kind="escalation", follow_up_due_at=срок)


# --- И9 ---------------------------------------------------------------------

def test_и9_задача_без_срока_не_создаётся(среда):
    case = среда.довести(среда.черновик(), ASSIGNED)
    with pytest.raises(CaseError):
        среда.s.create_task(case.case_id, "first_contact", due_at=None, who=ОПЕРАТОР)
    with pytest.raises(CaseError):
        среда.s.create_task(case.case_id, "first_contact", due_at=datetime(2026, 10, 2, 9),
                            who=ОПЕРАТОР)
    задача = среда.s.create_task(case.case_id, "first_contact",
                                 due_at=среда.часы.now + timedelta(hours=4), who=ОПЕРАТОР)
    assert задача.due_at == среда.часы.now + timedelta(hours=4)
    assert среда.s.tasks(case.case_id) == [задача]


# --- И10 --------------------------------------------------------------------

def test_и10_до_согласия_обращения_нет(среда):
    uid = среда.человек()
    with pytest.raises(CaseError):
        среда.s.open_draft("max", uid, consent_version="", consent_text_hash="h",
                           questionnaire_version="q-1")
    assert среда.s.cases_of("max", uid) == []
    # В обход модуля обращение на основании согласия без снимка не фиксируется.
    repo = среда.s.repo
    with pytest.raises(CaseError):
        with repo.transaction():
            person = repo.lock_person("max", uid, среда.часы.now)
            repo.insert_case(Case(None, person.person_id, "bot", "consent", DRAFT, среда.часы.now))
    assert среда.s.cases_of("max", uid) == []


def test_и10_режим_б_экстренное_обращение(среда):
    uid = среда.человек()
    case = среда.s.open_emergency("max", uid, sign_group="не дышит", questionnaire_version="q-1")
    assert case.status == NEW and НОМЕР.match(case.number)
    assert case.urgency == P0 and case.legal_basis == VITAL_INTEREST
    анкета, = среда.s.intakes(case.case_id)
    assert анкета.alerts == ["не дышит"] and анкета.answers == {}     # Ч8
    assert среда.s.consents(case.case_id) == []
    # Человек потом согласился — то же обращение, основание — согласие.
    после = среда.черновик(uid)
    assert после.case_id == case.case_id
    assert после.legal_basis == "consent" and после.number == case.number
    assert [c.kind for c in среда.s.consents(case.case_id)] == [PROCESSING]
    assert "legal_basis_changed" in _события(среда, case)


# --- И11 --------------------------------------------------------------------

def test_и11_снимок_согласия_на_каждом_обращении(среда):
    uid = среда.человек()
    первое = среда.черновик(uid, "1.0")
    снимок, = среда.s.consents(первое.case_id)
    assert (снимок.kind, снимок.version, снимок.text_hash, снимок.given_via) == \
        (PROCESSING, "1.0", "hash-1.0", "bot")
    assert снимок.given_at == среда.часы.now and снимок.withdrawn_at is None
    среда.довести(первое, CLOSED)
    второе = среда.черновик(uid, "1.0")
    assert [c.kind for c in среда.s.consents(второе.case_id)] == [PROCESSING]
    with pytest.raises(CaseError):
        среда.s.add_consent(второе.case_id, kind=PROCESSING, version="1.0", text_hash="h",
                            given_via="phone", who=ОПЕРАТОР)                   # Ч9


def test_и11_новый_текст_согласия_не_меняет_старые_снимки(среда):
    uid = среда.человек()
    старое = среда.довести(среда.черновик(uid, "1.0"), CLOSED)
    новое = среда.черновик(uid, "1.1")
    среда.s.add_consent(новое.case_id, kind=PROCESSING, version="1.2", text_hash="hash-1.2",
                        given_via="bot", who=BOT)
    assert [(c.version, c.text_hash) for c in среда.s.consents(старое.case_id)] == [("1.0", "hash-1.0")]
    assert [c.version for c in среда.s.consents(новое.case_id)] == ["1.1", "1.2"]


def test_и11_отзыв_медицинского_согласия_не_закрывает_обращение(среда):
    case = среда.довести(среда.черновик(), ASSIGNED)
    среда.s.add_consent(case.case_id, kind=MEDICAL_TRANSFER, version="форма-3", text_hash=None,
                        given_via="paper", who=ОПЕРАТОР)
    среда.часы.сдвинуть(hours=1)
    среда.s.withdraw_medical_consent(case.case_id, who=ОПЕРАТОР)
    по_видам = {c.kind: c for c in среда.s.consents(case.case_id)}
    assert по_видам[MEDICAL_TRANSFER].withdrawn_at == среда.часы.now
    assert по_видам[PROCESSING].withdrawn_at is None
    assert среда.s.case(case.case_id).status == ASSIGNED
    with pytest.raises(CaseError):
        среда.s.withdraw_medical_consent(case.case_id, who=ОПЕРАТОР)
    with pytest.raises(ImmutableRecord):
        with среда.s.repo.transaction():
            среда.s.repo.set_consent_withdrawn(по_видам[MEDICAL_TRANSFER].consent_id, среда.часы.now)


# --- И12 --------------------------------------------------------------------

def test_и12_версия_справочника_не_меняется(среда):
    первая = среда.запись(phone="8 8482 00-00-01", hours="пн–пт 9–17")
    case = среда.довести(среда.черновик(), ROUTE_CONFIRMED)
    среда.s.transition(case.case_id, REFERRED, who=ОПЕРАТОР,
                       directory_entry_id=первая.directory_entry_id, referral_channel="call")
    среда.часы.сдвинуть(days=90)
    вторая = среда.s.replace_directory_entry(первая.directory_entry_id, phone="8 8482 00-00-02")
    старая = среда.s.directory_entry(первая.directory_entry_id)
    assert старая.phone == "8 8482 00-00-01" and старая.valid_to == среда.часы.now
    assert вторая.phone == "8 8482 00-00-02" and вторая.provider_key == первая.provider_key
    assert вторая.hours == "пн–пт 9–17" and вторая.valid_to is None
    направление, = среда.s.referrals(case.case_id)
    assert направление.directory_entry_id == первая.directory_entry_id
    with pytest.raises(CaseError):
        среда.s.replace_directory_entry(первая.directory_entry_id, phone="другой")
    with pytest.raises(CaseError):
        среда.s.replace_directory_entry(вторая.directory_entry_id, provider_key="чужой")
    другое = среда.довести(среда.черновик(), ROUTE_CONFIRMED)
    with pytest.raises(CaseError):
        среда.s.transition(другое.case_id, REFERRED, who=ОПЕРАТОР,
                           directory_entry_id=первая.directory_entry_id, referral_channel="call")
    with pytest.raises(CaseError):
        среда.запись(route="М3", available=False)          # 4.5: без fallback нельзя
    м3 = среда.запись(route="М3", available=False,
                      fallback="старшему координатору для ручной маршрутизации")
    assert м3.available is False


# --- И13 --------------------------------------------------------------------

def test_и13_реконструкция_не_попадает_в_предложенный_маршрут(среда):
    case = среда.черновик()
    среда.s.record_route_reconstruction(case.case_id, route="М2", reason="две сферы",
                                        signals=["self_care"], rules_version="routing-1")
    assert среда.s.case(case.case_id).suggested_route is None
    событие = [e for e in среда.s.events(case.case_id) if e.kind == "route_reconstructed"][-1]
    assert событие.payload["route"] == "М2" and событие.payload["rules_version"] == "routing-1"
    with pytest.raises(CaseError):
        среда.s.suggest_route(case.case_id, route="М2", reason="r", signals=["a"],
                              rules_version="migration")
    assert среда.s.case(case.case_id).suggested_route is None


# --- И14 --------------------------------------------------------------------

def test_и14_удаление_стирает_все_обращения_человека(среда):
    uid = среда.человек()
    закрытое = среда.довести(среда.черновик(uid), CLOSED)
    case = _в_контроле_для(среда, uid)
    среда.s.record_outcome(case.case_id, need="home_social_service", action="referral",
                           result="received", who=ОПЕРАТОР)
    среда.s.update_intake(case.case_id, who=BOT, story="мама после инсульта, лежит второй год")
    чужое = среда.довести(среда.черновик(), ASSIGNED)
    чужие_события = среда.s.events(чужое.case_id)

    assert среда.s.withdraw_processing_consent("max", uid, who=ОПЕРАТОР) is True

    assert среда.s.cases_of("max", uid) == []
    for case_id in (закрытое.case_id, case.case_id):
        for чтение in (среда.s.consents, среда.s.intakes, среда.s.events, среда.s.tasks,
                       среда.s.referrals, среда.s.outcomes):
            assert чтение(case_id) == [], чтение.__name__
    with среда.s.repo.transaction():
        отметка = среда.s.repo.find_person("max", uid)
    assert отметка.deleted_at == среда.часы.now and отметка.first_seen_at is None   # Ч1
    assert среда.s.events(чужое.case_id) == чужие_события

    вернулся = среда.черновик(uid)
    assert вернулся.status == DRAFT
    with среда.s.repo.transaction():
        assert среда.s.repo.find_person("max", uid).deleted_at is None


def _в_контроле_для(среда: Среда, uid: str) -> Case:
    case = среда.довести(среда.черновик(uid), REFERRED)
    среда.s.transition(case.case_id, SERVICE_STARTED, who=ОПЕРАТОР)
    return среда.s.transition(case.case_id, CONTROL, who=ОПЕРАТОР)


# --- Прочие правила раздела 5 и 7 -------------------------------------------

def test_повторное_заново_даёт_новую_версию_анкеты(среда):
    uid = среда.человек()
    case = среда.черновик(uid)
    среда.s.update_intake(case.case_id, who=BOT, answers={"who": "О близком человеке"})
    вторая = среда.s.new_intake_version(case.case_id, who=BOT, questionnaire_version="q-1",
                                        answers={"who": "О себе"})
    assert вторая.version == 2
    среда.s.update_intake(case.case_id, who=BOT, answers={"who": "О себе", "name": "Анна"})
    первая, вторая = среда.s.intakes(case.case_id)
    assert первая.answers == {"who": "О близком человеке"}
    assert вторая.answers == {"who": "О себе", "name": "Анна"}
    assert среда.черновик(uid).case_id == case.case_id         # «заново» — не новое обращение
    assert len(среда.s.cases_of("max", uid)) == 1
    with pytest.raises(CaseError):
        среда.s.update_intake(case.case_id, who=BOT, story="я" * 4001)
    assert "intake_version" in _события(среда, case)


def test_эскалация_возвращает_в_прежний_статус(среда):
    case = среда.довести(среда.черновик(), ESCALATED)
    assert case.status_before_escalation == NEW
    with pytest.raises(ForbiddenTransition):
        среда.s.transition(case.case_id, ASSIGNED, who=ОПЕРАТОР, assigned_to=ОПЕРАТОР)
    with pytest.raises(ForbiddenTransition):
        среда.s.transition(case.case_id, ESCALATED, who=ОПЕРАТОР, reason="ещё раз")
    назад = среда.s.transition(case.case_id, NEW, who=ОПЕРАТОР)
    assert назад.status == NEW and назад.status_before_escalation is None
    with pytest.raises(CaseError):
        среда.s.transition(назад.case_id, ESCALATED, who=ОПЕРАТОР)      # без причины


def test_дубликат_закрывается_со_ссылкой(среда):
    основное = среда.довести(среда.черновик(), ASSIGNED)
    повтор = среда.довести(среда.черновик(), NEW)
    with pytest.raises(CaseError):
        среда.s.transition(повтор.case_id, CLOSED, who=ОПЕРАТОР, reason="duplicate")
    with pytest.raises(CaseError):
        среда.s.transition(повтор.case_id, CLOSED, who=ОПЕРАТОР, reason="duplicate",
                           duplicate_of=повтор.case_id)
    закрыто = среда.s.transition(повтор.case_id, CLOSED, who=ОПЕРАТОР, reason="duplicate",
                                 duplicate_of=основное.case_id)
    assert закрыто.close_reason == "duplicate" and закрыто.duplicate_of == основное.case_id


def test_г1_продление_контроля_ждёт_редакции_3(среда):
    case = _в_контроле(среда)
    with pytest.raises(ContractGap, match="Г1"):
        среда.s.transition(case.case_id, CONTROL, who=ОПЕРАТОР, reason="помощь идёт, позвонить ещё")
    assert среда.s.case(case.case_id) == case


def test_г5_эскалация_черновика_ждёт_редакции_3(среда):
    case = среда.черновик()
    with pytest.raises(ContractGap, match="Г5"):
        среда.s.transition(case.case_id, ESCALATED, who=ОПЕРАТОР, reason="просрочка")
    assert среда.s.case(case.case_id) == case

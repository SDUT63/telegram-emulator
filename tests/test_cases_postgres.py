"""Модель обращения на настоящем PostgreSQL: то, что в памяти не проверить.

Гонки одновременных транзакций, ограничения и триггеры миграции 014 в
обход модуля обращений, общая транзакция с событием MAX (И15).
"""
from __future__ import annotations

import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

psycopg = pytest.importorskip("psycopg")

from cases import BOT, CaseService, NEW, SAMARA  # noqa: E402

DSN = (os.getenv("SDUT_DATABASE_URL") or "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="SDUT_DATABASE_URL is not configured")


@pytest.fixture
def люди():
    созданные: list[str] = []

    def новый() -> str:
        uid = str(900000000000 + uuid.uuid4().int % 99999999999)
        созданные.append(uid)
        return uid

    yield новый
    with psycopg.connect(DSN) as conn:
        conn.execute("DELETE FROM cases WHERE person_id IN "
                     "(SELECT person_id FROM persons WHERE channel_user_id = ANY(%s))", (созданные,))
        conn.execute("DELETE FROM persons WHERE channel_user_id = ANY(%s)", (созданные,))
        for uid in созданные:
            for таблица in ("outbox_messages", "audit_events", "processed_events", "survey_state"):
                conn.execute(f"DELETE FROM {таблица} WHERE user_id = %s", (uid,))


def _сервис() -> CaseService:
    from cases_postgres import PostgresCaseRepository
    return CaseService(PostgresCaseRepository(DSN))


def _черновик(s: CaseService, uid: str):
    return s.open_draft("max", uid, consent_version="1.0", consent_text_hash="h",
                        questionnaire_version="q-1")


def _одновременно(сколько: int, работа) -> list:
    """Запустить работу в потоках так, чтобы они стартовали в один момент."""
    барьер = threading.Barrier(сколько)
    итоги: list = [None] * сколько
    ошибки: list = []

    def поток(i: int) -> None:
        try:
            барьер.wait()
            итоги[i] = работа(i)
        except BaseException as exc:      # pragma: no cover — видно в ошибке ниже
            ошибки.append(exc)

    потоки = [threading.Thread(target=поток, args=(i,)) for i in range(сколько)]
    for п in потоки:
        п.start()
    for п in потоки:
        п.join()
    assert not ошибки, ошибки
    return итоги


# --- И1, И2: гонки ------------------------------------------------------------

def test_и1_гонка_двух_согласий_даёт_одно_обращение(люди):
    uid = люди()
    итоги = _одновременно(4, lambda _: _черновик(_сервис(), uid).case_id)
    assert len(set(итоги)) == 1
    assert len(_сервис().cases_of("max", uid)) == 1


def test_и2_одновременные_переходы_без_повторов_и_пропусков(люди):
    s = _сервис()
    черновики = [_черновик(s, люди()) for _ in range(12)]
    год = datetime.now(timezone.utc).astimezone(SAMARA).year
    with psycopg.connect(DSN) as conn:
        row = conn.execute("SELECT last_value FROM case_number_counters WHERE year = %s", (год,)).fetchone()
    до = row[0] if row else 0
    итоги = _одновременно(len(черновики), lambda i: _сервис().transition(
        черновики[i].case_id, NEW, who=BOT, trigger="checkpoint").number)
    номера = sorted(int(re.match(rf"^SDUT-{год}-(\d{{5,}})$", n).group(1)) for n in итоги)
    assert номера == list(range(до + 1, до + 1 + len(черновики)))


# --- Ограничения и триггеры 014 в обход модуля --------------------------------

def _сырой(запрос: str, параметры: tuple = ()) -> None:
    with psycopg.connect(DSN) as conn:
        conn.execute(запрос, параметры)


def test_и3_база_не_даёт_изменить_событие(люди):
    case = _черновик(_сервис(), люди())
    with pytest.raises(psycopg.errors.RaiseException, match="И3"):
        _сырой("UPDATE case_events SET kind = 'подмена' WHERE case_id = %s", (case.case_id,))
    with pytest.raises(psycopg.errors.RaiseException, match="И3"):
        _сырой("DELETE FROM case_events WHERE case_id = %s", (case.case_id,))
    # Вместе с обращением — можно: так работает удаление по просьбе.
    _сырой("DELETE FROM cases WHERE case_id = %s", (case.case_id,))
    with psycopg.connect(DSN) as conn:
        assert conn.execute("SELECT count(*) FROM case_events WHERE case_id = %s",
                            (case.case_id,)).fetchone()[0] == 0


def test_и5_база_не_даёт_переписать_предложенный_маршрут(люди):
    s = _сервис()
    case = s.suggest_route(_черновик(s, люди()).case_id, route="М2", reason="две сферы",
                           signals=["self_care"], rules_version="routing-1")
    with pytest.raises(psycopg.errors.RaiseException, match="И5"):
        _сырой("UPDATE cases SET suggested_route = '{\"route\": \"М1\"}' WHERE case_id = %s",
               (case.case_id,))
    case = s.transition(case.case_id, NEW, who=BOT, trigger="checkpoint")
    with pytest.raises(psycopg.errors.RaiseException, match="И2"):
        _сырой("UPDATE cases SET number = 'SDUT-2026-99999' WHERE case_id = %s", (case.case_id,))


def test_и7_база_не_даёт_закрыть_без_причины(люди):
    case = _черновик(_сервис(), люди())
    with pytest.raises(psycopg.errors.CheckViolation, match="cases_closed_has_reason_ck"):
        _сырой("UPDATE cases SET status = 'CLOSED' WHERE case_id = %s", (case.case_id,))
    with pytest.raises(psycopg.errors.CheckViolation, match="cases_working_has_number_ck"):
        _сырой("UPDATE cases SET status = 'ASSIGNED' WHERE case_id = %s", (case.case_id,))


def test_и11_база_не_даёт_обращение_без_согласия(люди):
    uid = люди()
    with pytest.raises(psycopg.errors.RaiseException, match="И11"):
        with psycopg.connect(DSN) as conn:
            person_id = conn.execute(
                "INSERT INTO persons(channel, channel_user_id, first_seen_at) "
                "VALUES ('max', %s, now()) RETURNING person_id", (uid,)).fetchone()[0]
            conn.execute("INSERT INTO cases(person_id, source, legal_basis, status, created_at) "
                         "VALUES (%s, 'bot', 'consent', 'DRAFT', now())", (person_id,))
    case = _черновик(_сервис(), uid)
    with pytest.raises(psycopg.errors.RaiseException, match="И11"):
        _сырой("UPDATE case_consents SET version = '9.9' WHERE case_id = %s", (case.case_id,))
    with pytest.raises(psycopg.errors.RaiseException, match="И11"):
        _сырой("DELETE FROM case_consents WHERE case_id = %s", (case.case_id,))


def test_и12_база_не_даёт_изменить_версию_справочника():
    s = _сервис()
    ключ = f"test-{uuid.uuid4().hex}"
    запись = s.add_directory_entry(provider_key=ключ, route="М2", provider="КЦСОН", available=True,
                                   phone="8 8482 00-00-01")
    try:
        with pytest.raises(psycopg.errors.RaiseException, match="И12"):
            _сырой("UPDATE route_directory SET phone = 'другой' WHERE directory_entry_id = %s",
                   (запись.directory_entry_id,))
        _сырой("UPDATE route_directory SET valid_to = now() WHERE directory_entry_id = %s",
               (запись.directory_entry_id,))
        with pytest.raises(psycopg.errors.RaiseException, match="И12"):
            _сырой("UPDATE route_directory SET valid_to = now() + interval '1 day' "
                   "WHERE directory_entry_id = %s", (запись.directory_entry_id,))
    finally:
        _сырой("DELETE FROM route_directory WHERE provider_key = %s", (ключ,))


# --- И14 ------------------------------------------------------------------------

def test_и14_все_таблицы_с_данными_человека_учтены(люди):
    from cases_memory import PERSON_DATA_TABLES as В_ПАМЯТИ
    from cases_postgres import PERSON_DATA_TABLES

    with psycopg.connect(DSN) as conn:
        в_базе = {r[0] for r in conn.execute(
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND column_name IN ('case_id', 'person_id')")}
    assert в_базе == set(PERSON_DATA_TABLES), (
        "новая таблица с данными человека не внесена в PERSON_DATA_TABLES — "
        "удаление по просьбе её не проверяет")
    assert set(В_ПАМЯТИ) == set(PERSON_DATA_TABLES)


def test_и14_удаление_проходит_по_каждой_таблице(люди):
    """Т1: до удаления в каждой таблице с данными человека есть его строка,
    после — ни одной. Иначе проверка «пусто после удаления» ничего не
    доказывает: таблица могла быть пустой с самого начала."""
    from cases import (ASSIGNED, CONTACTED, CONTROL, MEDICAL_TRANSFER, REFERRED,
                       ROUTE_CONFIRMED, SERVICE_STARTED)
    from cases_postgres import PERSON_DATA_TABLES

    s = _сервис()
    uid = люди()
    ключ = f"test-{uuid.uuid4().hex}"
    запись = s.add_directory_entry(provider_key=ключ, route="М2", provider="КЦСОН", available=True)
    try:
        case = s.transition(_черновик(s, uid).case_id, NEW, who=BOT, trigger="checkpoint")
        s.add_consent(case.case_id, kind=MEDICAL_TRANSFER, version="форма-3", text_hash=None,
                      given_via="paper", who="op")
        s.update_intake(case.case_id, who=BOT, answers={"who": "О близком человеке"},
                        story="мама после инсульта, лежит второй год")
        s.transition(case.case_id, ASSIGNED, who="op", assigned_to="op")
        s.transition(case.case_id, CONTACTED, who="op")
        s.transition(case.case_id, ROUTE_CONFIRMED, who="op", final_route="М2", reason="по разговору")
        s.transition(case.case_id, REFERRED, who="op", directory_entry_id=запись.directory_entry_id,
                     referral_channel="call")
        s.transition(case.case_id, SERVICE_STARTED, who="op")
        s.transition(case.case_id, CONTROL, who="op")
        s.record_outcome(case.case_id, need="home_social_service", action="referral",
                         result="received", who="op", directory_entry_id=запись.directory_entry_id)

        def строк(conn, таблица: str) -> int:
            if таблица == "persons":
                запрос = ("SELECT count(*) FROM persons WHERE channel_user_id = %s "
                          "AND deleted_at IS NULL")
                return conn.execute(запрос, (uid,)).fetchone()[0]
            return conn.execute(f"SELECT count(*) FROM {таблица} WHERE case_id = %s",
                                (case.case_id,)).fetchone()[0]

        with psycopg.connect(DSN) as conn:
            до = {т: строк(conn, т) for т in PERSON_DATA_TABLES}
        assert all(до.values()), f"тест обходит таблицу: {до}"

        assert s.delete_person("max", uid, who="op") is True

        with psycopg.connect(DSN) as conn:
            после = {т: строк(conn, т) for т in PERSON_DATA_TABLES}
            отметка = conn.execute("SELECT first_seen_at, deleted_at FROM persons "
                                   "WHERE channel_user_id = %s", (uid,)).fetchone()
            справочник = conn.execute("SELECT count(*) FROM route_directory WHERE directory_entry_id = %s",
                                      (запись.directory_entry_id,)).fetchone()[0]
        assert после == {т: 0 for т in PERSON_DATA_TABLES}
        assert отметка[0] is None and отметка[1] is not None        # Ч1
        assert справочник == 1          # справочник исполнителей — не данные человека
    finally:
        _сырой("DELETE FROM cases WHERE person_id IN "
               "(SELECT person_id FROM persons WHERE channel_user_id = %s)", (uid,))
        _сырой("DELETE FROM route_directory WHERE provider_key = %s", (ключ,))


# --- И15: одна транзакция с событием MAX ---------------------------------------

def _событие(survey, uid: str, event_id: str, работа):
    from storage_postgres import _TX_EVENT
    token = _TX_EVENT.set(event_id)
    try:
        with survey._atomic(uid, "callback", {"action": "grant_consent"}) as принято:
            if принято:
                работа()
            return принято
    finally:
        _TX_EVENT.reset(token)


def test_и15_повтор_события_ничего_не_меняет(люди):
    from storage_postgres import TransactionalPostgresSurvey
    survey = TransactionalPostgresSurvey(DSN)
    s = _сервис()
    uid = люди()
    event_id = f"cases-{uuid.uuid4().hex}"
    assert _событие(survey, uid, event_id, lambda: _черновик(s, uid)) is True
    было = s.cases_of("max", uid)
    assert len(было) == 1
    события = s.events(было[0].case_id)
    # Повторная доставка того же события: работа не должна выполниться —
    # иначе черновик стал бы NEW и получил номер.
    assert _событие(survey, uid, event_id, lambda: s.transition(
        было[0].case_id, NEW, who=BOT, trigger="checkpoint")) is False
    assert s.cases_of("max", uid) == было
    assert s.events(было[0].case_id) == события


def test_и15_откат_события_откатывает_обращение(люди):
    from storage_postgres import TransactionalPostgresSurvey
    survey = TransactionalPostgresSurvey(DSN)
    s = _сервис()
    uid = люди()

    def сломаться() -> None:
        _черновик(s, uid)
        raise RuntimeError("ошибка после создания обращения")

    with pytest.raises(RuntimeError):
        _событие(survey, uid, f"cases-{uuid.uuid4().hex}", сломаться)
    assert s.cases_of("max", uid) == []
    with psycopg.connect(DSN) as conn:
        assert conn.execute("SELECT count(*) FROM persons WHERE channel_user_id = %s",
                            (uid,)).fetchone()[0] == 0


# --- Редакция 3: ограничения миграции 015 и гонки И16 ---------------------------

def _к_разговору(s: CaseService, uid: str):
    case = s.transition(_черновик(s, uid).case_id, NEW, who=BOT, trigger="checkpoint")
    s.transition(case.case_id, "ASSIGNED", who="op", assigned_to="op")
    return s.transition(case.case_id, "CONTACTED", who="op")


_НОВАЯ_ЗАДАЧА = ("INSERT INTO tasks(case_id, kind, due_at, status, created_at) "
                 "VALUES (%s, 'first_contact', now() + interval '1 hour', 'open', now())")


def test_и16_база_не_даёт_закрыть_с_открытой_задачей(люди):
    s = _сервис()
    case = _к_разговору(s, люди())
    s.create_task(case.case_id, "first_contact",
                  due_at=datetime.now(timezone.utc) + timedelta(hours=1), who="op")
    with pytest.raises(psycopg.errors.RaiseException, match="И16"):
        _сырой("UPDATE cases SET status = 'CLOSED', close_reason = 'refused', closed_at = now() "
               "WHERE case_id = %s", (case.case_id,))
    with pytest.raises(psycopg.errors.RaiseException, match="И16"):
        _сырой("INSERT INTO tasks(case_id, kind, due_at, status, created_at) VALUES "
               "(%s, 'control_extra', now() + interval '1 day', 'open', now())", (case.case_id,))
    assert s.case(case.case_id).status == "CONTACTED"
    assert [t.kind for t in s.tasks(case.case_id)] == ["first_contact"]


def test_и7_база_держит_правила_закрытия_редакции_3(люди):
    s = _сервис()
    обычное = _к_разговору(s, люди())
    закрыть = ("UPDATE cases SET status = 'CLOSED', close_reason = %s, closed_at = now() "
               "WHERE case_id = %s")
    with pytest.raises(psycopg.errors.CheckViolation, match="cases_consent_not_given_ck"):
        _сырой(закрыть, ("consent_not_given", обычное.case_id))
    экстренное = s.open_emergency("max", люди(), sign_group="не дышит", questionnaire_version="q-1")
    with pytest.raises(psycopg.errors.RaiseException, match="И7"):      # из NEW, не из CONTACTED
        _сырой(закрыть, ("consent_not_given", экстренное.case_id))
    черновик = _черновик(s, люди())
    with pytest.raises(psycopg.errors.RaiseException, match="Г5"):
        _сырой("UPDATE cases SET status = 'CLOSED', close_reason = 'duplicate', duplicate_of = %s, "
               "closed_at = now() WHERE case_id = %s", (обычное.case_id, черновик.case_id))
    with pytest.raises(psycopg.errors.RaiseException, match="Г5"):
        _сырой(закрыть, ("ward_died", черновик.case_id))
    with pytest.raises(psycopg.errors.CheckViolation, match="tasks_kind_ck"):
        _сырой("INSERT INTO tasks(case_id, kind, due_at, status, created_at) VALUES "
               "(%s, 'control_xx', now(), 'open', now())", (обычное.case_id,))
    assert {s.case(c.case_id).status for c in (обычное, экстренное, черновик)} == \
        {"CONTACTED", "NEW", "DRAFT"}


def test_и16_задача_до_закрытия_отменяется_закрытием(люди):
    """Гонка 1: задачу вставили в обход модуля и ещё не зафиксировали.
    Закрытие ждёт блокировку строки обращения, а потом отменяет и её."""
    s = _сервис()
    case = _к_разговору(s, люди())
    вставлена, можно = threading.Event(), threading.Event()
    ошибки: list = []

    def вставить() -> None:
        try:
            with psycopg.connect(DSN) as conn:
                conn.execute(_НОВАЯ_ЗАДАЧА, (case.case_id,))
                вставлена.set()
                можно.wait(10)
        except BaseException as exc:              # pragma: no cover — видно в ошибке ниже
            ошибки.append(exc)
            вставлена.set()

    def закрыть() -> None:
        try:
            _сервис().transition(case.case_id, "CLOSED", who="op", reason="refused")
        except BaseException as exc:              # pragma: no cover
            ошибки.append(exc)

    первый = threading.Thread(target=вставить)
    первый.start()
    assert вставлена.wait(10)
    второй = threading.Thread(target=закрыть)
    второй.start()
    второй.join(0.5)
    assert второй.is_alive(), "закрытие должно ждать блокировку строки обращения"
    можно.set()
    первый.join(10)
    второй.join(10)
    assert not ошибки, ошибки
    assert s.case(case.case_id).status == "CLOSED"
    assert [(t.kind, t.status) for t in s.tasks(case.case_id)] == [("first_contact", "cancelled")]


def test_и16_задача_после_закрытия_не_проходит(люди):
    """Гонка 2: обращение закрыто в ещё не зафиксированной транзакции.
    Задача в обход модуля ждёт блокировку строки обращения, а после
    фиксации закрытия её отвергает проверка И16."""
    s = _сервис()
    case = _к_разговору(s, люди())
    закрыто, можно = threading.Event(), threading.Event()
    итог: dict = {}

    def закрыть() -> None:
        with psycopg.connect(DSN) as conn:
            conn.execute("SELECT 1 FROM cases WHERE case_id = %s FOR UPDATE", (case.case_id,))
            conn.execute("UPDATE cases SET status = 'CLOSED', close_reason = 'refused', "
                         "closed_at = now() WHERE case_id = %s", (case.case_id,))
            закрыто.set()
            можно.wait(10)

    def вставить() -> None:
        try:
            with psycopg.connect(DSN) as conn:
                conn.execute(_НОВАЯ_ЗАДАЧА, (case.case_id,))
            итог["прошла"] = True
        except psycopg.Error as exc:
            итог["ошибка"] = exc

    первый = threading.Thread(target=закрыть)
    первый.start()
    assert закрыто.wait(10)
    второй = threading.Thread(target=вставить)
    второй.start()
    второй.join(0.5)
    assert второй.is_alive(), "задача должна ждать блокировку строки обращения"
    можно.set()
    первый.join(10)
    второй.join(10)
    assert isinstance(итог.get("ошибка"), psycopg.errors.RaiseException), итог
    assert "И16" in str(итог["ошибка"])
    assert s.case(case.case_id).status == "CLOSED"
    assert s.tasks(case.case_id) == []


def test_и16_правка_задачи_и_закрытие_не_расходятся(люди):
    """Гонка 3: правка существующей задачи. Внешний ключ здесь строку
    обращения не блокирует (ссылка не меняется), и сериализацию даёт
    только триггер tasks_lock_case: закрытие ждёт, пока правка
    зафиксируется, и отменяет переоткрытую задачу."""
    s = _сервис()
    case = _к_разговору(s, люди())
    задача = s.create_task(case.case_id, "first_contact",
                           due_at=datetime.now(timezone.utc) + timedelta(hours=1), who="op")
    s.complete_task(задача.task_id, "дозвонились", who="op")
    правка, можно = threading.Event(), threading.Event()
    ошибки: list = []

    def переоткрыть() -> None:
        try:
            with psycopg.connect(DSN) as conn:
                conn.execute("UPDATE tasks SET status = 'open', done_at = NULL, done_by = NULL, "
                             "result = NULL WHERE task_id = %s", (задача.task_id,))
                правка.set()
                можно.wait(10)
        except BaseException as exc:              # pragma: no cover — видно в ошибке ниже
            ошибки.append(exc)
            правка.set()

    def закрыть() -> None:
        try:
            _сервис().transition(case.case_id, "CLOSED", who="op", reason="refused")
        except BaseException as exc:              # pragma: no cover
            ошибки.append(exc)

    первый = threading.Thread(target=переоткрыть)
    первый.start()
    assert правка.wait(10)
    второй = threading.Thread(target=закрыть)
    второй.start()
    второй.join(0.5)
    assert второй.is_alive(), "закрытие должно ждать, пока правка задачи зафиксируется"
    можно.set()
    первый.join(10)
    второй.join(10)
    assert not ошибки, ошибки
    assert [(t.task_id, t.status) for t in s.tasks(case.case_id)] == [(задача.task_id, "cancelled")]

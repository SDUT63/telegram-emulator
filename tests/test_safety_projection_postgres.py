"""Сигналы безопасности на боевом контуре: PostgreSQL, событие MAX, очередь.

То же, что tests/test_safety_projection.py, но настоящим боевым ботом
(ProductionPrivacySurvey) в транзакции события: задача safety_contact
проходит проверку вида в базе (миграция 019), кнопка сценария — через
handle_callback_event, ответ и клавиатура — в очереди исходящих.
"""
from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from cases import P0, CaseService
from cases_postgres import PostgresCaseRepository
from max_case_bridge import SAFETY_TASK

DSN = (os.getenv("SDUT_DATABASE_URL") or "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="SDUT_DATABASE_URL is not configured")


def _service() -> CaseService:
    return CaseService(PostgresCaseRepository(DSN))


def _cleanup(uid: str) -> None:
    with psycopg.connect(DSN) as conn:
        conn.execute("DELETE FROM cases WHERE person_id IN "
                     "(SELECT person_id FROM persons WHERE channel='max' AND channel_user_id=%s)", (uid,))
        conn.execute("DELETE FROM persons WHERE channel='max' AND channel_user_id=%s", (uid,))
        for table in ("outbox_messages", "audit_events", "processed_events", "survey_state"):
            conn.execute(f"DELETE FROM {table} WHERE user_id=%s", (uid,))


@pytest.fixture()
def бот(monkeypatch):
    from production_privacy import ProductionPrivacySurvey
    monkeypatch.setenv("SDUT_SCENARIOS", "all")
    uid = str(860000000000 + uuid.uuid4().int % 9999999999)
    survey = ProductionPrivacySurvey()
    yield survey, uid
    _cleanup(uid)


def _событие(работа):
    from storage_postgres import _TX_EVENT
    токен = _TX_EVENT.set(f"safety-{uuid.uuid4().hex}")
    try:
        return работа()
    finally:
        _TX_EVENT.reset(токен)


def _последний(uid: str) -> dict:
    with psycopg.connect(DSN) as conn:
        строка = conn.execute("SELECT payload FROM outbox_messages WHERE user_id=%s "
                              "ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    return dict(строка[0] or {})


def _в_анкете(survey, uid):
    _событие(lambda: survey.start_event(uid))
    _событие(lambda: survey.handle_callback_event(uid, "c", ["y"]))


def test_ра4_ставит_p1_и_задачу_кнопки_в_очереди(бот):
    survey, uid = бот
    _в_анкете(survey, uid)
    _событие(lambda: survey.handle_message_event(uid, "муж просит дать ему умереть"))

    ответ = _последний(uid)
    assert "попытки причинить себе вред" in ответ["text"]
    действия = [д for ряд in ответ["keyboard_rows"] for _, д in ряд]
    assert действия and all(д.startswith("z:RA4_relative_death_wish:danger_q:") for д in действия)

    обращение = _service().open_case("max", uid)
    assert обращение.priority == "P1"
    задачи = [т for т in _service().tasks(обращение.case_id) if т.kind == SAFETY_TASK]
    assert len(задачи) == 1

    # «Нет» кнопкой — через боевой обработчик нажатий.
    _событие(lambda: survey.handle_callback_event(uid, "z", ["RA4_relative_death_wish", "danger_q", "1"]))
    ответ = _последний(uid)
    assert "координатору в первую очередь" in ответ["text"]
    assert [д for ряд in ответ["keyboard_rows"] for _, д in ряд][0].startswith(
        "z:RA4_relative_death_wish:self_check:")
    # Повторный разбор не плодит задач (тест 8).
    assert len([т for т in _service().tasks(обращение.case_id) if т.kind == SAFETY_TASK]) == 1


def test_кризис_после_согласия_ставит_p0(бот):
    survey, uid = бот
    _в_анкете(survey, uid)
    _событие(lambda: survey.handle_message_event(uid, "не хочу больше жить"))
    обращение = _service().open_case("max", uid)
    assert обращение.urgency == P0
    with psycopg.connect(DSN) as conn:
        анкета = conn.execute("SELECT state_json FROM survey_state WHERE user_id=%s", (uid,)).fetchone()[0]
    # В состоянии — уровень и сценарий, а не слова человека (тест 3).
    assert "не хочу больше" not in str(анкета.get("safety"))


def test_до_согласия_в_базе_ничего(бот):
    survey, uid = бот
    _событие(lambda: survey.start_event(uid))
    _событие(lambda: survey.handle_message_event(uid, "я на пределе, не справляюсь"))
    assert _service().cases_of("max", uid) == []
    with psycopg.connect(DSN) as conn:
        анкета = conn.execute("SELECT state_json FROM survey_state WHERE user_id=%s", (uid,)).fetchone()[0]
    assert not анкета.get("safety") and not анкета.get("scenario") and not анкета.get("alerts")


def test_миграция_019_пускает_только_известные_виды_задач():
    """Тест 11: вид задачи safety_contact есть в базе, чужого вида нет."""
    with psycopg.connect(DSN) as conn:
        определение = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='tasks_kind_ck'"
        ).fetchone()[0]
    assert "safety_contact" in определение

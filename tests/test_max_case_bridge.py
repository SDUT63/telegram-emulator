from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from cases import DRAFT, NEW, CaseService
from cases_postgres import PostgresCaseRepository
from max_case_bridge import MaxCaseBridge
from survey_questions import CHECKPOINT_ID

DSN = (os.getenv("SDUT_DATABASE_URL") or "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="SDUT_DATABASE_URL is not configured")


def _service() -> CaseService:
    return CaseService(PostgresCaseRepository(DSN))


def _uid() -> str:
    return str(800000000000 + uuid.uuid4().int % 99999999999)


def _cleanup(uid: str) -> None:
    with psycopg.connect(DSN) as conn:
        conn.execute(
            "DELETE FROM cases WHERE person_id IN "
            "(SELECT person_id FROM persons WHERE channel='max' AND channel_user_id=%s)",
            (uid,),
        )
        conn.execute("DELETE FROM persons WHERE channel='max' AND channel_user_id=%s", (uid,))
        for table in ("outbox_messages", "audit_events", "processed_events", "survey_state"):
            conn.execute(f"DELETE FROM {table} WHERE user_id=%s", (uid,))


def test_max_consent_creates_domain_draft():
    uid = _uid()
    try:
        bridge = MaxCaseBridge()
        state = {"consent": {"at": "2026-10-01T12:00:00", "version": "1.0"},
                 "answers": {}, "alerts": []}
        bridge.sync(uid, state, create_if_missing=True)
        cases = _service().cases_of("max", uid)
        assert len(cases) == 1
        assert cases[0].status == DRAFT
        assert cases[0].number is None
        assert _service().consents(cases[0].case_id)[0].kind == "processing"
        assert _service().intakes(cases[0].case_id)[0].questionnaire_version == "max-2026-10-01-v1"
    finally:
        _cleanup(uid)


def test_max_checkpoint_promotes_draft_to_new():
    uid = _uid()
    try:
        bridge = MaxCaseBridge()
        state = {"consent": {"at": "2026-10-01T12:00:00", "version": "1.0"},
                 "answers": {CHECKPOINT_ID: "Продолжить"}, "alerts": []}
        bridge.sync(uid, state, create_if_missing=True)
        case = _service().open_case("max", uid)
        assert case is not None
        assert case.status == NEW
        assert case.number and case.number.startswith("SDUT-")
        assert case.suggested_route is None
    finally:
        _cleanup(uid)


def test_completed_max_intake_suggests_route_once():
    uid = _uid()
    try:
        bridge = MaxCaseBridge()
        state = {
            "consent": {"at": "2026-10-01T12:00:00", "version": "1.0"},
            "answers": {
                "flags": "Ничего из этого нет",
                "need": "Не знаю, с чего",
                CHECKPOINT_ID: "Достаточно, свяжитесь",
            },
            "alerts": [],
            "finished": "2026-10-01T12:05:00+00:00",
        }
        bridge.sync(uid, state, create_if_missing=True)
        case = _service().open_case("max", uid)
        assert case is not None
        assert case.suggested_route is not None
        assert case.suggested_route["route"] == "М4"
        assert case.suggested_route["rules_version"] == "routing-2026-10-01-v1"

        # The suggestion is immutable at the CASE level; a later sync cannot
        # replace it with a different route.
        state["answers"]["need"] = "Помощь на дому"
        bridge.sync(uid, state)
        again = _service().open_case("max", uid)
        assert again is not None
        assert again.suggested_route["route"] == "М4"
    finally:
        _cleanup(uid)


# --- Через настоящего бота: событие MAX, его транзакция, очередь ответов --------

@pytest.fixture()
def бот():
    from production_privacy import ProductionPrivacySurvey
    люди: list[str] = []
    survey = ProductionPrivacySurvey()
    survey._люди = люди
    yield survey
    for uid in люди:
        _cleanup(uid)


def _событие(survey, работа):
    from storage_postgres import _TX_EVENT
    токен = _TX_EVENT.set(f"e-{uuid.uuid4().hex}")
    try:
        return работа()
    finally:
        _TX_EVENT.reset(токен)


def _человек(survey) -> str:
    uid = _uid()
    survey._люди.append(uid)
    return uid


def _ответы_бота(uid: str) -> list[tuple[str, bool]]:
    with psycopg.connect(DSN) as conn:
        return [(r[0], r[1]) for r in conn.execute(
            "SELECT status, automatic FROM outbox_messages WHERE user_id = %s ORDER BY id", (uid,))]


def test_согласие_в_боте_открывает_черновик_до_согласия_ничего(бот):
    uid = _человек(бот)
    _событие(бот, lambda: бот.handle(uid, "здравствуйте"))
    assert _service().cases_of("max", uid) == []          # И10: до согласия обращения нет
    _событие(бот, lambda: бот.grant_consent(uid))
    case = _service().open_case("max", uid)
    assert case is not None and case.status == DRAFT


def test_закрытое_обращение_не_возобновляется_а_бот_отвечает(бот):
    from cases import CLOSED, SYSTEM
    uid = _человек(бот)
    _событие(бот, lambda: бот.handle(uid, "здравствуйте"))
    _событие(бот, lambda: бот.grant_consent(uid))
    старое = _service().open_case("max", uid)
    _service().transition(старое.case_id, CLOSED, who=SYSTEM, reason="abandoned_draft")
    до = len(_ответы_бота(uid))

    _событие(бот, lambda: бот.handle(uid, "мама"))

    assert _service().open_case("max", uid) is None       # И7: закрытое не открывается
    assert [c.case_id for c in _service().cases_of("max", uid)] == [старое.case_id]
    новые = _ответы_бота(uid)[до:]
    assert новые and all(status == "pending" and not automatic for status, automatic in новые)


def test_заново_после_закрытия_требует_нового_согласия(бот):
    """7.6: новое обращение после закрытого — только с новым согласием."""
    from cases import CLOSED, SYSTEM
    uid = _человек(бот)
    _событие(бот, lambda: бот.handle(uid, "здравствуйте"))
    _событие(бот, lambda: бот.grant_consent(uid))
    старое = _service().open_case("max", uid)
    _service().transition(старое.case_id, CLOSED, who=SYSTEM, reason="abandoned_draft")

    _событие(бот, lambda: бот.handle(uid, "заново"))
    assert бот.stage(uid) == "consent"                      # спрашиваем согласие заново
    assert _service().open_case("max", uid) is None

    _событие(бот, lambda: бот.grant_consent(uid))
    новое = _service().open_case("max", uid)
    assert новое is not None and новое.case_id != старое.case_id
    assert _service().case(старое.case_id).status == CLOSED
    assert len(_service().consents(новое.case_id)) == 1   # свой снимок согласия

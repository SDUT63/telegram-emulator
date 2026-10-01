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

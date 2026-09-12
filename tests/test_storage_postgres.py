from __future__ import annotations

import os

import pytest

from storage_postgres import PersistentSeen, PostgresSurvey


@pytest.fixture()
def postgres_dsn():
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")
    return dsn


def test_postgres_survey_round_trip(postgres_dsn):
    survey = PostgresSurvey(db_url=postgres_dsn, list_options=False)
    survey.state["pytest-user"] = {
        "step": 2,
        "answers": {"name": "pytest"},
        "finished": None,
    }
    survey.save()

    restored = PostgresSurvey(db_url=postgres_dsn, list_options=False)
    assert restored.state["pytest-user"]["step"] == 2
    assert restored.state["pytest-user"]["answers"]["name"] == "pytest"


def test_postgres_event_lease(postgres_dsn):
    seen = PersistentSeen(db_url=postgres_dsn, lease_seconds=60)
    key = "pytest-event-lease"
    assert seen.fresh(key) is True
    assert seen.fresh(key) is False

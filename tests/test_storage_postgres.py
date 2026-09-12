from __future__ import annotations

import os
import uuid

import pytest

from storage_postgres import PersistentSeen, PostgresSurvey


@pytest.fixture()
def postgres_dsn():
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")
    return dsn


def test_postgres_survey_round_trip(postgres_dsn):
    user_id = f"pytest-user-{uuid.uuid4()}"
    survey = PostgresSurvey(db_url=postgres_dsn, list_options=False)
    survey.state[user_id] = {
        "step": 2,
        "answers": {"name": "pytest"},
        "finished": None,
    }
    survey.save()

    restored = PostgresSurvey(db_url=postgres_dsn, list_options=False)
    assert restored.state[user_id]["step"] == 2
    assert restored.state[user_id]["answers"]["name"] == "pytest"

    del restored.state[user_id]
    restored.save()
    assert user_id not in PostgresSurvey(db_url=postgres_dsn, list_options=False).state


def test_postgres_event_lease(postgres_dsn):
    seen = PersistentSeen(db_url=postgres_dsn, lease_seconds=60)
    key = f"pytest-event-lease-{uuid.uuid4()}"
    assert seen.fresh(key) is True
    assert seen.fresh(key) is False

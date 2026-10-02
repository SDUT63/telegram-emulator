from __future__ import annotations

import json
import sqlite3
import time

from storage_sqlite import PersistentSeen, SQLiteSurvey


def test_survey_state_survives_restart(tmp_path):
    db = tmp_path / "sdut.sqlite3"
    first = SQLiteSurvey(db_path=str(db), list_options=False)
    first.state["123"] = {"step": 7, "answers": {"name": "Тест"}, "finished": None}
    first.save()

    second = SQLiteSurvey(db_path=str(db), list_options=False)
    assert second.state["123"]["step"] == 7
    assert second.state["123"]["answers"]["name"] == "Тест"


def test_persistent_seen_survives_restart_and_keeps_limit(tmp_path):
    db = tmp_path / "sdut.sqlite3"
    first = PersistentSeen(limit=2, db_path=str(db))
    assert first.fresh("e1") is True
    assert first.fresh("e1") is False
    assert first.fresh("e2") is True
    assert first.fresh("e3") is True

    second = PersistentSeen(limit=2, db_path=str(db))
    assert second.fresh("e3") is False
    assert second.fresh("e1") is True

    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM event_leases").fetchone()[0]
    assert count == 2


def test_event_claim_can_be_recovered_after_crash(tmp_path):
    db = tmp_path / "sdut.sqlite3"
    seen = PersistentSeen(db_path=str(db), lease_seconds=1)
    assert seen.fresh("crashed-event") is True
    assert seen.fresh("crashed-event") is False
    time.sleep(1.05)
    assert seen.fresh("crashed-event") is True


def test_audit_is_persisted(tmp_path):
    db = tmp_path / "sdut.sqlite3"
    survey = SQLiteSurvey(db_path=str(db), list_options=False)
    survey.audit("123", "test_event", {"kind": "smoke", "ok": True})

    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT user_id, event_type, event_json FROM audit_events ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "123"
    assert row[1] == "test_event"
    assert json.loads(row[2]) == {"kind": "smoke", "ok": True}


def test_empty_event_id_is_not_marked_as_duplicate(tmp_path):
    db = tmp_path / "sdut.sqlite3"
    seen = PersistentSeen(db_path=str(db))
    assert seen.fresh(None) is True
    assert seen.fresh("") is True

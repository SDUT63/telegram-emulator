"""Ноутбучный пилот не должен раскладывать ответы людей по диску.

Пилот запускают из папки пользователя — часто синхронизируемой (OneDrive,
Dropbox). Базовая анкета вызывает export_csv при каждом завершении и при
удалении: одна строка кода тихо копирует медицинские ответы всех
респондентов в чужое облако.
"""
from __future__ import annotations

import sqlite3

from storage_sqlite import SQLiteSurvey


def _pilot(tmp_path, monkeypatch) -> SQLiteSurvey:
    monkeypatch.chdir(tmp_path)
    return SQLiteSurvey(db_path=str(tmp_path / "pilot.sqlite3"), list_options=False)


def test_pilot_never_writes_a_plaintext_answer_table(tmp_path, monkeypatch):
    survey = _pilot(tmp_path, monkeypatch)
    survey.handle("u1", "здравствуйте")
    survey.grant_consent("u1")
    survey.handle("u1", "Иванова Мария Петровна")

    # Прямой вызов — то, что делает базовая анкета на завершении анкеты.
    assert survey.export_csv() is None

    csv_files = list(tmp_path.rglob("*.csv"))
    assert csv_files == [], f"на диск попала таблица с ответами: {csv_files}"


def test_pilot_erase_removes_state_and_audit_and_confirms(tmp_path, monkeypatch):
    from chatbot_survey import ERASED

    survey = _pilot(tmp_path, monkeypatch)
    survey.handle("u1", "здравствуйте")
    survey.grant_consent("u1")
    survey.handle("u1", "Иванова Мария Петровна")
    survey.audit("u1", "message", {"kind": "message"})

    assert survey.handle("u1", "удалить") == ERASED

    conn = sqlite3.connect(str(tmp_path / "pilot.sqlite3"))
    try:
        assert conn.execute("SELECT count(*) FROM survey_state WHERE user_id=?", ("u1",)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM audit_events WHERE user_id=?", ("u1",)).fetchone()[0] == 0
    finally:
        conn.close()

    assert list(tmp_path.rglob("*.csv")) == []


def test_every_deletion_word_reaches_the_pilot_purge(tmp_path, monkeypatch):
    from chatbot_survey import ERASE_WORDS, ERASED

    for word in sorted(ERASE_WORDS):
        survey = _pilot(tmp_path, monkeypatch)
        survey.handle("u1", "здравствуйте")
        survey.grant_consent("u1")
        survey.handle("u1", "Иванова Мария Петровна")

        assert survey.handle("u1", word) == ERASED, word
        assert survey.state.get("u1") is None, word

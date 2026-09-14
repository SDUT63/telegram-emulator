#!/usr/bin/env python3
"""Production PostgreSQL facade for the MAX bot."""
from __future__ import annotations

import copy
import time

from chatbot_survey import ALREADY_DONE, CONSENT_NO, CONSENT_SHORT, RESUMED, Survey
from survey_questions import CHECKPOINT_ID, QUESTIONS
from storage_postgres import (
    _TX_CONNECTION,
    _TX_EVENT,
    _TX_USER,
    TransactionalPersistentSeen,
    TransactionalPostgresSurvey,
)


class ProductionPostgresSurvey(TransactionalPostgresSurvey):
    """Production PostgreSQL survey with explicit transactional adapters."""

    def user_state(self, user_id: str) -> dict:
        """Return an isolated user snapshot from the PostgreSQL source of truth.

        The legacy ``Survey.state`` object is deliberately not exposed to the
        production UI. Inside an active event transaction the snapshot is the
        state already loaded under PostgreSQL row/advisory locks; outside a
        transaction a fresh read is made from PostgreSQL.
        """
        uid = str(user_id)
        if _TX_CONNECTION.get() is not None and _TX_USER.get() == uid:
            return copy.deepcopy(self.state.get(uid) or {})
        with self._connect() as conn:
            row = conn.execute("SELECT state_json FROM survey_state WHERE user_id=%s", (uid,)).fetchone()
        return copy.deepcopy(row[0]) if row and isinstance(row[0], dict) else {}

    def handle(self, user_id: str, text: str) -> str:
        return self._mutate(str(user_id), "message", {"kind": "message"}, lambda: Survey.handle(self, str(user_id), text), "")

    def grant_consent(self, user_id: str) -> str:
        return self._mutate(str(user_id), "callback", {"action": "grant_consent"}, lambda: Survey.grant_consent(self, str(user_id)), "")

    def refuse_consent(self, user_id: str) -> str:
        return self._mutate(str(user_id), "callback", {"action": "refuse_consent"}, lambda: Survey.refuse_consent(self, str(user_id)), "")

    def toggle(self, user_id: str, step: int, index: int) -> bool:
        return self._mutate(str(user_id), "callback", {"action": "toggle", "step": step, "index": index}, lambda: Survey.toggle(self, str(user_id), step, index), False)

    def answer_by_numbers(self, user_id: str, numbers: list[int]) -> str:
        return self._mutate(str(user_id), "callback", {"action": "answer", "numbers": numbers}, lambda: Survey.answer_by_numbers(self, str(user_id), numbers), "")

    def restart_after_consent(self, user_id: str) -> str:
        """Restart only the detailed assessment, retaining the intake data.

        The short intake contains identity/contact/routing data (including
        name, phone and address). Re-entering those fields is both frustrating
        and unnecessary. The checkpoint answer is normalized to "Продолжить"
        and the first detailed question becomes current. Existing consent and
        conversation metadata remain untouched.
        """
        uid = str(user_id)

        def restart_loaded() -> str:
            person = self.state.get(uid)
            if not person:
                return Survey.restart_after_consent(self, uid)

            checkpoint = next((i for i, q in enumerate(QUESTIONS) if q["id"] == CHECKPOINT_ID), None)
            if checkpoint is None:
                raise RuntimeError(f"Survey checkpoint {CHECKPOINT_ID!r} is not defined")

            keep_ids = {q["id"] for q in QUESTIONS[: checkpoint + 1]}
            # Derived address fields are produced by the address question and
            # are part of the same primary intake record.
            keep_ids.update({"district", "lift"})
            answers = {
                key: value
                for key, value in (person.get("answers") or {}).items()
                if key in keep_ids
            }
            answers[CHECKPOINT_ID] = "Продолжить"

            person["answers"] = answers
            person["step"] = self._next(checkpoint + 1, answers)
            person["history"] = [
                step for step in (person.get("history") or []) if step < checkpoint + 1
            ]
            person["pending"] = None
            person["alerts"] = []
            person["finished"] = None
            person["total_seen"] = 0
            person["reading"] = False
            return (
                "Основные данные уже сохранены — имя, телефон и адрес повторно вводить не нужно.\n\n"
                + self._ask(uid, person["step"])
            )

        return self._mutate(uid, "callback", {"action": "restart"}, restart_loaded, "")

    def start(self, user_id: str) -> str:
        """Start/resume atomically using the state loaded under the row lock.

        An existing row is never passed to Survey.start(): the legacy method
        intentionally creates a fresh case and would be destructive here.
        """
        user_id = str(user_id)
        token = None
        if _TX_CONNECTION.get() is None:
            token = _TX_EVENT.set(f"start:{user_id}:{time.time_ns()}")
        try:
            def start_loaded() -> str:
                person = self.state.get(user_id)
                if person is not None:
                    if person.get("finished"):
                        return ALREADY_DONE
                    consent = person.get("consent") or {}
                    if consent.get("refused") and not consent.get("at"):
                        return CONSENT_NO
                    if consent.get("at"):
                        return RESUMED + "\n\n" + self.question_text(user_id)
                    return CONSENT_SHORT
                return Survey.start(self, user_id)

            return self._mutate(user_id, "bot_started", {"kind": "bot_started"}, start_loaded, "")
        finally:
            if token is not None:
                _TX_EVENT.reset(token)

    def understood(self, user_id: str) -> None:
        """Persist read/navigation state without reusing the parent event claim."""
        user_id = str(user_id)
        if _TX_CONNECTION.get() is not None:
            Survey.understood(self, user_id)
            return
        event_id = _TX_EVENT.get()
        if not event_id:
            Survey.understood(self, user_id)
            return
        token = _TX_EVENT.set(f"{event_id}:understood")
        try:
            self._mutate(user_id, "navigation", {"action": "understood"}, lambda: Survey.understood(self, user_id), None)
        finally:
            _TX_EVENT.reset(token)

    def note_message(self, user_id: str, text: str, files: list | None = None) -> None:
        """Persist dispatcher follow-up without the legacy lambda adapter."""
        user_id = str(user_id)
        if _TX_CONNECTION.get() is not None:
            Survey.note_message(self, user_id, text, files)
            return
        event_id = _TX_EVENT.get()
        if not event_id:
            Survey.note_message(self, user_id, text, files)
            return
        token = _TX_EVENT.set(f"{event_id}:message-note")
        try:
            self._mutate(user_id, "message_attachment", {"kind": "message_attachment", "has_files": bool(files)}, lambda: Survey.note_message(self, user_id, text, files), None)
        finally:
            _TX_EVENT.reset(token)


__all__ = ["ProductionPostgresSurvey", "TransactionalPersistentSeen"]

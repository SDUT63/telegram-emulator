#!/usr/bin/env python3
"""Production PostgreSQL facade for the MAX bot."""
from __future__ import annotations

import time

from chatbot_survey import ALREADY_DONE, CONSENT_NO, RESUMED, Survey
from storage_postgres import (
    _TX_CONNECTION,
    _TX_EVENT,
    TransactionalPersistentSeen,
    TransactionalPostgresSurvey,
)


class ProductionPostgresSurvey(TransactionalPostgresSurvey):
    """Production PostgreSQL survey with explicit transactional adapters."""

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
        return self._mutate(str(user_id), "callback", {"action": "restart"}, lambda: Survey.restart_after_consent(self, str(user_id)), "")

    def start(self, user_id: str) -> str:
        """Start/resume without wiping an existing production case."""
        user_id = str(user_id)
        existing = self.state.get(user_id)
        if existing is not None:
            if existing.get("finished"):
                return ALREADY_DONE
            if (existing.get("consent") or {}).get("refused"):
                return CONSENT_NO
            if (existing.get("consent") or {}).get("at"):
                return RESUMED + "\n\n" + self.question_text(user_id)

        token = _TX_EVENT.set(f"start:{user_id}:{time.time_ns()}")
        try:
            return self._mutate(user_id, "bot_started", {"kind": "bot_started"}, lambda: Survey.start(self, user_id), "")
        finally:
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

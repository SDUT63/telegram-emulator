#!/usr/bin/env python3
"""Production PostgreSQL facade for the MAX bot.

The legacy ``chatbot_survey.Survey`` remains the source of survey semantics.
This facade supplies explicit method adapters so the production path does not
inherit the legacy adapter's zero-argument ``super()`` lambdas.  It also makes
navigation/read-state mutations participate in the same durable event model.
"""
from __future__ import annotations

from chatbot_survey import (
    ALREADY_DONE,
    CONSENT_NO,
    RESUMED,
    Survey,
)
from storage_postgres import (
    _TX_CONNECTION,
    _TX_EVENT,
    TransactionalPersistentSeen,
    TransactionalPostgresSurvey,
)


class ProductionPostgresSurvey(TransactionalPostgresSurvey):
    """Production PostgreSQL survey with explicit transactional adapters."""

    def handle(self, user_id: str, text: str) -> str:
        return self._mutate(
            str(user_id),
            "message",
            {"kind": "message"},
            lambda: Survey.handle(self, str(user_id), text),
            "",
        )

    def grant_consent(self, user_id: str) -> str:
        return self._mutate(
            str(user_id),
            "callback",
            {"action": "grant_consent"},
            lambda: Survey.grant_consent(self, str(user_id)),
            "",
        )

    def refuse_consent(self, user_id: str) -> str:
        return self._mutate(
            str(user_id),
            "callback",
            {"action": "refuse_consent"},
            lambda: Survey.refuse_consent(self, str(user_id)),
            "",
        )

    def toggle(self, user_id: str, step: int, index: int) -> bool:
        return self._mutate(
            str(user_id),
            "callback",
            {"action": "toggle", "step": step, "index": index},
            lambda: Survey.toggle(self, str(user_id), step, index),
            False,
        )

    def answer_by_numbers(self, user_id: str, numbers: list[int]) -> str:
        return self._mutate(
            str(user_id),
            "callback",
            {"action": "answer", "numbers": numbers},
            lambda: Survey.answer_by_numbers(self, str(user_id), numbers),
            "",
        )

    def restart_after_consent(self, user_id: str) -> str:
        return self._mutate(
            str(user_id),
            "callback",
            {"action": "restart"},
            lambda: Survey.restart_after_consent(self, str(user_id)),
            "",
        )

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

        return self._mutate(
            user_id,
            "bot_started",
            {"kind": "bot_started"},
            lambda: Survey.start(self, user_id),
            "",
        )

    def understood(self, user_id: str) -> None:
        """Persist a navigation/read acknowledgement as an idempotent event.

        ``asyncio.create_task`` inherits ContextVars from the dispatcher task,
        so a detached knowledge-base task must use a derived event id rather
        than reusing the original MAX delivery id.
        """
        user_id = str(user_id)
        if _TX_CONNECTION.get() is not None:
            Survey.understood(self, user_id)
            return

        event_id = _TX_EVENT.get()
        if not event_id:
            Survey.understood(self, user_id)
            return

        followup = f"{event_id}:understood"
        token = _TX_EVENT.set(followup)
        try:
            self._mutate(
                user_id,
                "navigation",
                {"action": "understood"},
                lambda: Survey.understood(self, user_id),
                None,
            )
        finally:
            _TX_EVENT.reset(token)

    def note_message(self, user_id: str, text: str, files: list | None = None) -> None:
        """Persist the dispatcher follow-up without the broken legacy lambda."""
        user_id = str(user_id)
        if _TX_CONNECTION.get() is not None:
            Survey.note_message(self, user_id, text, files)
            return

        event_id = _TX_EVENT.get()
        if not event_id:
            Survey.note_message(self, user_id, text, files)
            return

        followup = f"{event_id}:message-note"
        token = _TX_EVENT.set(followup)
        try:
            self._mutate(
                user_id,
                "message_attachment",
                {"kind": "message_attachment", "has_files": bool(files)},
                lambda: Survey.note_message(self, user_id, text, files),
                None,
            )
        finally:
            _TX_EVENT.reset(token)


__all__ = ["ProductionPostgresSurvey", "TransactionalPersistentSeen"]

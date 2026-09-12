#!/usr/bin/env python3
"""Production storage facade for the MAX bot.

Keeps the transactional adapter isolated from the legacy Survey semantics.
In particular, repeated BotStarted events must never wipe an existing case.
"""
from __future__ import annotations

from storage_postgres import (
    TransactionalPersistentSeen,
    TransactionalPostgresSurvey,
)


class ProductionPostgresSurvey(TransactionalPostgresSurvey):
    """Transactional PostgreSQL survey with idempotent bot-start behavior."""

    def start(self, user_id: str) -> str:
        existing = self.state.get(str(user_id))
        if existing is not None:
            if existing.get("finished"):
                from chatbot_survey import ALREADY_DONE

                return ALREADY_DONE
            if (existing.get("consent") or {}).get("refused"):
                from chatbot_survey import CONSENT_NO

                return CONSENT_NO
            if (existing.get("consent") or {}).get("at"):
                from chatbot_survey import RESUMED

                return RESUMED + "\n\n" + self.question_text(str(user_id))
        return super().start(str(user_id))


__all__ = ["ProductionPostgresSurvey", "TransactionalPersistentSeen"]

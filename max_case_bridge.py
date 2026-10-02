#!/usr/bin/env python3
"""Bridge between the MAX questionnaire and the domain CASE model.

The questionnaire remains the conversational UI/state machine. CASE is the
business source of truth. This bridge never copies CASE state into survey
state and never makes routing/referral decisions.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from cases import BOT, DRAFT, NEW, CaseService
from cases_postgres import PostgresCaseRepository
from chatbot_survey import CONSENT_FULL, CONSENT_VERSION
from survey_questions import CHECKPOINT_ID
from routing_rules import route as calculate_route

QUESTIONNAIRE_VERSION = "max-2026-10-01-v1"
CHANNEL = "max"
ROUTING_RULES_VERSION = "routing-2026-10-01-v1"


def consent_text_hash() -> str:
    return hashlib.sha256(CONSENT_FULL.encode("utf-8")).hexdigest()


class MaxCaseBridge:
    """Synchronise durable questionnaire milestones into CASE."""

    def __init__(self) -> None:
        self.service = CaseService(PostgresCaseRepository())

    def open_case(self, user_id: str):
        return self.service.open_case(CHANNEL, str(user_id))

    def sync(self, user_id: str, state: dict[str, Any], *, create_if_missing: bool = False) -> None:
        """Project the current survey milestone into the open CASE.

        Called inside the MAX event transaction. The CASE repository therefore
        participates in the same PostgreSQL transaction as survey_state.
        """
        consent = state.get("consent") or {}
        if not consent.get("at"):
            return

        case = self.open_case(str(user_id))
        if case is None and create_if_missing:
            case = self.service.open_draft(
                CHANNEL,
                str(user_id),
                consent_version=str(consent.get("version") or CONSENT_VERSION),
                consent_text_hash=consent_text_hash(),
                questionnaire_version=QUESTIONNAIRE_VERSION,
                given_via="bot",
                source="max",
                who=BOT,
            )

        if case is None:
            return

        answers = dict(state.get("answers") or {})
        alerts = list(state.get("alerts") or [])
        finished = state.get("finished")
        checkpoint_at = self._parse_time(state.get("checkpoint_at"))
        completed_at = self._parse_time(finished)
        if CHECKPOINT_ID in answers and checkpoint_at is None:
            checkpoint_at = datetime.now(timezone.utc)

        current = max(self.service.intakes(case.case_id), key=lambda intake: intake.version)
        if (
            current.answers != answers
            or current.alerts != alerts
            or (checkpoint_at is not None and current.checkpoint_at != checkpoint_at)
            or (completed_at is not None and current.completed_at != completed_at)
        ):
            self.service.update_intake(
                case.case_id,
                who=BOT,
                answers=answers,
                alerts=alerts,
                checkpoint_at=checkpoint_at,
                completed_at=completed_at,
            )

        # Ч7: checkpoint is one of the explicit grounds for DRAFT -> NEW.
        if case.status == DRAFT and CHECKPOINT_ID in answers:
            case = self.service.transition(
                case.case_id,
                NEW,
                who=BOT,
                trigger="checkpoint",
            )

        # ROUTE is calculated only after the questionnaire is actually
        # finished. The checkpoint is deliberately not enough: detailed
        # answers collected after it can change the route. CASE stores the
        # suggestion once (И5), with the exact rule version and signals.
        if completed_at is not None and case.suggested_route is None:
            route, reason, also = calculate_route(answers)
            signals = [
                f"{key}={answers[key]}"
                for key in sorted(answers)
                if answers.get(key)
            ]
            case = self.service.suggest_route(
                case.case_id,
                route=route,
                reason=reason,
                signals=signals,
                rules_version=ROUTING_RULES_VERSION,
                also=also,
                who=BOT,
            )

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

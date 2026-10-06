#!/usr/bin/env python3
"""Bridge between the MAX questionnaire and the domain CASE model.

The questionnaire remains the conversational UI/state machine. CASE is the
business source of truth. This bridge never copies CASE state into survey
state and never makes routing/referral decisions.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import рабочее_время
from cases import BOT, CLOSED, DRAFT, NEW, OPEN_TASK_STATUSES, P0, CaseService
from cases_postgres import PostgresCaseRepository
from chatbot_survey import CONSENT_FULL, CONSENT_VERSION
from survey_questions import CHECKPOINT_ID
from routing_rules import route as calculate_route

QUESTIONNAIRE_VERSION = "max-2026-10-01-v1"
CHANNEL = "max"
ROUTING_RULES_VERSION = "routing-2026-10-01-v1"
# Версия правил, по которым сценарий безопасности ставит приоритет (И6).
SAFETY_RULES_VERSION = "scenarios-1"
SAFETY_TASK = "safety_contact"
# Чем меньше, тем старше: P0 — клиническая экстренность, затем P1–P3.
_СТАРШИНСТВО = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_ПРИЧИНЫ = {
    "P1": "Близкий передаёт просьбу дать ему умереть (Р-А4)",
    "P2": "Сообщил(а), что на пределе (Р-А2)",
}

log = logging.getLogger("sdut.case_bridge")


def consent_text_hash() -> str:
    return hashlib.sha256(CONSENT_FULL.encode("utf-8")).hexdigest()


class MaxCaseBridge:
    """Synchronise durable questionnaire milestones into CASE."""

    def __init__(self, service: CaseService | None = None) -> None:
        self.service = service or CaseService(PostgresCaseRepository())

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

        case = self._сигналы_безопасности(case, state, answers)

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

    def _сигналы_безопасности(self, case, state: dict[str, Any], answers: dict[str, Any]):
        """Уровни сценариев безопасности — на обращение (редакция 5, К2–К5).

        Сбой здесь не должен отменить ответ человеку: событие MAX и ответ
        бота в одной транзакции, и человек в кризисе остался бы без
        телефонов. Поэтому проекция идёт под точкой сохранения: ошибка
        откатывает только её, пишется в журнал и в метрику, а пометка
        в карточке и ответ остаются.
        """
        записи = [з for з in (state.get("safety") or []) if з.get("level") in _СТАРШИНСТВО]
        if not записи or case.status == CLOSED:
            return case
        from storage_postgres import _TX_CONNECTION

        соединение = _TX_CONNECTION.get()
        точка = соединение.transaction() if соединение is not None else contextlib.nullcontext()
        try:
            with точка:
                return self._спроецировать(case, записи, answers)
        except Exception:                                    # noqa: BLE001
            log.exception("сигнал безопасности не лёг на обращение %s", case.case_id)
            from metrics import METRICS
            METRICS.inc("sdut_safety_projection_errors_total")
            return case

    def _спроецировать(self, case, записи: list[dict[str, Any]], answers: dict[str, Any]):
        s = self.service
        сигналы = sorted({f"{з['level']}:{з['kind']}:{з['scenario']}@{з['version']}" for з in записи})

        # P0 — клиническая экстренность. Черновик с известным телефоном
        # сразу становится обращением: координатор должен его увидеть (Ч5).
        if any(з["level"] == "P0" for з in записи):
            if case.urgency != P0:
                case = s.set_urgency_p0(case.case_id, who=BOT,
                                        signals=[с for с in сигналы if с.startswith("P0:")])
            if case.status == DRAFT and answers.get("phone"):
                case = s.transition(case.case_id, NEW, who=BOT, trigger="p0_alert_with_phone")

        # P1 и P2 — приоритет службы; только повышается, правилом с версией (И6).
        приоритеты = [з["level"] for з in записи if з["level"] in ("P1", "P2")]
        if приоритеты:
            лучший = min(приоритеты, key=_СТАРШИНСТВО.get)
            текущий = case.priority
            if текущий is None or _СТАРШИНСТВО[лучший] < _СТАРШИНСТВО.get(текущий, 9):
                case = s.set_priority(case.case_id, лучший, who=BOT, reason=_ПРИЧИНЫ[лучший],
                                      rules_version=SAFETY_RULES_VERSION,
                                      signals=[с for с in сигналы if с.startswith(лучший + ":")])

        # Одна открытая задача «связаться» на обращение. Новая — только на
        # сигнал, пришедший после последней такой задачи: повторный разбор
        # того же состояния задачу не плодит.
        задачи = [т for т in s.tasks(case.case_id) if т.kind == SAFETY_TASK]
        if any(т.status in OPEN_TASK_STATUSES for т in задачи):
            return case
        последняя = max((т.created_at for т in задачи), default=None)
        новые = [з for з in записи
                 if последняя is None or (self._local_time(з.get("at")) or последняя) > последняя]
        if новые:
            уровень = min((з["level"] for з in новые), key=_СТАРШИНСТВО.get)
            сейчас = datetime.now(timezone.utc)
            s.create_task(case.case_id, SAFETY_TASK, due_at=рабочее_время.срок(уровень, сейчас),
                          who=BOT)
        return case

    @staticmethod
    def _local_time(value: Any) -> datetime | None:
        """Время записи бота: без пояса — местное время этого компьютера."""
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is None else parsed

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

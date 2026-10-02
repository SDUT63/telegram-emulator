#!/usr/bin/env python3
"""CASE-centered operator facade for the production CRM."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from cases import (
    ASSIGNED, CLOSED, CONTACTED, CONTROL, NO_CONTACT, REFERRED, ROUTE_CONFIRMED,
    SERVICE_STARTED, SYSTEM, WAITING_EXTERNAL, CaseService, NotFound,
)
from cases_postgres import PostgresCaseRepository
from storage_postgres import database_url


class CaseOperatorCRM:
    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = db_url or database_url()

    def _service(self) -> CaseService:
        return CaseService(PostgresCaseRepository(self.db_url))

    def _case_id(self, user_id: str) -> int:
        uid = str(user_id).strip()
        if not uid:
            raise ValueError("user_id не должен быть пустым")
        with psycopg.connect(self.db_url, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT c.case_id FROM cases c JOIN persons p ON p.person_id=c.person_id "
                "WHERE p.channel='max' AND p.channel_user_id=%s AND c.status <> 'CLOSED' "
                "ORDER BY c.case_id DESC LIMIT 1",
                (uid,),
            ).fetchone()
        if row is None:
            raise ValueError("открытого CASE для пользователя нет")
        return int(row["case_id"])

    def get_case(self, user_id: str) -> dict[str, Any] | None:
        uid = str(user_id).strip()
        with psycopg.connect(self.db_url, row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT c.*, p.channel, p.channel_user_id,
                       (SELECT jsonb_agg(i ORDER BY i.version DESC)
                          FROM intakes i WHERE i.case_id=c.case_id) AS intakes,
                       (SELECT jsonb_agg(t ORDER BY t.task_id)
                          FROM tasks t WHERE t.case_id=c.case_id) AS tasks,
                       (SELECT jsonb_agg(r ORDER BY r.referral_id)
                          FROM referrals r WHERE r.case_id=c.case_id) AS referrals,
                       (SELECT jsonb_agg(o ORDER BY o.outcome_id)
                          FROM outcomes o WHERE o.case_id=c.case_id) AS outcomes,
                       (SELECT jsonb_agg(e ORDER BY e.event_id DESC)
                          FROM (SELECT * FROM case_events
                                 WHERE case_id=c.case_id
                                 ORDER BY event_id DESC LIMIT 100) e) AS events
                  FROM cases c
                  JOIN persons p ON p.person_id=c.person_id
                 WHERE p.channel='max' AND p.channel_user_id=%s
                 ORDER BY c.case_id DESC
                 LIMIT 1
                """,
                (uid,),
            ).fetchone()
        if row is None:
            return None
        карточка = dict(row)
        карточка["allowed"] = self._разрешено(карточка)
        return карточка

    @staticmethod
    def _разрешено(case: dict[str, Any]) -> dict[str, Any]:
        """Что координатор может сделать сейчас — по таблице 5.2 из модуля
        обращений, чтобы интерфейс не держал своей копии правил."""
        from cases import (CLOSE_REASONS, CONSENT_NOT_GIVEN, ESCALATED, STATUSES,
                           SYSTEM_CLOSE_REASONS, allowed)

        frm = case["status"]
        переходы = [to for to in STATUSES
                    if to not in (CLOSED, ESCALATED, CONTROL) and allowed(frm, to)]
        причины = [r for r in CLOSE_REASONS
                   if r not in SYSTEM_CLOSE_REASONS and allowed(frm, CLOSED, r)
                   and (r != CONSENT_NOT_GIVEN or case.get("legal_basis") == "vital_interest")]
        return {"transitions": переходы, "close_reasons": причины,
                "extend_control": frm == CONTROL}

    def list_open_cases(self) -> list[dict[str, Any]]:
        with psycopg.connect(self.db_url, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT c.case_id,c.number,c.status,c.legal_basis,c.source,c.created_at,
                       c.opened_at,c.assigned_to,c.urgency,c.priority,c.suggested_route,
                       c.final_route,c.route_confirmed_at,c.referred_at,
                       c.service_started_at,c.due_at,c.close_reason,
                       p.channel_user_id
                  FROM cases c JOIN persons p ON p.person_id=c.person_id
                 WHERE p.channel='max' AND c.status <> 'CLOSED'
                 ORDER BY c.urgency DESC NULLS LAST,c.created_at ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def assign(self, user_id: str, operator_id: str):
        return self._service().transition(
            self._case_id(user_id), ASSIGNED, who=operator_id,
            assigned_to=operator_id,
        )

    def contacted(self, user_id: str, operator_id: str):
        return self._service().transition(self._case_id(user_id), CONTACTED, who=operator_id)

    def confirm_route(self, user_id: str, final_route: str, reason: str, operator_id: str):
        return self._service().transition(
            self._case_id(user_id), ROUTE_CONFIRMED, who=operator_id,
            final_route=final_route, reason=reason,
        )

    def refer(self, user_id: str, directory_entry_id: int, channel: str, operator_id: str):
        return self._service().transition(
            self._case_id(user_id), REFERRED, who=operator_id,
            directory_entry_id=int(directory_entry_id), referral_channel=channel,
        )

    def no_contact(self, user_id: str, operator_id: str):
        return self._service().transition(self._case_id(user_id), NO_CONTACT, who=operator_id)

    def waiting_external(self, user_id: str, operator_id: str):
        return self._service().transition(self._case_id(user_id), WAITING_EXTERNAL, who=operator_id)

    def start_service(self, user_id: str, operator_id: str, started_at: datetime | None = None):
        """Помощь началась — и контроль начинается сразу, в той же транзакции.

        В CONTROL переводит система (5.1), Д+7 и Д+30 считаются от начала
        помощи (5.4). Ждать с переходом нечего, а обращение, застрявшее
        в SERVICE_STARTED, осталось бы без контрольных звонков.
        """
        service = self._service()
        case_id = self._case_id(user_id)
        with service.repo.transaction():
            service.transition(case_id, SERVICE_STARTED, who=operator_id,
                               service_started_at=started_at)
            return service.transition(case_id, CONTROL, who=SYSTEM)

    def complete_task(self, user_id: str, task_id: int, result: str, operator_id: str,
                      follow_up_kind: str | None = None,
                      follow_up_due_at: datetime | None = None):
        service = self._service()
        case_id = self._case_id(user_id)
        with service.repo.transaction():
            задача = service.repo.get_task(int(task_id))
        if задача is None or задача.case_id != case_id:
            # Номер задачи приходит из запроса: чужую по нему не закрыть.
            raise NotFound(f"у этого обращения нет задачи {task_id}")
        service.complete_task(int(task_id), result, who=operator_id,
                              follow_up_kind=follow_up_kind,
                              follow_up_due_at=follow_up_due_at)
        return service.case(case_id)

    def extend_control(self, user_id: str, due_at: datetime, reason: str, operator_id: str):
        service = self._service()
        case_id = self._case_id(user_id)
        service.extend_control(case_id, due_at=due_at, reason=reason, who=operator_id)
        return service.case(case_id)

    def directory(self, route: str | None = None) -> list[dict[str, Any]]:
        """Действующие записи справочника — для направления (И12)."""
        with psycopg.connect(self.db_url, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT directory_entry_id, provider_key, route, provider, available, "
                "fallback, phone, hours, address FROM route_directory "
                "WHERE valid_to IS NULL AND (%s::text IS NULL OR route = %s) "
                "ORDER BY route, provider",
                (route, route),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self, user_id: str, reason: str, operator_id: str,
              duplicate_of_number: str | None = None):
        duplicate_of = None
        if duplicate_of_number:
            with psycopg.connect(self.db_url) as conn:
                row = conn.execute("SELECT case_id FROM cases WHERE number = %s",
                                   (duplicate_of_number.strip(),)).fetchone()
            if row is None:
                raise NotFound(f"обращения {duplicate_of_number} нет")
            duplicate_of = int(row[0])
        return self._service().transition(
            self._case_id(user_id), CLOSED, who=operator_id, reason=reason,
            duplicate_of=duplicate_of,
        )

    def reassign(self, user_id: str, assigned_to: str, operator_id: str):
        return self._service().reassign(
            self._case_id(user_id), assigned_to, who=operator_id,
        )


__all__ = ["CaseOperatorCRM"]

#!/usr/bin/env python3
"""CASE-centered operator facade for the production CRM."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from cases import (
    ASSIGNED, BOT, CLOSED, CONTACTED, NEW, REFERRED, ROUTE_CONFIRMED,
    SERVICE_STARTED, CaseService,
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
                          FROM case_events e WHERE e.case_id=c.case_id LIMIT 100) AS events
                  FROM cases c
                  JOIN persons p ON p.person_id=c.person_id
                 WHERE p.channel='max' AND p.channel_user_id=%s
                 ORDER BY c.case_id DESC
                 LIMIT 1
                """,
                (uid,),
            ).fetchone()
        return dict(row) if row else None

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

    def start_service(self, user_id: str, operator_id: str, started_at: datetime | None = None):
        return self._service().transition(
            self._case_id(user_id), SERVICE_STARTED, who=operator_id,
            service_started_at=started_at,
        )

    def close(self, user_id: str, reason: str, operator_id: str):
        return self._service().transition(
            self._case_id(user_id), CLOSED, who=operator_id, reason=reason,
        )

    def reassign(self, user_id: str, assigned_to: str, operator_id: str):
        return self._service().reassign(
            self._case_id(user_id), assigned_to, who=operator_id,
        )


__all__ = ["CaseOperatorCRM"]

#!/usr/bin/env python3
"""Production survey facade with transaction-safe privacy deletion."""
from __future__ import annotations
from typing import Any, Callable, TypeVar
from privacy_deletion import UserDeletionMixin
from production_outbox import DurableProductionPostgresSurvey
from storage_postgres import _TX_CONNECTION, _TX_USER
from chatbot_survey import Survey
from max_case_bridge import MaxCaseBridge
T=TypeVar("T")

class ProductionPrivacySurvey(UserDeletionMixin, DurableProductionPostgresSurvey):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.case_bridge = MaxCaseBridge()

    def _delete_user_in_transaction(self,conn,uid:str)->None:
        conn.execute("INSERT INTO deleted_users(user_id) VALUES(%s) ON CONFLICT(user_id) DO UPDATE SET deleted_at=CURRENT_TIMESTAMP",(uid,))
        conn.execute("INSERT INTO deleted_event_tombstones(event_id,event_type,event_hash) SELECT event_id,event_type,event_hash FROM processed_events WHERE user_id=%s ON CONFLICT(event_id) DO UPDATE SET event_type=EXCLUDED.event_type,event_hash=EXCLUDED.event_hash,deleted_at=CURRENT_TIMESTAMP",(uid,))
        conn.execute("DELETE FROM outbox_messages WHERE user_id=%s",(uid,))
        conn.execute("DELETE FROM audit_events WHERE user_id=%s",(uid,))
        conn.execute("WITH removed AS (DELETE FROM processed_events WHERE user_id=%s RETURNING event_id) DELETE FROM event_leases WHERE event_id IN (SELECT event_id FROM removed)",(uid,))
        conn.execute("DELETE FROM operator_cases WHERE user_id=%s",(uid,))
        conn.execute("DELETE FROM survey_state WHERE user_id=%s",(uid,))
        conn.execute("DELETE FROM cases WHERE person_id IN (SELECT person_id FROM persons WHERE channel='max' AND channel_user_id=%s)",(uid,))
        conn.execute("DELETE FROM persons WHERE channel='max' AND channel_user_id=%s",(uid,))

    def erase(self,user_id:str)->str:return self.delete_user(user_id)
    def export_csv(self,*args,**kwargs):return None

    def _mutate(self,user_id:str,event_type:str,payload:dict[str,Any],fn:Callable[[],T],duplicate:T)->T:
        if _TX_CONNECTION.get() is not None and _TX_USER.get()==str(user_id):
            result = fn()
            state = self.state.get(str(user_id)) or {}
            create_case = event_type == "callback" and payload.get("action") == "grant_consent"
            self.case_bridge.sync(str(user_id), state, create_if_missing=create_case)
            return result
        original=super()._mutate
        def wrapped()->T:
            conn=_TX_CONNECTION.get()
            if conn is not None:
                conn.execute("DELETE FROM deleted_users WHERE user_id=%s",(str(user_id),))
            result = fn()
            state = self.state.get(str(user_id)) or {}
            create_case = event_type == "callback" and payload.get("action") == "grant_consent"
            self.case_bridge.sync(str(user_id), state, create_if_missing=create_case)
            return result
        return original(user_id,event_type,payload,wrapped,duplicate)

    def restart_after_consent(self, user_id: str) -> str:
        """После закрытого обращения новое — только с новым согласием (7.6).

        Открытое обращение есть — «заново» продолжает его, как раньше. Нет —
        анкета сбрасывается вместе с согласием, и человек получает текст
        согласия снова; новое согласие откроет новое обращение. self.start()
        здесь не годится: production-версия намеренно не трогает существующую
        анкету и отвечала бы «продолжаем с того места» — без согласия
        и без обращения, так что ответы человека до CRM не доходили бы.
        """
        uid = str(user_id)
        if self.case_bridge.open_case(uid) is not None:
            return super().restart_after_consent(uid)
        return self._mutate(uid, "callback", {"action": "restart"}, lambda: Survey.start(self, uid), "")

    def deleted_event_tombstone(self,event_id:str):
        key=str(event_id).strip()
        if not key:return None
        with self._connect() as conn:return conn.execute("SELECT event_type,event_hash FROM deleted_event_tombstones WHERE event_id=%s",(key,)).fetchone()
__all__=["ProductionPrivacySurvey"]

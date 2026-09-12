#!/usr/bin/env python3
"""Transactional outbound bridge for the production MAX bot."""
from __future__ import annotations

import contextvars
import hashlib
import json
import threading
import weakref
from typing import Any, Callable, TypeVar

from chatbot_survey import Survey
from outbox_postgres import PostgresOutbox, delivery_key
from production_storage import ProductionPostgresSurvey
from storage_postgres import _TX_CONNECTION, _TX_EVENT, _TX_USER, _TX_ACCEPTED

T = TypeVar("T")
_OUTBOX_RESULT: contextvars.ContextVar[Any] = contextvars.ContextVar("sdut_outbox_result", default=None)
_OUTBOX_KEYBOARD: contextvars.ContextVar[Any] = contextvars.ContextVar("sdut_outbox_keyboard", default=None)


def _message_fingerprint(text: str, files: list[dict[str, Any]]) -> str:
    canonical = json.dumps({"text": text, "files": files}, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _keyboard_rows(survey: ProductionPostgresSurvey, user_id: str) -> list[list[list[str]]]:
    from max_bot import layout
    return [[[str(label), str(action)] for label, action in row]
            for row in layout(survey, str(user_id))]


def _navigation_keyboard(action: str, args: list[str], survey: ProductionPostgresSurvey,
                          user_id: str) -> list[list[list[str]]]:
    import knowledge
    from max_bot import НАЗАД, ДАЛЬШЕ, ВСЕ_ТЕМЫ, К_АНКЕТЕ, _fits
    rows: list[list[list[str]]] = []
    if action == "map":
        for branch in knowledge.карта():
            rows.append([[_fits(branch["название"]), "v:" + branch["id"] + ":1"]])
        if survey.current(user_id) is not None:
            rows.append([[К_АНКЕТЕ, "q"]])
        return rows
    if action == "v" and args:
        number = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
        page = knowledge.страница(args[0], number)
        if not page:
            return [[[_fits(ВСЕ_ТЕМЫ), "map"]]]
        for title in page["статьи"]:
            rows.append([[_fits(knowledge.подпись(title)), "k:" + title[:60]]])
        nav: list[list[str]] = []
        if page["номер"] > 1:
            nav.append([НАЗАД, f"v:{page['id']}:{page['номер'] - 1}"])
        if page["номер"] < page["всего"]:
            nav.append([ДАЛЬШЕ, f"v:{page['id']}:{page['номер'] + 1}"])
        if nav:
            rows.append(nav)
        rows.append([[ВСЕ_ТЕМЫ, "map"]])
        if survey.current(user_id) is not None:
            rows.append([[К_АНКЕТЕ, "q"]])
        return rows
    if action == "k" and args:
        title = args[0]
        for neighbor in knowledge.соседи(title, сколько=3):
            rows.append([[_fits(knowledge.подпись(neighbor)), "k:" + neighbor[:60]]])
        branch = knowledge.ветвь(knowledge.где(title) or "")
        bottom: list[list[str]] = []
        if branch:
            bottom.append(["‹ " + branch["кратко"], f"v:{branch['id']}:1"])
        bottom.append([ВСЕ_ТЕМЫ, "map"])
        rows.append(bottom)
        if survey.current(user_id) is not None:
            rows.append([[К_АНКЕТЕ, "q"]])
        return rows
    if action == "q":
        from max_bot import keyboard_for
        # q returns the questionnaire screen; its keyboard is the canonical
        # scenario layout, not the navigation layout.
        return _keyboard_rows(survey, user_id)
    return rows


def _enqueue_text(outbox: PostgresOutbox, conn, survey: ProductionPostgresSurvey,
                  user_id: str, event_id: str, text: str, ordinal: int = 0,
                  keyboard_rows: list[list[list[str]]] | None = None) -> None:
    if not text:
        return
    rows = keyboard_rows if keyboard_rows is not None else _keyboard_rows(survey, user_id)
    outbox.enqueue(delivery_key=delivery_key(event_id, ordinal=ordinal), user_id=str(user_id),
                   payload={"kind": "max_text", "text": str(text), "keyboard_rows": rows}, conn=conn)


class DurableProductionPostgresSurvey(ProductionPostgresSurvey):
    """Production survey with one durable transaction boundary per MAX event."""

    _lock_registry_guard = threading.RLock()
    _mutation_locks: weakref.WeakValueDictionary[str, threading.RLock] = weakref.WeakValueDictionary()

    @classmethod
    def _mutation_lock_for(cls, user_id: str) -> threading.RLock:
        key = str(user_id)
        with cls._lock_registry_guard:
            lock = cls._mutation_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._mutation_locks[key] = lock
            return lock

    def health(self) -> bool:
        required = {"survey_state", "processed_events", "audit_events", "outbox_messages"}
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name = ANY(%s)", (list(required),)).fetchall()
            return {str(row[0]) for row in rows} == required
        except Exception:
            return False

    def _mutate(self, user_id: str, event_type: str, payload: dict[str, Any],
                fn: Callable[[], T], duplicate: T) -> T:
        lock = self._mutation_lock_for(str(user_id))
        with lock:
            token_result = _OUTBOX_RESULT.set(None)
            token_keyboard = _OUTBOX_KEYBOARD.set(None)
            def wrapped() -> T:
                result = fn()
                _OUTBOX_RESULT.set(result)
                return result
            try:
                return super()._mutate(user_id, event_type, payload, wrapped, duplicate)
            finally:
                _OUTBOX_RESULT.reset(token_result)
                _OUTBOX_KEYBOARD.reset(token_keyboard)

    def handle_message_event(self, user_id: str, text: str, files: list[dict[str, Any]] | None = None) -> str:
        uid = str(user_id); normalized_text = str(text or ""); normalized_files = list(files or [])
        payload = {"kind": "message", "has_files": bool(normalized_files),
                   "fingerprint": _message_fingerprint(normalized_text, normalized_files)}
        def mutate() -> str:
            result = Survey.handle(self, uid, normalized_text)
            if normalized_files:
                Survey.note_message(self, uid, normalized_text, normalized_files)
            return result
        return self._mutate(uid, "message", payload, mutate, "")

    def handle_navigation_event(self, user_id: str, action: str, args: list[str]) -> str:
        uid = str(user_id); action = str(action or ""); args = [str(v) for v in args]
        payload = {"kind": "navigation", "action": action, "args": args}
        def mutate() -> str:
            from max_bot import экран_карты, экран_ветви, экран_статьи, КАРТА_ЗАГОЛОВОК, КАРТА_ПОДПИСЬ
            Survey.understood(self, uid)
            if action == "map":
                text, _ = экран_карты(self, uid)
            elif action == "v" and args:
                number = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
                screen = экран_ветви(args[0], number, self, uid)
                text = screen[0] if screen else f"Тема не найдена.\n\n{КАРТА_ЗАГОЛОВОК}\n{КАРТА_ПОДПИСЬ}"
            elif action == "k" and args:
                screen = экран_статьи(args[0], self, uid)
                text = screen[0] if screen else f"Материал не найден.\n\n{КАРТА_ЗАГОЛОВОК}\n{КАРТА_ПОДПИСЬ}"
            elif action == "q":
                spot = self.current(uid)
                if not spot:
                    text = self.summary(uid)
                else:
                    text = self.question_text(uid)
            elif action == "cfull":
                text = self.consent_text(uid)
            else:
                return ""
            _OUTBOX_KEYBOARD.set(_navigation_keyboard(action, args, self, uid)
                                 if action not in {"cfull"} else _keyboard_rows(self, uid))
            return text
        return self._mutate(uid, "navigation", payload, mutate, "")

    def handle_callback_event(self, user_id: str, action: str, args: list[str]) -> str:
        uid = str(user_id); action = str(action or ""); args = [str(v) for v in args]
        payload = {"kind": "callback", "action": action, "args": args}
        def mutate() -> str:
            spot = self.current(uid)
            if action in {"a", "s", "d"} and args and (not spot or str(spot[0]) != args[0]):
                return ""
            if action == "c":
                if self.stage(uid) != "consent": return ""
                return self.grant_consent(uid) if args[:1] == ["y"] else self.refuse_consent(uid)
            if action == "a" and len(args) == 2:
                if not spot: return ""
                index = int(args[1])
                if index < 0 or index >= len(spot[1].get("options", [])): return ""
                return self.answer_by_numbers(uid, [index + 1])
            if action == "s" and args: return self.handle(uid, "далее")
            if action == "d" and args:
                step = int(args[0]); picked = self.picked(uid, step); current = self.current(uid)
                if not picked:
                    if not current or current[1].get("required", True): return ""
                    return self.handle(uid, "далее")
                return self.answer_by_numbers(uid, [index + 1 for index in picked])
            if action == "b": return self.handle(uid, "назад")
            if action == "n": return self.restart_after_consent(uid)
            if action == "m": return self.summary(uid)
            return ""
        return self._mutate(uid, "callback", payload, mutate, "")

    def start_event(self, user_id: str) -> str:
        uid = str(user_id); token = None
        if _TX_EVENT.get() is None:
            import time
            token = _TX_EVENT.set(f"start:{uid}:{time.time_ns()}")
        try:
            return self._mutate(uid, "bot_started", {"kind": "bot_started"}, lambda: self._start_loaded(uid), "")
        finally:
            if token is not None: _TX_EVENT.reset(token)

    def _start_loaded(self, uid: str) -> str:
        from chatbot_survey import ALREADY_DONE, CONSENT_NO, CONSENT_SHORT, RESUMED
        person = self.state.get(uid)
        if person is None: return Survey.start(self, uid)
        if person.get("finished"): return ALREADY_DONE
        consent = person.get("consent") or {}
        if consent.get("refused") and not consent.get("at"): return CONSENT_NO
        if consent.get("at"): return RESUMED + "\n\n" + self.question_text(uid)
        return CONSENT_SHORT

    def _finish_event(self, ctx, user_id: str, event_type: str, payload: dict[str, Any] | None,
                      error: BaseException | None) -> None:
        if not ctx: return
        conn, token_conn, token_user, token_accepted = ctx
        try:
            if error is not None:
                conn.rollback(); return
            self._save_user(conn, str(user_id))
            audit_payload = dict(payload or {}); audit_payload.pop("fingerprint", None)
            self.audit(str(user_id), event_type, audit_payload)
            result = _OUTBOX_RESULT.get(); keyboard = _OUTBOX_KEYBOARD.get(); event_id = _TX_EVENT.get()
            outbox = PostgresOutbox(db_url=self.db_url)
            if isinstance(result, str) and result and event_id:
                _enqueue_text(outbox, conn, self, str(user_id), event_id, result, 0, keyboard)
            if payload and payload.get("has_files") and event_id:
                from max_bot import FILES_TAKEN
                _enqueue_text(outbox, conn, self, str(user_id), event_id, FILES_TAKEN, 1)
            conn.commit()
        except BaseException:
            conn.rollback(); raise
        finally:
            _TX_CONNECTION.reset(token_conn); _TX_USER.reset(token_user); _TX_ACCEPTED.reset(token_accepted); conn.close()


__all__ = ["DurableProductionPostgresSurvey"]

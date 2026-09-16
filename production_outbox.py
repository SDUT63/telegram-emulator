#!/usr/bin/env python3
"""Transactional outbound bridge for the production MAX bot."""
from __future__ import annotations
import contextvars
import hashlib
import json
import threading
import weakref
from typing import Any, Callable, TypeVar
import fallback
import knowledge
from chatbot_survey import CONSENT_SHORT, ERASED, Survey
from outbox_postgres import PostgresOutbox, delivery_key
from production_storage import ProductionPostgresSurvey
from storage_postgres import _TX_CONNECTION, _TX_EVENT, _TX_USER, _TX_ACCEPTED
from max_ui import FILES_TAKEN, СПРАВКА_ПОДПИСЬ, article_screen, branch_screen, consent_keyboard, map_screen, questionnaire_keyboard
T = TypeVar("T")
_OUTBOX_RESULT: contextvars.ContextVar[Any] = contextvars.ContextVar("sdut_outbox_result", default=None)
_OUTBOX_KEYBOARD: contextvars.ContextVar[Any] = contextvars.ContextVar("sdut_outbox_keyboard", default=None)
# Message the pressed button lives on. Browsing the topic map rewrites that one
# screen instead of stacking a new message per tap; the durable payload carries
# the target so the worker, not the handler, talks to MAX.
_OUTBOX_EDIT_TARGET: contextvars.ContextVar[str | None] = contextvars.ContextVar("sdut_outbox_edit_target", default=None)


def _message_fingerprint(text: str, files: list[dict[str, Any]]) -> str:
    canonical = json.dumps({"text": text, "files": files}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _keyboard_rows(survey: ProductionPostgresSurvey, user_id: str) -> list[list[list[str]]]:
    return questionnaire_keyboard(survey, str(user_id))


def _enqueue_text(outbox: PostgresOutbox, conn, survey: ProductionPostgresSurvey, user_id: str, event_id: str, text: str, ordinal: int = 0, keyboard_rows: list[list[list[str]]] | None = None, farewell: bool = False, edit_message_id: str | None = None) -> None:
    if not text:
        return
    rows = keyboard_rows if keyboard_rows is not None else _keyboard_rows(survey, user_id)
    payload: dict[str, Any] = {"kind": "max_text", "text": str(text), "keyboard_rows": rows}
    if edit_message_id:
        # Same durable guarantees, one extra instruction: replace this screen
        # rather than append one. The worker falls back to sending when the
        # target is gone, so a stale button never leaves the person in silence.
        payload = {"kind": "max_edit", "message_id": str(edit_message_id), "text": str(text), "keyboard_rows": rows}
    outbox.enqueue(delivery_key=delivery_key(event_id, ordinal=ordinal), user_id=str(user_id), payload=payload, conn=conn, farewell=farewell)


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

    def _mutate(self, user_id: str, event_type: str, payload: dict[str, Any], fn: Callable[[], T], duplicate: T) -> T:
        with self._mutation_lock_for(str(user_id)):
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

    def _reference_reply(self, user_id: str, question: str) -> str:
        # Одна реализация на оба транспорта: пока она была только здесь,
        # пилот на тот же вопрос не отвечал ничем.
        return Survey.справка_по_вопросу(self, user_id, question)

    def handle_message_event(self, user_id: str, text: str, files: list[dict[str, Any]] | None = None) -> str:
        uid = str(user_id)
        normalized_text = str(text or "")
        normalized_files = list(files or [])
        payload = {"kind": "message", "has_files": bool(normalized_files), "fingerprint": _message_fingerprint(normalized_text, normalized_files)}
        def mutate() -> str:
            if normalized_files and not normalized_text:
                # Справка или фотография — это ответ, а не пустое сообщение.
                # Отвечать «напишите ответ текстом» здесь значит сказать
                # человеку, что он ничего не прислал. Подтверждение приёма
                # файла уходит отдельным сообщением и несёт те же кнопки.
                Survey.note_message(self, uid, "", normalized_files)
                return ""
            result = Survey.handle(self, uid, normalized_text)
            if normalized_files:
                Survey.note_message(self, uid, "" if not result else normalized_text, normalized_files)
            if result or not normalized_text:
                return result
            return self._reference_reply(uid, normalized_text)
        return self._mutate(uid, "message", payload, mutate, "")

    def handle_navigation_event(self, user_id: str, action: str, args: list[str]) -> str:
        uid = str(user_id)
        action = str(action or "")
        args = [str(v) for v in args]
        payload = {"kind": "navigation", "action": action, "args": args}
        def mutate() -> str:
            Survey.understood(self, uid)
            if action == "map":
                text, keyboard = map_screen(self, uid)
            elif action == "v" and args:
                screen = branch_screen(args[0], int(args[1]) if len(args) > 1 and args[1].isdigit() else 1, self, uid)
                if not screen:
                    return ""
                text, keyboard = screen
            elif action == "k" and args:
                screen = article_screen(args[0], self, uid)
                if not screen:
                    return ""
                text, keyboard = screen
            elif action == "q":
                spot = self.current(uid)
                text = self.summary(uid) if not spot else self.question_text(uid)
                keyboard = questionnaire_keyboard(self, uid)
            elif action == "cfull":
                text = self.consent_text(uid)
                keyboard = consent_keyboard(full=True)
            else:
                return ""
            _OUTBOX_KEYBOARD.set(keyboard)
            return text
        return self._mutate(uid, "navigation", payload, mutate, "")

    def handle_callback_event(self, user_id: str, action: str, args: list[str]) -> str:
        uid = str(user_id)
        action = str(action or "")
        args = [str(v) for v in args]
        payload = {"kind": "callback", "action": action, "args": args}
        def mutate() -> str:
            spot = self.current(uid)
            if action in {"a", "s", "d", "t"} and args and (not spot or str(spot[0]) != args[0]):
                return ""
            if action == "c":
                if args[:1] == ["back"]:
                    if self.stage(uid) != "consent":
                        return ""
                    _OUTBOX_KEYBOARD.set(consent_keyboard(full=False))
                    return CONSENT_SHORT
                if self.stage(uid) != "consent":
                    return ""
                return self.grant_consent(uid) if args[:1] == ["y"] else self.refuse_consent(uid)
            if action == "a" and len(args) == 2:
                if not spot:
                    return ""
                try:
                    index = int(args[1])
                except ValueError:
                    return ""
                if index < 0 or index >= len(spot[1].get("options", [])):
                    return ""
                return self.answer_by_numbers(uid, [index + 1])
            if action == "s" and args:
                return self.handle(uid, "далее")
            if action == "d" and args:
                try:
                    step = int(args[0])
                except ValueError:
                    return ""
                picked = self.picked(uid, step)
                current = self.current(uid)
                if not picked:
                    if not current or current[1].get("required", True):
                        return ""
                    return self.handle(uid, "далее")
                return self.answer_by_numbers(uid, [picked_index + 1 for picked_index in picked])
            if action == "t" and len(args) == 2:
                # Множественный выбор: «тревожные признаки» — пролежни, одышка,
                # боль. Клавиатура шлёт именно t:<шаг>:<вариант>, и без этой
                # ветки нажатие не делало ничего: человек не мог отметить
                # ничего из того, ради чего этот вопрос и задан.
                if not spot:
                    return ""
                try:
                    index = int(args[1])
                except ValueError:
                    return ""
                if index < 0 or index >= len(spot[1].get("options", [])):
                    return ""
                self.toggle(uid, int(args[0]), index)
                _OUTBOX_KEYBOARD.set(questionnaire_keyboard(self, uid))
                return self.question_text(uid)
            if action == "b":
                return self.handle(uid, "назад")
            if action == "n":
                # Продолжить подробную часть после «Достаточно, свяжитесь».
                # Раньше «n» начинало анкету заново, а подпись на кнопке
                # обещала продолжение — человек терял всё, что ответил.
                return self.continue_detailed(uid)
            if action == "r":
                return self.restart_after_consent(uid)
            if action == "m":
                return self.summary(uid)
            return ""
        return self._mutate(uid, "callback", payload, mutate, "")

    def start_event(self, user_id: str) -> str:
        uid = str(user_id)
        token = None
        if _TX_EVENT.get() is None:
            import time
            token = _TX_EVENT.set(f"start:{uid}:{time.time_ns()}")
        try:
            return self._mutate(uid, "bot_started", {"kind": "bot_started"}, lambda: self._start_loaded(uid), "")
        finally:
            if token is not None:
                _TX_EVENT.reset(token)

    def _start_loaded(self, uid: str) -> str:
        from chatbot_survey import ALREADY_DONE, CONSENT_NO, CONSENT_SHORT, RESUMED
        person = self.state.get(uid)
        if person is None:
            return Survey.start(self, uid)
        if person.get("finished"):
            return ALREADY_DONE
        consent = person.get("consent") or {}
        if consent.get("refused") and not consent.get("at"):
            return CONSENT_NO
        if consent.get("at"):
            return RESUMED + "\n\n" + self.question_text(uid)
        return CONSENT_SHORT

    def _finish_event(self, ctx, user_id: str, event_type: str, payload: dict[str, Any] | None, error: BaseException | None) -> None:
        if not ctx:
            return
        conn, token_conn, token_user, token_accepted = ctx
        try:
            if error is not None:
                conn.rollback()
                return
            self._save_user(conn, str(user_id))
            audit_payload = dict(payload or {})
            audit_payload.pop("fingerprint", None)
            # A deletion is itself the privacy operation. Do not create a
            # post-deletion audit record containing the deleted user id.
            is_deletion = event_type == "message" and audit_payload.get("kind") == "delete"
            if not is_deletion:
                self.audit(str(user_id), event_type, audit_payload)
            result = _OUTBOX_RESULT.get()
            keyboard = _OUTBOX_KEYBOARD.get()
            event_id = _TX_EVENT.get()
            outbox = PostgresOutbox(db_url=self.db_url)
            if is_deletion and event_id:
                # The purge wiped this user's queue and raised the delivery
                # gate. Queue the one message they are still owed — a constant
                # confirmation, carrying no answers — in the same transaction,
                # so it is durable exactly when the deletion is. The keyboard is
                # empty on purpose: there is no state left to offer buttons for.
                _enqueue_text(outbox, conn, self, str(user_id), event_id, ERASED, 0, [], farewell=True)
            if not is_deletion and isinstance(result, str) and result and event_id:
                edit_target = _OUTBOX_EDIT_TARGET.get() if event_type == "navigation" else None
                _enqueue_text(outbox, conn, self, str(user_id), event_id, result, 0, keyboard, edit_message_id=edit_target)
            if not is_deletion and payload and payload.get("has_files") and event_id:
                _enqueue_text(outbox, conn, self, str(user_id), event_id, FILES_TAKEN, 1)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            _TX_CONNECTION.reset(token_conn)
            _TX_USER.reset(token_user)
            _TX_ACCEPTED.reset(token_accepted)
            conn.close()


__all__ = ["DurableProductionPostgresSurvey"]

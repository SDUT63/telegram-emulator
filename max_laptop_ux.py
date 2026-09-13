"""Explicit UX helpers for the local MAX pilot."""
from __future__ import annotations
from chatbot_survey import CONSENT_SHORT
from survey_questions import CHECKPOINT_ID, QUESTIONS

FULL_BACK = "c:back"
CONTINUE_DETAILED = "n"
RESTART = "r"


def consent_rows(full: bool = False):
    if full:
        return [[("Согласен, продолжим", "c:y")], [("Не согласен", "c:n")], [("← Вернуться к краткому тексту", FULL_BACK)]]
    return [[("Согласен, продолжим", "c:y")], [("Прочитать полный текст", "c:full")], [("Не согласен", "c:n")], [("Просто почитать", "map")]]


def completed_rows():
    return [[("Мои ответы", "m")], [("Продолжить подробную анкету", CONTINUE_DETAILED)], [("Заполнить заново", RESTART)]]


def short_to_detailed_state(survey, user_id: str) -> str:
    """Preserve the completed short intake and reopen only the detailed part."""
    uid = str(user_id)
    person = survey.state.get(uid)
    if not person or not person.get("finished"):
        return survey.question_text(uid)
    checkpoint = next(i for i, q in enumerate(QUESTIONS) if q["id"] == CHECKPOINT_ID)
    keep = {q["id"] for q in QUESTIONS[: checkpoint + 1]} | {"district", "lift"}
    person["answers"] = {k: v for k, v in (person.get("answers") or {}).items() if k in keep}
    person["answers"][CHECKPOINT_ID] = "Продолжить"
    person["step"] = survey._next(checkpoint + 1, person["answers"])
    person["history"] = [s for s in (person.get("history") or []) if s < checkpoint + 1]
    person["pending"] = None
    person["alerts"] = []
    person["finished"] = None
    person["reading"] = False
    survey.save()
    return "Основные данные уже сохранены — повторно вводить имя, телефон и адрес не нужно.\n\n" + survey.question_text(uid)

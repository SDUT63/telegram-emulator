from max_laptop_pilot_v2 import LaptopSurvey, rows
from transparent_max_pilot import START_GUIDE, consent_keyboard
from survey_questions import CHECKPOINT_ID, QUESTIONS


def test_full_consent_has_return_and_help_buttons():
    actions = [action for row in consent_keyboard(True) for _, action in row]
    labels = [label for row in consent_keyboard(True) for label, _ in row]
    assert "c:back" in actions
    assert "c:full" not in actions
    assert "h" in actions
    assert "Что можно написать" in labels


def test_consent_offers_help_before_data_entry():
    actions = [action for row in consent_keyboard(False) for _, action in row]
    labels = [label for row in consent_keyboard(False) for label, _ in row]
    assert "h" in actions
    assert "Что можно написать" in labels
    assert "помощь" in START_GUIDE
    assert "темы" in START_GUIDE
    assert "что умеешь" in START_GUIDE


def test_help_keeps_transparent_consent_controls_visible():
    from transparent_max_pilot import visible_rows

    class ConsentStub:
        def stage(self, user_id): return "consent"
        def reading(self, user_id): return False

    actions = [action for row in visible_rows(ConsentStub(), "42") for _, action in row]
    assert actions == ["c:y", "c:full", "c:n", "map", "h"]


def test_short_completed_offers_detailed_continuation(tmp_path):
    survey = LaptopSurvey(db_path=str(tmp_path / "survey.sqlite3"), list_options=False)
    uid = "test-user"
    checkpoint = next(i for i,q in enumerate(QUESTIONS) if q["id"] == CHECKPOINT_ID)
    answers = {q["id"]: "test" for q in QUESTIONS[:checkpoint + 1]}
    answers[CHECKPOINT_ID] = "Достаточно, свяжитесь"
    survey.state[uid] = {
        "answers": answers,
        "history": list(range(checkpoint + 1)),
        "finished": "2026-09-13T10:00:00",
        "pending": None,
        "alerts": [],
        "reading": False,
        "consent": {"at": "2026-09-13T09:00:00", "version": "1.0"},
    }
    actions = [action for row in rows(survey, uid) for _, action in row]
    assert "n" in actions
    assert "r" in actions

    reply = survey.continue_detailed(uid)
    assert "Основные данные уже сохранены" in reply
    assert survey.state[uid]["finished"] is None
    assert survey.state[uid]["answers"][CHECKPOINT_ID] == "Продолжить"
    assert survey.current(uid) is not None

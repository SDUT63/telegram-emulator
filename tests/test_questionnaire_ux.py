from __future__ import annotations

from typing import Any, Callable, TypeVar

from max_ui import consent_keyboard
from production_storage import ProductionPostgresSurvey
from survey_questions import CHECKPOINT_ID

T = TypeVar("T")


class InMemoryProductionSurvey(ProductionPostgresSurvey):
    """Deterministic unit-test seam: keep production restart logic, skip DB I/O."""

    def _mutate(
        self,
        user_id: str,
        event_type: str,
        payload: dict[str, Any],
        fn: Callable[[], T],
        duplicate: T,
    ) -> T:
        return fn()


def test_full_consent_screen_has_a_way_back_and_a_way_forward() -> None:
    keyboard = consent_keyboard(full=True)
    actions = [button[1] for row in keyboard for button in row]
    labels = [button[0] for row in keyboard for button in row]

    assert actions == ["c:y", "c:n", "c:back"]
    assert "Согласен, продолжим" in labels
    assert "← Вернуться к краткому тексту" in labels


def test_restart_preserves_primary_intake_and_resets_detailed_assessment() -> None:
    survey = InMemoryProductionSurvey.__new__(InMemoryProductionSurvey)
    survey.state = {
        "42": {
            "consent": {"at": "2026-09-13T10:00:00", "version": "1.0"},
            "started": "2026-09-13T10:00:00",
            "finished": "2026-09-13T10:10:00",
            "step": 99,
            "answers": {
                "who": "О близком человеке",
                "relation": "Дочь или сын",
                "aware": "Да, знает",
                "name": "Мария",
                "patient_name": "Иван",
                "phone": "+7 917 123-45-67",
                "when_call": "Вечером",
                "address": "Автозаводский, Ворошилова 19, кв. 5",
                "district": "Автозаводский",
                "lift": "Есть лифт",
                "age": "85 и старше",
                "flags": "Боль",
                "need": "Помощь на дому",
                CHECKPOINT_ID: "Достаточно, свяжитесь",
                "mobility": "Не встаёт",
                "turning": "Нужна помощь",
            },
            "alerts": ["старое предупреждение"],
            "history": list(range(14)),
            "pending": {"step": 14, "picked": [1]},
            "reading": True,
            "messages": [{"text": "не терять переписку"}],
            "total_seen": 40,
        }
    }

    text = survey.restart_after_consent("42")
    person = survey.state["42"]

    assert "имя, телефон и адрес повторно вводить не нужно" in text
    assert person["answers"]["name"] == "Мария"
    assert person["answers"]["patient_name"] == "Иван"
    assert person["answers"]["phone"] == "+7 917 123-45-67"
    assert person["answers"]["address"] == "Автозаводский, Ворошилова 19, кв. 5"
    assert person["answers"]["district"] == "Автозаводский"
    assert person["answers"]["lift"] == "Есть лифт"
    assert person["answers"][CHECKPOINT_ID] == "Продолжить"
    assert "mobility" not in person["answers"]
    assert "turning" not in person["answers"]
    assert person["finished"] is None
    assert person["alerts"] == []
    assert person["pending"] is None
    assert person["reading"] is False
    assert person["messages"] == [{"text": "не терять переписку"}]
    assert person["step"] > 0
    assert "Как человек передвигается?" in text

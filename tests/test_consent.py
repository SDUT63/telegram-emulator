"""Согласие на обработку данных — правовой рубеж релиза.

До согласия анкета не должна собирать ничего. Проверяем это буквально:
не «первый вопрос не показан», а «ответ не записан ни при каком вводе».
"""
import pytest

from chatbot_survey import CONSENT, CONSENT_NO, CONSENT_VERSION


def test_первое_сообщение_показывает_согласие(survey):
    out = survey.handle("u1", "здравствуйте")
    assert "закон разрешает их собирать только с вашего согласия" in out
    assert survey.stage("u1") == "consent"


def test_до_согласия_вопросов_нет(survey):
    survey.handle("u1", "здравствуйте")
    assert survey.current("u1") is None


@pytest.mark.parametrize("ввод", ["Мария", "89171234567", "2", "далее", "назад", "1, 3"])
def test_до_согласия_ничего_не_записывается(survey, ввод):
    survey.handle("u1", "здравствуйте")
    survey.handle("u1", ввод)
    assert survey.state["u1"]["answers"] == {}
    assert survey.stage("u1") == "consent"


def test_согласие_записывает_дату_и_версию(survey):
    survey.handle("u1", "здравствуйте")
    survey.grant_consent("u1")
    mark = survey.consented("u1")
    assert mark["version"] == CONSENT_VERSION
    assert mark["at"]
    assert survey.stage("u1") == "survey"


def test_отказ_не_оставляет_ответов(survey):
    survey.handle("u1", "здравствуйте")
    survey.handle("u1", "2")                       # попытка ответить
    out = survey.refuse_consent("u1")
    assert out == CONSENT_NO
    assert survey.state["u1"]["answers"] == {}
    assert survey.state["u1"]["consent"]["refused"]


def test_после_отказа_можно_передумать(survey):
    survey.handle("u1", "здравствуйте")
    survey.refuse_consent("u1")
    survey.handle("u1", "начать")
    assert survey.stage("u1") == "survey"


def test_заново_не_переспрашивает_согласие(consented):
    consented.answer_by_numbers("u1", [2])
    consented.restart_after_consent("u1")
    assert consented.stage("u1") == "survey"
    assert consented.state["u1"]["answers"] == {}


def test_удаление_стирает_всё(consented):
    consented.answer_by_numbers("u1", [2])
    consented.handle("u1", "Мария")
    consented.handle("u1", "удалить")
    assert "u1" not in consented.state


def test_удаление_работает_и_до_согласия(survey):
    survey.handle("u1", "здравствуйте")
    survey.handle("u1", "удалить")
    assert "u1" not in survey.state


def test_повторное_согласие_не_меняет_дату(survey):
    """Дата согласия — доказательство. Переписывать её нельзя."""
    survey.handle("u1", "здравствуйте")
    survey.grant_consent("u1")
    было = survey.consented("u1")["at"]
    survey.grant_consent("u1")
    assert survey.consented("u1")["at"] == было


def test_старая_кнопка_согласия_не_сбрасывает_анкету(consented):
    """Кнопка из истории чата не должна ничего ломать."""
    consented.answer_by_numbers("u1", [2])
    было = consented.consented("u1")["at"]
    consented.grant_consent("u1")
    assert consented.consented("u1")["at"] == было
    assert consented.state["u1"]["answers"]["who"] == "О близком человеке"

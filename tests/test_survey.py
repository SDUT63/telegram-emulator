"""Движок анкеты: ветвление, возврат, мультивыбор, проверка ввода."""
import pytest

import walk
from survey_questions import QUESTIONS, STOP_OPTION


def test_анкета_доходит_до_развилки(consented):
    """После обязательной части человека спрашивают, продолжать ли."""
    s = consented
    step, q = walk.дойти_до(s, "continue")
    assert STOP_OPTION in q["options"], "остановиться можно на развилке"


def test_остановка_на_развилке_закрывает_анкету(consented):
    s = consented
    step, q = walk.дойти_до(s, "continue")
    out = s.answer_by_numbers("u1", [q["options"].index(STOP_OPTION) + 1])
    assert s.state["u1"]["finished"]
    assert "Координатор свяжется" in out


def test_обязательная_часть_собирает_контакты(consented):
    """До развилки должно быть собрано всё, без чего не выехать к человеку."""
    s = consented
    заданные = []
    while True:
        step, q = s.current("u1")
        if q["id"] == "continue":
            break
        заданные.append(q["id"])
        walk.ответить(s, "u1", q, {"who": [2]})
    for нужный in ("name", "patient_name", "phone", "district", "address"):
        assert нужный in заданные, f"без «{нужный}» координатор не доедет"


def test_ветвление_лежачий_пропускает_падения(consented):
    s = consented
    заданные = walk.пройти(s, ответы={"mobility": [5]})       # «Не встаёт»
    assert "falls" not in заданные, "у лежачего не спрашивают про падения"
    assert "turning" in заданные, "а про переворачивание — спрашивают"


def test_ходячему_не_задают_вопрос_про_кожу(consented):
    s = consented
    заданные = walk.пройти(s, ответы={"mobility": [1]})       # «Ходит сам»
    assert "skin" not in заданные
    assert "falls" in заданные


def test_назад_стирает_ответ(consented):
    s = consented
    s.answer_by_numbers("u1", [2])
    assert s.state["u1"]["answers"]["who"] == "О близком человеке"
    s.handle("u1", "назад")
    assert "who" not in s.state["u1"]["answers"]


def test_телефон_проверяется(consented):
    s = consented
    walk.дойти_до(s, "phone")
    out = s.handle("u1", "12345")
    assert "не меньше десяти цифр" in out
    assert "phone" not in s.state["u1"]["answers"]


def test_обязательный_вопрос_не_пропускается(consented):
    s = consented
    out = s.handle("u1", "далее")
    assert "пропустить нельзя" in out


def test_мультивыбор_ничего_отменяет_остальное(consented):
    s = consented
    step, q = walk.дойти_до(s, "flags")
    s.toggle("u1", step, 0)
    s.toggle("u1", step, 3)
    assert len(s.picked("u1", step)) == 2
    s.toggle("u1", step, len(q["options"]) - 1)      # «Ничего из этого нет»
    assert s.picked_names("u1", step) == ["Ничего из этого нет"]


def test_отметки_переживают_перезапуск(consented):
    from chatbot_survey import Survey
    s = consented
    step, _ = walk.дойти_до(s, "flags")
    s.toggle("u1", step, 3)
    снова = Survey(storage_path=s.storage_path, list_options=False)
    assert снова.picked("u1", step) == [3], "недоотмеченное не должно теряться"


def test_кнопка_от_другого_вопроса_не_срабатывает(consented):
    s = consented
    step, _ = walk.дойти_до(s, "flags")
    assert s.toggle("u1", step + 5, 0) is False


def test_прогресс_не_идёт_назад(consented):
    s = consented
    walk.дойти_до(s, "need")
    было = s.state["u1"]["total_seen"]
    s.answer_by_numbers("u1", [1])
    assert s.state["u1"]["total_seen"] >= было

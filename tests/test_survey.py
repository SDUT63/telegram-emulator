"""Движок анкеты: ветвление, возврат, мультивыбор, проверка ввода."""
import pytest

from survey_questions import QUESTIONS, STOP_OPTION


def дойти(s, u, *ответы):
    """Пройти анкету списком ответов и вернуть последний текст бота."""
    out = ""
    for a in ответы:
        out = s.answer_by_numbers(u, a) if isinstance(a, list) else s.handle(u, a)
    return out


def test_обязательные_семь_вопросов(consented):
    s = consented
    дойти(s, "u1", [2], "Мария", "89171234567", "далее", [11], [6])
    step, q = s.current("u1")
    assert q["id"] == "continue", "седьмым должен быть выбор, продолжать ли"


def test_остановка_после_седьмого_закрывает_анкету(consented):
    s = consented
    дойти(s, "u1", [2], "Мария", "89171234567", "далее", [11], [6])
    step, q = s.current("u1")
    out = s.answer_by_numbers("u1", [q["options"].index(STOP_OPTION) + 1])
    assert s.state["u1"]["finished"]
    assert "Координатор свяжется" in out


def test_ветвление_лежачий_пропускает_падения(consented):
    s = consented
    дойти(s, "u1", [2], "Мария", "89171234567", "далее", [11], [6], [1], [5])
    заданные = []
    guard = 0
    while s.current("u1") and guard < 40:
        guard += 1
        step, q = s.current("u1")
        заданные.append(q["id"])
        s.handle("u1", "далее") if not q.get("required", True) else (
            s.answer_by_numbers("u1", [1]) if q["kind"] == "choice"
            else s.handle("u1", "Ответ текстом"))
    assert "falls" not in заданные, "у лежачего не спрашивают про падения"
    assert "turning" in заданные, "а про переворачивание — спрашивают"


def test_ходячему_не_задают_вопрос_про_кожу(consented):
    s = consented
    дойти(s, "u1", [2], "Мария", "89171234567", "далее", [11], [6], [1], [1])
    заданные = []
    guard = 0
    while s.current("u1") and guard < 40:
        guard += 1
        step, q = s.current("u1")
        заданные.append(q["id"])
        s.answer_by_numbers("u1", [1]) if q["kind"] == "choice" else s.handle("u1", "Текст")
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
    дойти(s, "u1", [2], "Мария")
    out = s.handle("u1", "12345")
    assert "не меньше десяти цифр" in out
    assert "phone" not in s.state["u1"]["answers"]


def test_обязательный_вопрос_не_пропускается(consented):
    s = consented
    out = s.handle("u1", "далее")
    assert "пропустить нельзя" in out


def test_мультивыбор_ничего_отменяет_остальное(consented):
    s = consented
    дойти(s, "u1", [2], "Мария", "89171234567", "далее")
    step, q = s.current("u1")
    s.toggle("u1", step, 0)
    s.toggle("u1", step, 3)
    assert len(s.picked("u1", step)) == 2
    s.toggle("u1", step, len(q["options"]) - 1)      # «Ничего из этого нет»
    assert s.picked_names("u1", step) == ["Ничего из этого нет"]


def test_отметки_переживают_перезапуск(consented, tmp_path):
    from chatbot_survey import Survey
    s = consented
    дойти(s, "u1", [2], "Мария", "89171234567", "далее")
    step, _ = s.current("u1")
    s.toggle("u1", step, 3)
    снова = Survey(storage_path=s.storage_path, list_options=False)
    assert снова.picked("u1", step) == [3], "недоотмеченное не должно теряться"


def test_кнопка_от_другого_вопроса_не_срабатывает(consented):
    s = consented
    дойти(s, "u1", [2], "Мария", "89171234567", "далее")
    step, _ = s.current("u1")
    assert s.toggle("u1", step + 5, 0) is False


def test_прогресс_не_идёт_назад(consented):
    s = consented
    дойти(s, "u1", [2], "Мария", "89171234567", "далее", [11], [6], [1])
    было = s.state["u1"]["total_seen"]
    дойти(s, "u1", [1])
    assert s.state["u1"]["total_seen"] >= было

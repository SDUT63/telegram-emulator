"""Контактная часть анкеты: адрес, имена, ветвление по тому, о ком речь.

Без адреса служба помощи на дому не может приехать, без имени подопечного
карточка бессмысленна. Эти проверки сторожат ровно это.
"""
import pytest

import walk
from survey_questions import QUESTIONS


# ----------------------------------------------------------------- адрес

@pytest.mark.parametrize("ветка", [1, 2, 3])
def test_адрес_спрашивается_всегда(consented, ветка):
    """Ни одна группа обратившихся не проходит анкету без адреса."""
    walk.дойти_до(consented, "address", ветка=ветка)


@pytest.mark.parametrize("плохой", ["Ворошилова", "дом", "тут", "улица моя"])
def test_адрес_без_номера_дома_не_принимается(consented, плохой):
    s = consented
    walk.дойти_до(s, "address", ветка=1)
    out = s.handle("u1", плохой)
    assert "номером дома" in out
    assert "address" not in s.state["u1"]["answers"]


@pytest.mark.parametrize("хороший", ["Ворошилова 19, кв. 5", "ул. Мира, д. 3",
                                     "Автозаводское шоссе 12-45"])
def test_нормальный_адрес_принимается(consented, хороший):
    s = consented
    walk.дойти_до(s, "address", ветка=1)
    s.handle("u1", хороший)
    assert s.state["u1"]["answers"]["address"] == хороший


def test_адрес_нельзя_пропустить(consented):
    s = consented
    walk.дойти_до(s, "address", ветка=1)
    out = s.handle("u1", "далее")
    assert "пропустить нельзя" in out


def test_район_отдельным_вопросом(consented):
    """Разбирать район из свободной строки ненадёжно, поэтому он — кнопки."""
    s = consented
    step, q = walk.дойти_до(s, "district", ветка=1)
    assert q["kind"] == "choice"
    assert "Автозаводский" in q["options"]


# -------------------------------------------------------------- о ком речь

def test_о_себе_не_спрашивают_лишнего(consented):
    заданные = walk.пройти(consented, ветка=1)
    assert "relation" not in заданные, "о себе — не спрашиваем, кем приходится"
    assert "patient_name" not in заданные, "о себе — имя уже есть"
    assert "aware" not in заданные, "о себе — уведомлять некого"


def test_о_близком_спрашивают_имя_подопечного(consented):
    заданные = walk.пройти(consented, ветка=2)
    assert "relation" in заданные
    assert "patient_name" in заданные


def test_специалисту_не_задают_кем_он_приходится(consented):
    """Специалист никем не приходится — он по работе."""
    заданные = walk.пройти(consented, ветка=3)
    assert "relation" not in заданные
    assert "patient_name" in заданные, "имя пациента специалист назвать должен"
    assert "aware" in заданные, "уведомить человека всё равно надо"


def test_вопрос_про_осведомлённость_есть(consented):
    """Часть 3 статьи 18: если данные не от субъекта, его надо уведомить."""
    s = consented
    step, q = walk.дойти_до(s, "aware", ветка=2)
    assert "знает" in q["text"].lower()


# ------------------------------------------------------------- сводка

def test_в_сводке_координатора_есть_адрес_и_имя_подопечного(consented):
    s = consented
    walk.дойти_до(s, "patient_name", ветка=2)
    s.handle("u1", "Анна Петровна")
    walk.дойти_до(s, "address", ответы={"district": [1]})
    s.handle("u1", "Ворошилова 19, кв. 5")
    сводка = s.brief("u1")
    assert "Анна Петровна" in сводка, "звонят подопечному, а не только заявителю"
    assert "Ворошилова 19" in сводка
    assert "Автозаводский" in сводка


def test_у_каждого_нового_вопроса_короткие_подписи():
    """MAX режет длинные подписи. Новые вопросы — не исключение."""
    import max_bot
    новые = ("relation", "aware", "when_call", "district", "floor")
    беда = []
    for q in QUESTIONS:
        if q["id"] in новые:
            for o in q["options"]:
                if max_bot._width(o) > 17.5:
                    беда.append((q["id"], o))
    assert not беда, f"не влезет в два столбца: {беда}"

"""Как анкета читается человеком.

Проверки здесь не про логику, а про язык и вёрстку: вопрос должен быть
вопросом, а не графой в бланке, и подпись кнопки должна быть видна
целиком. MAX не переносит подпись — он обрезает её многоточием, и
человек видит «Программа соцусл…», не понимая, что выбирает.
"""
import max_bot
from chatbot_survey import Survey
from survey_questions import QUESTIONS


def показанные(q):
    """Варианты так, как их раскладывает бот: «ничего из этого» уходит вниз."""
    options = q.get("options") or []
    nothing = Survey.none_index(q) if q.get("multi") else None
    return [o for i, o in enumerate(options) if i != nothing]


# ------------------------------------------------------------- подписи

def test_ни_одна_подпись_не_обрежется():
    беда = []
    for q in QUESTIONS:
        варианты = показанные(q)
        if not варианты:
            continue
        multi = bool(q.get("multi"))
        предел = max_bot.TWO_COLUMNS_AT_MULTI if multi else max_bot.TWO_COLUMNS_AT
        if max_bot._columns(варианты, multi) != 2:
            continue                       # в один столбец влезает всё
        беда += [(q["id"], o, round(max_bot._width(o), 1))
                 for o in варианты if max_bot._width(o) > предел]
    assert not беда, f"в два столбца не влезает: {беда}"


def test_подписи_не_длиннее_предела_max():
    for q in QUESTIONS:
        for o in q.get("options", []):
            assert len(o) <= max_bot.BUTTON_LIMIT, (q["id"], o)


def test_кнопок_под_вопросом_не_больше_дюжины():
    """Экран из двадцати кнопок человек не читает, а закрывает."""
    for q in QUESTIONS:
        варианты = показанные(q)
        assert len(варианты) <= 12, f"{q['id']}: {len(варианты)} вариантов"


# --------------------------------------------------------------- вопросы

def первая_строка(q):
    """Сам вопрос. Ниже могут идти подсказка и пример — их не проверяем."""
    return q["text"].split("\n")[0].strip()


def test_вопрос_сформулирован_а_не_назван():
    """«Туалет.» — это графа в бланке. Человека спрашивают словами."""
    коротко = [q["id"] for q in QUESTIONS if len(первая_строка(q).split()) < 2]
    assert not коротко, f"однословная формулировка: {коротко}"


def test_вопрос_заканчивается_знаком():
    для_беды = [q["id"] for q in QUESTIONS
                if not первая_строка(q).endswith((".", "?", ":"))]
    assert not для_беды, для_беды


def test_варианты_не_повторяются_внутри_вопроса():
    for q in QUESTIONS:
        варианты = q.get("options") or []
        assert len(варианты) == len(set(варианты)), q["id"]


def test_у_свободного_ответа_есть_пример_или_подсказка():
    """Открытый вопрос без примера каждый понимает по-своему."""
    for q in QUESTIONS:
        if q["kind"] not in ("text", "address", "phone"):
            continue
        текст = q["text"]
        assert len(текст) > 20 or q["id"] in ("name", "phone"), (
            f"{q['id']}: короткий открытый вопрос без подсказки")


# ---------------------------------------------------------------- разделы

def test_у_каждого_вопроса_есть_раздел():
    """Строка «Подвижность · вопрос 8 из 29» — это ориентир в анкете."""
    раздел = None
    for q in QUESTIONS:
        раздел = q.get("section", раздел)
        assert раздел, f"{q['id']} идёт раньше первого раздела"


def test_разделы_не_чередуются():
    """Раздел, встретившийся дважды с перерывом, — это сбитый порядок."""
    встречены, прошлый, повтор = set(), None, []
    for q in QUESTIONS:
        section = q.get("section")
        if not section or section == прошлый:
            continue
        if section in встречены:
            повтор.append(section)
        встречены.add(section)
        прошлый = section
    assert not повтор, f"раздел начинается заново: {повтор}"

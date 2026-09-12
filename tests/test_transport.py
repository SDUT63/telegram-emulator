"""Транспорт: дедупликация, ширина кнопок, отсутствие ПД в журнале."""
import re

import walk
import max_bot
from chatbot_survey import Survey
from survey_questions import QUESTIONS


# ------------------------------------------------- повторная доставка

def test_повтор_события_отсекается():
    seen = max_bot.Seen()
    assert seen.fresh("mid-1") is True
    assert seen.fresh("mid-1") is False
    assert seen.fresh("mid-2") is True


def test_без_ключа_событие_обрабатывается():
    """Потерять обращение хуже, чем обработать дважды."""
    seen = max_bot.Seen()
    assert seen.fresh(None) is True
    assert seen.fresh(None) is True


def test_память_не_растёт_бесконечно():
    seen = max_bot.Seen(limit=10)
    for i in range(50):
        seen.fresh(f"k{i}")
    assert len(seen._keys) <= 10
    assert seen.fresh("k49") is False, "свежие должны оставаться"
    assert seen.fresh("k0") is True, "старые вытесняются"


# ------------------------------------------------- клавиатуры

def test_ни_одна_подпись_не_будет_обрезана(consented):
    """MAX не переносит подпись, а режет многоточием. Проверяем ширину."""
    s = consented
    беда = []
    for i, q in enumerate(QUESTIONS):
        if q["kind"] != "choice":
            continue
        s.state["z"] = {**Survey._blank(), "step": i, "history": [0],
                        "consent": {"at": "now", "version": "1.0"},
                        "answers": {"continue": "Продолжить",
                                    "mobility": "Не встаёт", "pain": "Постоянная"}}
        m = max_bot.keyboard_for(s, "z")
        for row in m.payload.buttons:
            if len(row) < 2:
                continue                      # во всю ширину помещается всё
            for b in row:
                if max_bot._width(b.text) > 18.5:
                    беда.append((q["id"], b.text))
    assert not беда, f"в два столбца не влезает: {беда}"


def test_раскладка_и_клавиатура_совпадают(consented):
    """Мануал рисуется по layout, бот шлёт keyboard_for. Разойтись нельзя."""
    s = consented
    walk.дойти_до(s, "flags")
    подписи = [[label for label, _ in row] for row in max_bot.layout(s, "u1")]
    m = max_bot.keyboard_for(s, "u1")
    assert подписи == [[b.text for b in row] for row in m.payload.buttons]


def test_кнопки_согласия_вместо_вопросов(survey):
    survey.handle("u1", "здравствуйте")
    m = max_bot.keyboard_for(survey, "u1")
    тексты = [b.text for r in m.payload.buttons for b in r]
    assert тексты == ["Согласен, продолжим", "Прочитать полностью",
                      "Не согласен", "Просто почитать"]
    assert тексты[1] != тексты[-1], "чтение стоит между согласием и отказом"
    # Решение — три равновеликие кнопки; четвёртая не решение, а выход
    # мимо порога: материалы читаются и без согласия.
    assert тексты.index("Просто почитать") == len(тексты) - 1


def test_после_согласия_появляются_варианты(consented):
    m = max_bot.keyboard_for(consented, "u1")
    тексты = [b.text for r in m.payload.buttons for b in r]
    assert "О себе" in тексты


def test_подпись_не_длиннее_предела_max(consented):
    for i, q in enumerate(QUESTIONS):
        for o in q.get("options", []):
            assert len(o) <= max_bot.BUTTON_LIMIT, (q["id"], o)


# ------------------------------------------------- журнал

def test_в_журнал_не_пишется_текст_сообщения():
    """Регресс-тест: строка с текстом входящего однажды уже была."""
    src = open(max_bot.__file__, encoding="utf-8").read()
    подозрительные = re.findall(r'log\.\w+\([^)]*incoming[^)]*\)', src)
    assert not подозрительные, f"текст входящего попадает в журнал: {подозрительные}"


def test_в_журнал_не_пишутся_ответы():
    src = open(max_bot.__file__, encoding="utf-8").read()
    for плохо in ("log.info(\"%s\", reply", "log.info(reply", "log.info(chosen"):
        assert плохо not in src


# ------------------------------------------------- вебхук

def test_вебхук_требует_https():
    import max_webhook
    беды = max_webhook.check_settings("http://bot.example.ru/max", 8443)
    assert any("https" in b for b in беды)


def test_вебхук_отсекает_неподдерживаемый_порт():
    import max_webhook
    беды = max_webhook.check_settings("https://bot.example.ru/max", 5000)
    assert any("5000" in b for b in беды)
    assert max_webhook.check_settings("https://bot.example.ru/max", 8443) == []
    assert max_webhook.check_settings("https://bot.example.ru/max", 20000) == []


def test_вебхук_требует_адрес():
    import max_webhook
    assert max_webhook.check_settings("", 443)

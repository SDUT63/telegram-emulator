"""Бот не молчит и не повторяется.

Человек, у которого дома лежачий отец, уходит после второй одинаковой
отписки и не возвращается. Здесь проверяется, что вместо отписки идёт
лесенка: каждая следующая попытка получает другой ответ и другой выход.
"""
import pytest

import fallback
import walk


# --------------------------------------------------------- сама лесенка

@pytest.mark.parametrize("где", list(fallback.ЛЕСЕНКИ))
def test_ступеней_хватает(где):
    assert fallback.ступеней(где) >= 6


def test_у_главных_лесенок_десять_ступеней():
    """Столько, сколько заказано: до десяти разных фраз."""
    assert fallback.ступеней(fallback.ВОПРОС) == 10
    assert fallback.ступеней(fallback.АНКЕТА) == 10


@pytest.mark.parametrize("где", list(fallback.ЛЕСЕНКИ))
def test_фразы_не_повторяются(где):
    лесенка = fallback.ЛЕСЕНКИ[где]
    assert len(set(лесенка)) == len(лесенка)


@pytest.mark.parametrize("где", list(fallback.ЛЕСЕНКИ))
def test_из_каждой_фразы_есть_выход(где):
    """Тупиков нет: в каждой фразе названо, что человек может сделать."""
    без_выхода = [ф for ф in fallback.ЛЕСЕНКИ[где]
                  if not any(в in ф.lower() for в in fallback.ВЫХОДЫ)]
    assert not без_выхода, без_выхода


@pytest.mark.parametrize("где", list(fallback.ЛЕСЕНКИ))
def test_к_концу_лесенки_зовут_человека(где):
    """Бот, который до последнего делает вид, что справляется, — врёт."""
    лесенка = fallback.ЛЕСЕНКИ[где]
    вторая_половина = " ".join(лесенка[len(лесенка) // 2:]).lower()
    assert any(с in вторая_половина
               for с in ("координатор", "103", "позвонят", "телефон"))


@pytest.mark.parametrize("где", list(fallback.ЛЕСЕНКИ))
def test_никого_не_винят(где):
    """Непонимание — наша беда. Слова обвинения в лесенке недопустимы."""
    плохое = ("неправильно", "ошибк", "вы не поняли", "внимательн")
    беда = [ф for ф in fallback.ЛЕСЕНКИ[где]
            if any(с in ф.lower() for с in плохое)]
    assert not беда, беда


@pytest.mark.parametrize("где", list(fallback.ЛЕСЕНКИ))
def test_фразы_короткие(где):
    """Длинную отписку не читают вовсе — она хуже короткой."""
    длинные = [ф for ф in fallback.ЛЕСЕНКИ[где] if len(ф) > 240]
    assert not длинные, длинные


def test_за_последней_ступенью_бот_не_замолкает():
    """Сотая попытка подряд — тоже попытка. Ответ должен быть."""
    for n in (11, 25, 100, 10_000):
        assert fallback.фраза(n, fallback.АНКЕТА).strip()
    assert fallback.фраза(99, fallback.ВОПРОС) == fallback.ПО_ВОПРОСУ[-1]


def test_нулевая_и_отрицательная_попытка_дают_первую_ступень():
    for n in (0, -1, None):
        assert fallback.фраза(n) == fallback.ПО_ВОПРОСУ[0]


def test_неизвестное_место_разговора_не_роняет_бота():
    assert fallback.фраза(3, "чего-то такого") == fallback.ПО_ВОПРОСУ[2]


# ------------------------------------------------------- лесенка в анкете

def test_анкета_не_повторяет_одну_придирку(consented):
    """Три промаха подряд — три разных ответа, а не одна и та же строка."""
    ответы = [consented.handle("u1", "ыыы") for _ in range(3)]
    assert len(set(ответы)) == 3, "бот твердит одно и то же"
    assert consented.misses("u1") == 3


def test_принятый_ответ_обнуляет_лесенку(consented):
    consented.handle("u1", "ыыы")
    consented.handle("u1", "ыыы")
    assert consented.misses("u1") == 2
    walk.ответить(consented, "u1", consented.current("u1")[1])
    assert consented.misses("u1") == 0


def test_узнанное_слово_обнуляет_лесенку(consented):
    consented.handle("u1", "ыыы")
    consented.handle("u1", "помощь")
    assert consented.misses("u1") == 0


def test_в_анкете_предлагают_выход_а_не_только_придирку(consented):
    """Со второго промаха человеку называют, как выйти из тупика."""
    consented.handle("u1", "ыыы")
    второй = consented.handle("u1", "ыыы")
    assert fallback.фраза(2, fallback.АНКЕТА) in второй


def test_вопрос_остаётся_на_месте(consented):
    """Лесенка не пропускает вопрос: человек стоит там же, где стоял."""
    было = consented.current("u1")[0]
    for _ in range(5):
        consented.handle("u1", "ыыы")
    assert consented.current("u1")[0] == было


# ------------------------------------------------------ лесенка до согласия

def test_до_согласия_бот_тоже_не_повторяется(survey):
    survey.handle("u1", "здравствуйте")
    ответы = [survey.handle("u1", f"а что это вообще такое {n}")
              for n in range(4)]
    assert len(set(ответы)) == 4


def test_согласие_обнуляет_лесенку(survey):
    survey.handle("u1", "здравствуйте")
    survey.handle("u1", "непонятно что-то")
    assert survey.misses("u1") == 1
    survey.handle("u1", "согласен")
    assert survey.misses("u1") == 0


def test_до_согласия_ничего_не_записывается(survey):
    """Лесенка не должна стать поводом сохранить что-то до согласия."""
    survey.handle("u1", "здравствуйте")
    for _ in range(5):
        survey.handle("u1", "мама лежит и не встаёт совсем")
    assert not survey.state["u1"]["answers"]
    assert survey.stage("u1") == "consent"


# --------------------------------------------- обязательный вопрос не врёт

def test_обязательный_вопрос_не_обещают_пропустить():
    """Пообещать и не сделать хуже, чем не обещать."""
    for n in fallback.ПО_АНКЕТЕ_БЕЗ_ПРОПУСКА:
        сказано = fallback.фраза(n, fallback.АНКЕТА, пропуск=False)
        assert "пропустить»" not in сказано.lower()
        assert сказано != fallback.фраза(n, fallback.АНКЕТА)


def test_у_замен_тоже_есть_выход():
    for сказано in fallback.ПО_АНКЕТЕ_БЕЗ_ПРОПУСКА.values():
        assert any(в in сказано.lower() for в in fallback.ВЫХОДЫ), сказано


def test_в_анкете_не_предлагают_пропустить_обязательный(consented):
    """Первый вопрос обязательный: обещания пропуска быть не должно."""
    сказанное = [consented.handle("u1", "ыыы") for _ in range(10)]
    assert all("напишите «пропустить»" not in с.lower() for с in сказанное)


def test_обещанный_на_восьмой_ступени_телефон_доходит(consented):
    """Бот предлагает оставить номер вместо ответа — значит, номер дойдёт."""
    for _ in range(7):
        consented.handle("u1", "ыыы")
    восьмая = consented.handle("u1", "ыыы")
    assert "телефон" in восьмая.lower()

    consented.handle("u1", "89171234567")
    переписка = " ".join(m["text"] for m in consented.messages("u1"))
    assert "89171234567" in переписка, "номер потерялся, а его обещали передать"

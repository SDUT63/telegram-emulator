"""Согласие на обработку данных — правовой рубеж релиза.

До согласия анкета не должна собирать ничего. Проверяем это буквально:
не «первый вопрос не показан», а «ответ не записан ни при каком вводе».
"""
import pytest

import max_bot
from chatbot_survey import (CONSENT_FULL, CONSENT_NO, CONSENT_SHORT,
                            CONSENT_VERSION)


def test_первое_сообщение_показывает_согласие(survey):
    out = survey.handle("u1", "здравствуйте")
    assert "только с вашего согласия" in out
    assert survey.stage("u1") == "consent"


def test_первый_экран_не_длиннее_страницы(survey):
    """Стена юридического текста на входе — потерянный человек.

    Длину сторожим числом: она незаметно отрастает от правки к правке.
    """
    out = survey.handle("u1", "здравствуйте")
    assert len(out) < 800, f"первый экран разросся до {len(out)} знаков"


def test_полный_текст_приходит_в_чат_а_не_ссылкой(survey):
    """Уводить на сайт за тем, под чем человек подпишется, нельзя."""
    survey.handle("u1", "здравствуйте")
    полный = survey.consent_text("u1")
    assert полный.startswith(CONSENT_FULL[:40])
    assert "http" not in полный, "никаких ссылок — текст читают здесь"
    кнопки = [действие for row in max_bot.layout(survey, "u1") for _, действие in row]
    assert "c:full" in кнопки, "полный текст должен быть в одно нажатие"


def test_в_полном_тексте_есть_всё_обязательное(survey):
    """Часть 4 статьи 9 ФЗ-152 перечисляет, что там должно стоять."""
    survey.handle("u1", "здравствуйте")
    полный = survey.consent_text("u1")
    for обязательное in ("1266300009766",          # ОГРН оператора
                         "6320093220",             # ИНН
                         "Ворошилова",             # адрес оператора
                         "телефон",                # перечень данных
                         "координатор",            # цель
                         "храним",                 # срок
                         "удалить"):               # порядок отзыва
        assert обязательное in полный, обязательное


def test_после_согласия_видно_когда_оно_дано(consented):
    полный = consented.consent_text("u1")
    assert "Вы дали согласие" in полный
    assert "года" in полный, "дату человек должен прочитать, а не расшифровать"


def test_полный_текст_можно_попросить_словом(survey):
    survey.handle("u1", "здравствуйте")
    out = survey.handle("u1", "полностью")
    assert "КТО СОБИРАЕТ" in out
    assert survey.stage("u1") == "consent", "чтение — не согласие"


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
    assert "горячую линию" not in CONSENT_NO, "линии пока нет — не обещаем"
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


def test_документ_согласия_совпадает_с_ботом():
    """Бумага и чат — один текст. Разойтись им нельзя.

    Человек подписывает при встрече то, что читал в чате. Комплект форм
    собирается из этих же констант (docs/сборка-согласия.py), и проверка
    ловит случай, когда текст правили, а документ не пересобрали.
    """
    docx = pytest.importorskip("docx")
    файл = "docs/Согласие-на-обработку-ПД-СДУТ.docx"
    d = docx.Document(файл)
    куски = [par.text for par in d.paragraphs]
    for таблица in d.tables:
        for строка in таблица.rows:
            куски.extend(ячейка.text for ячейка in строка.cells)
    документ = "\n".join(куски)

    for текст in (CONSENT_SHORT, CONSENT_FULL):
        отсутствуют = [s for s in текст.split("\n") if s.strip() and s not in документ]
        assert not отсутствуют, (
            "документ не пересобран: " + отсутствуют[0][:60]
            + " — запустите python docs/сборка-согласия.py")


def test_согласие_не_закрывает_доступ_к_материалам(survey):
    """Экран согласия — первый порог. На нём должен быть выход мимо него.

    Человек, который ещё не решил, готов ли он оставлять о себе сведения,
    всё равно имеет право прочитать, что положено и куда идти. Выход сделан
    кнопкой, а не строчкой в тексте: первый экран и так на пределе длины.
    """
    import max_bot
    survey.handle("u1", "здравствуйте")
    адреса = [адрес for ряд in max_bot.layout(survey, "u1") for _, адрес in ряд]
    assert "map" in адреса, "с экрана согласия нет дороги к материалам"


def test_до_согласия_материалы_действительно_открываются(survey):
    """Обещание на экране согласия должно работать, а не быть фигурой речи."""
    import asyncio
    import max_bot
    from tests.test_map import Кнопки

    survey.handle("u1", "здравствуйте")
    assert survey.stage("u1") == "consent"

    бот = Кнопки()
    asyncio.run(max_bot.меню_тем(бот, 1, "u1", survey))
    assert бот.кнопки[0], "карта тем до согласия не открылась"
    # Ничего при этом не записано: согласия ещё нет
    assert not survey.state["u1"]["answers"]

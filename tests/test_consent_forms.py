"""Формы согласия по группам обратившихся.

Проверяем не тексты, а то, что от них зависит: каждому варианту первого
вопроса соответствует своя форма, и все три варианта покрыты.
"""
import consent_forms as cf
import walk
from survey_questions import QUESTIONS


def первый_вопрос():
    return next(q for q in QUESTIONS if q["id"] == "who")


def test_каждый_вариант_первого_вопроса_имеет_форму():
    непокрытые = [o for o in первый_вопрос()["options"] if cf.for_answer(o) is None]
    assert not непокрытые, f"нет формы для: {непокрытые}"


def test_форма_привязана_к_первому_вопросу():
    assert первый_вопрос().get("after") is cf.note


def test_на_чужой_ответ_ничего_не_говорим():
    assert cf.for_answer("что-то другое") is None
    assert cf.note({"who": "что-то другое"}) == ""


def test_о_близком_упоминает_документ_о_полномочиях():
    текст = cf.for_answer("О близком человеке")["text"]
    assert "полномочи" in текст.lower()
    assert "родство" in текст.lower()


def test_специалисту_сказано_что_он_не_может_дать_согласие():
    текст = cf.for_answer("Я специалист")["text"]
    assert "не может" in текст.lower()
    assert "18" in текст, "нужна ссылка на обязанность уведомить субъекта"


def test_к_сообщению_приложена_ссылка_на_формы():
    ответ = cf.note({"who": "О себе"})
    assert isinstance(ответ, dict)
    подпись, адрес = ответ["links"][0]
    assert адрес.startswith("https://")
    assert "#b-docs" in адрес, "ссылка должна вести в раздел документов"


def test_раздел_документов_существует_в_справочнике():
    """Ссылка не должна вести в никуда."""
    страница = open("webapp/index.html", encoding="utf-8").read()
    якорь = cf.DOCS_ANCHOR.split("#")[1]
    assert f'id="{якорь}"' in страница
    assert f'href="#{якорь}"' in страница, "нужен и чип в навигации"


def test_ссылка_снимается_после_следующего_ответа(consented):
    s = consented
    s.answer_by_numbers("u1", [2])
    assert s.links("u1"), "после первого вопроса ссылка есть"
    step, q = s.current("u1")
    walk.ответить(s, "u1", q)
    assert s.links("u1") == [], "на следующем шаге её быть не должно"


def test_реквизиты_организации_совпадают_с_егрюл():
    """Один и тот же ОГРН в справочнике и в формах — иначе кто-то устарел."""
    страница = open("webapp/index.html", encoding="utf-8").read()
    for значение in ("1266300009766", "6320093220", "632001001"):
        assert значение in страница, f"в справочнике нет {значение}"

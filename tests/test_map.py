"""Карта тем: человек не должен теряться.

Двести с лишним статей в двадцати шести разделах — это не оглавление,
а свалка: половина разделов пришла из справочника, половина из наших
файлов, и они дублируют друг друга. Карта собирает из них одиннадцать
ветвей, которые человек называет сам, когда у него беда.

Здесь проверяется главное свойство навигации: из любого места видно,
где ты находишься, и есть дорога назад. Тупиков нет.
"""
import asyncio
import os
import tempfile

import pytest

import knowledge
import max_bot


class Кнопки:
    """Фейковый бот, который запоминает и подписи, и payload кнопок."""

    def __init__(self):
        self.ушло: list[str] = []
        self.кнопки: list[list[tuple[str, str]]] = []

    async def send_message(self, **kw):
        self.ушло.append(kw.get("text", ""))
        собрано = []
        for вложение in (kw.get("attachments") or []):
            for строка in вложение.payload.buttons:
                собрано += [(к.text, getattr(к, "payload", "")) for к in строка]
        self.кнопки.append(собрано)

    @property
    def подписи(self):
        return [т for т, _ in self.кнопки[0]]

    @property
    def адреса(self):
        return [п for _, п in self.кнопки[0]]


@pytest.fixture
def анкета_в_процессе():
    from chatbot_survey import Survey
    папка = tempfile.mkdtemp()
    s = Survey(storage_path=os.path.join(папка, "r.json"), list_options=False)
    s.handle("u1", "здравствуйте")
    s.grant_consent("u1")
    return s


# ------------------------------------------------------------ сама карта

def test_ни_одна_статья_не_потеряна():
    """Главное свойство: в карте есть всё, кроме нарочно исключённого."""
    assert knowledge.вне_карты() == []


def test_исключённое_в_карту_не_попало():
    """Текст согласия — не тема для чтения, у него своя кнопка в анкете."""
    в_карте = {з for в in knowledge.карта()
               for з in knowledge.ветвь(в["id"])["статьи"]}
    лишние = [с.заголовок for с in knowledge.загрузить()
              if с.раздел in knowledge.НЕ_В_КАРТЕ and с.заголовок in в_карте]
    assert not лишние, лишние


def test_каждая_статья_ровно_в_одной_ветви():
    видели: dict[str, str] = {}
    for в in knowledge.карта():
        for з in knowledge.ветвь(в["id"])["статьи"]:
            assert з not in видели, f"{з}: и {видели.get(з)}, и {в['id']}"
            видели[з] = в["id"]


def test_пустых_ветвей_нет():
    assert all(в["сколько"] > 0 for в in knowledge.карта())


def test_ветвь_не_глубже_семи_страниц():
    """Длинная ветвь — та же свалка, только в кнопках."""
    длинные = [(в["название"], -(-в["сколько"] // knowledge.НА_СТРАНИЦЕ))
               for в in knowledge.карта()
               if в["сколько"] > 7 * knowledge.НА_СТРАНИЦЕ]
    assert not длинные, длинные


def test_на_странице_не_больше_шести():
    for в in knowledge.карта():
        стр = knowledge.страница(в["id"], 1)
        assert len(стр["статьи"]) <= knowledge.НА_СТРАНИЦЕ


def test_закреплённые_статьи_существуют():
    """Опечатка в списке «первыми» молча теряет закрепление."""
    пропали = [з for в in knowledge.КАРТА for з in в.первыми
               if not knowledge.по_заголовку(з)]
    assert not пропали, пропали


def test_закреплённые_идут_первыми():
    for в in knowledge.КАРТА:
        статьи = knowledge.ветвь(в.id)["статьи"]
        живые = [з for з in в.первыми if knowledge.по_заголовку(з)]
        assert статьи[:len(живые)] == живые, в.id


def test_свои_статьи_раньше_карточек_справочника():
    """Карточки справочника коротки и написаны для страницы, а не для чата."""
    откуда = {с.заголовок: с.id.split("#")[0] for с in knowledge.загрузить()}
    for в in knowledge.карта():
        статьи = knowledge.ветвь(в["id"])["статьи"]
        свои = [i for i, з in enumerate(статьи)
                if not откуда[з].startswith("b-") and откуда[з] != "шкала"]
        чужие = [i for i, з in enumerate(статьи) if откуда[з].startswith("b-")]
        if свои and чужие:
            assert max(свои) < min(чужие), в["название"]


def test_ветви_именуются_латиницей():
    """Идентификатор уезжает в payload кнопки — там кириллице не место."""
    for в in knowledge.КАРТА:
        assert в.id.isascii() and в.id.isidentifier(), в.id
    assert len({в.id for в in knowledge.КАРТА}) == len(knowledge.КАРТА)


@pytest.mark.parametrize("поле", ["название", "кратко"])
def test_подписи_ветвей_влезают_на_кнопку(поле):
    for в in knowledge.КАРТА:
        текст = getattr(в, поле)
        assert len(текст) <= max_bot.BUTTON_LIMIT
        assert max_bot._fits(текст) == текст, текст


def test_короткое_имя_влезает_в_два_столбца():
    """Кнопка «назад» стоит в одном ряду с «Все темы»."""
    for в in knowledge.КАРТА:
        assert max_bot._width("‹ " + в.кратко) <= max_bot.TWO_COLUMNS_AT, в.кратко


# --------------------------------------------------------------- страницы

def test_номер_страницы_не_выходит_за_края():
    for номер in (-5, 0, 1, 999):
        стр = knowledge.страница("uhod", номер)
        assert 1 <= стр["номер"] <= стр["всего"]


def test_страницы_покрывают_ветвь_без_дыр():
    for в in knowledge.карта():
        собрано = []
        for n in range(1, knowledge.страница(в["id"], 1)["всего"] + 1):
            собрано += knowledge.страница(в["id"], n)["статьи"]
        assert собрано == knowledge.ветвь(в["id"])["статьи"], в["id"]


def test_неизвестная_ветвь_не_роняет_бота():
    assert knowledge.ветвь("такого-нет") is None
    assert knowledge.страница("такого-нет", 1) is None


# ------------------------------------------------------- путь и соседи

def test_у_статьи_есть_путь():
    путь = knowledge.путь("Пролежни: где искать и что делать при покраснении")
    assert путь and путь.endswith("›")


def test_соседи_из_той_же_ветви():
    з = "Пролежни: где искать и что делать при покраснении"
    моя = knowledge.где(з)
    assert all(knowledge.где(с) == моя for с in knowledge.соседи(з))
    assert з not in knowledge.соседи(з)


# ------------------------------------------------------------- в боте

def test_на_карте_кнопка_на_каждую_ветвь():
    бот = Кнопки()
    asyncio.run(max_bot.меню_тем(бот, 1, "u1"))
    assert бот.подписи == [в["название"] for в in knowledge.карта()]
    for адрес in бот.адреса:
        ветвь = адрес.split(":")[1]
        assert knowledge.ветвь(ветвь), адрес


def test_карта_это_не_стена_текста():
    """Человек в беде не читает оглавление. Он смотрит на кнопки."""
    бот = Кнопки()
    asyncio.run(max_bot.меню_тем(бот, 1, "u1"))
    assert len(бот.ушло[0]) < 200


def test_в_ветви_видно_где_ты_и_сколько_ещё():
    бот = Кнопки()
    assert asyncio.run(max_bot.ветвь(бот, 1, "u1", "uhod", 2))
    текст = бот.ушло[0]
    стр = knowledge.страница("uhod", 2)
    assert стр["название"] in текст
    assert str(стр["статей"]) in текст
    assert max_bot.ВСЕ_ТЕМЫ in бот.подписи


def test_листалка_появляется_и_исчезает():
    первая = Кнопки(); asyncio.run(max_bot.ветвь(первая, 1, "u1", "uhod", 1))
    assert max_bot.НАЗАД not in первая.подписи
    assert max_bot.ДАЛЬШЕ in первая.подписи

    последняя_n = knowledge.страница("uhod", 1)["всего"]
    последняя = Кнопки()
    asyncio.run(max_bot.ветвь(последняя, 1, "u1", "uhod", последняя_n))
    assert max_bot.НАЗАД in последняя.подписи
    assert max_bot.ДАЛЬШЕ not in последняя.подписи

    одна = Кнопки(); asyncio.run(max_bot.ветвь(одна, 1, "u1", "sluzhba", 1))
    assert knowledge.страница("sluzhba", 1)["всего"] >= 1


def test_под_статьёй_есть_дорога_назад():
    бот = Кнопки()
    assert asyncio.run(max_bot.статья(
        бот, 1, "u1", "Пролежни: где искать и что делать при покраснении"))
    assert бот.ушло[0].startswith(knowledge.путь(
        "Пролежни: где искать и что делать при покраснении"))
    assert max_bot.ВСЕ_ТЕМЫ in бот.подписи
    assert any(п.startswith("v:") for п in бот.адреса), "нет кнопки в раздел"


def test_ни_один_экран_не_тупик():
    """На каждом экране карты есть хотя бы одна кнопка навигации."""
    экраны = []
    б = Кнопки(); asyncio.run(max_bot.меню_тем(б, 1, "u1")); экраны.append(б)
    for в in knowledge.карта():
        б = Кнопки()
        asyncio.run(max_bot.ветвь(б, 1, "u1", в["id"], 1))
        экраны.append(б)
        первая = knowledge.страница(в["id"], 1)["статьи"][0]
        б = Кнопки()
        asyncio.run(max_bot.статья(б, 1, "u1", первая))
        экраны.append(б)
    for б in экраны:
        assert б.кнопки[0], "экран без кнопок"
        assert any(п == "m" or п.startswith("v:") for _, п in б.кнопки[0])


def test_все_кнопки_тем_ведут_в_живые_статьи():
    for в in knowledge.карта():
        for n in range(1, knowledge.страница(в["id"], 1)["всего"] + 1):
            б = Кнопки()
            asyncio.run(max_bot.ветвь(б, 1, "u1", в["id"], n))
            for подпись, адрес in б.кнопки[0]:
                if адрес.startswith("k:"):
                    assert knowledge.по_заголовку(адрес[2:]) or \
                        any(з.startswith(адрес[2:]) for з
                            in knowledge.ветвь(в["id"])["статьи"]), адрес


# ------------------------------------------------ дорога обратно в анкету

def test_из_чтения_есть_возврат_к_анкете(анкета_в_процессе):
    """Иначе человек уходит в базу и не возвращается, а анкета — пустая."""
    s = анкета_в_процессе
    for зовём in (
        lambda б: max_bot.меню_тем(б, 1, "u1", s),
        lambda б: max_bot.ветвь(б, 1, "u1", "uhod", 1, s),
        lambda б: max_bot.статья(б, 1, "u1", "Что делать, если человек упал", s),
    ):
        б = Кнопки()
        asyncio.run(зовём(б))
        assert max_bot.К_АНКЕТЕ in б.подписи
        assert "q" in б.адреса


def test_без_анкеты_кнопки_возврата_нет():
    б = Кнопки()
    asyncio.run(max_bot.меню_тем(б, 1, "u1"))
    assert max_bot.К_АНКЕТЕ not in б.подписи

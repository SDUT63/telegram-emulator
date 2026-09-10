"""Переписка человека с координатором.

Человек, дозаполнивший анкету, пишет в чат дальше: спрашивает, когда
позвонят, присылает фотографию выписки, отвечает на сообщение оператора.
Раньше всё это пропадало — бот отвечал «анкета уже заполнена» и забывал.
Здесь проверяется, что не пропадает.
"""
import max_bot
import walk
from chatbot_survey import ANSWERED


def дозаполнить(s, u="u1"):
    """Пройти анкету до конца коротким путём."""
    walk.дойти_до(s, "continue", u=u, ветка=1)
    step, q = s.current(u)
    s.answer_by_numbers(u, [q["options"].index("Достаточно, свяжитесь") + 1])
    assert s.state[u]["finished"]


# ----------------------------------------------------------- что сохраняем

def test_сообщение_после_анкеты_сохраняется(consented):
    s = consented
    дозаполнить(s)
    out = s.handle("u1", "А когда примерно позвонят? Мама совсем ослабла")
    assert out == ANSWERED, "человеку надо сказать, что сообщение дошло"
    assert [m["text"] for m in s.messages("u1")] == [
        "А когда примерно позвонят? Мама совсем ослабла"]


def test_вопрос_посреди_анкеты_не_теряется(consented):
    """Человек спрашивает, не дойдя до конца, — это тоже обращение."""
    s = consented
    walk.дойти_до(s, "phone", ветка=1)
    s.handle("u1", "а можно позвонить вам самому?")
    assert s.messages("u1"), "вопрос вместо телефона — всё равно вопрос"


def test_опечатка_не_попадает_в_переписку(consented):
    """«12345» вместо телефона — промах, а не разговор."""
    s = consented
    walk.дойти_до(s, "phone", ветка=1)
    s.handle("u1", "12345")
    assert s.messages("u1") == []


def test_ответы_анкеты_не_дублируются_в_переписку(consented):
    s = consented
    дозаполнить(s)
    assert s.messages("u1") == [], "ответы уже лежат в анкете"


def test_переписка_не_растёт_без_предела(consented):
    s = consented
    дозаполнить(s)
    for i in range(s.MESSAGES_LIMIT + 20):
        s.handle("u1", f"сообщение номер {i} с достаточной длиной")
    assert len(s.messages("u1")) == s.MESSAGES_LIMIT
    assert "номер 219" in s.messages("u1")[-1]["text"], "остаются свежие"


def test_переписка_переживает_перезапуск(consented, tmp_path):
    from chatbot_survey import Survey
    s = consented
    дозаполнить(s)
    s.handle("u1", "Пришлите, пожалуйста, список документов")
    снова = Survey(storage_path=s.storage_path, list_options=False)
    assert len(снова.messages("u1")) == 1


def test_удаление_стирает_и_переписку(consented):
    s = consented
    дозаполнить(s)
    s.handle("u1", "Спасибо вам большое за помощь")
    s.handle("u1", "удалить")
    assert "u1" not in s.state


# ----------------------------------------------------------------- файлы

def test_вложения_запоминаются_с_именем_и_адресом(consented):
    s = consented
    дозаполнить(s)
    s.note_message("u1", "Вот выписка", [
        {"kind": "file", "name": "выписка.docx",
         "url": "https://max.ru/f/1", "size": 4096}])
    файл = s.messages("u1")[-1]["files"][0]
    assert файл["name"] == "выписка.docx"
    assert файл["url"].startswith("https://")


def test_вложение_без_текста_тоже_сохраняется(consented):
    s = consented
    дозаполнить(s)
    s.note_message("u1", "", [{"kind": "image", "name": "", "url": "https://max.ru/i/2"}])
    assert s.messages("u1")[-1]["files"], "фотографию без подписи тоже видно"


def test_пустое_сообщение_не_сохраняется(consented):
    s = consented
    дозаполнить(s)
    s.note_message("u1", "   ")
    assert s.messages("u1") == []


def test_бот_разбирает_вложения_входящего():
    """Из события MAX берём имя, вид и адрес — сам файл не качаем."""
    class Кнопки:
        type = "inline_keyboard"
        payload = None

    class Файл:
        type = "file"
        filename = "направление.pdf"
        size = 2048

        class payload:
            url = "https://max.ru/f/9"

    class Тело:
        attachments = [Кнопки(), Файл()]

    найдено = max_bot._files_of(Тело())
    assert len(найдено) == 1, "клавиатура — не вложение человека"
    assert найдено[0] == {"kind": "file", "name": "направление.pdf",
                          "url": "https://max.ru/f/9", "size": 2048}


def test_у_бота_нет_копий_присланных_файлов():
    """Файл лежит у MAX. Копия у нас — это лишние данные о людях."""
    src = open(max_bot.__file__, encoding="utf-8").read()
    начало = src.index("def _files_of")
    конец = src.index("class Seen:")
    assert "download" not in src[начало:конец].lower()
    assert "open(" not in src[начало:конец]


# --------------------------------------------------- справка по базе знаний

class ФейкБот:
    """Бот, который никуда не ходит: собирает отправленное в список."""

    def __init__(self):
        self.ушло = []

    async def send_message(self, **kw):
        self.ушло.append(kw.get("text", ""))


def test_на_вопрос_приходит_справка_из_базы(consented):
    """Вопрос человека получает ответ по материалам службы, а не молчание."""
    import asyncio
    s = consented
    дозаполнить(s)
    вопрос = "Скажите, сколько часов помощи дают на третьем уровне?"
    s.handle("u1", вопрос)

    бот = ФейкБот()
    asyncio.run(max_bot.справка(бот, 1, "u1", вопрос))
    assert бот.ушло, "справка не отправилась"
    assert "уровень" in бот.ушло[0].lower()
    assert "103" in бот.ушло[0], "внизу всегда путь к экстренной помощи"


def test_на_невнятное_справка_не_шлётся(consented):
    import asyncio
    бот = ФейкБот()
    asyncio.run(max_bot.справка(бот, 1, "u1", "ну как там дела вообще у вас"))
    assert бот.ушло == [], "лучше промолчать, чем ответить не по делу"


def test_отказ_справки_не_ломает_бота(consented, monkeypatch):
    """Справка — дополнение. Её сбой не должен ронять обработку сообщения."""
    import asyncio

    class Падучий(ФейкБот):
        async def send_message(self, **kw):
            raise RuntimeError("сеть отвалилась")

    asyncio.run(max_bot.справка(Падучий(), 1, "u1",
                                "сколько часов помощи на третьем уровне"))

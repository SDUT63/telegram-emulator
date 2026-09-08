"""CRM оператора: лента переписки и вложения.

Проверяем не оформление, а то, из-за чего теряют людей: что сообщение
человека доходит до карточки, что видно, кто чего ждёт, и что оператор
может послать памятку файлом.
"""
import os

import pytest

import crm_server
import crm_store
import walk


@pytest.fixture
def crm(tmp_path, monkeypatch):
    """CRM со своими файлами во временной папке — не трогаем рабочие."""
    monkeypatch.setattr(crm_store, "CRM_DATA", str(tmp_path / "crm.json"))
    monkeypatch.setattr(crm_store, "OPERATORS", str(tmp_path / "operators.json"))
    monkeypatch.setattr(crm_store, "OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setattr(crm_store, "SENT_DIR", str(tmp_path / "outbox" / "sent"))
    monkeypatch.setattr(crm_store, "FILES_DIR", str(tmp_path / "outbox" / "files"))
    return crm_store


def заполнить(s, u="u1"):
    walk.дойти_до(s, "continue", u=u, ветка=1)
    step, q = s.current(u)
    s.answer_by_numbers(u, [q["options"].index("Достаточно, свяжитесь") + 1])


# ------------------------------------------------------------------ лента

def test_лента_сводит_обе_стороны(consented, crm):
    s = consented
    заполнить(s)
    s.handle("u1", "А когда примерно позвонят?")
    crm.queue_message("u1", "Завтра до обеда.", "Анна Ивановна")

    лента = crm_server._dialogue(s.state["u1"], crm.case("u1")["sent"])
    assert [m["from"] for m in лента] == ["человек", "оператор"]
    assert лента[0]["text"] == "А когда примерно позвонят?"
    assert лента[1]["who"] == "Анна Ивановна"


def test_лента_идёт_по_времени(consented, crm):
    s = consented
    заполнить(s)
    crm.queue_message("u1", "Здравствуйте!", "Анна Ивановна")
    s.handle("u1", "Здравствуйте, спасибо что откликнулись")

    лента = crm_server._dialogue(s.state["u1"], crm.case("u1")["sent"])
    времена = [m["at"] for m in лента]
    assert времена == sorted(времена)


def test_видно_кто_ждёт_ответа(consented, crm):
    s = consented
    заполнить(s)
    s.handle("u1", "Подскажите, что взять с собой на встречу?")
    вид = crm_server._case_view("u1", s.state["u1"], {})
    assert вид["waiting"] is True, "последним написал человек — ждут нас"

    crm.queue_message("u1", "Паспорт и полис.", "Анна Ивановна")
    вид = crm_server._case_view("u1", s.state["u1"], {})
    assert вид["waiting"] is False, "ответили — больше не ждут"


def test_без_переписки_никто_не_ждёт(consented):
    s = consented
    заполнить(s)
    assert crm_server._case_view("u1", s.state["u1"], {})["waiting"] is False


def test_в_карточке_есть_адрес_и_разобранное_из_него(consented):
    s = consented
    walk.дойти_до(s, "address", ветка=1)
    s.handle("u1", "Автозаводский, Ворошилова 19, кв. 5, 5 этаж, лифта нет")
    вид = crm_server._case_view("u1", s.state["u1"], {})
    assert "Ворошилова 19" in вид["address"]
    assert вид["district"] == "Автозаводский"
    assert вид["lift"] == "Лифта нет"


# ---------------------------------------------------------------- вложения

def test_файл_кладётся_в_очередь_путём(crm, tmp_path):
    памятка = tmp_path / "памятка.pdf"
    памятка.write_bytes(b"%PDF-1.4 ...")
    файл = crm.save_file("u1", "памятка.pdf", памятка.read_bytes())

    crm.queue_message("u1", "Направляю памятку.", "Анна Ивановна", [файл])
    в_очереди = crm.pending_messages()[0]
    assert os.path.exists(в_очереди["files"][0]["path"])
    assert в_очереди["files"][0]["name"] == "памятка.pdf"


def test_имя_из_браузера_не_становится_путём(crm):
    """«../../token.txt» в имени файла не должно никуда увести."""
    файл = crm.save_file("u1", "../../token.txt.pdf", b"x")
    assert os.path.dirname(файл["path"]) == crm.FILES_DIR
    assert ".." not in os.path.basename(файл["path"])


@pytest.mark.parametrize("имя", ["скрипт.exe", "архив.zip", "безрасширения"])
def test_чужие_форматы_не_отправляем(crm, имя):
    with pytest.raises(ValueError):
        crm.save_file("u1", имя, b"x")


def test_слишком_большой_файл_не_принимаем(crm):
    with pytest.raises(ValueError):
        crm.save_file("u1", "фото.jpg", b"x" * (crm.FILE_LIMIT + 1))


def test_в_карточке_виден_отправленный_файл(crm, tmp_path):
    памятка = tmp_path / "памятка.pdf"
    памятка.write_bytes(b"%PDF")
    файл = crm.save_file("u1", "памятка.pdf", памятка.read_bytes())
    crm.queue_message("u1", "Направляю памятку.", "Анна Ивановна", [файл])

    записано = crm.case("u1")["sent"][0]
    assert записано["files"][0]["name"] == "памятка.pdf"
    assert "path" not in записано["files"][0], "путь на диске карточке ни к чему"


def test_пропавший_файл_не_отменяет_сообщение():
    """Файл удалили — текст всё равно должен уйти."""
    import max_bot
    готово = max_bot._outgoing_files(
        {"files": [{"path": "/нет/такого/файла.pdf", "name": "файл.pdf"}]})
    assert готово is None

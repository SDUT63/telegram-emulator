"""Что ломается, когда людей больше одного.

Эти проверки — сжатые версии стендов из нагрузка/. Стенд гоняют руками
и подолгу; сюда вынесено то, что должно падать сразу, если защиту
случайно снимут. Все три ошибки ниже были настоящими и найдены
нагрузкой, а не чтением кода: поодиночке всё работало.
"""

from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(КОРЕНЬ))


# ------------------------------------------------------- анкета под нагрузкой

def test_ответы_разных_людей_не_перемешиваются(tmp_path):
    """Сто человек пишут одновременно, у каждого своё имя.

    До замков из ста доходил один: две нити писали в один и тот же
    временный файл, первая переименовывала его, вторая падала
    с «нет такого файла» — и ответ пропадал вместе с исключением.
    """
    from chatbot_survey import Survey

    анкета = Survey(storage_path=str(tmp_path / "анкета.json"))
    беды: list[str] = []

    def один(номер: int) -> None:
        кто, имя = f"u{номер}", f"Человек{номер}"
        try:
            анкета.grant_consent(кто)
            for шаг in ["О близком человеке", "Дочь или сын", "Да, знает", имя]:
                анкета.handle(кто, шаг)
        except Exception as беда:                               # noqa: BLE001
            беды.append(f"{кто}: {type(беда).__name__}: {беда}")

    with ThreadPoolExecutor(max_workers=16) as пул:
        list(пул.map(один, range(100)))

    assert беды == [], беды[:3]
    assert len(анкета.state) == 100
    чужие = [(к, (з.get("answers") or {}).get("name"))
             for к, з in анкета.state.items()
             if (з.get("answers") or {}).get("name") != f"Человек{к[1:]}"]
    assert чужие == [], чужие[:3]


def test_записанное_читается_обратно(tmp_path):
    """Файл после одновременной записи должен остаться разбираемым."""
    from chatbot_survey import Survey

    путь = tmp_path / "анкета.json"
    анкета = Survey(storage_path=str(путь))

    def один(номер: int) -> None:
        анкета.grant_consent(f"u{номер}")
        анкета.handle(f"u{номер}", "О себе")

    with ThreadPoolExecutor(max_workers=16) as пул:
        list(пул.map(один, range(60)))

    прочитано = json.loads(путь.read_text(encoding="utf-8"))
    assert len(прочитано) == 60


def test_дубль_доставки_не_уезжает_в_следующий_вопрос(tmp_path):
    """Мессенджер повторяет доставку, когда не дождался ответа,
    а человек нажимает кнопку дважды. Второе сообщение не должно
    ответить на следующий вопрос."""
    from chatbot_survey import Survey

    for попытка in range(20):
        анкета = Survey(storage_path=str(tmp_path / f"а{попытка}.json"))
        анкета.grant_consent("u")
        анкета.handle("u", "О близком человеке")

        барьер = threading.Barrier(2)

        def послать() -> None:
            барьер.wait()
            анкета.handle("u", "Дочь или сын")

        нити = [threading.Thread(target=послать) for _ in range(2)]
        for н in нити:
            н.start()
        for н in нити:
            н.join()

        ответы = анкета.state["u"]["answers"]
        assert ответы.get("relation") == "Дочь или сын", попытка
        assert ответы.get("aware") != "Дочь или сын", попытка


def test_замок_человека_возвратный(tmp_path):
    """Внутри разбора бот вызывает себя: ответ номером кнопки идёт
    тем же путём, что и ответ текстом. С обычным замком прогон
    проверок переставал заканчиваться вовсе."""
    from chatbot_survey import Survey

    анкета = Survey(storage_path=str(tmp_path / "а.json"))
    анкета.grant_consent("u")
    готово: list[str] = []

    нить = threading.Thread(
        target=lambda: готово.append(анкета.answer_by_numbers("u", [2])))
    нить.start()
    нить.join(timeout=10)
    assert not нить.is_alive(), "разбор встал намертво на собственном замке"
    assert готово


# ---------------------------------------------------------- CRM под нагрузкой

def test_заметки_двух_координаторов_не_затирают_друг_друга(tmp_path, monkeypatch):
    """Один ставит статус, другой пишет заметку. Без замка второй
    записывает файл, прочитанный до чужой правки: из шестидесяти
    заметок доживало две."""
    import crm_store

    monkeypatch.setattr(crm_store, "CRM_DATA", str(tmp_path / "crm.json"))
    monkeypatch.setattr(crm_store, "OUTBOX_DIR", str(tmp_path / "outbox"))

    беды: list[str] = []

    def пишет(номер: int) -> None:
        try:
            crm_store.add_note("карточка", f"заметка {номер}", "координатор")
        except Exception as беда:                               # noqa: BLE001
            беды.append(f"{type(беда).__name__}: {беда}")

    with ThreadPoolExecutor(max_workers=12) as пул:
        list(пул.map(пишет, range(40)))

    assert беды == [], беды[:3]
    assert len(crm_store.case("карточка").get("notes", [])) == 40


def test_очередь_исходящих_не_теряет_и_не_двоит(tmp_path, monkeypatch):
    import crm_store

    monkeypatch.setattr(crm_store, "CRM_DATA", str(tmp_path / "crm.json"))
    monkeypatch.setattr(crm_store, "OUTBOX_DIR", str(tmp_path / "outbox"))

    with ThreadPoolExecutor(max_workers=12) as пул:
        list(пул.map(
            lambda н: crm_store.queue_message("карточка", f"текст {н}", "кто"),
            range(30)))

    в_очереди = crm_store.pending_messages()
    тексты = [с["text"] for с in в_очереди]
    assert len(тексты) == 30
    assert len(set(тексты)) == 30, "в очереди повторы — человек получит дважды"


# --------------------------------------------------- формулы в выгрузке

def test_ответ_человека_не_станет_формулой_в_excel():
    """Человек пишет в ответ на вопрос об имени =HYPERLINK(...),
    координатор открывает выгрузку — и формула выполняется у него
    на компьютере. Между «написал в чат» и «выполнилось на рабочем
    месте» не было ни одного препятствия."""
    import openpyxl

    import таблицы

    книга = openpyxl.Workbook()
    лист = книга.active
    опасные = ['=HYPERLINK("http://зло/","жми")', "+1+1", "-2+3", "@СУМ(A1)",
               "\t=1+1", "\r=1+1"]
    таблицы.строкой(лист, ["обычное имя", *опасные, 42])

    for строка in лист.iter_rows():
        for ячейка in строка:
            if not isinstance(ячейка.value, str):
                continue
            if ячейка.value[:1] in "=+-@\t\r\n":
                assert ячейка.data_type == "s", ячейка.value
                assert ячейка.quotePrefix, ячейка.value
            else:
                assert not ячейка.quotePrefix, ячейка.value


def test_csv_тоже_обезврежен():
    import таблицы

    assert таблицы.для_csv('=HYPERLINK("x","y")').startswith("'")
    assert таблицы.для_csv("Анна Петровна") == "Анна Петровна"
    assert таблицы.для_csv(42) == 42
    assert таблицы.для_csv("") == ""


def test_выгрузка_анкеты_обезврежена(tmp_path):
    """Не помощник обезврежен, а сама выгрузка."""
    from chatbot_survey import Survey

    анкета = Survey(storage_path=str(tmp_path / "а.json"))
    анкета.grant_consent("u")
    for шаг in ["О близком человеке", "Дочь или сын", "Да, знает",
                '=HYPERLINK("http://зло/","жми")']:
        анкета.handle("u", шаг)

    путь = str(tmp_path / "выгрузка.csv")
    анкета.export_csv(путь)
    текст = Path(путь).read_text(encoding="utf-8-sig")
    assert "=HYPERLINK" in текст, "ответ человека должен сохраниться как есть"
    assert "'=HYPERLINK" in текст, "но таблица не должна его выполнить"


# -------------------------------------------------- заголовки и доступ в CRM

def test_закрытые_адреса_crm_не_отвечают_без_входа():
    import crm_server

    crm_server.app.config["TESTING"] = True
    клиент = crm_server.app.test_client()
    for адрес in ("/api/cases", "/api/export", "/api/funnel", "/"):
        ответ = клиент.get(адрес)
        assert ответ.status_code in (401, 302, 303), (адрес, ответ.status_code)
    for адрес in ("/api/case/x/status", "/api/case/x/reply", "/api/case/x/note"):
        ответ = клиент.post(адрес, json={})
        assert ответ.status_code in (401, 302, 303), (адрес, ответ.status_code)


def test_у_crm_есть_защитные_заголовки():
    """Дёшевы и нужны, даже когда CRM стоит на локальном компьютере."""
    import crm_server

    crm_server.app.config["TESTING"] = True
    заголовки = crm_server.app.test_client().get("/login").headers
    for имя in ("X-Content-Type-Options", "X-Frame-Options",
                "Content-Security-Policy", "Referrer-Policy"):
        assert имя in заголовки, имя
    assert "frame-ancestors 'none'" in заголовки["Content-Security-Policy"]


def test_из_каталога_картинок_не_выйти():
    import webapp_server

    webapp_server.app.config["TESTING"] = True
    клиент = webapp_server.app.test_client()
    for адрес in ("/img/../../token.txt",
                  "/img/..%2f..%2ftoken.txt",
                  "/img/%2e%2e/%2e%2e/survey_responses.json",
                  "/img/../../../../etc/passwd"):
        ответ = клиент.get(адрес)
        assert ответ.status_code != 200 or not ответ.data, адрес

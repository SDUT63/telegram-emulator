#!/usr/bin/env python3
"""Нагрузка и поиск уязвимостей: CRM, веб-сервер, выгрузка.

Запуск:
    python нагрузка/сервер.py

Здесь не «проверка работоспособности», а попытка сделать то, чего
делать нельзя: зайти без пароля, подобрать пароль, прочитать чужую
карточку, вылезти из каталога, положить в анкету то, что выполнится
у координатора на компьютере.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, КОРЕНЬ)

НАХОДКИ: list[tuple[str, str]] = []
ЗАМОК = threading.Lock()


def находка(важность: str, что: str) -> None:
    with ЗАМОК:
        НАХОДКИ.append((важность, что))
    print(f"  [{важность}] {что}")


def хорошо(что: str) -> None:
    print(f"  [ок] {что}")


# ------------------------------------------------------------------- CRM

def _crm_клиент(каталог: str):
    """CRM на временных данных, с одним оператором и одной карточкой."""
    # Пути в CRM — константы модуля, не переменные окружения.
    os.environ.pop("SDUT_DATABASE_URL", None)
    # То же хранилище, что читает CRM: иначе стенд проверял бы не то,
    # что работает у службы.
    os.environ["SDUT_DB_PATH"] = os.path.join(каталог, "survey.sqlite")

    import access_log
    import crm_store
    import crm_server
    import chatbot_survey

    crm_store.CRM_DATA = os.path.join(каталог, "crm_data.json")
    crm_store.OPERATORS = os.path.join(каталог, "operators.json")
    crm_store.OUTBOX_DIR = os.path.join(каталог, "outbox")
    crm_store.SENT_DIR = os.path.join(crm_store.OUTBOX_DIR, "sent")
    crm_store.FILES_DIR = os.path.join(crm_store.OUTBOX_DIR, "files")
    chatbot_survey.STORAGE = os.environ["SDUT_DB_PATH"]
    access_log.ЖУРНАЛ = os.path.join(каталог, "access.log")
    crm_server.SECRET_FILE = os.path.join(каталог, ".crm_secret")

    crm_server.app.config["TESTING"] = True
    return crm_server, crm_store


def проверить_crm() -> None:
    каталог = tempfile.mkdtemp(prefix="сдут-нагрузка-")
    crm_server, crm_store = _crm_клиент(каталог)

    # оператор и карточка
    crm_store.add_operator("operator", "Оператор", "Пароль-Оператора-1", "operator")
    crm_store.add_operator("viewer", "Смотрящий", "Пароль-Смотрящего-2", "viewer")

    import storage
    анкета = storage.открыть()
    анкета.grant_consent("жертва")
    for шаг in ["О близком человеке", "Дочь или сын", "Да, знает",
                '<img src=x onerror="alert(1)">',
                "=HYPERLINK(\"http://зло/\",\"жми\")", "89170000000"]:
        анкета.handle("жертва", шаг)
    анкета.save()

    клиент = crm_server.app.test_client()

    # --- 1. без входа
    закрытые = ["/api/cases", "/api/export", "/api/funnel", "/"]
    for адрес in закрытые:
        ответ = клиент.get(адрес)
        if ответ.status_code not in (401, 302, 303):
            находка("!", f"{адрес} открывается без входа: {ответ.status_code}")
    for адрес in ["/api/case/жертва/status", "/api/case/жертва/reply",
                  "/api/case/жертва/note", "/api/case/жертва/assign",
                  "/api/case/жертва/call"]:
        ответ = клиент.post(адрес, json={})
        if ответ.status_code not in (401, 302, 303):
            находка("!", f"{адрес} принимает POST без входа: {ответ.status_code}")
    else:
        хорошо("закрытые адреса не отвечают без входа")

    # --- 2. подбор пароля (на отдельном логине: перебор запирает дверь,
    #        и запирать ею же оператора, который нужен дальше, незачем)
    crm_store.add_operator("мишень", "Мишень", "Пароль-Мишени-3", "operator")
    начало = time.time()
    коды = []
    for попытка in range(8):
        ответ = клиент.post("/api/login",
                            json={"login": "мишень", "password": f"нет{попытка}"})
        коды.append(ответ.status_code)
    прошло = time.time() - начало
    if all(код == 401 for код in коды):
        находка("!", "восемь попыток пароля подряд без единой задержки "
                     f"и без блокировки ({прошло:.2f} с)")
    else:
        хорошо(f"перебор пароля тормозится: коды {sorted(set(коды))}, "
               f"заперто после {коды.index(429) if 429 in коды else '?'} попыток")

    # Правильный пароль после блокировки тоже не пускает — это верно,
    # но проверим, что дверь открывается кому-то другому.
    чужой = crm_server.app.test_client()
    ответ = чужой.post("/api/login",
                       json={"login": "operator", "password": "Пароль-Оператора-1"})
    if ответ.status_code != 200:
        находка("!", "блокировка одного логина мешает войти другому оператору")
    else:
        хорошо("блокировка не запирает остальных операторов")

    # --- 3. вход и права
    вошёл = клиент.post("/api/login",
                        json={"login": "operator", "password": "Пароль-Оператора-1"})
    if вошёл.status_code != 200:
        находка("!", f"оператор не может войти правильным паролем: {вошёл.data[:200]!r}")
        return
    хорошо("оператор входит правильным паролем")

    карточки = клиент.get("/api/cases")
    if карточки.status_code != 200:
        находка("!", f"оператор не видит карточки: {карточки.status_code}")

    # --- 4. чужой идентификатор
    for чужой in ["../../etc/passwd", "нет-такого", "%2e%2e%2f", "'; DROP TABLE x--"]:
        ответ = клиент.post(f"/api/case/{чужой}/note", json={"text": "тест"})
        if ответ.status_code >= 500:
            находка("!", f"карточка {чужой!r} роняет сервер: {ответ.status_code}")
    хорошо("подставные идентификаторы карточек не роняют сервер")

    # --- 5. выгрузка и формулы Excel
    выгрузка = клиент.get("/api/export")
    if выгрузка.status_code == 200:
        import openpyxl
        книга = openpyxl.load_workbook(io.BytesIO(выгрузка.data))
        формулы, опасных = [], 0
        for лист in книга.worksheets:
            for строка in лист.iter_rows():
                for ячейка in строка:
                    значение = ячейка.value
                    if not isinstance(значение, str) or значение[:1] not in "=+-@\t\r":
                        continue
                    опасных += 1
                    # Формула — это тип «f». Строка с таким же началом,
                    # помеченная как текст, Excel не выполняет.
                    if ячейка.data_type == "f" or not ячейка.quotePrefix:
                        формулы.append(значение[:60])
        if формулы:
            находка("!", "в выгрузке есть ячейки, которые Excel выполнит: "
                         f"{формулы[:2]}")
        elif опасных:
            хорошо(f"выгрузка: {опасных} опасных значений помечены как текст")
        else:
            находка("?", "в выгрузке не оказалось подставленного значения — "
                         "проверка ничего не проверила")
    else:
        находка("?", f"выгрузка не отдалась: {выгрузка.status_code}")

    # --- 6. права роли «смотрящий»
    клиент.post("/api/logout")
    клиент.post("/api/login",
                json={"login": "viewer", "password": "Пароль-Смотрящего-2"})
    ответ = клиент.post("/api/case/жертва/reply", json={"text": "я смотрящий"})
    if ответ.status_code == 200:
        находка("!", "роль «смотрящий» смогла ответить человеку")
    else:
        хорошо(f"смотрящему нельзя отвечать: {ответ.status_code}")

    # --- 7. заголовки
    ответ = клиент.get("/login")
    отсутствуют = [з for з in ("X-Content-Type-Options", "X-Frame-Options",
                               "Content-Security-Policy")
                   if з not in ответ.headers]
    if отсутствуют:
        находка("?", f"страница входа без защитных заголовков: {отсутствуют}")

    # --- 8. одновременная работа операторов
    def читает(_: int) -> int:
        свой = crm_server.app.test_client()
        свой.post("/api/login",
                  json={"login": "operator", "password": "Пароль-Оператора-1"})
        return свой.get("/api/cases").status_code

    начало = time.time()
    with ThreadPoolExecutor(max_workers=16) as пул:
        коды = list(пул.map(читает, range(64)))
    прошло = time.time() - начало
    плохие = [к for к in коды if к != 200]
    if плохие:
        находка("!", f"под одновременной нагрузкой CRM отвечает {set(плохие)}")
    else:
        хорошо(f"64 одновременных входа и чтения за {прошло:.1f} с")


# ---------------------------------------------------------- веб-сервер

def проверить_веб() -> None:
    import webapp_server

    webapp_server.app.config["TESTING"] = True
    клиент = webapp_server.app.test_client()

    ответ = клиент.get("/")
    if ответ.status_code != 200:
        находка("!", f"корень не отдаётся: {ответ.status_code}")
    else:
        хорошо(f"корень отдаёт мини-приложение ({len(ответ.data) // 1024} КБ)")

    # выход из каталога картинок
    попытки = [
        "/img/../../token.txt",
        "/img/..%2f..%2ftoken.txt",
        "/img/....//token.txt",
        "/img/%2e%2e/%2e%2e/survey_responses.json",
        "/img/../../../../etc/passwd",
    ]
    ушло = []
    for адрес in попытки:
        ответ = клиент.get(адрес)
        if ответ.status_code == 200 and len(ответ.data) > 0:
            ушло.append(адрес)
    if ушло:
        находка("!", f"из каталога картинок можно выйти: {ушло}")
    else:
        хорошо("выход из каталога картинок закрыт")

    заголовки = клиент.get("/").headers
    if "X-Content-Type-Options" not in заголовки:
        находка("?", "нет заголовка X-Content-Type-Options")
    else:
        хорошо("заголовки на месте")


def проверить_запись_crm() -> None:
    """Два координатора работают одновременно.

    Так и бывает: один ставит статус, другой пишет заметку. Если запись
    в файл не защищена, один затирает другого — и заметка, на которую
    человек рассчитывал, исчезает молча.
    """
    каталог = tempfile.mkdtemp(prefix="сдут-crm-")
    import crm_store

    crm_store.CRM_DATA = os.path.join(каталог, "crm_data.json")
    crm_store.OUTBOX_DIR = os.path.join(каталог, "outbox")

    заметок = 60

    def пишет(номер: int) -> str:
        try:
            crm_store.add_note("карточка", f"заметка {номер}", "координатор")
            return ""
        except Exception as беда:                               # noqa: BLE001
            return f"{type(беда).__name__}: {беда}"

    with ThreadPoolExecutor(max_workers=16) as пул:
        ошибки = [о for о in пул.map(пишет, range(заметок)) if о]

    записано = crm_store.case("карточка").get("notes", [])
    if ошибки:
        находка("!", f"одновременная запись в CRM ломается: {ошибки[:2]}")
    if len(записано) != заметок:
        находка("!", f"из {заметок} заметок двух координаторов сохранилось "
                     f"{len(записано)} — остальные затёрты")
    else:
        хорошо(f"{заметок} одновременных заметок сохранены все")

    # очередь исходящих: сообщения не должны пропадать и двоиться
    def ставит(номер: int) -> str:
        try:
            crm_store.queue_message("карточка", f"текст {номер}", "координатор")
            return ""
        except Exception as беда:                               # noqa: BLE001
            return f"{type(беда).__name__}: {беда}"

    сообщений = 40
    with ThreadPoolExecutor(max_workers=16) as пул:
        беды = [б for б in пул.map(ставит, range(сообщений)) if б]
    в_очереди = crm_store.pending_messages()
    if беды:
        находка("!", f"очередь исходящих ломается под нагрузкой: {беды[:2]}")
    if len(в_очереди) != сообщений:
        находка("!", f"в очереди {len(в_очереди)} сообщений из {сообщений}")
    else:
        хорошо(f"очередь исходящих: все {сообщений} сообщений на месте")

    тексты = [с.get("text") for с in в_очереди]
    if len(set(тексты)) != len(тексты):
        находка("!", "в очереди есть повторы — человек получит сообщение дважды")


def главное() -> int:
    print("НАГРУЗКА И УЯЗВИМОСТИ: CRM")
    проверить_crm()
    print("\nНАГРУЗКА: ДВА КООРДИНАТОРА СРАЗУ")
    проверить_запись_crm()
    print("\nНАГРУЗКА И УЯЗВИМОСТИ: ВЕБ-СЕРВЕР")
    проверить_веб()

    print("\nИТОГ")
    опасные = [т for в, т in НАХОДКИ if в == "!"]
    прочие = [т for в, т in НАХОДКИ if в != "!"]
    print(f"  опасных находок: {len(опасные)}, прочих: {len(прочие)}")
    return 1 if опасные else 0


if __name__ == "__main__":
    raise SystemExit(главное())

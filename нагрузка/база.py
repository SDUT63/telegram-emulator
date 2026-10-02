#!/usr/bin/env python3
"""Нагрузка на рабочее хранилище — PostgreSQL.

Запуск (с адресом базы в окружении):
    SDUT_DATABASE_URL=postgresql://... python нагрузка/база.py

Файловое хранилище — запасной вариант для ноутбука. Служба, к которой
пошла реклама, работает на PostgreSQL, и проверять надо именно его:
у него другие способы потерять данные. Ищем потерянный ответ при
одновременной записи, перепутанные между людьми записи и то, во что
превращается очередь исходящих под нагрузкой.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, КОРЕНЬ)

ШАГИ = ["О близком человеке", "Дочь или сын", "Да, знает", "{имя}",
        "Анна Петровна", "89170000000", "Утром", "Тольятти, Ленина 1",
        "85 и старше", "Боль", "Помощь на дому"]

НАХОДКИ: list[str] = []
ЗАМОК = threading.Lock()


def находка(что: str) -> None:
    with ЗАМОК:
        НАХОДКИ.append(что)
    print(f"  [!]  {что}")


def хорошо(что: str) -> None:
    print(f"  [ок] {что}")


def _анкета():
    from storage_postgres import PostgresSurvey
    return PostgresSurvey()


def очистить() -> None:
    import psycopg
    from storage_postgres import database_url

    with psycopg.connect(database_url()) as соединение:
        for таблица in ("outbox", "messages", "answers", "people"):
            try:
                соединение.execute(f"TRUNCATE {таблица} CASCADE")
            except Exception:                                   # noqa: BLE001
                соединение.rollback()
        соединение.commit()


def многолюдно(людей: int = 120) -> None:
    """Каждый пишет своё имя. В чужой записи его быть не должно."""
    анкета = _анкета()

    def один(номер: int) -> None:
        кто = f"нагрузка-{номер}"
        имя = f"Человек{номер}"
        try:
            анкета.grant_consent(кто)
            for шаг in ШАГИ:
                анкета.handle(кто, шаг.replace("{имя}", имя))
        except Exception:                                       # noqa: BLE001
            находка(f"{кто}: {traceback.format_exc(limit=2)}")

    начало = time.time()
    with ThreadPoolExecutor(max_workers=16) as пул:
        list(пул.map(один, range(людей)))
    прошло = time.time() - начало

    свежая = _анкета()
    свежая.load()
    чужие, пустые = [], []
    for номер in range(людей):
        кто = f"нагрузка-{номер}"
        ответы = (свежая.state.get(кто) or {}).get("answers") or {}
        имя = ответы.get("name")
        if имя is None:
            пустые.append(кто)
        elif имя != f"Человек{номер}":
            чужие.append((кто, имя))
    if чужие:
        находка(f"перепутаны записи: {чужие[:3]}")
    if пустые:
        находка(f"у {len(пустые)} человек ответ не записался: {пустые[:3]}")
    if not чужие and not пустые:
        хорошо(f"{людей} человек × {len(ШАГИ)} сообщений за {прошло:.1f} с "
               f"({людей * len(ШАГИ) / прошло:.0f} сообщений/с), записи не перепутаны")


def дубль_доставки(попыток: int = 60) -> None:
    """Мессенджер повторил доставку. Ответ не должен уехать в следующий вопрос."""
    анкета = _анкета()
    беды = 0
    for номер in range(попыток):
        кто = f"дубль-{номер}"
        анкета.grant_consent(кто)
        анкета.handle(кто, "О близком человеке")
        барьер = threading.Barrier(2)

        def послать() -> None:
            барьер.wait()
            try:
                анкета.handle(кто, "Дочь или сын")
            except Exception:                                   # noqa: BLE001
                находка(f"{кто}: {traceback.format_exc(limit=2)}")

        нити = [threading.Thread(target=послать) for _ in range(2)]
        for н in нити:
            н.start()
        for н in нити:
            н.join()

        свежая = _анкета()
        свежая.load()
        ответы = (свежая.state.get(кто) or {}).get("answers") or {}
        if ответы.get("relation") != "Дочь или сын":
            беды += 1
        if ответы.get("aware") == "Дочь или сын":
            находка(f"{кто}: дубль записан в следующий вопрос")
    if беды:
        находка(f"из {попыток} дублей доставки {беды} потеряли ответ")
    else:
        хорошо(f"дубль доставки: {попыток} попыток без потерь")


def один_человек_много_сообщений(сколько: int = 500) -> None:
    """Карточка не должна расти без предела: это отказ в обслуживании
    силами одного человека."""
    анкета = _анкета()
    кто = "болтун"
    анкета.grant_consent(кто)
    for шаг in ШАГИ:
        анкета.handle(кто, шаг.replace("{имя}", "Болтун"))
    начало = time.time()
    for номер in range(сколько):
        анкета.handle(кто, f"вопрос {номер} про пролежни и коляску")
    прошло = time.time() - начало

    свежая = _анкета()
    свежая.load()
    сообщений = len((свежая.state.get(кто) or {}).get("messages") or [])
    хорошо(f"{сколько} сообщений от одного за {прошло:.1f} с, "
           f"в карточке сохранено {сообщений}")
    if сообщений >= сколько:
        находка(f"карточка хранит все {сообщений} сообщений без предела")


def главное() -> int:
    if not (os.getenv("SDUT_DATABASE_URL") or "").strip():
        print("SDUT_DATABASE_URL не задан — нечего проверять")
        return 0
    print("НАГРУЗКА НА POSTGRESQL")
    очистить()
    многолюдно()
    дубль_доставки()
    один_человек_много_сообщений()
    print(f"\n  находок: {len(НАХОДКИ)}")
    return 1 if НАХОДКИ else 0


if __name__ == "__main__":
    raise SystemExit(главное())

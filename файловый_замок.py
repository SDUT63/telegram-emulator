#!/usr/bin/env python3
"""Замок на файл данных — общий для нитей и для процессов.

Зачем. CRM хранит карточки в одном файле JSON и правит его целиком:
прочитать, изменить, записать. Пока это не защищено, два координатора,
работающие одновременно, затирают работу друг друга — и делают это
молча. Стенд нагрузка/сервер.py показал: из шестидесяти заметок,
написанных одновременно, сохранялось две.

Нитей мало не бывает: Flask обслуживает запросы в нескольких. Процессов
тоже больше одного — рядом с CRM работает отправщик очереди, и он
правит тот же файл, отмечая доставленные сообщения. Поэтому замка
нужно два: внутрипроцессный и файловый.

Windows и Linux блокируют файлы по-разному, и оба способа здесь есть:
служба работает на локальном компьютере, а он может оказаться любым.
Если не сработал ни один — остаётся замок нитей: он всё равно спасает
от большинства случаев, а отказать координатору в записи из-за
неподдержанной блокировки было бы хуже.
"""

from __future__ import annotations

import contextlib
import os
import threading

try:
    import fcntl
except ImportError:                                             # Windows
    fcntl = None                                                # type: ignore

try:
    import msvcrt
except ImportError:                                             # всё остальное
    msvcrt = None                                               # type: ignore

_ЗАМКИ_НИТЕЙ: dict[str, threading.RLock] = {}
_ОХРАНА = threading.Lock()


def _замок_нитей(путь: str) -> threading.RLock:
    ключ = os.path.abspath(путь)
    with _ОХРАНА:
        замок = _ЗАМКИ_НИТЕЙ.get(ключ)
        if замок is None:
            замок = _ЗАМКИ_НИТЕЙ[ключ] = threading.RLock()
        return замок


def _захватить(файл) -> None:
    if fcntl is not None:
        fcntl.flock(файл.fileno(), fcntl.LOCK_EX)
    elif msvcrt is not None:
        файл.seek(0)
        msvcrt.locking(файл.fileno(), msvcrt.LK_LOCK, 1)


def _отпустить(файл) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(файл.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:
            файл.seek(0)
            msvcrt.locking(файл.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


@contextlib.contextmanager
def занять(путь: str):
    """Держать файл данных, пока идёт «прочитать — изменить — записать»."""
    with _замок_нитей(путь):
        каталог = os.path.dirname(os.path.abspath(путь)) or "."
        os.makedirs(каталог, exist_ok=True)
        замок = путь + ".lock"
        файл = None
        try:
            файл = open(замок, "a+b")
            _захватить(файл)
        except OSError:
            # Файловый замок не получился — работаем под замком нитей.
            файл = None
        try:
            yield
        finally:
            if файл is not None:
                _отпустить(файл)
                файл.close()


def записать_надёжно(путь: str, содержимое: str) -> None:
    """Записать файл целиком, не потеряв старое и не столкнувшись с чужой записью.

    Времянка у каждой записи своя. Пока имя было общим (`файл.tmp`),
    две одновременные записи дрались за него: первая переименовывала,
    вторая падала с «нет такого файла» — и вместе с исключением
    пропадала заметка координатора.
    """
    import tempfile

    каталог = os.path.dirname(os.path.abspath(путь)) or "."
    os.makedirs(каталог, exist_ok=True)
    дескриптор, времянка = tempfile.mkstemp(dir=каталог, prefix=".запись-",
                                            suffix=".tmp")
    try:
        with os.fdopen(дескриптор, "w", encoding="utf-8") as файл:
            файл.write(содержимое)
            файл.flush()
            os.fsync(файл.fileno())
        os.replace(времянка, путь)
    except BaseException:
        try:
            os.unlink(времянка)
        except OSError:
            pass
        raise

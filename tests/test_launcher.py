"""Запуск в одно нажатие.

Человек скачивает папку, открывает «Запустить-бота» и получает бота.
Всё, что для этого нужно, делает launcher.py; здесь проверяется то,
что можно проверить без Windows и без сети.
"""
import io
import os
import re

import pytest

import launcher

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ПУСКАЧИ = ("Запустить-бота.bat", "Рабочее-место.bat",
           "Проверка.bat", "Связь-с-MAX.bat")


# ------------------------------------------------------------- по-русски

@pytest.mark.parametrize("число, ждём", [
    (1, "1 вопрос"), (2, "2 вопроса"), (4, "4 вопроса"), (5, "5 вопросов"),
    (11, "11 вопросов"), (14, "14 вопросов"), (21, "21 вопрос"),
    (34, "34 вопроса"), (100, "100 вопросов"), (112, "112 вопросов"),
])
def test_числа_согласованы_со_словом(число, ждём):
    """«34 вопросов» читается как небрежность — и справедливо."""
    assert launcher.сколько(число, "вопрос", "вопроса", "вопросов") == ждём


# --------------------------------------------------------- самопроверка

def test_самопроверка_проходит_на_чистом_коде():
    assert launcher.самопроверка()


def test_проверка_не_запускает_бота(monkeypatch, capsys):
    """«Проверка» именно проверяет: ничего не стартует и ничего не ставит."""
    monkeypatch.setattr(launcher, "мы_в_окружении", lambda: True)
    monkeypatch.setattr(launcher, "запустить",
                        lambda что: pytest.fail(f"запустился {что}"))
    assert launcher.main(["check"]) == 0
    assert "Проверка закончена" in capsys.readouterr().out


def test_без_токена_бот_не_стартует(monkeypatch):
    """Обещать запуск и не запустить хуже, чем честно спросить токен."""
    monkeypatch.setattr(launcher, "мы_в_окружении", lambda: True)
    monkeypatch.setattr(launcher, "токен_есть", lambda: False)
    monkeypatch.setattr(launcher, "спросить_токен", lambda: False)
    monkeypatch.setattr(launcher, "запустить",
                        lambda что: pytest.fail("стартовал без токена"))
    assert launcher.main([]) == 4


def test_неизвестная_команда_ведёт_к_боту(monkeypatch):
    """Опечатка в ярлыке не должна оставлять человека ни с чем."""
    monkeypatch.setattr(launcher, "мы_в_окружении", lambda: True)
    monkeypatch.setattr(launcher, "токен_есть", lambda: True)
    пуски = []
    monkeypatch.setattr(launcher, "запустить", lambda что: пуски.append(что) or 0)
    assert launcher.main(["чепуха"]) == 0
    assert пуски == ["бот"]


def test_сломанная_база_останавливает_запуск(monkeypatch):
    monkeypatch.setattr(launcher, "мы_в_окружении", lambda: True)
    monkeypatch.setattr(launcher, "самопроверка", lambda: False)
    monkeypatch.setattr(launcher, "запустить",
                        lambda что: pytest.fail("стартовал со сломанной базой"))
    assert launcher.main([]) == 1


# ------------------------------------------------------------- ярлыки

@pytest.mark.parametrize("имя", ПУСКАЧИ)
def test_ярлык_на_месте(имя):
    assert os.path.exists(os.path.join(КОРЕНЬ, имя))


@pytest.mark.parametrize("имя", ПУСКАЧИ)
def test_ярлык_читается_виндой(имя):
    """UTF-8 без BOM и переводы строк CRLF: иначе cmd спотыкается."""
    сырьё = io.open(os.path.join(КОРЕНЬ, имя), "rb").read()
    assert not сырьё.startswith(b"\xef\xbb\xbf"), "BOM ломает первую строку"
    assert b"\r\n" in сырьё, "нужны переводы строк Windows"
    сырьё.decode("utf-8")


@pytest.mark.parametrize("имя", ПУСКАЧИ)
def test_в_ярлыке_нет_кириллических_имён(имя):
    """Русские буквы — только в том, что человек читает.

    Имена переменных и меток cmd разбирает в текущей кодовой странице,
    и кириллица в них ломается непредсказуемо.
    """
    for строка in io.open(os.path.join(КОРЕНЬ, имя), encoding="utf-8"):
        голая = строка.strip()
        if голая.lower().startswith(("set ", "goto ", ":")) and "echo" not in голая:
            assert not re.search(r"[А-Яа-яЁё]", голая), голая


@pytest.mark.parametrize("имя", ПУСКАЧИ)
def test_ярлык_не_закрывает_окно_молча(имя):
    """Окно, схлопнувшееся с ошибкой, — это потерянный человек."""
    текст = io.open(os.path.join(КОРЕНЬ, имя), encoding="utf-8").read()
    assert "pause" in текст
    assert "chcp 65001" in текст
    assert "launcher.py" in текст


def test_ярлыки_зовут_разное():
    команды = set()
    for имя in ПУСКАЧИ:
        текст = io.open(os.path.join(КОРЕНЬ, имя), encoding="utf-8").read()
        команды.add(re.search(r"launcher\.py\s+(\w+)", текст).group(1))
    assert команды == {"bot", "crm", "check", "doctor"}


def test_есть_запуск_для_линукса():
    путь = os.path.join(КОРЕНЬ, "запустить.sh")
    assert os.path.exists(путь)
    assert os.access(путь, os.X_OK), "файл должен быть исполняемым"
    assert "launcher.py" in io.open(путь, encoding="utf-8").read()

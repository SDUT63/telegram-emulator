"""Юниты systemd: круглосуточная работа без человека у терминала.

Бот, запущенный из окна терминала, живёт до первой перезагрузки сервера
и до первого падения. Для службы, которую показали в рекламе, это
значит: люди пишут, им не отвечают, и узнают об этом наутро.

Проверки следят не за форматом (за ним следит systemd-analyze), а за
тем, что юниты не разошлись с кодом: запускают существующие файлы
и сохраняют решения, принятые в этом проекте.
"""
from __future__ import annotations

import configparser
import pathlib

import pytest

КОРЕНЬ = pathlib.Path(__file__).resolve().parent.parent
ЮНИТЫ = КОРЕНЬ / "ops" / "systemd"


def _разобрать(имя: str) -> configparser.ConfigParser:
    разбор = configparser.ConfigParser(strict=False)
    # systemd допускает повторяющиеся ключи (два ExecStart подряд),
    # configparser по умолчанию — нет.
    разбор.optionxform = str
    разбор.read(ЮНИТЫ / имя, encoding="utf-8")
    return разбор


СЛУЖБЫ = ["sdut-bot.service", "sdut-crm.service", "sdut-backup.service",
          "sdut-retention.service"]


@pytest.mark.parametrize("имя", СЛУЖБЫ + ["sdut-backup.timer", "sdut-retention.timer"])
def test_юнит_на_месте_и_читается(имя):
    assert (ЮНИТЫ / имя).is_file(), f"нет юнита {имя}"
    assert _разобрать(имя).sections(), имя


@pytest.mark.parametrize("имя", СЛУЖБЫ)
def test_запускаются_существующие_файлы(имя):
    """Юнит, ссылающийся на переименованный файл, молчит до первого запуска."""
    разбор = _разобрать(имя)
    строки = [разбор["Service"].get(ключ, "")
              for ключ in ("ExecStart", "ExecStartPre")]
    пути = []
    for строка in строки:
        for кусок in строка.split():
            if кусок.startswith("/opt/sdut/") and not кусок.endswith("/python"):
                пути.append(кусок[len("/opt/sdut/"):])
    assert пути, f"{имя}: не видно, что запускается"
    пропало = [п for п in пути if not (КОРЕНЬ / п).exists()]
    assert пропало == [], f"{имя} запускает то, чего нет: {пропало}"


def test_бот_поднимается_только_после_проверки_готовности():
    """Лучше не подняться и сказать об этом, чем принимать людей
    и терять их карточки."""
    разбор = _разобрать("sdut-bot.service")
    assert "preflight.py" in разбор["Service"].get("ExecStartPre", "")


@pytest.mark.parametrize("имя", ["sdut-bot.service", "sdut-crm.service"])
def test_упавшая_служба_поднимается_сама(имя):
    служба = _разобрать(имя)["Service"]
    assert служба.get("Restart") == "always"
    # Без задержки бот при недоступной базе крутится в цикле
    # перезапусков и забивает журнал вместо того, чтобы дать её поднять.
    assert служба.get("RestartSec"), f"{имя}: перезапуск без задержки"


def test_crm_слушает_только_свой_компьютер():
    """В CRM имена, телефоны, адреса и сведения о здоровье."""
    окружение = _разобрать("sdut-crm.service")["Service"].get("Environment", "")
    assert "CRM_HOST=127.0.0.1" in окружение


def test_неназначенный_срок_хранения_не_считается_поломкой():
    """Возврат 2 значит «срок не назначен» — это не повод слать тревогу
    каждую ночь, но и не успех, который надо спрятать."""
    служба = _разобрать("sdut-retention.service")["Service"]
    assert "2" in служба.get("SuccessExitStatus", "")


@pytest.mark.parametrize("имя", ["sdut-backup.timer", "sdut-retention.timer"])
def test_пропущенный_запуск_навёрстывается(имя):
    """Машина была выключена ночью — копия снимется после включения,
    а не пропустится молча до завтра."""
    assert _разобрать(имя)["Timer"].get("Persistent") == "true"


@pytest.mark.parametrize("имя", ["sdut-bot.service", "sdut-crm.service"])
def test_права_службы_сужены(имя):
    """За этой границей персональные данные, и сужать её дёшево."""
    служба = _разобрать(имя)["Service"]
    for ключ, значение in (("NoNewPrivileges", "true"),
                           ("ProtectHome", "true"),
                           ("ProtectSystem", "strict")):
        assert служба.get(ключ) == значение, f"{имя}: {ключ}"


def test_настройки_читаются_из_защищённого_файла():
    """Адрес базы с паролем не должен лежать в самом юните."""
    for имя in СЛУЖБЫ:
        служба = _разобрать(имя)["Service"]
        assert служба.get("EnvironmentFile", "").startswith("/etc/sdut/"), имя
        текст = (ЮНИТЫ / имя).read_text(encoding="utf-8")
        assert "postgresql://" not in текст, f"{имя}: адрес базы внутри юнита"

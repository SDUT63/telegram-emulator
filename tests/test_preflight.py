"""Проверка готовности: можно ли включать бота круглосуточно.

Пока бота знают пять человек, беду замечают и чинят руками. Когда
по объявлению приходит сотня семей, никто не заметит, что карточки
не сохраняются, — их просто не будет, а люди решат, что им не ответили.

Поэтому проверка должна быть строгой там, где теряются данные, и
не должна пугать там, где всё в порядке.
"""
from __future__ import annotations

import os

import pytest

import preflight

# Адрес базы запоминается при импорте: фикстура «чисто» стирает
# переменные окружения раньше, чем тело теста успевает их прочитать.
АДРЕС_БАЗЫ = (os.getenv("SDUT_DATABASE_URL") or "").strip()


@pytest.fixture()
def чисто(monkeypatch):
    """Окружение без настроек: как на свежей машине."""
    for имя in ("MAX_BOT_TOKEN", "SDUT_DATABASE_URL", "SDUT_BACKUP_PASSPHRASE_FILE",
                "SDUT_METRICS_TOKEN", "CRM_HOST", "MAX_WEBHOOK_HOST"):
        monkeypatch.delenv(имя, raising=False)
    return monkeypatch


def _итог(итоги, что: str) -> preflight.Итог:
    for и in итоги:
        if и.что == что:
            return и
    raise AssertionError(f"нет проверки «{что}»: {[и.что for и in итоги]}")


# ------------------------------------------------------- красный путь

def test_без_настроек_запускать_нельзя(чисто):
    итоги = preflight.проверить()

    assert _итог(итоги, "Токен MAX").уровень == preflight.НЕЛЬЗЯ
    assert _итог(итоги, "База данных").уровень == preflight.НЕЛЬЗЯ
    assert "Запускать нельзя" in preflight.отчёт(итоги)
    assert preflight.main() == 1


def test_отчёт_называет_что_делать(чисто):
    """Строка «нельзя» без объяснения — это тупик, а не проверка."""
    for итог in preflight.проверить():
        if итог.уровень == preflight.НЕЛЬЗЯ:
            assert итог.делать or итог.подробность, итог.что


def test_упавшая_проверка_не_обрывает_отчёт(чисто, monkeypatch):
    """Одна поломка не должна прятать остальные девять."""
    def падает():
        raise SystemExit("что-то пошло не так")

    monkeypatch.setattr(preflight, "ПРОВЕРКИ", (падает, preflight.угрозы_жизни))
    итоги = preflight.проверить()

    assert len(итоги) == 2
    assert _итог(итоги, "Распознавание угрозы жизни").уровень == preflight.ГОТОВО


# ------------------------------------------------------- существо проверок

def test_файловое_хранилище_не_годится_для_круглосуточной_работы(чисто):
    """Файл рядом с ботом не переживает двух процессов сразу."""
    assert preflight.хранилище().уровень == preflight.НЕЛЬЗЯ


def test_база_которая_не_отвечает_это_стоп(чисто):
    чисто.setenv("SDUT_DATABASE_URL", "postgresql://нет:нет@127.0.0.1:1/нет")
    итог = preflight.хранилище()

    assert итог.уровень == preflight.НЕЛЬЗЯ
    assert "ни одного ответа" in итог.делать


def test_ключ_шифрования_копий_обязателен(чисто):
    assert preflight.резервные_копии().уровень == preflight.НЕЛЬЗЯ


def test_ключ_копий_доступный_посторонним_это_внимание(чисто, tmp_path):
    ключ = tmp_path / "passphrase"
    ключ.write_text("длинный-пароль", encoding="utf-8")
    ключ.chmod(0o644)
    чисто.setenv("SDUT_BACKUP_PASSPHRASE_FILE", str(ключ))

    итог = preflight.резервные_копии()
    assert итог.уровень == preflight.ВНИМАНИЕ
    assert "chmod 600" in итог.делать


def test_ключ_копий_с_верными_правами_это_готово(чисто, tmp_path):
    ключ = tmp_path / "passphrase"
    ключ.write_text("длинный-пароль", encoding="utf-8")
    ключ.chmod(0o600)
    чисто.setenv("SDUT_BACKUP_PASSPHRASE_FILE", str(ключ))

    assert preflight.резервные_копии().уровень == preflight.ГОТОВО


def test_пустой_файл_ключа_не_проходит(чисто, tmp_path):
    ключ = tmp_path / "passphrase"
    ключ.write_text("", encoding="utf-8")
    ключ.chmod(0o600)
    чисто.setenv("SDUT_BACKUP_PASSPHRASE_FILE", str(ключ))

    assert preflight.резервные_копии().уровень == preflight.НЕЛЬЗЯ


def test_crm_открытая_в_сеть_это_предупреждение(чисто):
    чисто.setenv("CRM_HOST", "0.0.0.0")
    итог = preflight.crm_наружу()

    assert итог.уровень == preflight.ВНИМАНИЕ
    assert "телефоны" in итог.делать


def test_метрики_наружу_без_токена_это_предупреждение(чисто):
    чисто.setenv("MAX_WEBHOOK_HOST", "0.0.0.0")
    assert preflight.метрики_наружу().уровень == preflight.ВНИМАНИЕ

    чисто.setenv("SDUT_METRICS_TOKEN", "секрет")
    assert preflight.метрики_наружу().уровень == preflight.ГОТОВО


def test_угрозы_жизни_проверяются_поведением_а_не_наличием_файла(чисто, monkeypatch):
    """Модуль на месте, а фразу не узнаёт — это «нельзя», а не «готово»."""
    import emergency

    monkeypatch.setattr(emergency, "распознать", lambda *а, **к: None)
    итог = preflight.угрозы_жизни()

    assert итог.уровень == preflight.НЕЛЬЗЯ
    assert "103" in итог.делать


def test_ложная_тревога_тоже_замечается(чисто, monkeypatch):
    import emergency

    настоящая = emergency.распознать
    monkeypatch.setattr(
        emergency, "распознать",
        lambda т, в=None: настоящая(т, в) or emergency.Тревога(
            вид="скорая", признак="всё подряд", ответ=emergency.СКОРАЯ),
    )
    assert preflight.угрозы_жизни().уровень == preflight.ВНИМАНИЕ


def test_операторы_без_старшего_это_предупреждение(чисто, tmp_path, monkeypatch):
    import crm_store as store

    monkeypatch.setattr(store, "OPERATORS", str(tmp_path / "operators.json"))
    store.add_operator("anna", "Анна", "длинный-пароль", role="operator")

    итог = preflight.операторы()
    assert итог.уровень == preflight.ВНИМАНИЕ
    assert "supervisor" in итог.делать


def test_пароль_по_умолчанию_это_стоп(чисто, tmp_path, monkeypatch):
    import crm_store as store

    monkeypatch.setattr(store, "OPERATORS", str(tmp_path / "operators.json"))
    store.add_operator("boss", "Старший", "123456", role="supervisor")

    итог = preflight.операторы()
    assert итог.уровень == preflight.НЕЛЬЗЯ
    assert "boss" in итог.подробность


def test_заведённые_операторы_со_старшим_это_готово(чисто, tmp_path, monkeypatch):
    import crm_store as store

    monkeypatch.setattr(store, "OPERATORS", str(tmp_path / "operators.json"))
    store.add_operator("anna", "Анна", "длинный-пароль-1", role="operator")
    store.add_operator("boss", "Старший", "длинный-пароль-2", role="supervisor")

    assert preflight.операторы().уровень == preflight.ГОТОВО


def test_анкета_проходится_живой_речью(чисто):
    """Та же проверка, что в тестах, но запускаемая на живой машине."""
    assert preflight.анкета_проходится().уровень == preflight.ГОТОВО


# ------------------------------------------------------- зелёный путь

@pytest.mark.skipif(not АДРЕС_БАЗЫ, reason="зелёный путь требует настоящей базы")
def test_на_настроенной_машине_отмашка_даётся(чисто, tmp_path, monkeypatch):
    """Проверка должна не только пугать, но и уметь сказать «можно»."""
    import crm_store as store

    ключ = tmp_path / "passphrase"
    ключ.write_text("длинный-пароль", encoding="utf-8")
    ключ.chmod(0o600)

    чисто.setenv("MAX_BOT_TOKEN", "x" * 40)
    чисто.setenv("SDUT_DATABASE_URL", АДРЕС_БАЗЫ)
    чисто.setenv("SDUT_BACKUP_PASSPHRASE_FILE", str(ключ))
    monkeypatch.setattr(store, "OPERATORS", str(tmp_path / "operators.json"))
    store.add_operator("anna", "Анна", "длинный-пароль-1", role="operator")
    store.add_operator("boss", "Старший", "длинный-пароль-2", role="supervisor")

    итоги = preflight.проверить()
    стоп = [и for и in итоги if и.уровень == preflight.НЕЛЬЗЯ]
    assert стоп == [], [(и.что, и.подробность) for и in стоп]


def test_расхождение_бота_и_crm_это_стоп(чисто, monkeypatch):
    """Бот на PostgreSQL, а читается файл — координатор увидит пустоту.

    Это ровно та беда, ради которой проверка и добавлена: она не ломает
    бота и ничего не пишет в логи, а вся работа службы стоит.
    """
    import storage

    чисто.setenv("SDUT_DATABASE_URL", "postgresql://x@127.0.0.1/x")
    monkeypatch.setattr(storage, "где_данные", lambda: "файл")
    monkeypatch.setattr(storage, "открыть",
                        lambda **к: type("П", (), {"stats": lambda с: (5, 1),
                                                   "state": {}})())

    итог = preflight.карточки_видны_координатору()
    assert итог.уровень == preflight.НЕЛЬЗЯ
    assert "ноль обращений" in итог.делать


def test_обращения_без_единого_телефона_это_внимание(чисто, monkeypatch):
    """Полная база и ни одного телефона — звонить некуда."""
    import storage

    monkeypatch.setattr(storage, "где_данные", lambda: "файл")
    monkeypatch.setattr(
        storage, "открыть",
        lambda **к: type("П", (), {
            "stats": lambda с: (7, 0),
            "state": {"a": {"answers": {"who": "О близком человеке"}}},
        })())

    итог = preflight.карточки_видны_координатору()
    assert итог.уровень == preflight.ВНИМАНИЕ
    assert "funnel" in итог.делать


def test_неназначенный_срок_хранения_это_внимание(чисто):
    """Молча хранить персональные данные вечно нельзя."""
    итог = preflight.срок_хранения_анкет()

    assert итог.уровень == preflight.ВНИМАНИЕ
    assert "вечно" in итог.делать
    assert "purge_expired_cases" in итог.делать


def test_назначенный_срок_хранения_это_готово(чисто):
    чисто.setenv("SDUT_CASE_RETENTION_DAYS", "365")
    assert preflight.срок_хранения_анкет().уровень == preflight.ГОТОВО


def test_бессмысленный_срок_хранения_это_стоп(чисто):
    for плохое in ("год", "0", "99999"):
        чисто.setenv("SDUT_CASE_RETENTION_DAYS", плохое)
        assert preflight.срок_хранения_анкет().уровень == preflight.НЕЛЬЗЯ, плохое

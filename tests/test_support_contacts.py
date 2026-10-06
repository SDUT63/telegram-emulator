"""Телефоны помощи: справочник вместо цифр в тексте (Р-А3).

Контрактные тесты 1, 2 и 12 мануала по архиву. Бот называл человеку,
который пишет о желании умереть, детский телефон доверия; заметить это
можно было, только прочитав код. Эти проверки держат три вещи: детский
номер не попадает во взрослые ответы, номера 8-800 в текстах бота есть
только из справочника, и у каждого номера есть дата проверки.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

import emergency
import support_contacts

КОРЕНЬ = Path(__file__).resolve().parents[1]
ДЕТСКИЙ = support_contacts.СПРАВОЧНИК["children_line"].phone

# Номер 8-800 в любом написании и группировке: 8-800-100-49-94,
# 8 (800) 2000-122, 88001004994.
_ВОСЕМЬ_СОТ = re.compile(r"(?<!\d)8[\s(-]*800[\s)-]*(?:\d[\s-]?){6}\d(?!\d)")


def _цифры(номер: str) -> str:
    return re.sub(r"\D", "", номер)


def _тексты_бота() -> list[Path]:
    """Всё, что бот или приложение показывает людям, кроме самого справочника."""
    файлы = [p for p in КОРЕНЬ.glob("*.py")]
    файлы += list((КОРЕНЬ / "база").glob("*.md"))
    for папка, маски in (("webapp", ("*.html", "*.json", "*.js")), ("crm", ("*.html", "*.js"))):
        for маска in маски:
            файлы += list((КОРЕНЬ / папка).glob(маска))
    return [p for p in файлы if p.is_file()]


# ------------------------------------------- тест 1: детский номер взрослым


def test_ответ_о_смерти_называет_взрослую_линию_а_не_детскую():
    сигнал = emergency.распознать("не хочу больше жить")
    assert сигнал is not None and сигнал.вид == "поддержка"
    assert support_contacts.номер("crisis_adult") in сигнал.ответ
    assert ДЕТСКИЙ not in сигнал.ответ


@pytest.mark.parametrize("ответ", [emergency.ПОДДЕРЖКА, emergency.СКОРАЯ])
def test_экстренные_ответы_без_детского_номера(ответ):
    assert _цифры(ДЕТСКИЙ) not in _цифры(ответ)


def test_детский_номер_не_встречается_в_текстах_бота():
    """Ни в коде ответов, ни в базе знаний, ни в приложении."""
    найдено = [str(p.relative_to(КОРЕНЬ)) for p in _тексты_бота()
               if _цифры(ДЕТСКИЙ) in {_цифры(м) for м in
                                      _ВОСЕМЬ_СОТ.findall(p.read_text(encoding="utf-8", errors="ignore"))}]
    assert найдено == []


def test_детский_номер_во_взрослый_ответ_не_подставляется():
    with pytest.raises(ValueError):
        support_contacts.подставить("звоните {contact:children_line}")
    assert support_contacts.подставить("{contact:children_line}",
                                       несовершеннолетний=True) == ДЕТСКИЙ


# ------------------------------------------- тест 2: номера только из справочника


def test_в_шаблонах_экстренных_ответов_нет_номеров_цифрами():
    for шаблон in (emergency.ПОДДЕРЖКА_ШАБЛОН, emergency.СКОРАЯ_ШАБЛОН):
        без_ссылок = re.sub(r"\{contact:[a-z0-9_]+\}", "", шаблон)
        assert not re.search(r"\d{3}", без_ссылок), шаблон
        assert support_contacts.ссылки(шаблон)


def test_каждая_ссылка_есть_в_справочнике():
    for шаблон in (emergency.ПОДДЕРЖКА_ШАБЛОН, emergency.СКОРАЯ_ШАБЛОН):
        for id in support_contacts.ссылки(шаблон):
            assert id in support_contacts.СПРАВОЧНИК, id


def test_неизвестная_ссылка_не_проходит_молча():
    with pytest.raises(KeyError):
        support_contacts.подставить("{contact:nonexistent}")


def test_номер_8_800_в_текстах_бота_только_из_справочника():
    """Номер, которого нет в справочнике, никто не проверял."""
    известные = {_цифры(т.phone) for т in support_contacts.СПРАВОЧНИК.values()}
    чужие = {}
    for p in _тексты_бота():
        for м in _ВОСЕМЬ_СОТ.findall(p.read_text(encoding="utf-8", errors="ignore")):
            if _цифры(м) not in известные:
                чужие.setdefault(str(p.relative_to(КОРЕНЬ)), []).append(м)
    assert чужие == {}


# ------------------------------------------- тест 12: даты проверки


@pytest.mark.parametrize("id", sorted(support_contacts.СПРАВОЧНИК))
def test_у_номера_есть_дата_проверки_и_пересмотра(id):
    телефон = support_contacts.СПРАВОЧНИК[id]
    assert isinstance(телефон.verified_at, date)
    assert телефон.next_review_at > телефон.verified_at
    assert телефон.verification


def test_пометка_в_ответе_правда_о_линии():
    """«Круглосуточно, бесплатно, анонимно» — обещание человеку в кризисе.

    Если линию в справочнике сменят на ту, что работает не круглосуточно
    или не анонимно, этот тест заставит поправить и текст ответа.
    """
    линия = support_contacts.СПРАВОЧНИК["crisis_adult"]
    assert "круглосуточно, бесплатно, анонимно" in emergency.ПОДДЕРЖКА
    assert линия.status == "active"
    assert (линия.available_24_7, линия.free, линия.anonymous) == (True, True, True)


def test_взрослая_кризисная_линия_для_взрослых():
    assert support_contacts.СПРАВОЧНИК["crisis_adult"].audience == "adults"
    assert support_contacts.СПРАВОЧНИК["children_line"].status == "minors_only"


def test_битый_справочник_не_загружается(tmp_path):
    """Лучше не запуститься, чем назвать человеку битый номер."""
    битый = tmp_path / "contacts.json"
    битый.write_text('{"contacts": [{"id": "x"}]}', encoding="utf-8")
    with pytest.raises((TypeError, KeyError, ValueError)):
        support_contacts.загрузить(битый)

"""До любой статьи должно быть можно дойти кнопкой.

Заголовки здесь длинные и с двоеточиями («Выездная патронажная служба: что
это и как её получить»), а payload кнопки короткий. Ключ кнопки — устойчивый
дайджест, а не обрезанный заголовок: обрезка ломала каждую четвёртую статью
и делала это молча — нажатие просто не давало ничего.
"""
from __future__ import annotations

from collections import Counter

import knowledge
from max_production_dispatcher import _resolve_article_callback
from max_ui import _article_callback, article_screen


def test_каждая_статья_открывается_своей_кнопкой():
    недоступные = []
    for статья in knowledge.загрузить():
        ключ = _article_callback(статья.заголовок)
        assert ключ.startswith("k:@"), "ключ кнопки должен быть дайджестом, а не заголовком"
        заголовок = _resolve_article_callback(ключ[2:])
        if заголовок is None or article_screen(заголовок, None, "u1") is None:
            недоступные.append(статья.заголовок)

    assert недоступные == [], f"кнопка не открывает статью: {недоступные[:5]}"


def test_заголовки_статей_уникальны():
    """Два одинаковых названия — это и неоднозначная кнопка, и путаница в списке."""
    счёт = Counter(с.заголовок for с in knowledge.загрузить())
    дубли = {к: v for к, v in счёт.items() if v > 1}
    assert дубли == {}, f"дублирующиеся заголовки: {дубли}"


def test_ключ_кнопки_помещается_в_payload():
    """MAX ограничивает payload; дайджест держит длину постоянной."""
    длины = {len(_article_callback(с.заголовок)) for с in knowledge.загрузить()}
    assert длины == {19}, f"ключи кнопок разной длины: {sorted(длины)}"


def test_неизвестный_ключ_не_открывает_случайную_статью():
    assert _resolve_article_callback("@" + "0" * 16) is None
    assert _resolve_article_callback("@короткий") is None
    assert _resolve_article_callback("@ZZZZZZZZZZZZZZZZ") is None

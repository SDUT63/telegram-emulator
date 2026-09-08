"""Golos Text прямо в странице — чтобы сборка не зависела от сети.

Кадры мануала рисует браузер. Если шрифт он тянет с fonts.googleapis.com,
каждый кадр ждёт сеть: сорок кадров превращаются в двадцать минут, а без
интернета сборка молча выходит другой — не тем шрифтом.

Поэтому один раз скачиваем и складываем рядом файл `_шрифт.css`, где сами
буквы вшиты в текст стилей. Дальше сборка работает офлайн и мгновенно.
Файл можно удалить — он соберётся заново.
"""
from __future__ import annotations

import base64
import os
import re
import urllib.request

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
КЭШ = os.path.join(ЗДЕСЬ, "_шрифт.css")

ССЫЛКА = ("https://fonts.googleapis.com/css2"
          "?family=Golos+Text:wght@400;500;600;700;800&display=swap")
# Без такого User-Agent Google отдаёт ttf вместо woff2 — он в разы тяжелее
БРАУЗЕР = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _достать(адрес: str) -> bytes:
    запрос = urllib.request.Request(адрес, headers={"User-Agent": БРАУЗЕР})
    with urllib.request.urlopen(запрос, timeout=30) as ответ:
        return ответ.read()


def стили() -> str:
    """Содержимое `<style>` с вшитыми буквами. Скачивает только в первый раз."""
    if os.path.exists(КЭШ):
        return open(КЭШ, encoding="utf-8").read()

    css = _достать(ССЫЛКА).decode("utf-8")
    for адрес in sorted(set(re.findall(r"url\((https://[^)]+\.woff2)\)", css))):
        данные = base64.b64encode(_достать(адрес)).decode("ascii")
        css = css.replace(адрес, "data:font/woff2;base64," + данные)

    open(КЭШ, "w", encoding="utf-8").write(css)
    return css


def вшить(страница: str) -> str:
    """Заменить ссылку на Google Fonts вшитыми буквами.

    Если сети нет и кэша тоже — оставляем страницу как есть: пусть лучше
    соберётся запасным шрифтом, чем не соберётся вовсе. О подмене
    предупреждаем, чтобы это не прошло незамеченным.
    """
    try:
        встроенные = стили()
    except Exception as беда:                       # сеть, прокси, что угодно
        print("  шрифт не скачался (%s) — кадры выйдут запасным шрифтом" % беда)
        return страница

    return re.sub(
        r'<link rel="stylesheet" href="https://fonts\.googleapis\.com[^>]*>',
        "<style>" + встроенные + "</style>",
        страница,
    )

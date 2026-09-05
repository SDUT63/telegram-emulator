#!/usr/bin/env python3
"""
Собирает страницу-справочник в ОДИН файл, который работает без интернета.

Картинки и шрифт вшиваются прямо в HTML, поэтому получившийся файл можно
скачать, положить куда угодно и открыть двойным щелчком — ни папки с
картинками, ни сервера, ни сети не требуется.

Запуск:
    python build_offline.py

Результат:
    webapp/Навигатор-помощи-СДУТ.html
"""

from __future__ import annotations

import base64
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "webapp", "index.html")
IMG_DIR = os.path.join(HERE, "webapp", "img")
OUT = os.path.join(HERE, "webapp", "Навигатор-помощи-СДУТ.html")

FONT_CSS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Golos+Text:wght@400;500;600;700;800&display=swap"
)
# Кириллица и латиница: остальные подмножества на этой странице не нужны
WANTED_SUBSETS = ("cyrillic", "latin")
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def data_uri(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def inline_font() -> str | None:
    """Скачать шрифт и вернуть готовый блок @font-face с вшитыми файлами."""
    try:
        css = fetch(FONT_CSS_URL).decode("utf-8")
    except Exception as error:  # noqa: BLE001
        print(f"  шрифт скачать не удалось ({error})")
        return None

    # Google отдаёт css блоками, каждому предшествует комментарий с названием
    # подмножества: /* cyrillic */ @font-face { ... }
    blocks = re.findall(r"/\*\s*([a-z-]+)\s*\*/\s*(@font-face\s*\{[^}]+\})", css)
    kept: list[str] = []
    cache: dict[str, str] = {}

    for subset, block in blocks:
        if subset not in WANTED_SUBSETS:
            continue
        match = re.search(r"url\((https://[^)]+\.woff2)\)", block)
        if not match:
            continue
        url = match.group(1)
        if url not in cache:
            try:
                cache[url] = data_uri(fetch(url), "font/woff2")
            except Exception as error:  # noqa: BLE001
                print(f"  не скачался {url.rsplit('/', 1)[-1]} ({error})")
                continue
        kept.append(block.replace(url, cache[url]))

    if not kept:
        return None
    total = sum(len(b) for b in kept)
    print(f"  шрифт: {len(kept)} начертаний, {total / 1024:.0f} КБ в тексте файла")
    return "\n".join(kept)


def main() -> int:
    if not os.path.exists(SRC):
        print(f"Не найден {SRC}")
        return 1

    with open(SRC, encoding="utf-8") as fh:
        html = fh.read()

    # --- картинки ---
    names = sorted(set(re.findall(r'(?:src|href)="img/([^"]+)"', html)))
    print(f"Вшиваю картинки: {len(names)} шт.")
    embedded = 0
    for name in names:
        path = os.path.join(IMG_DIR, name)
        if not os.path.exists(path):
            # В файле есть образец разметки с вымышленным именем — это нормально
            continue
        with open(path, "rb") as fh:
            uri = data_uri(fh.read(), "image/jpeg")
        # href убираем совсем: огромный адрес незачем дублировать, картинку
        # открывает скрипт страницы. tabindex сохраняет доступ с клавиатуры.
        html = html.replace(f'<a class="poster" href="img/{name}" target="_blank" rel="noopener">',
                            '<a class="poster" role="button" tabindex="0">')
        html = html.replace(f'src="img/{name}"', f'src="{uri}"')
        embedded += 1
    print(f"  вшито: {embedded}")

    # --- шрифт ---
    print("Вшиваю шрифт")
    font_css = inline_font()
    link_re = re.compile(
        r'<link rel="preconnect"[^>]*>\s*<link rel="preconnect"[^>]*crossorigin>\s*'
        r'<link rel="stylesheet" href="https://fonts\.googleapis\.com[^"]*">'
    )
    if font_css:
        html = link_re.sub("<style>\n" + font_css + "\n</style>", html, count=1)
    else:
        # Без шрифта страница остаётся рабочей: сработает системный запасной
        print("  оставляю ссылку на Google Fonts — при отсутствии сети")
        print("  подставится системный шрифт, страница не сломается")

    # --- пометка о том, что это офлайн-версия ---
    html = html.replace(
        "<title>Навигатор помощи СДУТ</title>",
        "<title>Навигатор помощи СДУТ</title>\n"
        "<!-- Один файл: картинки и шрифт вшиты внутрь. Работает без интернета. -->",
        1,
    )

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)

    size = os.path.getsize(OUT) / 1024 / 1024
    left = re.findall(r'(?:src|href)="img/', html)
    print()
    print(f"Готово: {OUT}")
    print(f"Размер: {size:.1f} МБ")
    print(f"Осталось внешних ссылок на картинки: {len(left)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

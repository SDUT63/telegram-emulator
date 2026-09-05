#!/usr/bin/env python3
"""
Отдаёт страницу-справочник «Навигатор помощи СДУТ» по адресу /

Нужен, чтобы открыть справочник внутри MAX как мини-приложение: для этого
у страницы должен быть адрес на HTTPS. Сам по себе бот в этом сервере не
нуждается — он работает отдельно и подключается к MAX сам.

Запуск:
    python webapp_server.py

Проверить: http://localhost:5000
"""

from __future__ import annotations

import os

from flask import Flask, jsonify, send_from_directory

from chatbot_survey import Survey

HERE = os.path.dirname(os.path.abspath(__file__))
WEBAPP_DIR = os.path.join(HERE, "webapp")

# По сети отдаём обычную версию, а не единый файл: сама страница весит
# 135 КБ и открывается сразу, а картинки подгружаются по мере прокрутки
# и кэшируются на сутки. Единый файл в четыре с лишним мегабайта
# пришлось бы качать целиком при каждом открытии — он нужен для другого,
# чтобы страницу можно было скачать и открыть без интернета.
PAGE = "index.html"

app = Flask(__name__)


def serve_page():
    response = send_from_directory(WEBAPP_DIR, PAGE)
    # Кэш короткий: страницу правим часто, а мини-приложение должно
    # показывать свежую версию, а не вчерашнюю из памяти браузера.
    response.headers["Cache-Control"] = "public, max-age=60"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Заголовки X-Frame-Options и frame-ancestors намеренно НЕ ставим:
    # мини-приложение открывается внутри MAX, и запрет на встраивание
    # сделал бы страницу пустой.
    return response


@app.route("/")
@app.route("/app")
@app.route("/app/")
def index():
    """Страница-справочник. Корневой адрес — для мини-приложения MAX."""
    return serve_page()


@app.route("/img/<path:filename>")
@app.route("/app/img/<path:filename>")
def image(filename: str):
    """Постеры — нужны только обычной версии страницы."""
    response = send_from_directory(os.path.join(WEBAPP_DIR, "img"), filename)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/health")
def health():
    return jsonify({"status": "ok", "page": PAGE})


@app.route("/stats")
def stats():
    """Сколько обращений собрал бот. Без персональных данных."""
    started, finished = Survey().stats()
    return jsonify({"started": started, "finished": finished})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))

    print()
    print("=" * 62)
    print(f"  Страница открыта: http://localhost:{port}")
    print()
    print("  Чтобы страница открылась в MAX, нужен адрес на HTTPS.")
    print("  Как его получить — написано в МИНИ_ПРИЛОЖЕНИЕ.md")
    print()
    print("  Остановить: Ctrl+C")
    print("=" * 62)
    print()

    app.run(host="0.0.0.0", port=port, debug=os.getenv("DEBUG") == "True")

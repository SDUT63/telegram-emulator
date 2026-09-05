#!/usr/bin/env python3
"""
Отдаёт страницу мини-приложения «Навигатор помощи СДУТ» по адресу /app.

Это НЕ бот. Бот живёт в max_bot.py и работает сам по себе — ему не нужен
ни этот сервер, ни публичный адрес. Сервер нужен только тогда, когда
страницу-справочник надо открыть внутри MAX как мини-приложение: для этого
у неё должен быть адрес на домене с сертификатом.

Запуск:
    python webapp_server.py

Проверить: http://localhost:5000/app
"""

from __future__ import annotations

import os

from flask import Flask, jsonify, send_from_directory

from chatbot_survey import Survey

HERE = os.path.dirname(os.path.abspath(__file__))
WEBAPP_DIR = os.path.join(HERE, "webapp")

app = Flask(__name__)


@app.route("/app")
@app.route("/app/")
def webapp_index():
    """Страница-справочник: срочная помощь, куда обратиться, ответы на вопросы."""
    response = send_from_directory(WEBAPP_DIR, "index.html")
    response.headers["Cache-Control"] = "public, max-age=300"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route("/app/img/<path:filename>")
def webapp_image(filename: str):
    """Постеры канала, встроенные в страницу."""
    response = send_from_directory(os.path.join(WEBAPP_DIR, "img"), filename)
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/stats")
def stats():
    """Сколько обращений собрал бот. Без персональных данных."""
    started, finished = Survey().stats()
    return jsonify({"started": started, "finished": finished})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print()
    print("=" * 62)
    print(f"  Страница-справочник: http://localhost:{port}/app")
    print("  Остановить: Ctrl+C")
    print("=" * 62)
    print()
    app.run(host="0.0.0.0", port=port, debug=os.getenv("DEBUG") == "True")

#!/usr/bin/env python3
"""
CRM оператора СДУТ: обращения из бота в удобном виде.

Запуск:
    python crm_server.py

Откроется по адресу http://localhost:5001

Почему отдельный сервер
-----------------------
Здесь имена, телефоны и описания состояния людей. Страница-справочник
(webapp_server.py) висит на публичном адресе через туннель — CRM на такой
адрес выставлять нельзя. Поэтому это отдельный сервер, который по
умолчанию слушает ТОЛЬКО локальный адрес: снаружи к нему не подключиться
даже зная порт.

Данные CRM (статусы, заметки, исходящие) живут в своих файлах. Файл с
анкетами сервер только читает — пишет туда исключительно бот.
"""

from __future__ import annotations

import functools
import os
import secrets

from flask import (
    Flask,
    jsonify,
    redirect,
    request,
    send_from_directory,
    session,
    url_for,
)

import crm_store as store
from chatbot_survey import Survey
from survey_questions import QUESTIONS

HERE = os.path.dirname(os.path.abspath(__file__))
CRM_DIR = os.path.join(HERE, "crm")
SECRET_FILE = os.path.join(HERE, ".crm_secret")

app = Flask(__name__)


def _secret() -> bytes:
    """Ключ для подписи сессий. Постоянный, чтобы вход не слетал при перезапуске."""
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "rb") as fh:
            data = fh.read().strip()
            if data:
                return data
    data = secrets.token_bytes(32)
    with open(SECRET_FILE, "wb") as fh:
        fh.write(data)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return data


app.secret_key = _secret()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
)


def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("operator"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Нужно войти"}), 401
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)

    return wrapper


# --------------------------------------------------------------------- вход


@app.route("/login")
def login_page():
    return send_from_directory(CRM_DIR, "login.html")


@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    name = store.verify(
        (data.get("login") or "").strip(), data.get("password") or ""
    )
    if not name:
        return jsonify({"error": "Неверный логин или пароль"}), 401
    session.permanent = True
    session["operator"] = name
    return jsonify({"operator": name})


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


# -------------------------------------------------------------------- данные


def _questions_map() -> list[dict[str, str]]:
    return [
        {"id": q["id"], "text": q["text"].splitlines()[0], "section": q.get("section", "")}
        for q in QUESTIONS
    ]


def _case_view(user_id: str, person: dict, delivered: dict) -> dict:
    """Одно обращение в том виде, в каком его показывает CRM."""
    operator = store.case(user_id)
    answers = person.get("answers", {})
    sent = []
    for message in operator.get("sent", []):
        info = delivered.get(message.get("id"), {})
        sent.append(
            {
                **message,
                "delivered": info.get("delivered"),
                "error": info.get("error", ""),
            }
        )
    return {
        "user_id": user_id,
        "name": answers.get("name", ""),
        "phone": answers.get("phone", ""),
        "who": answers.get("who", ""),
        "need": answers.get("need", ""),
        "mobility": answers.get("mobility", ""),
        "started": person.get("started", ""),
        "finished": person.get("finished", ""),
        "alerts": person.get("alerts", []),
        "answers": answers,
        "status": operator.get("status", store.STATUSES[0]),
        "assigned": operator.get("assigned", ""),
        "notes": operator.get("notes", []),
        "sent": sent,
        "complete": bool(person.get("finished")),
    }


@app.get("/api/cases")
@login_required
def api_cases():
    survey = Survey()  # только чтение: конструктор ничего не пишет
    delivered = store.delivery_state()
    cases = [
        _case_view(user_id, person, delivered)
        for user_id, person in survey.state.items()
    ]
    # Свежие сверху, а обращения с тревожными признаками — ещё выше
    cases.sort(key=lambda c: (not c["alerts"], c["started"] or ""), reverse=True)
    cases.sort(key=lambda c: bool(c["alerts"]), reverse=True)
    return jsonify(
        {
            "operator": session.get("operator"),
            "statuses": store.STATUSES,
            "questions": _questions_map(),
            "cases": cases,
        }
    )


@app.post("/api/case/<user_id>/status")
@login_required
def api_status(user_id: str):
    data = request.get_json(silent=True) or {}
    try:
        store.set_status(user_id, data.get("status", ""), session["operator"])
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({"ok": True})


@app.post("/api/case/<user_id>/assign")
@login_required
def api_assign(user_id: str):
    store.assign(user_id, session["operator"])
    return jsonify({"ok": True})


@app.post("/api/case/<user_id>/note")
@login_required
def api_note(user_id: str):
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    if not text:
        return jsonify({"error": "Пустая заметка"}), 400
    store.add_note(user_id, text, session["operator"])
    return jsonify({"ok": True})


@app.post("/api/case/<user_id>/reply")
@login_required
def api_reply(user_id: str):
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    if not text:
        return jsonify({"error": "Пустое сообщение"}), 400
    if len(text) > 2000:
        return jsonify({"error": "Слишком длинное сообщение"}), 400
    message_id = store.queue_message(user_id, text, session["operator"])
    return jsonify({"ok": True, "id": message_id})


# -------------------------------------------------------------------- страница


@app.get("/")
@login_required
def index():
    response = send_from_directory(CRM_DIR, "index.html")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return jsonify({"status": "ok", "operators": len(store.load_operators())})


if __name__ == "__main__":
    port = int(os.getenv("CRM_PORT", "5001"))
    # 127.0.0.1 — слушаем только этот компьютер. Менять на 0.0.0.0 нельзя
    # без разговора о том, кто ещё получит доступ к персональным данным.
    host = os.getenv("CRM_HOST", "127.0.0.1")

    if not store.load_operators():
        print()
        print("=" * 62)
        print("  Ни одного оператора не заведено — войти будет некому.")
        print()
        print("  Заведите первого:")
        print("      python crm_store.py")
        print("=" * 62)
        print()

    print()
    print("=" * 62)
    print(f"  CRM оператора: http://localhost:{port}")
    print()
    if host == "127.0.0.1":
        print("  Слушает только этот компьютер — снаружи не подключиться.")
    else:
        print(f"  ВНИМАНИЕ: слушает {host}. Здесь персональные данные людей.")
        print("  Убедитесь, что до этого адреса не добраться из интернета.")
    print()
    print("  Остановить: Ctrl+C")
    print("=" * 62)
    print()

    app.run(host=host, port=port, debug=os.getenv("DEBUG") == "True")

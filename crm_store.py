#!/usr/bin/env python3
"""
Хранилище данных оператора: статусы, заметки, исходящие сообщения.

Почему отдельно от анкет
------------------------
Бот держит анкеты в памяти и целиком перезаписывает survey_responses.json
после каждого ответа. Если бы CRM писала заметки туда же, бот затирал бы
их следующей записью. Поэтому:

    survey_responses.json   пишет только бот,   CRM читает
    crm_data.json           пишет только CRM,   бот не трогает
    outbox/                 CRM кладёт файлы,   бот их забирает

В outbox каждое сообщение — отдельный файл. Так два процесса никогда не
пишут в один и тот же файл, и блокировки не нужны.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))

CRM_DATA = os.path.join(HERE, "crm_data.json")
OPERATORS = os.path.join(HERE, "operators.json")
OUTBOX_DIR = os.path.join(HERE, "outbox")
SENT_DIR = os.path.join(OUTBOX_DIR, "sent")

STATUSES = ["Новое", "В работе", "Закрыто"]

# Контрольные звонки после закрытия случая — как описано в регламенте
# службы: через 7 дней и через 30 дней.
CALL_STAGES = {"7": 7, "30": 30}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def _write(path: str, data: Any) -> None:
    """Запись через временный файл: обрыв не оставит покорёженный файл."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- операторы


def hash_password(password: str, salt: str | None = None) -> str:
    """Пароль хранится не в открытом виде, а как соль и хеш."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"{salt}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def load_operators() -> dict[str, dict[str, str]]:
    return _read(OPERATORS, {})


def save_operators(data: dict[str, dict[str, str]]) -> None:
    _write(OPERATORS, data)


def add_operator(login: str, name: str, password: str) -> None:
    data = load_operators()
    data[login] = {"name": name, "password": hash_password(password)}
    save_operators(data)


def verify(login: str, password: str) -> str | None:
    """Вернуть имя оператора, если логин и пароль сошлись."""
    operator = load_operators().get(login)
    if not operator:
        return None
    if not check_password(password, operator.get("password", "")):
        return None
    return operator.get("name") or login


# -------------------------------------------------------------- дела в работе


def _blank_case() -> dict[str, Any]:
    return {"status": STATUSES[0], "assigned": "", "notes": [], "sent": [], "calls": {}}


def _all() -> dict[str, Any]:
    return _read(CRM_DATA, {"cases": {}})


def case(user_id: str) -> dict[str, Any]:
    """Данные оператора по одному обращению."""
    data = _all()
    return data["cases"].get(user_id, _blank_case())


def all_cases() -> dict[str, Any]:
    return _all()["cases"]


def _update(user_id: str, change) -> dict[str, Any]:
    data = _all()
    entry = data["cases"].setdefault(user_id, _blank_case())
    change(entry)
    _write(CRM_DATA, data)
    return entry


def set_status(user_id: str, status: str, who: str) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"неизвестный статус: {status}")

    def change(entry: dict[str, Any]) -> None:
        entry["status"] = status
        entry.setdefault("notes", []).append(
            {"at": now(), "who": who, "text": f"Статус: {status}", "system": True}
        )

    return _update(user_id, change)


def assign(user_id: str, who: str) -> dict[str, Any]:
    def change(entry: dict[str, Any]) -> None:
        entry["assigned"] = who
        entry.setdefault("notes", []).append(
            {"at": now(), "who": who, "text": "Взял в работу", "system": True}
        )

    return _update(user_id, change)


def mark_call(user_id: str, which: str, who: str) -> dict[str, Any]:
    """Отметить контрольный звонок сделанным. which — «7» или «30»."""
    if which not in CALL_STAGES:
        raise ValueError(f"неизвестный звонок: {which}")

    def change(entry: dict[str, Any]) -> None:
        entry.setdefault("calls", {})[which] = {"at": now(), "who": who}
        entry.setdefault("notes", []).append(
            {
                "at": now(),
                "who": who,
                "text": f"Контрольный звонок через {which} дней — сделан",
                "system": True,
            }
        )

    return _update(user_id, change)


def undo_call(user_id: str, which: str) -> dict[str, Any]:
    def change(entry: dict[str, Any]) -> None:
        entry.setdefault("calls", {}).pop(which, None)

    return _update(user_id, change)


def add_note(user_id: str, text: str, who: str) -> dict[str, Any]:
    def change(entry: dict[str, Any]) -> None:
        entry.setdefault("notes", []).append({"at": now(), "who": who, "text": text})

    return _update(user_id, change)


# ------------------------------------------------------------ исходящие


def queue_message(user_id: str, text: str, who: str) -> str:
    """Положить сообщение в очередь. Отправит его бот — у него есть связь."""
    os.makedirs(OUTBOX_DIR, exist_ok=True)
    message_id = uuid.uuid4().hex
    payload = {
        "id": message_id,
        "user_id": str(user_id),
        "text": text,
        "who": who,
        "created": now(),
    }
    # Пишем во временный файл и переименовываем: бот не подхватит
    # наполовину записанное сообщение
    tmp = os.path.join(OUTBOX_DIR, f".{message_id}.tmp")
    final = os.path.join(OUTBOX_DIR, f"{message_id}.json")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, final)

    def change(entry: dict[str, Any]) -> None:
        entry.setdefault("sent", []).append(
            {"id": message_id, "at": now(), "who": who, "text": text}
        )

    _update(user_id, change)
    return message_id


def pending_messages() -> list[dict[str, Any]]:
    """Что боту предстоит отправить. Вызывает бот."""
    if not os.path.isdir(OUTBOX_DIR):
        return []
    out: list[dict[str, Any]] = []
    for name in sorted(os.listdir(OUTBOX_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(OUTBOX_DIR, name)
        payload = _read(path, None)
        if payload:
            payload["_path"] = path
            out.append(payload)
    return out


def mark_sent(payload: dict[str, Any], error: str = "") -> None:
    """Убрать сообщение из очереди. Вызывает бот после отправки."""
    os.makedirs(SENT_DIR, exist_ok=True)
    path = payload.pop("_path", None)
    payload["delivered"] = not error
    payload["delivered_at"] = now()
    if error:
        payload["error"] = error
    _write(os.path.join(SENT_DIR, f"{payload['id']}.json"), payload)
    if path and os.path.exists(path):
        os.remove(path)


def delivery_state() -> dict[str, dict[str, Any]]:
    """Что уже отправлено: id сообщения -> сведения о доставке."""
    if not os.path.isdir(SENT_DIR):
        return {}
    state: dict[str, dict[str, Any]] = {}
    for name in os.listdir(SENT_DIR):
        if not name.endswith(".json"):
            continue
        payload = _read(os.path.join(SENT_DIR, name), None)
        if payload and payload.get("id"):
            state[payload["id"]] = payload
    return state


# ------------------------------------------------------------------ запуск


def _cli() -> None:
    """Завести оператора: python crm_store.py"""
    import getpass

    print()
    print("=" * 62)
    print("  Новый оператор CRM")
    print("=" * 62)
    print()

    login = input("  Логин (латиницей, без пробелов): ").strip()
    if not login:
        print("\n  Пусто, отмена.\n")
        return
    name = input("  Имя, как показывать в заметках: ").strip() or login
    password = getpass.getpass("  Пароль: ")
    if len(password) < 8:
        print("\n  Пароль короче восьми символов — так нельзя.\n")
        return
    if password != getpass.getpass("  Пароль ещё раз: "):
        print("\n  Пароли не совпали.\n")
        return

    add_operator(login, name, password)
    print()
    print(f"  Готово. Оператор «{name}» может входить под логином {login}.")
    print(f"  Всего операторов: {len(load_operators())}")
    print()


if __name__ == "__main__":
    _cli()

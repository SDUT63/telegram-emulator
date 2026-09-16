#!/usr/bin/env python3
"""
Хранилище данных оператора: статусы, заметки, исходящие сообщения.

Почему отдельно от анкет
------------------------
Бот держит анкеты в памяти и целиком перезаписывает survey_responses.json
после каждого ответа. Если бы CRM писала заметки туда же, бот затирал
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

import файловый_замок

HERE = os.path.dirname(os.path.abspath(__file__))

CRM_DATA = os.path.join(HERE, "crm_data.json")
OPERATORS = os.path.join(HERE, "operators.json")
OUTBOX_DIR = os.path.join(HERE, "outbox")
SENT_DIR = os.path.join(OUTBOX_DIR, "sent")
FILES_DIR = os.path.join(OUTBOX_DIR, "files")

FILE_TYPES = {".pdf", ".docx", ".doc", ".rtf", ".txt", ".odt",
              ".jpg", ".jpeg", ".png", ".heic", ".webp"}
FILE_LIMIT = 10 * 1024 * 1024

STATUSES = ["Новое", "В работе", "Закрыто"]
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
    файловый_замок.записать_надёжно(
        path, json.dumps(data, ensure_ascii=False, indent=2))


def hash_password(password: str, salt: str | None = None) -> str:
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


def add_operator(login: str, name: str, password: str, role: str = "operator") -> None:
    data = load_operators()
    data[login] = {"name": name, "password": hash_password(password), "role": role}
    save_operators(data)


def role_of(login: str) -> str:
    """Роль оператора для RBAC.

    Записи, созданные до появления ролей, поля не содержат: такой оператор
    получает обычные права оператора, а не расширенные. Повышение роли —
    осознанное действие администратора, а не следствие давности записи.
    """
    return str((load_operators().get(login) or {}).get("role") or "operator")


def verify(login: str, password: str) -> str | None:
    operator = load_operators().get(login)
    if not operator:
        return None
    if not check_password(password, operator.get("password", "")):
        return None
    return operator.get("name") or login


def _blank_case() -> dict[str, Any]:
    return {"status": STATUSES[0], "assigned": "", "notes": [], "sent": [], "calls": {}}


def _all() -> dict[str, Any]:
    return _read(CRM_DATA, {"cases": {}})


def case(user_id: str) -> dict[str, Any]:
    data = _all()
    return data["cases"].get(user_id, _blank_case())


def all_cases() -> dict[str, Any]:
    return _all()["cases"]


def _update(user_id: str, change) -> dict[str, Any]:
    """Прочитать, изменить, записать — целиком под замком.

    Без замка это классическая потеря обновления: один координатор
    ставит статус, другой в тот же момент пишет заметку, и каждый
    записывает файл, прочитанный до чужой правки. Стенд показал:
    из шестидесяти одновременных заметок доживало две.
    """
    with файловый_замок.занять(CRM_DATA):
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
    if which not in CALL_STAGES:
        raise ValueError(f"неизвестный звонок: {which}")

    def change(entry: dict[str, Any]) -> None:
        entry.setdefault("calls", {})[which] = {"at": now(), "who": who}
        entry.setdefault("notes", []).append(
            {"at": now(), "who": who,
             "text": f"Контрольный звонок через {which} дней — сделан",
             "system": True}
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


def check_file(filename: str, data: bytes) -> str:
    """Проверить вложение и вернуть безопасное короткое имя.

    Вынесено отдельно от записи: в production файл не попадает на локальный
    диск вообще — он уходит в очередь содержимым, — но проверки те же.
    """
    короткое = os.path.basename(filename or "").strip() or "файл"
    расширение = os.path.splitext(короткое)[1].lower()
    if расширение not in FILE_TYPES:
        raise ValueError(f"такие файлы не отправляем: {расширение or 'без расширения'}")
    if len(data) > FILE_LIMIT:
        raise ValueError("файл больше 10 МБ — его не примет и мессенджер")
    return короткое


def save_file(user_id: str, filename: str, data: bytes) -> dict[str, Any]:
    """Store an operator attachment under a server-generated filename."""
    короткое = check_file(filename, data)
    расширение = os.path.splitext(короткое)[1].lower()

    os.makedirs(FILES_DIR, exist_ok=True)
    path = os.path.join(FILES_DIR, uuid.uuid4().hex + расширение)
    with open(path, "wb") as fh:
        fh.write(data)
    return {"path": path, "name": короткое, "size": len(data)}


def queue_message(user_id: str, text: str, who: str,
                  files: list[dict[str, Any]] | None = None) -> str:
    """Queue an outbound message for the MAX bot."""
    os.makedirs(OUTBOX_DIR, exist_ok=True)
    message_id = uuid.uuid4().hex
    payload = {
        "id": message_id,
        "user_id": str(user_id),
        "text": text,
        "who": who,
        "files": [dict(f) for f in (files or [])],
        "created": now(),
    }
    tmp = os.path.join(OUTBOX_DIR, f".{message_id}.tmp")
    final = os.path.join(OUTBOX_DIR, f"{message_id}.json")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, final)

    def change(entry: dict[str, Any]) -> None:
        entry.setdefault("sent", []).append(
            {"id": message_id, "at": now(), "who": who, "text": text,
             "files": [{"name": f.get("name", ""), "size": f.get("size", 0)}
                       for f in (files or [])]}
        )

    _update(user_id, change)
    return message_id


def pending_messages() -> list[dict[str, Any]]:
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
    """Commit successful delivery or persist failure for retry.

    A failed MAX send must remain in outbox. The previous implementation
    moved failures to ``sent`` and thereby lost operator messages forever.
    """
    path = payload.pop("_path", None)
    if error:
        payload["attempts"] = int(payload.get("attempts", 0)) + 1
        payload["last_error"] = error[:1000]
        payload["last_attempt_at"] = now()
        if path:
            _write(path, payload)
        return

    os.makedirs(SENT_DIR, exist_ok=True)
    payload["delivered"] = True
    payload["delivered_at"] = now()
    _write(os.path.join(SENT_DIR, f"{payload['id']}.json"), payload)
    if path and os.path.exists(path):
        os.remove(path)


def delivery_state() -> dict[str, dict[str, Any]]:
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


# Слова для готового пароля: только кириллица, без «ё», без слов,
# которые путаются на слух. Шестьдесят слов по четыре — тринадцать
# миллионов сочетаний; вместе с задержкой после неудачных попыток
# перебор занимает годы.
СЛОВА = (
    "берег", "ветер", "гнездо", "дерево", "камень", "лодка", "мостик",
    "облако", "письмо", "ручей", "свеча", "тропа", "уголь", "фонарь",
    "хлеб", "чайник", "шишка", "якорь", "ягода", "заря", "искра",
    "колос", "невод", "остров", "парус", "роща", "сугроб", "терем",
    "варенье", "грядка", "дорога", "ежевика", "жаворонок", "зеркало",
    "калина", "ландыш", "малина", "невеста", "орешник", "пристань",
    "радуга", "скатерть", "тропинка", "утёс", "форточка", "холмы",
    "цапля", "черника", "шиповник", "щегол", "эхо", "юрта", "ясень",
    "амбар", "бузина", "валенки", "горница", "дубрава", "ельник",
    "жёлудь",
)
# «ё» всё же встречается в двух словах — заменяем на «е»: пароль
# набирают с чужой клавиатуры, где «ё» ищут дольше, чем печатают.
СЛОВА = tuple(с.replace("ё", "е") for с in СЛОВА)

РОЛИ = {
    "1": ("operator", "оператор — ведёт обращения, ставит статусы и заметки"),
    "2": ("supervisor", "старший — то же плюс отмена контрольного звонка"),
    "3": ("viewer", "наблюдатель — только смотрит, ничего не меняет"),
}


def _пароль_по_умолчанию() -> str:
    """Надёжный пароль, который человеку не надо придумывать.

    Четыре слова читаются вслух по телефону и набираются без ошибок,
    в отличие от «Xq7!vB2z». Словарь короткий и намеренно бытовой:
    его задача — не быть словарём для перебора, а дать человеку
    что-то, что он не постесняется продиктовать коллеге.
    """
    # Без «ё» и без похожих пар: пароль диктуют по телефону и набирают
    # с чужой клавиатуры.
    return "-".join(secrets.choice(СЛОВА) for _ in range(4))


def _cli() -> None:
    print()
    print("=" * 62)
    print("  Новый оператор CRM")
    print("=" * 62)

    уже = load_operators()
    if уже:
        print()
        print(f"  Сейчас заведено: {len(уже)}")
        for логин, запись in sorted(уже.items()):
            роль = (запись or {}).get("role") or "operator"
            print(f"    {логин:<16} {(запись or {}).get('name', ''):<24} {роль}")
    print()

    login = input("  Логин (латиницей, без пробелов): ").strip()
    if not login:
        print("\n  Пусто, отмена.\n")
        return
    if login in уже:
        ответ = input(f"  Логин «{login}» уже есть. Сменить ему пароль? (да/нет): ")
        if ответ.strip().lower() not in ("да", "y", "yes", "д"):
            print("\n  Отмена.\n")
            return

    name = input("  Имя, как показывать в заметках: ").strip() or login

    print()
    print("  Роль:")
    for ключ, (_, описание) in РОЛИ.items():
        print(f"    {ключ} — {описание}")
    выбор = input("  Номер роли [1]: ").strip() or "1"
    if выбор not in РОЛИ:
        print("\n  Такой роли нет, отмена.\n")
        return
    role = РОЛИ[выбор][0]

    # Пароль виден на экране, и это осознанно. Программа запускается
    # на том же компьютере, где лежит CRM с телефонами и адресами;
    # скрытый ввод здесь ничего не защищает, зато человек не видит
    # раскладку, опечатку и залипший Caps Lock — и узнаёт об этом
    # только когда оператор не может войти.
    предложенный = _пароль_по_умолчанию()
    print()
    print("  Пароль будет виден на экране — если рядом посторонние,")
    print("  отойдите или закройте окно после записи.")
    print(f"  Готовый надёжный пароль: {предложенный}")
    password = input("  Пароль (Enter — взять готовый): ").strip() or предложенный

    if len(password) < 8:
        print("\n  Пароль короче восьми знаков — так нельзя.\n")
        return
    if password.lower() in ("password", "пароль", "12345678", "qwertyui"):
        print("\n  Такой пароль подберут за минуту. Возьмите готовый.\n")
        return

    add_operator(login, name, password, role=role)

    print()
    print("  " + "-" * 58)
    print("  Готово. Передайте оператору эти три строки:")
    print()
    print(f"    адрес    http://127.0.0.1:5001")
    print(f"    логин    {login}")
    print(f"    пароль   {password}")
    print()
    print(f"  Роль: {role}. Всего операторов: {len(load_operators())}")
    старшие = [л for л, з in load_operators().items()
               if ((з or {}).get("role") or "operator") in ("supervisor", "admin")]
    if not старшие:
        print()
        print("  Ни одного старшего. Отменить ошибочный контрольный звонок")
        print("  будет некому — заведите кого-то с ролью 2.")
    print()


if __name__ == "__main__":
    _cli()

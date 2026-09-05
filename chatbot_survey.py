#!/usr/bin/env python3
"""
Опросник АНО «СДУТ» — логика анкеты.

Модуль ничего не знает о мессенджере: он принимает текст от человека и
возвращает текст ответа. Транспорт живёт отдельно (max_bot.py), поэтому
анкету можно проверить в командной строке без токена и без интернета:

    python chatbot_survey.py
"""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime
from typing import Any

STORAGE = "survey_responses.json"
CSV_EXPORT = "survey_responses.csv"

QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "name",
        "text": "Как вас зовут?",
        "kind": "text",
        "required": True,
    },
    {
        "id": "phone",
        "text": "Ваш номер телефона — по нему с вами свяжутся.",
        "kind": "phone",
        "required": True,
    },
    {
        "id": "email",
        "text": "Электронная почта. Если её нет, напишите «нет».",
        "kind": "email",
        "required": False,
    },
    {
        "id": "organization",
        "text": "Организация или место работы. Если пишете как родственник — напишите «нет».",
        "kind": "text",
        "required": False,
    },
    {
        "id": "topic",
        "text": "С чем связано обращение?",
        "kind": "choice",
        "options": ["Консультация", "Обучение", "Партнёрство", "Другое"],
        "required": True,
    },
    {
        "id": "message",
        "text": (
            "Опишите ситуацию своими словами.\n\n"
            "Чем конкретнее, тем быстрее подберут помощь. Например: "
            "«мама не встаёт две недели, кожа на крестце покраснела, "
            "переворачиваем сами два раза в день, функциональной кровати нет»."
        ),
        "kind": "text",
        "required": True,
    },
]

GREETING = (
    "Здравствуйте! Это служба долговременного ухода Тольятти.\n\n"
    "Задам шесть коротких вопросов — это займёт около двух минут. "
    "После этого с вами свяжется координатор.\n\n"
    "Если человеку плохо прямо сейчас — закройте анкету и звоните 103."
)

HELP = (
    "Команды:\n"
    "start — начать анкету заново\n"
    "ответы — показать, что вы уже написали\n"
    "отмена — прервать анкету"
)

DONE = (
    "Спасибо, анкета заполнена. Мы получили ваше обращение и свяжемся с вами.\n\n"
    "Если станет хуже до того, как мы позвоним, — звоните 103."
)

# Слова, по которым человек может прервать или перезапустить анкету
RESTART_WORDS = {"start", "старт", "начать", "заново", "/start"}
CANCEL_WORDS = {"отмена", "стоп", "cancel", "/cancel"}
SUMMARY_WORDS = {"ответы", "результаты", "мои ответы", "/answers"}
HELP_WORDS = {"помощь", "help", "/help"}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
NO_WORDS = {"нет", "-", "—", "no", "нету", "отсутствует"}


class Survey:
    """Хранит состояние анкеты по каждому человеку и ведёт диалог."""

    def __init__(self, storage_path: str = STORAGE) -> None:
        self.storage_path = storage_path
        self.state: dict[str, dict[str, Any]] = {}
        self.load()

    # ---------- хранение ----------

    def load(self) -> None:
        if not os.path.exists(self.storage_path):
            self.state = {}
            return
        try:
            with open(self.storage_path, encoding="utf-8") as fh:
                self.state = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # Повреждённый файл не должен ронять бота: отодвигаем его в сторону
            # и начинаем с чистого состояния, чтобы данные не потерялись молча.
            broken = self.storage_path + ".broken"
            try:
                os.replace(self.storage_path, broken)
            except OSError:
                pass
            self.state = {}

    def save(self) -> None:
        # Пишем через временный файл: если запись оборвётся, старые ответы целы.
        tmp = self.storage_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.storage_path)

    def _person(self, user_id: str) -> dict[str, Any]:
        person = self.state.get(user_id)
        if person is None:
            person = {"step": 0, "answers": {}, "started": None, "finished": None}
            self.state[user_id] = person
        person.setdefault("step", 0)
        person.setdefault("answers", {})
        return person

    # ---------- диалог ----------

    def start(self, user_id: str) -> str:
        """Начать анкету заново. Возвращает приветствие и первый вопрос."""
        self.state[user_id] = {
            "step": 0,
            "answers": {},
            "started": datetime.now().isoformat(timespec="seconds"),
            "finished": None,
        }
        self.save()
        return GREETING + "\n\n" + self._ask(0)

    def handle(self, user_id: str, text: str) -> str:
        """Обработать сообщение человека и вернуть текст ответа."""
        text = (text or "").strip()
        low = text.lower()

        if low in RESTART_WORDS:
            return self.start(user_id)
        if low in HELP_WORDS:
            return HELP
        if low in SUMMARY_WORDS:
            return self.summary(user_id)
        if low in CANCEL_WORDS:
            self.state.pop(user_id, None)
            self.save()
            return "Анкета отменена. Напишите «start», когда будете готовы."

        person = self._person(user_id)
        step = person["step"]

        if step >= len(QUESTIONS):
            return (
                "Анкета уже заполнена — координатор с вами свяжется.\n\n"
                "Напишите «ответы», чтобы посмотреть, что вы указали, "
                "или «start», чтобы заполнить заново."
            )

        if not text:
            return "Напишите, пожалуйста, ответ текстом.\n\n" + self._ask(step)

        question = QUESTIONS[step]
        ok, cleaned, problem = self._check(question, text)
        if not ok:
            return problem + "\n\n" + self._ask(step)

        person["answers"][question["id"]] = cleaned
        person["step"] = step + 1
        if person["started"] is None:
            person["started"] = datetime.now().isoformat(timespec="seconds")

        if person["step"] >= len(QUESTIONS):
            person["finished"] = datetime.now().isoformat(timespec="seconds")
            self.save()
            self.export_csv()
            return DONE + "\n\n" + self.summary(user_id)

        self.save()
        return self._ask(person["step"])

    def _ask(self, step: int) -> str:
        question = QUESTIONS[step]
        head = f"Вопрос {step + 1} из {len(QUESTIONS)}\n\n{question['text']}"
        if question["kind"] == "choice":
            options = "\n".join(
                f"{i}. {name}" for i, name in enumerate(question["options"], 1)
            )
            head += "\n\n" + options + "\n\nНапишите номер или название."
        return head

    # ---------- проверка ответов ----------

    def _check(self, question: dict[str, Any], text: str) -> tuple[bool, str, str]:
        """Возвращает (годится, что записать, что сказать при ошибке)."""
        kind = question["kind"]

        if kind == "phone":
            digits = re.sub(r"\D", "", text)
            if len(digits) < 10:
                return False, "", (
                    "Не похоже на номер телефона — в нём должно быть "
                    "не меньше десяти цифр."
                )
            return True, text, ""

        if kind == "email":
            if text.lower() in NO_WORDS:
                return True, "не указана", ""
            if not EMAIL_RE.match(text):
                return False, "", (
                    "Не похоже на адрес почты. Он выглядит так: имя@почта.ру\n"
                    "Если почты нет — напишите «нет»."
                )
            return True, text, ""

        if kind == "choice":
            options: list[str] = question["options"]
            if text.isdigit():
                number = int(text)
                if 1 <= number <= len(options):
                    return True, options[number - 1], ""
                return False, "", f"Нужен номер от 1 до {len(options)}."
            for name in options:
                if text.lower() == name.lower():
                    return True, name, ""
            return False, "", "Выберите один из вариантов — напишите его номер."

        if not question["required"] and text.lower() in NO_WORDS:
            return True, "не указано", ""

        if len(text) < 2:
            return False, "", "Слишком короткий ответ — напишите чуть подробнее."

        return True, text, ""

    # ---------- вывод ----------

    def summary(self, user_id: str) -> str:
        person = self.state.get(user_id)
        if not person or not person.get("answers"):
            return "Вы пока ничего не заполнили. Напишите «start», чтобы начать."
        lines = ["Ваши ответы:", ""]
        for question in QUESTIONS:
            value = person["answers"].get(question["id"])
            if value:
                lines.append(f"• {question['text'].splitlines()[0]}")
                lines.append(f"  {value}")
        return "\n".join(lines)

    def export_csv(self, path: str = CSV_EXPORT) -> str:
        """Выгрузить все ответы в таблицу для Excel."""
        header = ["Кто ответил", "Начато", "Завершено"] + [
            q["text"].splitlines()[0] for q in QUESTIONS
        ]
        # utf-8-sig — чтобы Excel открыл кириллицу без «кракозябр»
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(header)
            for user_id, person in self.state.items():
                answers = person.get("answers", {})
                writer.writerow(
                    [user_id, person.get("started", ""), person.get("finished", "")]
                    + [answers.get(q["id"], "") for q in QUESTIONS]
                )
        return path

    def stats(self) -> tuple[int, int]:
        """Сколько человек начали и сколько дошли до конца."""
        started = len(self.state)
        finished = sum(1 for p in self.state.values() if p.get("finished"))
        return started, finished


def _demo() -> None:
    """Проверка анкеты без мессенджера: прогоняем ответы по очереди."""
    survey = Survey(storage_path="demo_responses.json")
    user = "проверка"

    print(survey.start(user))
    print("-" * 60)

    for reply in [
        "Иван Петров",
        "89171234567",
        "ivan@example.ru",
        "нет",
        "1",
        "Мама не встаёт две недели, кожа на крестце покраснела.",
    ]:
        print(f"> {reply}\n")
        print(survey.handle(user, reply))
        print("-" * 60)

    started, finished = survey.stats()
    print(f"\nНачали: {started}, дошли до конца: {finished}")
    print(f"Таблица: {survey.export_csv('demo_responses.csv')}")


if __name__ == "__main__":
    _demo()

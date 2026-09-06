#!/usr/bin/env python3
"""
Опросник АНО «СДУТ» — логика анкеты.

Модуль ничего не знает о мессенджере: принимает текст от человека и
возвращает текст ответа. Транспорт живёт отдельно (max_bot.py), поэтому
анкету можно прогнать в командной строке без токена и без интернета:

    python chatbot_survey.py

Сами вопросы вынесены в survey_questions.py — их можно править, не трогая
эту логику.
"""

from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime
from typing import Any

from survey_questions import CHECKPOINT_ID, QUESTIONS, STOP_OPTION

STORAGE = "survey_responses.json"
CSV_EXPORT = "survey_responses.csv"

GREETING = (
    "Здравствуйте! Это служба долговременного ухода Тольятти.\n\n"
    "Задам несколько вопросов, чтобы координатор пришёл к вам "
    "подготовленным. Почти везде нужно выбрать номер ответа.\n\n"
    "Если человеку плохо прямо сейчас — закройте анкету и звоните 103."
)

HELP = (
    "Что можно написать в любой момент:\n\n"
    "далее — пропустить вопрос, если он не обязательный\n"
    "назад — вернуться к предыдущему вопросу\n"
    "ответы — показать, что уже заполнено\n"
    "заново — начать анкету сначала\n"
    "отмена — прервать\n\n"
    "Анкета сохраняется. Можно закрыть и вернуться позже — "
    "продолжим с того же места."
)

DONE_FULL = (
    "Спасибо, анкета заполнена. Координатор свяжется с вами.\n\n"
    "Если состояние ухудшится до того, как мы позвоним, — звоните 103."
)

DONE_SHORT = (
    "Спасибо, записали. Координатор свяжется с вами по указанному телефону.\n\n"
    "Если захотите дополнить — напишите «заново», анкета откроется снова.\n"
    "Если станет хуже — звоните 103."
)

RESTART_WORDS = {"заново", "start", "старт", "начать", "/start", "продолжить"}
CANCEL_WORDS = {"отмена", "стоп", "cancel", "/cancel"}
SUMMARY_WORDS = {"ответы", "результаты", "мои ответы", "/answers"}
HELP_WORDS = {"помощь", "help", "/help", "?"}
SKIP_WORDS = {"далее", "пропустить", "skip", "-"}
BACK_WORDS = {"назад", "back"}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
NO_WORDS = {"нет", "-", "—", "no", "нету", "отсутствует", "не знаю"}


class Survey:
    """Ведёт анкету по каждому человеку и хранит состояние между запусками."""

    def __init__(self, storage_path: str = STORAGE) -> None:
        self.storage_path = storage_path
        self.state: dict[str, dict[str, Any]] = {}
        self.load()

    # ------------------------------------------------------------ хранение

    def load(self) -> None:
        if not os.path.exists(self.storage_path):
            self.state = {}
            return
        try:
            with open(self.storage_path, encoding="utf-8") as fh:
                self.state = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # Повреждённый файл отодвигаем, чтобы данные не потерялись молча
            try:
                os.replace(self.storage_path, self.storage_path + ".broken")
            except OSError:
                pass
            self.state = {}

    def save(self) -> None:
        tmp = self.storage_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.storage_path)

    def _person(self, user_id: str) -> dict[str, Any]:
        person = self.state.get(user_id)
        if person is None:
            person = self._blank()
            self.state[user_id] = person
        for key, value in self._blank().items():
            person.setdefault(key, value)
        return person

    @staticmethod
    def _blank() -> dict[str, Any]:
        return {
            "step": 0,
            "answers": {},
            "alerts": [],
            "history": [],
            "started": None,
            "finished": None,
            "shown_sections": [],
            "total_seen": 0,
        }

    # ------------------------------------------------------- ход по вопросам

    @staticmethod
    def _asked(index: int, answers: dict[str, str]) -> bool:
        """Задаём ли этот вопрос при текущих ответах."""
        when = QUESTIONS[index].get("when")
        return True if when is None else bool(when(answers))

    def _next(self, index: int, answers: dict[str, str]) -> int:
        while index < len(QUESTIONS) and not self._asked(index, answers):
            index += 1
        return index

    def _progress(self, person: dict[str, Any], index: int) -> str:
        """Номер вопроса и сколько всего при нынешних ответах.

        Ветвление может как добавлять вопросы, так и убирать, поэтому
        итог держим неубывающим: счётчик, который скачет назад, выглядит
        как ошибка.
        """
        answers = person["answers"]
        total = sum(
            1 for q in QUESTIONS if q.get("when") is None or q["when"](answers)
        )
        total = max(total, person.get("total_seen", 0))
        person["total_seen"] = total
        seen = sum(
            1
            for i, q in enumerate(QUESTIONS)
            if i <= index and (q.get("when") is None or q["when"](answers))
        )
        return f"Вопрос {seen} из {total}"

    # -------------------------------------------------------------- диалог

    def start(self, user_id: str) -> str:
        self.state[user_id] = self._blank()
        self.state[user_id]["started"] = datetime.now().isoformat(timespec="seconds")
        self.save()
        return GREETING + "\n\n" + self._ask(user_id, 0)

    def handle(self, user_id: str, text: str) -> str:
        text = (text or "").strip()
        low = text.lower()

        if low in RESTART_WORDS and low != "продолжить":
            return self.start(user_id)
        if low in HELP_WORDS:
            return HELP
        if low in SUMMARY_WORDS:
            return self.summary(user_id)
        if low in CANCEL_WORDS:
            self.state.pop(user_id, None)
            self.save()
            return "Анкета отменена. Напишите «заново», когда будете готовы."

        person = self._person(user_id)
        answers = person["answers"]
        step = self._next(person["step"], answers)

        if step >= len(QUESTIONS) or person["finished"]:
            return (
                "Анкета уже заполнена — координатор с вами свяжется.\n\n"
                "«ответы» — посмотреть заполненное, «заново» — пройти снова."
            )

        # «продолжить» после предупреждения просто повторяет вопрос
        if low == "продолжить":
            return self._ask(user_id, step)

        if low in BACK_WORDS:
            return self._go_back(user_id)

        question = QUESTIONS[step]

        if low in SKIP_WORDS:
            if question.get("required", True):
                return "Этот вопрос пропустить нельзя.\n\n" + self._ask(user_id, step)
            return self._accept(user_id, step, "не указано")

        if not text:
            return "Напишите ответ текстом.\n\n" + self._ask(user_id, step)

        ok, cleaned, problem = self._check(question, text)
        if not ok:
            return problem + "\n\n" + self._ask(user_id, step)

        return self._accept(user_id, step, cleaned)

    @staticmethod
    def _alert_rules(question: dict[str, Any]) -> list[dict[str, Any]]:
        """Правила предупреждений: одно поле alert или список alerts."""
        if question.get("alerts"):
            return question["alerts"]
        return [question["alert"]] if question.get("alert") else []

    @staticmethod
    def _alert_fires(rule: dict[str, Any], value: str) -> bool:
        """Сработало ли правило на данном ответе."""
        options = rule.get("options")
        if options and any(opt.lower() in value.lower() for opt in options):
            return True

        # Правило по количеству: человек отметил слишком много признаков
        # сразу. «Ничего из перечисленного» при подсчёте не считается.
        least = rule.get("min_selected")
        if least:
            chosen = [
                part.strip()
                for part in value.split(";")
                if part.strip() and "ничего" not in part.lower()
            ]
            if len(chosen) >= least:
                return True

        return False

    def _accept(self, user_id: str, step: int, value: str) -> str:
        person = self._person(user_id)
        question = QUESTIONS[step]
        person["answers"][question["id"]] = value
        person["history"].append(step)
        if person["started"] is None:
            person["started"] = datetime.now().isoformat(timespec="seconds")

        prefix = ""
        for rule in self._alert_rules(question):
            if not self._alert_fires(rule, value):
                continue
            prefix += rule["text"] + "\n\n" + "—" * 20 + "\n\n"
            # В сводку для координатора идёт человеческая подпись, а не
            # внутреннее имя поля
            note = f"{rule.get('label', question['text'])}: {value}"
            if note not in person["alerts"]:
                person["alerts"].append(note)

        # Человек решил не проходить подробную часть
        if question["id"] == CHECKPOINT_ID and value == STOP_OPTION:
            person["step"] = len(QUESTIONS)
            person["finished"] = datetime.now().isoformat(timespec="seconds")
            self.save()
            self.export_csv()
            return prefix + DONE_SHORT

        person["step"] = self._next(step + 1, person["answers"])
        if person["step"] >= len(QUESTIONS):
            person["finished"] = datetime.now().isoformat(timespec="seconds")
            self.save()
            self.export_csv()
            return prefix + DONE_FULL + "\n\n" + self.summary(user_id)

        self.save()
        return prefix + self._ask(user_id, person["step"])

    def _go_back(self, user_id: str) -> str:
        person = self._person(user_id)
        if not person["history"]:
            return "Это первый вопрос, возвращаться некуда.\n\n" + self._ask(user_id, 0)
        previous = person["history"].pop()
        person["answers"].pop(QUESTIONS[previous]["id"], None)
        person["step"] = previous
        self.save()
        return "Вернулись назад.\n\n" + self._ask(user_id, previous)

    def _ask(self, user_id: str, step: int) -> str:
        person = self._person(user_id)
        question = QUESTIONS[step]
        parts: list[str] = []

        section = question.get("section")
        if section and section not in person["shown_sections"]:
            person["shown_sections"].append(section)
            parts.append(f"— {section} —")
            self.save()

        parts.append(self._progress(person, step))
        parts.append("")
        parts.append(question["text"])

        if question["kind"] == "choice":
            parts.append("")
            for number, name in enumerate(question["options"], 1):
                parts.append(f"{number}. {name}")
            parts.append("")
            if question.get("multi"):
                parts.append("Напишите номера через запятую, например: 1, 3")
            else:
                parts.append("Напишите номер ответа.")

        if not question.get("required", True):
            parts.append("Можно пропустить: напишите «далее».")

        return "\n".join(parts)

    # ------------------------------------------------------------ проверка

    def _check(self, question: dict[str, Any], text: str) -> tuple[bool, str, str]:
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
                    "Не похоже на адрес почты. Он выглядит так: имя@почта.ру"
                )
            return True, text, ""

        if kind == "choice":
            options: list[str] = question["options"]
            if question.get("multi"):
                return self._check_multi(options, text)
            return self._check_one(options, text)

        if not question.get("required", True) and text.lower() in NO_WORDS:
            return True, "не указано", ""
        if len(text) < 2:
            return False, "", "Слишком короткий ответ — напишите чуть подробнее."
        return True, text, ""

    @staticmethod
    def _check_one(options: list[str], text: str) -> tuple[bool, str, str]:
        if text.isdigit():
            number = int(text)
            if 1 <= number <= len(options):
                return True, options[number - 1], ""
            return False, "", f"Нужен номер от 1 до {len(options)}."
        for name in options:
            if text.lower() == name.lower():
                return True, name, ""
        return False, "", f"Напишите номер ответа — от 1 до {len(options)}."

    @staticmethod
    def _check_multi(options: list[str], text: str) -> tuple[bool, str, str]:
        raw = [p.strip() for p in re.split(r"[,\s;]+", text) if p.strip()]
        chosen: list[str] = []
        for part in raw:
            if part.isdigit():
                number = int(part)
                if not 1 <= number <= len(options):
                    return False, "", f"Номер {number} не подходит — есть только 1–{len(options)}."
                name = options[number - 1]
            else:
                match = [o for o in options if o.lower() == part.lower()]
                if not match:
                    return False, "", (
                        f"Не понял «{part}». Напишите номера через запятую, "
                        "например: 1, 3"
                    )
                name = match[0]
            if name not in chosen:
                chosen.append(name)
        if not chosen:
            return False, "", "Напишите хотя бы один номер."
        return True, "; ".join(chosen), ""

    # -------------------------------------------------------------- вывод

    def summary(self, user_id: str) -> str:
        person = self.state.get(user_id)
        if not person or not person.get("answers"):
            return "Вы пока ничего не заполнили. Напишите «заново», чтобы начать."
        lines = ["Что записано:", ""]
        for question in QUESTIONS:
            value = person["answers"].get(question["id"])
            if value:
                lines.append(f"• {question['text'].splitlines()[0]}")
                lines.append(f"  {value}")
        if person.get("alerts"):
            lines.append("")
            lines.append("Отмечено как требующее внимания:")
            for note in person["alerts"]:
                lines.append(f"  — {note}")
        return "\n".join(lines)

    def brief(self, user_id: str) -> str:
        """Короткая сводка для координатора — без лишних слов."""
        person = self.state.get(user_id)
        if not person:
            return ""
        a = person["answers"]
        bits = [
            a.get("name", "без имени"),
            a.get("phone", "телефон не указан"),
            a.get("mobility") or a.get("need", ""),
        ]
        line = " · ".join(b for b in bits if b)
        if person.get("alerts"):
            line += "  ⚠ " + "; ".join(n.split(": ", 1)[-1] for n in person["alerts"])
        return line

    def export_csv(self, path: str = CSV_EXPORT) -> str:
        header = ["Кто ответил", "Начато", "Завершено", "Требует внимания"] + [
            q["text"].splitlines()[0] for q in QUESTIONS
        ]
        # utf-8-sig — чтобы Excel открыл кириллицу без «кракозябр»
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(header)
            for user_id, person in self.state.items():
                answers = person.get("answers", {})
                writer.writerow(
                    [
                        user_id,
                        person.get("started", ""),
                        person.get("finished", ""),
                        "; ".join(person.get("alerts", [])),
                    ]
                    + [answers.get(q["id"], "") for q in QUESTIONS]
                )
        return path

    def stats(self) -> tuple[int, int]:
        started = len(self.state)
        finished = sum(1 for p in self.state.values() if p.get("finished"))
        return started, finished


def _demo() -> None:
    """Прогон анкеты без мессенджера. Реальные ответы не затрагиваются."""
    survey = Survey(storage_path="demo_responses.json")
    user = "проверка"

    print(survey.start(user))
    print("=" * 60)

    # Тяжёлый лежачий пациент — проходим подробную часть целиком
    replies = [
        "2",              # о близком
        "Анна",
        "89171234567",
        "4",              # 85 и старше
        "2, 8",           # боль + рана на коже
        "6",              # регулярная помощь на дому
        "1",              # продолжить
        "5",              # не встаёт с постели
        "3",              # не переворачивается сам
        "2",              # покраснение -> предупреждение
        "3",              # кормить с ложки
        "4",              # поперхивается -> предупреждение
        "3", "4", "3",    # гигиена, туалет, одевание
        "2", "2", "3",    # речь, ориентация, одна нельзя
        "3",              # боль постоянная
        "1, 3",           # боль мешает движению и сну
        "2",              # мочевой катетер
        "3",              # лекарства даём мы
        "1",              # родственник живёт вместе
        "4",              # круглосуточно
        "4",              # уже не справляемся -> предупреждение
        "1",              # ничего из оборудования
        "2",              # инвалидность
        "Живём на пятом этаже без лифта.",
    ]

    for reply in replies:
        print(f"\n>>> {reply}\n")
        print(survey.handle(user, reply))
        print("=" * 60)

    print("\nСВОДКА ДЛЯ КООРДИНАТОРА:")
    print(" ", survey.brief(user))
    started, finished = survey.stats()
    print(f"\nНачали: {started}, дошли до конца: {finished}")
    print(f"Таблица: {survey.export_csv('demo_responses.csv')}")


if __name__ == "__main__":
    _demo()

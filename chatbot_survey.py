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

import consent_forms
import fallback
from survey_questions import CHECKPOINT_ID, QUESTIONS, STOP_OPTION

МЕСЯЦЫ = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


def _по_русски(момент: str) -> str:
    try:
        d = datetime.fromisoformat(момент)
    except (TypeError, ValueError):
        return момент or "ранее"
    return f"{d.day} {МЕСЯЦЫ[d.month - 1]} {d.year} года"

STORAGE = "survey_responses.json"
CSV_EXPORT = "survey_responses.csv"

GREETING = (
    "Здравствуйте! Это служба долговременного ухода, Тольятти.\n\n"
    "Мы помогаем семьям, которые ухаживают дома за пожилым "
    "или тяжелобольным человеком.\n\n"
    "Задам несколько вопросов — это минут пять — и передам всё "
    "координатору, живому человеку, который вам перезвонит.\n\n"
    "Если человеку плохо прямо сейчас — звоните 103. Анкета подождёт."
)

HELP = (
    "Отвечать проще кнопками. Если их не видно — напишите номер ответа.\n\n"
    "Слова, которые понимаю в любой момент:\n\n"
    "назад — вернуться к предыдущему вопросу\n"
    "далее — пропустить необязательный вопрос\n"
    "ответы — показать, что уже заполнено\n"
    "спросить — что можно узнать об уходе, документах и помощи\n"
    "заново — начать подробную часть анкеты заново, не повторяя имя, телефон и адрес\n"
    "удалить — стереть всё, что вы рассказали\n"
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
    "Если захотите дополнить — нажмите «Продолжить анкету»: имя, телефон и адрес уже сохранены.\n"
    "Если станет хуже — звоните 103."
)

CONSENT_VERSION = "1.0"
ORG_FULL = "АНО «Система долговременного ухода г. Тольятти»"
ORG_OGRN = "1266300009766"
ORG_INN = "6320093220"
ORG_ADDRESS = "445044, Самарская область, г. Тольятти, ул. Ворошилова, д. 19"
CONSENT_SHORT = (
    "Сначала одно короткое дело.\n\n"
    "Вопросы будут о здоровье — как человек ходит, ест, спит, что "
    "беспокоит. Такие сведения закон разрешает записывать только "
    "с вашего согласия.\n\n"
    "Коротко, о чём речь:\n\n"
    "• записываем имя, телефон, адрес и ваши ответы;\n"
    "• читают их только координаторы службы;\n"
    "• врачам передаём лишь с вашего отдельного разрешения;\n"
    "• напишете «удалить» — сотрём всё и подтвердим.\n\n"
    "Полный текст — по кнопке ниже, прямо здесь в чате."
)
CONSENT_FULL = (
    "Согласие на обработку персональных данных\n\n"
    "КТО СОБИРАЕТ\n"
    f"{ORG_FULL}\n"
    f"ОГРН {ORG_OGRN}, ИНН {ORG_INN}\n"
    f"{ORG_ADDRESS}\n\n"
    "ЧТО ЗАПИСЫВАЕМ\n"
    "Имя, по которому к вам обращаться, и телефон для связи. Имя "
    "человека, которому нужна помощь, и адрес, куда приезжать. Ваши "
    "ответы о его состоянии: как он ходит, ест, спит, что беспокоит, "
    "кто рядом. Дату обращения.\n\n"
    "Ответы о состоянии здоровья — особая категория данных, часть 1 "
    "статьи 10 закона 152-ФЗ. Без вашего согласия мы их не записываем.\n\n"
    "ЗАЧЕМ\n"
    "Чтобы координатор перезвонил, понял, что нужно, и подобрал помощь: "
    "уход на дому, обучение родных, оборудование, оформление документов.\n\n"
    "ЧТО МЫ С НИМИ ДЕЛАЕМ\n"
    "Записываем, храним, показываем координаторам службы и готовим "
    "по ним сводку к звонку. Ни рекламы, ни продажи, ни передачи "
    "кому-то ещё.\n\n"
    "КТО ВИДИТ\n"
    "Только сотрудники службы, которые ведут ваше обращение. "
    "В медицинскую организацию — лишь с вашего отдельного согласия, "
    "и его вы подписываете отдельно.\n\n"
    "СКОЛЬКО ХРАНИМ\n"
    "Пока вы не попросите удалить. Срока в годах нет — есть ваше "
    "слово.\n\n"
    "КАК ОТОЗВАТЬ\n"
    "Напишите боту «удалить» — в любой момент, без объяснения причин. "
    "Согласие прекращается, записи стираются, мы подтверждаем "
    "сообщением.\n\n"
    "КАК ВЫ ЕГО ДАЁТЕ\n"
    "Нажатием кнопки «Согласен, продолжим». Мы записываем дату, время "
    "и редакцию этого текста. При встрече координатор даст ту же форму "
    "на бумаге — подписать её нужно будет один раз."
)
CONSENT_GIVEN_AT = "Вы дали согласие {когда}. Отозвать — напишите «удалить»."
CONSENT_WAIT = (
    "Пока кнопка не нажата, я ничего не записываю.\n\n"
    "«Согласен» — начнём анкету.\n"
    "«Полностью» — пришлю полный текст согласия сюда же.\n"
    "«Нет» — закроем, и я больше не побеспокою."
)
CONSENT_NO = (
    "Понимаем, и настаивать не будем.\n\n"
    "Без согласия заполнить анкету нельзя — так требует закон. "
    "Но помощь от этого не закрывается: напишите нам в сообществах "
    "службы, там можно спросить что угодно, ничего о себе "
    "не сообщая.\n\n"
    "Передумаете — напишите «начать», и мы продолжим."
)
CONSENT_YES = "Спасибо, согласие записали. Теперь к делу."
ERASED = (
    "Готово, всё удалено: и ответы, и контакты.\n\n"
    "Если понадобится помощь — просто напишите сюда, начнём заново."
)
RESUMED = (
    "Анкета не закончена — продолжаем с того места, где остановились.\n\n"
    "Если хотите начать сначала, напишите «заново»."
)
ALREADY_DONE = (
    "Анкета уже заполнена — координатор с вами свяжется.\n\n"
    "«ответы» — посмотреть заполненное, «заново» — пройти подробную часть ещё раз."
)
ANSWERED = (
    "Записал, передам координатору — он свяжется с вами."
)
SECTIONS: list[str] = []
for _question in QUESTIONS:
    SECTIONS.append(_question.get("section") or (SECTIONS[-1] if SECTIONS else ""))


class Survey:
    """Ведёт анкету по каждому человеку и хранит состояние между запусками."""

    def __init__(self, storage_path: str = STORAGE, *, list_options: bool = True) -> None:
        self.storage_path = storage_path
        self.list_options = list_options
        self.state: dict[str, dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.storage_path):
            self.state = {}
            return
        try:
            with open(self.storage_path, encoding="utf-8") as fh:
                self.state = json.load(fh)
        except (json.JSONDecodeError, OSError):
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
            "step": 0, "answers": {}, "alerts": [], "history": [], "started": None,
            "finished": None, "total_seen": 0, "consent": None, "pending": None,
            "reading": False, "messages": [], "missed": 0, "acked": None,
        }

    @staticmethod
    def _asked(index: int, answers: dict[str, str]) -> bool:
        when = QUESTIONS[index].get("when")
        return True if when is None else bool(when(answers))

    def _next(self, index: int, answers: dict[str, str]) -> int:
        while index < len(QUESTIONS) and not self._asked(index, answers): index += 1
        return index

    def _progress(self, person: dict[str, Any], index: int) -> str:
        answers = person["answers"]
        total = sum(1 for q in QUESTIONS if q.get("when") is None or q["when"](answers))
        total = max(total, person.get("total_seen", 0)); person["total_seen"] = total
        seen = sum(1 for i, q in enumerate(QUESTIONS) if i <= index and (q.get("when") is None or q["when"](answers)))
        return f"Вопрос {seen} из {total}"

    def stage(self, user_id: str) -> str:
        person = self.state.get(user_id)
        if not person: return "consent"
        if not (person.get("consent") or {}).get("at"): return "consent"
        if person.get("finished"): return "done"
        return "survey"

    def consented(self, user_id: str) -> dict[str, Any] | None:
        mark = (self.state.get(user_id) or {}).get("consent") or {}
        return mark if mark.get("at") else None

    def grant_consent(self, user_id: str) -> str:
        person = self._person(user_id)
        if not (person.get("consent") or {}).get("at"):
            person["consent"] = {"at": datetime.now().isoformat(timespec="seconds"), "version": CONSENT_VERSION}
        if person["started"] is None: person["started"] = person["consent"]["at"]
        self.save()
        return CONSENT_YES + "\n\n" + self._ask(user_id, self._next(0, person["answers"]))

    def refuse_consent(self, user_id: str) -> str:
        person = self._person(user_id)
        person["consent"] = {"refused": datetime.now().isoformat(timespec="seconds"), "version": CONSENT_VERSION}
        person["answers"] = {}; self.save(); return CONSENT_NO

    def consent_text(self, user_id: str) -> str:
        chunks = [CONSENT_FULL]
        signs = consent_forms.signs((self.state.get(user_id) or {}).get("answers") or {})
        if signs: chunks.append("Бумажную форму подписывает: " + signs + ".")
        given = self.consented(user_id)
        if given: chunks.append(CONSENT_GIVEN_AT.format(когда=_по_русски(given["at"])))
        return "\n\n".join(chunks)

    MESSAGES_LIMIT = 200

    def note_message(self, user_id: str, text: str, files: list | None = None) -> None:
        text = (text or "").strip()
        if not text and not files: return
        person = self.state.get(user_id)
        if person is None: return
        запись: dict[str, Any] = {"at": datetime.now().isoformat(timespec="seconds"), "text": text[:2000]}
        if files: запись["files"] = [dict(f) for f in files][:10]
        журнал = person.setdefault("messages", []); журнал.append(запись); del журнал[:-self.MESSAGES_LIMIT]; self.save()

    def messages(self, user_id: str) -> list[dict[str, Any]]:
        return list((self.state.get(user_id) or {}).get("messages") or [])

    def _надо_подтвердить(self, user_id: str, low: str) -> bool:
        person = self.state.get(user_id)
        if person is None: return True
        зовут = any(с in low for с in ЗОВУТ_ЧЕЛОВЕКА); если_было = person.get("acked"); свежо = False
        if если_было and not зовут:
            try: свежо = (datetime.now() - datetime.fromisoformat(если_было)).total_seconds() < СЕССИЯ_ЧАСОВ * 3600
            except (TypeError, ValueError): свежо = False
        if свежо: return False
        person["acked"] = datetime.now().isoformat(timespec="seconds"); self.save(); return True

    def misses(self, user_id: str) -> int: return int((self.state.get(user_id) or {}).get("missed") or 0)
    def miss(self, user_id: str) -> int:
        person = self.state.get(user_id)
        if person is None: return 1
        person["missed"] = self.misses(user_id) + 1; self.save(); return person["missed"]
    def understood(self, user_id: str) -> None:
        person = self.state.get(user_id)
        if person and person.get("missed"): person["missed"] = 0; self.save()

    def erase(self, user_id: str) -> str:
        self.state.pop(user_id, None); self.save(); self.export_csv(); return ERASED

    def start(self, user_id: str) -> str:
        self.state[user_id] = self._blank(); self.save(); return GREETING + "\n\n" + CONSENT_SHORT

    def restart_after_consent(self, user_id: str) -> str:
        """Повторить анкету состояния, сохранив первичные контактные данные.

        «Заново» после короткой части не должно превращаться в повторный
        сбор имени, телефона и адреса. Сохраняем всё до контрольного вопроса
        включительно, а подробную часть сбрасываем. Согласие, переписка и
        технические поля обращения тоже остаются.
        """
        person = self._person(user_id)
        keep_ids = {q["id"] for q in QUESTIONS[:next(i for i, q in enumerate(QUESTIONS) if q["id"] == CHECKPOINT_ID) + 1]}
        keep_ids.update({"district", "lift"})
        answers = {key: value for key, value in person.get("answers", {}).items() if key in keep_ids}
        answers[CHECKPOINT_ID] = "Продолжить"
        person["answers"] = answers
        person["step"] = self._next(next(i for i, q in enumerate(QUESTIONS) if q["id"] == CHECKPOINT_ID) + 1, answers)
        person["history"] = [i for i in person.get("history", []) if i < person["step"]]
        person["pending"] = None
        person["alerts"] = []
        person["finished"] = None
        person["total_seen"] = 0
        person["reading"] = False
        self.save()
        return "Основные данные уже сохранены — повторно вводить имя, телефон и адрес не нужно.\n\n" + self._ask(user_id, person["step"])

    def handle(self, user_id: str, text: str) -> str:
        text = (text or "").strip(); low = text.lower()
        if low in HELP_WORDS: return HELP
        if low in ERASE_WORDS: return self.erase(user_id)
        if low in ANSWERS_WORDS: return self.summary(user_id)
        if low in CANCEL_WORDS:
            self.state.pop(user_id, None); self.save(); return "Анкета отменена. Напишите «заново», когда будете готовы."
        person = self._person(user_id); answers = person["answers"]; step = self._next(person["step"], answers)
        if step >= len(QUESTIONS) or person["finished"]:
            if low in BEGIN_WORDS: return self.start(user_id)
            self.note_message(user_id, text)
            if self._надо_подтвердить(user_id, low): return ANSWERED
            return ""
        if low in BEGIN_WORDS: return RESUMED + "\n\n" + self._ask(user_id, step)
        if low in CONTINUE_WORDS: return self._ask(user_id, step)
        if low in BACK_WORDS: return self._go_back(user_id)
        question = QUESTIONS[step]
        if low in SKIP_WORDS:
            if question.get("required", True): return "Этот вопрос пропустить нельзя.\n\n" + self._ask(user_id, step)
            return self._accept(user_id, step, "не указано")
        if not text: return "Напишите ответ текстом.\n\n" + self._ask(user_id, step)
        ok, cleaned, problem = self._check(question, text)
        if not ok:
            if self._looks_like_speech(text): self.note_message(user_id, text)
            attempt = self.miss(user_id); hint = ""
            if attempt > 1 or question.get("options"):
                hint = fallback.фраза(attempt, fallback.АНКЕТА, пропуск=not question.get("required", True)) + "\n\n"
            return problem + "\n\n" + hint + self._ask(user_id, step)
        return self._accept(user_id, step, cleaned)

    @staticmethod
    def _looks_like_speech(text: str) -> bool:
        text = (text or "").strip()
        if len(re.sub(r"\D", "", text)) >= 10: return True
        return len(text) >= 12 and " " in text

    @staticmethod
    def _alert_rules(question: dict[str, Any]) -> list[dict[str, Any]]:
        if question.get("alerts"): return question["alerts"]
        return [question["alert"]] if question.get("alert") else []

    @staticmethod
    def _alert_fires(rule: dict[str, Any], value: str) -> bool:
        options = rule.get("options")
        if options and any(opt.lower() in value.lower() for opt in options): return True
        least = rule.get("min_selected")
        if least:
            chosen = [part.strip() for part in value.split(";") if part.strip() and "ничего" not in part.lower()]
            if len(chosen) >= least: return True
        return False

    def _accept(self, user_id: str, step: int, value: str) -> str:
        person = self._person(user_id); self.understood(user_id); question = QUESTIONS[step]
        person["answers"][question["id"]] = value; person["history"].append(step); person["pending"] = None
        derive = question.get("derive")
        if derive: person["answers"].update(derive(value))
        if person["started"] is None: person["started"] = datetime.now().isoformat(timespec="seconds")
        prefix = ""
        for rule in self._alert_rules(question):
            if not self._alert_fires(rule, value): continue
            prefix += rule["text"] + "\n\n" + "—" * 20 + "\n\n"
            note = f"{rule.get('label', question['text'])}: {value}"
            if note not in person["alerts"]: person["alerts"].append(note)
        if question["id"] == CHECKPOINT_ID and value == STOP_OPTION:
            person["step"] = len(QUESTIONS); person["finished"] = datetime.now().isoformat(timespec="seconds"); self.save(); self.export_csv(); return prefix + DONE_SHORT
        person["reading"] = False
        after = question.get("after")
        if after:
            said = after(person["answers"])
            if said: person["reading"] = True; prefix += said + "\n\n" + "—" * 20 + "\n\n"
        person["step"] = self._next(step + 1, person["answers"])
        if person["step"] >= len(QUESTIONS):
            person["finished"] = datetime.now().isoformat(timespec="seconds"); self.save(); self.export_csv(); return prefix + DONE_FULL + "\n\n" + self.summary(user_id)
        self.save(); return prefix + self._ask(user_id, person["step"])

    def _go_back(self, user_id: str) -> str:
        person = self._person(user_id)
        if not person["history"]: return "Это первый вопрос, возвращаться некуда.\n\n" + self._ask(user_id, 0)
        previous = person["history"].pop(); person["answers"].pop(QUESTIONS[previous]["id"], None); person["step"] = previous; self.save(); return "Вернулись назад.\n\n" + self._ask(user_id, previous)

    def _ask(self, user_id: str, step: int, *, hint: bool = True) -> str:
        person = self._person(user_id); question = QUESTIONS[step]; where = self._progress(person, step)
        if SECTIONS[step]: where = f"{SECTIONS[step]} · {where.lower()}"
        parts = [where, "", question["text"]]
        if question["kind"] == "choice" and self.list_options:
            parts.append("")
            for number, name in enumerate(question["options"], 1): parts.append(f"{number}. {name}")
            parts.append(""); parts.append("Напишите номера через запятую, например: 1, 3" if question.get("multi") else "Напишите номер ответа.")
            if not question.get("required", True): parts.append("Можно пропустить: напишите «далее».")
        elif hint and question.get("multi"): parts.append("\nОтметьте всё, что подходит.")
        return "\n".join(parts)

    def question_text(self, user_id: str, *, hint: bool = True) -> str:
        spot = self.current(user_id); return "" if spot is None else self._ask(user_id, spot[0], hint=hint)

    def current(self, user_id: str) -> tuple[int, dict[str, Any]] | None:
        person = self.state.get(user_id)
        if not person or person.get("finished") or not (person.get("consent") or {}).get("at"): return None
        step = self._next(person.get("step", 0), person.get("answers", {}))
        return None if step >= len(QUESTIONS) else (step, QUESTIONS[step])

    @staticmethod
    def _is_none_option(name: str) -> bool: return name.lower().startswith("ничего")
    @classmethod
    def none_index(cls, question: dict[str, Any]) -> int | None:
        for index, name in enumerate(question.get("options", [])):
            if cls._is_none_option(name): return index
        return None
    def picked(self, user_id: str, step: int) -> list[int]:
        pending = (self.state.get(user_id) or {}).get("pending") or {}
        return list(pending.get("picked", [])) if pending.get("step") == step else []
    def reading(self, user_id: str) -> bool: return bool((self.state.get(user_id) or {}).get("reading"))
    def picked_names(self, user_id: str, step: int) -> list[str]:
        spot = self.current(user_id)
        if not spot or spot[0] != step: return []
        options = spot[1].get("options", []); return [options[i] for i in self.picked(user_id, step) if i < len(options)]
    def toggle(self, user_id: str, step: int, index: int) -> bool:
        spot = self.current(user_id)
        if not spot or spot[0] != step: return False
        options = spot[1].get("options", [])
        if not 0 <= index < len(options): return False
        person = self._person(user_id); picked = self.picked(user_id, step)
        if index in picked: picked.remove(index)
        elif self._is_none_option(options[index]): picked = [index]
        else:
            picked = [i for i in picked if not self._is_none_option(options[i])]; picked.append(index)
        person["pending"] = {"step": step, "picked": sorted(picked)}; self.save(); return True
    def answer_by_numbers(self, user_id: str, numbers: list[int]) -> str: return self.handle(user_id, ", ".join(str(n) for n in numbers))

    def _check(self, question: dict[str, Any], text: str) -> tuple[bool, str, str]:
        kind = question["kind"]
        if kind == "phone":
            digits = re.sub(r"\D", "", text)
            return (True, text, "") if len(digits) >= 10 else (False, "", "Не похоже на номер телефона — в нём должно быть не меньше десяти цифр.")
        if kind == "address":
            if len(text) < 6 or not re.search(r"\d", text): return False, "", "В адресе нужен номер дома — без него координатор не найдёт. Напишите ещё раз, пожалуйста."
            return True, text, ""
        if kind == "email":
            if text.lower() in NO_WORDS: return True, "не указана", ""
            return (True, text, "") if EMAIL_RE.match(text) else (False, "", "Не похоже на адрес почты. Он выглядит так: имя@почта.ру")
        if kind == "choice":
            options = question["options"]
            return self._check_multi(options, text) if question.get("multi") else self._check_one(options, text)
        if not question.get("required", True) and text.lower() in NO_WORDS: return True, "не указано", ""
        return (False, "", "Слишком короткий ответ — напишите чуть подробнее.") if len(text) < 2 else (True, text, "")

    @staticmethod
    def _check_one(options: list[str], text: str) -> tuple[bool, str, str]:
        if text.isdigit():
            number = int(text)
            return (True, options[number - 1], "") if 1 <= number <= len(options) else (False, "", f"Нужен номер от 1 до {len(options)}.")
        for name in options:
            if text.lower() == name.lower(): return True, name, ""
        return False, "", f"Напишите номер ответа — от 1 до {len(options)}."

    @staticmethod
    def _check_multi(options: list[str], text: str) -> tuple[bool, str, str]:
        raw = [p.strip() for p in re.split(r"[,\s;]+", text) if p.strip()]; chosen: list[str] = []
        for part in raw:
            if part.isdigit():
                number = int(part)
                if not 1 <= number <= len(options): return False, "", f"Номер {number} не подходит — есть только 1–{len(options)}."
                name = options[number - 1]
            else:
                match = [o for o in options if o.lower() == part.lower()]
                if not match: return False, "", f"Не понял «{part}». Напишите номера через запятую, например: 1, 3"
                name = match[0]
            if name not in chosen: chosen.append(name)
        return (True, "; ".join(chosen), "") if chosen else (False, "", "Напишите хотя бы один номер.")

    def summary(self, user_id: str) -> str:
        person = self.state.get(user_id)
        if not person or not person.get("answers"): return "Вы пока ничего не заполнили. Напишите «заново», чтобы начать."
        lines = ["Что записано:", ""]
        for question in QUESTIONS:
            value = person["answers"].get(question["id"])
            if value: lines.extend([f"• {question['text'].splitlines()[0]}", f"  {value}"])
        if person.get("alerts"):
            lines.append(""); lines.append("Отмечено как требующее внимания:")
            lines.extend(f"  — {note}" for note in person["alerts"])
        return "\n".join(lines)

    def brief(self, user_id: str) -> str:
        person = self.state.get(user_id)
        if not person: return ""
        a = person["answers"]; куда = a.get("address") or a.get("district", "")
        bits = [a.get("patient_name") or a.get("name", "без имени"), a.get("phone", "телефон не указан"), куда, a.get("mobility") or a.get("need", "")]
        line = " · ".join(b for b in bits if b)
        if person.get("alerts"): line += "  ⚠ " + "; ".join(n.split(": ", 1)[-1] for n in person["alerts"])
        return line

    def export_csv(self, path: str = CSV_EXPORT) -> str:
        header = ["Кто ответил", "Начато", "Завершено", "Требует внимания"] + [q["text"].splitlines()[0] for q in QUESTIONS]
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=";"); writer.writerow(header)
            for user_id, person in self.state.items():
                answers = person.get("answers", {}); writer.writerow([user_id, person.get("started", ""), person.get("finished", ""), "; ".join(person.get("alerts", []))] + [answers.get(q["id"], "") for q in QUESTIONS])
        return path

    def stats(self) -> tuple[int, int]:
        started = len(self.state); return started, sum(1 for p in self.state.values() if p.get("finished"))

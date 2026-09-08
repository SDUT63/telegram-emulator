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
from survey_questions import CHECKPOINT_ID, QUESTIONS, STOP_OPTION

МЕСЯЦЫ = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


def _по_русски(момент: str) -> str:
    """«2026-09-08T15:40:12» → «8 сентября 2026 года».

    Дату согласия человек должен прочитать, а не расшифровать.
    """
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
    "заново — начать анкету сначала\n"
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
    "Если захотите дополнить — напишите «заново», анкета откроется снова.\n"
    "Если станет хуже — звоните 103."
)

# ---------------------------------------------------------------- согласие
#
# Анкета спрашивает о здоровье: 18 вопросов из 36 — подвижность, боль,
# питание, стомы, память. По ч. 1 ст. 10 ФЗ-152 это специальная категория
# персональных данных, и обрабатывать её без основания нельзя.
#
# Согласие показывается в две длины, и это не уловка, а вежливость.
# Первым экраном человек видит короткое изложение: кто спрашивает, что
# записываем, кто читает, как стереть. Полный текст — по кнопке, прямо
# в чате, без ухода на сайт. Оба текста — один документ и одна версия:
# правятся вместе, вместе же растёт CONSENT_VERSION.
#
# Почему коротким экраном: первое, что видит человек, пришедший за
# помощью для лежачей матери, не должно быть страницей юридического
# текста. Он закроет чат, и мы не узнаем, что кому-то было нужно.
# Полный текст от этого никуда не девается — он в одном нажатии,
# и нажатие «Согласен» доступно только рядом с обоими.
#
# Решение записывается вместе с датой и версией текста, и эта запись
# потом видна оператору и попадает в выгрузку. Какое именно основание
# из ч. 2 ст. 10 использует организация — согласие субъекта, оказание
# медико-социальных услуг или иное — определяет юрист. Механизм
# рассчитан на любой из вариантов: он фиксирует факт и момент.
#
# Приложение А комплекта форм согласия собирается из этих же констант
# (docs/сборка-согласия.py), поэтому бумага не может разойтись с чатом:
# правите текст здесь — пересобираете документ. Тест это сторожит.
#
# ВНИМАНИЕ: оба текста должны быть утверждены юристом до запуска, а
# политика обработки персональных данных — опубликована. Нажатие кнопки
# в мессенджере — это простая электронная подпись; приравнять её к
# письменной форме можно только при соблюдении ст. 9 ФЗ-152.

CONSENT_VERSION = "1.0"

ORG_FULL = "АНО «Система долговременного ухода г. Тольятти»"
ORG_OGRN = "1266300009766"
ORG_INN = "6320093220"
ORG_ADDRESS = "445044, Самарская область, г. Тольятти, ул. Ворошилова, д. 19"

# Короткий экран. Он же — то, что человек читает перед нажатием.
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

# Полный текст. Всё, что требует ч. 4 ст. 9 ФЗ-152, человеческими словами.
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

# Повторять целиком экран согласия на каждое непонятое слово — значит
# выкатывать человеку одну и ту же стену второй раз. Коротко напоминаем,
# что от него нужно, и даём слова — на случай, если кнопок не видно.
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
    "«ответы» — посмотреть заполненное, «заново» — пройти снова."
)

# Что бот отвечает на сообщение после анкеты. Обещание «передадим» тут
# не пустое: сообщение действительно ложится в карточку, и координатор
# видит его рядом со своими.
ANSWERED = (
    "Записал, передам координатору — он ответит при звонке "
    "или напишет сюда.\n\n"
    "Если срочно и человеку плохо — звоните 103."
)

# «заново» стирает ответы и начинает сначала — это осознанное действие.
# «/start» и «начать» ведут себя бережнее: если анкета не дозаполнена,
# они возвращают человека к тому же вопросу, а не выбрасывают ответы.
RESTART_WORDS = {"заново", "начать заново", "/restart", "сначала"}
BEGIN_WORDS = {"/start", "start", "старт", "начать", "начнём", "начнем"}
CANCEL_WORDS = {"отмена", "стоп", "cancel", "/cancel", "/stop"}
SUMMARY_WORDS = {"ответы", "результаты", "мои ответы", "/answers"}
HELP_WORDS = {"помощь", "help", "/help", "?"}
SKIP_WORDS = {"далее", "пропустить", "skip", "-"}
BACK_WORDS = {"назад", "back"}
CONTINUE_WORDS = {"продолжить", "продолжаем", "дальше"}
AGREE_WORDS = {"согласен", "согласна", "да", "принимаю", "хорошо"}
REFUSE_WORDS = {"не согласен", "не согласна", "нет", "отказываюсь"}
# Полный текст согласия — словом, а не только кнопкой: кнопку могут
# и не увидеть, а спросить «а полностью?» человеку естественно.
READ_WORDS = {"полностью", "полный текст", "согласие", "прочитать полностью",
              "подробнее", "/consent"}
# Право на удаление — ст. 14 и 21 ФЗ-152. Работает в любой момент.
ERASE_WORDS = {"удалить", "удалите", "сотрите", "забудь меня", "/delete"}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
NO_WORDS = {"нет", "-", "—", "no", "нету", "отсутствует", "не знаю"}

# Раздел действует до следующего заголовка, а не только на первом вопросе:
# строка «Подвижность · вопрос 8 из 29» говорит человеку, где он находится,
# и стоит она недорого — одна короткая строка.
SECTIONS: list[str] = []
for _question in QUESTIONS:
    SECTIONS.append(_question.get("section") or (SECTIONS[-1] if SECTIONS else ""))


class Survey:
    """Ведёт анкету по каждому человеку и хранит состояние между запусками."""

    def __init__(
        self, storage_path: str = STORAGE, *, list_options: bool = True
    ) -> None:
        """list_options=False — если варианты рисует сам транспорт.

        В чате варианты приходят кнопками, и перечислять их ещё и текстом
        значит показать человеку один и тот же список дважды. Сообщение
        разбухает, а читать его страшно. В командной строке и в разборе
        анкеты кнопок нет, поэтому там список нужен.
        """
        self.storage_path = storage_path
        self.list_options = list_options
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
            "total_seen": 0,
            # Согласие на обработку: {"at": ..., "version": ...} либо
            # {"refused": ...}. Пусто — человек ещё не отвечал.
            "consent": None,
            # Что человек уже отметил кнопками в вопросе с несколькими
            # ответами, пока не нажал «Готово»
            "pending": None,
            # Ссылки, приложенные к последнему сообщению: [[подпись, адрес]].
            # Живут до следующего ответа — кнопка не должна висеть вечно.
            "reading": False,
            "messages": [],
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

    # --------------------------------------------------------- согласие

    def stage(self, user_id: str) -> str:
        """Где человек находится: «consent», «survey» или «done».

        Транспорт по этому решает, какие кнопки рисовать. Разделение
        нужно потому, что до согласия вопросов не существует вовсе —
        не «первый вопрос заблокирован», а именно не существует.
        """
        person = self.state.get(user_id)
        if not person:
            return "consent"
        if not (person.get("consent") or {}).get("at"):
            return "consent"
        if person.get("finished"):
            return "done"
        return "survey"

    def consented(self, user_id: str) -> dict[str, Any] | None:
        """Запись о согласии: когда дано и по какой версии текста."""
        person = self.state.get(user_id) or {}
        mark = person.get("consent") or {}
        return mark if mark.get("at") else None

    def grant_consent(self, user_id: str) -> str:
        """Человек согласился. Записываем факт, время и версию текста.

        Запись делается один раз. Повторное нажатие не меняет дату:
        согласие — это доказательство законности обработки, и его момент
        подделывать нельзя даже случайно.
        """
        person = self._person(user_id)
        if not (person.get("consent") or {}).get("at"):
            person["consent"] = {
                "at": datetime.now().isoformat(timespec="seconds"),
                "version": CONSENT_VERSION,
            }
        if person["started"] is None:
            person["started"] = person["consent"]["at"]
        self.save()
        return CONSENT_YES + "\n\n" + self._ask(user_id, self._next(0, person["answers"]))

    def refuse_consent(self, user_id: str) -> str:
        """Отказ. Ничего, кроме самого отказа, не храним."""
        person = self._person(user_id)
        person["consent"] = {
            "refused": datetime.now().isoformat(timespec="seconds"),
            "version": CONSENT_VERSION,
        }
        person["answers"] = {}
        self.save()
        return CONSENT_NO

    def consent_text(self, user_id: str) -> str:
        """Полный текст согласия — в чат, а не ссылкой на сайт.

        Уводить человека на сайт за тем, под чем он сейчас подпишется,
        неправильно: он теряет нить, а мы — доверие. Текст один и тот же
        для всех; когда уже известно, о ком речь, снизу добавляется
        строка о том, кто подписывает бумажную форму.
        """
        куски = [CONSENT_FULL]

        подписывает = consent_forms.signs(
            (self.state.get(user_id) or {}).get("answers") or {})
        if подписывает:
            куски.append("Бумажную форму подписывает: " + подписывает + ".")

        дано = self.consented(user_id)
        if дано:
            куски.append(CONSENT_GIVEN_AT.format(когда=_по_русски(дано["at"])))
        return "\n\n".join(куски)

    # ------------------------------------------------------------ переписка

    # Что человек написал сверх анкеты. Ответы на вопросы сюда не идут —
    # они и так в answers; сюда попадает то, чего иначе никто не увидит:
    # вопрос координатору, уточнение, ответ на его сообщение.
    #
    # Держим у себя, а не в CRM: писать в файл анкеты имеет право только
    # бот, и это единственное место, где переписка гарантированно полна.
    MESSAGES_LIMIT = 200

    def note_message(self, user_id: str, text: str, files: list | None = None) -> None:
        """Запомнить сообщение человека, не разобранное как ответ."""
        text = (text or "").strip()
        if not text and not files:
            return
        person = self.state.get(user_id)
        if person is None:
            return

        запись: dict[str, Any] = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "text": text[:2000],
        }
        if files:
            # Только имя и адрес: сам файл лежит у MAX, качать его
            # к себе без нужды — значит хранить лишние данные о людях.
            запись["files"] = [dict(f) for f in files][:10]

        журнал = person.setdefault("messages", [])
        журнал.append(запись)
        del журнал[:-self.MESSAGES_LIMIT]
        self.save()

    def messages(self, user_id: str) -> list[dict[str, Any]]:
        """Переписка от человека, старые сверху."""
        return list((self.state.get(user_id) or {}).get("messages") or [])

    def erase(self, user_id: str) -> str:
        """Удалить всё об этом человеке. Право по ст. 14 и 21 ФЗ-152."""
        self.state.pop(user_id, None)
        self.save()
        self.export_csv()
        return ERASED

    # -------------------------------------------------------------- начало

    def start(self, user_id: str) -> str:
        self.state[user_id] = self._blank()
        self.save()
        return GREETING + "\n\n" + CONSENT_SHORT

    def restart_after_consent(self, user_id: str) -> str:
        """Начать анкету заново, не переспрашивая согласие.

        Согласие дано на обработку, а не на конкретный набор ответов:
        переспрашивать его на каждый круг — навязчиво и бессмысленно.
        """
        keep = (self.state.get(user_id) or {}).get("consent")
        self.state[user_id] = self._blank()
        self.state[user_id]["consent"] = keep
        self.state[user_id]["started"] = datetime.now().isoformat(timespec="seconds")
        self.save()
        return self._ask(user_id, 0)

    def handle(self, user_id: str, text: str) -> str:
        text = (text or "").strip()
        low = text.lower()

        # Первое сообщение от незнакомого человека начинает анкету, что бы
        # он ни написал. Событие «открыл диалог» приходит не всегда — если
        # полагаться только на него, человек пишет «здравствуйте» и получает
        # придирку к формату ответа вместо приветствия.
        if user_id not in self.state:
            return self.start(user_id)

        # Удаление работает в любой момент и не требует подтверждений:
        # человек имеет на это право, а лишний экран — препятствие.
        if low in ERASE_WORDS:
            return self.erase(user_id)

        # До согласия анкеты не существует. Никакие другие слова здесь
        # не обрабатываются — иначе получится, что мы что-то собираем
        # до того, как человек разрешил.
        if self.stage(user_id) == "consent":
            if low in AGREE_WORDS or low in BEGIN_WORDS or low in RESTART_WORDS:
                return self.grant_consent(user_id)
            if low in REFUSE_WORDS or low in CANCEL_WORDS:
                return self.refuse_consent(user_id)
            if low in HELP_WORDS:
                return HELP
            if low in READ_WORDS:
                return self.consent_text(user_id)
            return CONSENT_WAIT

        if low in RESTART_WORDS:
            return self.restart_after_consent(user_id)
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
            if low in BEGIN_WORDS:
                return self.start(user_id)
            # Анкета закончена, а человек пишет — это уже разговор
            # с координатором, и он не должен пропасть.
            self.note_message(user_id, text)
            return ANSWERED

        # «/start» на недозаполненной анкете возвращает к тому же вопросу.
        # Стирать чужие ответы по такой безобидной команде нельзя.
        if low in BEGIN_WORDS:
            return RESUMED + "\n\n" + self._ask(user_id, step)

        # «продолжить» после предупреждения просто повторяет вопрос
        if low in CONTINUE_WORDS:
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
            # Не подошло как ответ — возможно, это и не ответ, а вопрос
            # к нам. Опечатку координатор отличит от вопроса сам, а вот
            # потерянный вопрос не вернёшь.
            if self._looks_like_speech(text):
                self.note_message(user_id, text)
            return problem + "\n\n" + self._ask(user_id, step)

        return self._accept(user_id, step, cleaned)

    @staticmethod
    def _looks_like_speech(text: str) -> bool:
        """Похоже на обращённую к нам фразу, а не на промах по кнопке.

        «2», «12345», «дп» — промах или опечатка, их в переписку писать
        незачем. «А сколько это стоит?» — вопрос, и он должен дойти.
        """
        text = (text or "").strip()
        return len(text) >= 12 and " " in text

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
        person["pending"] = None

        # Что удаётся разобрать из свободного ответа — район в адресе,
        # например. Разобранное живёт отдельными полями: по ним считается
        # маршрут и строится карточка. Исходная строка при этом остаётся
        # нетронутой, и координатор всегда видит, что человек написал.
        derive = question.get("derive")
        if derive:
            person["answers"].update(derive(value))
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

        # Что сказать сразу после этого ответа — например, какое согласие
        # понадобится. Идёт после предупреждений: сначала здоровье,
        # потом бумаги.
        person["reading"] = False
        after = question.get("after")
        if after:
            said = after(person["answers"])
            if said:
                # Раз речь зашла о согласии — пусть полный текст будет
                # на расстоянии одного нажатия, здесь же, в чате.
                person["reading"] = True
                prefix += said + "\n\n" + "—" * 20 + "\n\n"

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

    def _ask(self, user_id: str, step: int, *, hint: bool = True) -> str:
        """Сообщение с вопросом.

        Держим его коротким: строка «где я», сам вопрос и, только если без
        неё непонятно, одна строка подсказки. Остальное человек видит
        кнопками — дублировать варианты ещё и текстом значит показать один
        и тот же список дважды.
        """
        person = self._person(user_id)
        question = QUESTIONS[step]

        where = self._progress(person, step)
        if SECTIONS[step]:
            where = f"{SECTIONS[step]} · {where.lower()}"

        parts = [where, "", question["text"]]

        if question["kind"] == "choice" and self.list_options:
            # Без кнопок список нужен: иначе отвечать не на что
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
        elif hint and question.get("multi"):
            parts.append("")
            parts.append("Отметьте всё, что подходит.")

        return "\n".join(parts)

    def question_text(self, user_id: str, *, hint: bool = True) -> str:
        """Текст текущего вопроса — чтобы транспорт мог переписать сообщение.

        hint=False для уже отвеченного вопроса: подсказка, как отвечать,
        в переписке потом только мешает.
        """
        spot = self.current(user_id)
        return "" if spot is None else self._ask(user_id, spot[0], hint=hint)

    # ------------------------------------------------------- варианты ответа
    # Всё, что нужно транспорту, чтобы показать варианты кнопками. Сама
    # анкета о кнопках ничего не знает: она отдаёт данные, а как их
    # нарисовать — дело max_bot.py. Ответ кнопкой проходит ровно тот же
    # путь, что и напечатанный номер, поэтому проверки и предупреждения
    # работают одинаково.

    def current(self, user_id: str) -> tuple[int, dict[str, Any]] | None:
        """Номер и содержание вопроса, на котором человек стоит сейчас."""
        person = self.state.get(user_id)
        if not person or person.get("finished"):
            return None
        if not (person.get("consent") or {}).get("at"):
            return None          # до согласия вопросов нет
        step = self._next(person.get("step", 0), person.get("answers", {}))
        if step >= len(QUESTIONS):
            return None
        return step, QUESTIONS[step]

    @staticmethod
    def _is_none_option(name: str) -> bool:
        """«Ничего из этого нет» и подобные — они исключают остальные."""
        return name.lower().startswith("ничего")

    @classmethod
    def none_index(cls, question: dict[str, Any]) -> int | None:
        """Где в списке вариант «ничего из этого нет», если он есть.

        Транспорт показывает его не в общем ряду, а отдельной кнопкой
        внизу: он не признак наравне с остальными, а ответ «признаков нет».
        """
        for index, name in enumerate(question.get("options", [])):
            if cls._is_none_option(name):
                return index
        return None

    def picked(self, user_id: str, step: int) -> list[int]:
        """Что уже отмечено в вопросе с несколькими ответами."""
        person = self.state.get(user_id) or {}
        pending = person.get("pending") or {}
        if pending.get("step") != step:
            return []
        return list(pending.get("picked", []))

    def reading(self, user_id: str) -> bool:
        """К последнему сообщению приложен полный текст согласия?

        Это не свойство вопроса, а свойство одного сообщения: кнопка
        живёт ровно до следующего ответа, чтобы не висеть под всей
        анкетой.
        """
        return bool((self.state.get(user_id) or {}).get("reading"))

    def picked_names(self, user_id: str, step: int) -> list[str]:
        """Отмеченное словами — чтобы написать его прямо в сообщении."""
        spot = self.current(user_id)
        if not spot or spot[0] != step:
            return []
        options = spot[1].get("options", [])
        return [options[i] for i in self.picked(user_id, step) if i < len(options)]

    def toggle(self, user_id: str, step: int, index: int) -> bool:
        """Отметить или снять вариант. False — кнопка от другого вопроса."""
        spot = self.current(user_id)
        if not spot or spot[0] != step:
            return False

        options: list[str] = spot[1].get("options", [])
        if not 0 <= index < len(options):
            return False

        person = self._person(user_id)
        picked = self.picked(user_id, step)

        if index in picked:
            picked.remove(index)
        elif self._is_none_option(options[index]):
            # «Ничего из перечисленного» снимает всё остальное — иначе
            # получается ответ, который сам себе противоречит
            picked = [index]
        else:
            picked = [i for i in picked if not self._is_none_option(options[i])]
            picked.append(index)

        person["pending"] = {"step": step, "picked": sorted(picked)}
        self.save()
        return True

    def answer_by_numbers(self, user_id: str, numbers: list[int]) -> str:
        """Ответ кнопками. Идёт тем же путём, что и напечатанные номера."""
        return self.handle(user_id, ", ".join(str(n) for n in numbers))

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

        if kind == "address":
            # Адрес без номера дома бесполезен: ехать всё равно некуда,
            # и координатор потратит звонок на уточнение.
            if len(text) < 6 or not re.search(r"\d", text):
                # Пример уже стоит в самом вопросе — повторять его
                # в ошибке значит написать его дважды подряд.
                return False, "", (
                    "В адресе нужен номер дома — без него координатор "
                    "не найдёт. Напишите ещё раз, пожалуйста."
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
        # Адрес человек пишет одной строкой и обычно называет район сам;
        # приписывать распознанный район ещё раз — значит удвоить его.
        куда = a.get("address") or a.get("district", "")
        bits = [
            a.get("patient_name") or a.get("name", "без имени"),
            a.get("phone", "телефон не указан"),
            куда,
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
        "2",              # о близком человеке
        "Анна",
        "89171234567",
        "4",              # 85 и старше
        "4, 8",           # боль + покраснение на коже
        "6",              # помощь по уходу на дому
        "1",              # продолжить
        "5",              # не встаёт с постели
        "3",              # не переворачивается сам
        "2",              # покраснение -> предупреждение
        "3",              # кормить с ложки
        "4",              # поперхивается -> предупреждение
        "3", "4", "3",    # гигиена, туалет, одевание
        "2", "2", "3",    # речь, ориентация, одного нельзя
        "3",              # боль постоянная
        "1, 3",           # боль мешает движению и сну
        "1",              # мочевой катетер
        "3",              # лекарства даём мы
        "1",              # родные, живём вместе
        "4",              # круглосуточно
        "4",              # уже не справляемся -> предупреждение
        "7",              # ничего из оборудования нет
        "1",              # инвалидность
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

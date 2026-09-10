#!/usr/bin/env python3
"""
Чат-бот АНО «СДУТ» для мессенджера MAX.

Бот сам подключается к серверам MAX и забирает сообщения (long polling),
поэтому ему НЕ нужен публичный адрес, домен, сертификат и открытый порт.
Его можно запустить прямо на ноутбуке.

Запуск:
    python max_bot.py

Токен бот берёт из файла token.txt рядом с этим файлом либо из переменной
окружения MAX_BOT_TOKEN. Как получить токен — написано в ЗАПУСК_БОТА.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import fallback
import knowledge
from chatbot_survey import Survey

# Модуль модели необязателен: без ключей и без папки ai/ бот работает
# ровно так же, только без справок, составленных моделью.
try:
    import ai as _ии
except Exception as _беда:                                      # noqa: BLE001
    _ии = None
    logging.getLogger("сдут-бот").info("модуль ИИ не подключён: %s", _беда)

TOKEN_FILE = "token.txt"
CERTS_FILE = "certs.pem"

HERE = os.path.dirname(os.path.abspath(__file__))

# Если рядом лежит certs.pem, добавляем его к списку доверенных центров.
# Нужно, когда соединение проверяет антивирус или корпоративный шлюз: они
# подменяют сертификат собой, и Python об этом центре ничего не знает.
# Переменную надо выставить ДО импорта aiohttp — он создаёт список доверия
# один раз при загрузке.
_certs = os.path.join(HERE, CERTS_FILE)
if os.path.exists(_certs):
    os.environ.setdefault("SSL_CERT_FILE", _certs)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("сдут-бот")

# Библиотека шумит в консоль на уровне INFO — человеку это мешает читать
logging.getLogger("maxapi").setLevel(logging.WARNING)


def read_token() -> str:
    """Достать токен: сначала из переменной окружения, потом из token.txt."""
    token = (os.getenv("MAX_BOT_TOKEN") or "").strip()
    if token:
        return token

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, TOKEN_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as fh:
            # Берём первую непустую строку, которая не является комментарием
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line

    print()
    print("=" * 62)
    print("  Не найден токен бота.")
    print()
    print("  Создайте рядом с этим файлом файл token.txt")
    print("  и вставьте в него одну строку — токен от MAX.")
    print()
    print("  Где взять токен: найдите в MAX бота @MasterBot,")
    print("  отправьте ему команду /create и скопируйте выданный токен.")
    print("=" * 62)
    print()
    sys.exit(1)


# Команды, которые видно в меню бота внутри MAX. Без этого списка меню
# пустое, и человек не понимает, что боту вообще можно написать.
# Слова, по которым бот показывает, о чём можно спросить. Человек,
# не знающий, что спрашивать, чаще всего пишет именно их.
ASK_WORDS = {"спросить", "подсказки", "темы", "что спросить", "справка",
             "/ask", "/faq", "не знаю с чего начать", "с чего начать"}

COMMANDS = [
    ("start", "Начать анкету"),
    ("ask", "Спросить об уходе, документах, помощи"),
    ("answers", "Показать, что уже заполнено"),
    ("help", "Что можно написать боту"),
    ("cancel", "Прервать анкету"),
]

BUTTON_LIMIT = 64  # столько символов помещается на кнопке MAX

# Насколько узкой должна быть самая длинная подпись, чтобы варианты встали
# в два столбца. Два столбца вдвое укорачивают список — но MAX не переносит
# длинную подпись на вторую строку, а обрезает многоточием: «Программа
# соцусл…». Обрезанную подпись человек прочитать не может, поэтому лучше
# один столбец. Мерка снята с записи экрана: «О близком человеке» влезает,
# «Программа соцуслуг» той же длины — уже нет.
TWO_COLUMNS_AT = 17.5
TWO_COLUMNS_AT_MULTI = 15.0     # тут впереди ещё галочка

# Ширина букв разная, и считать их поштучно нельзя: «мм» шире «ии» вдвое.
# Грубая мерка — по группам.
WIDE = set("шщмжюыфШЩМЖЮЫФ")
NARROW = set(" ьъiljt.,'!:;()-—·")


def _width(text: str) -> float:
    """Примерная ширина подписи в «средних буквах»."""
    return sum(1.35 if c in WIDE else 0.5 if c in NARROW else 1.0 for c in text)

# Отмеченное помечаем зелёной галочкой, неотмеченное — ничем. Ставить
# значок и на неотмеченные значит удвоить пестроту ради разницы, которую
# и так видно: кнопки выровнены по центру, ряд не разъезжается.
MARK_ON = "✅ "
MARK_OFF = ""


def _fits(text: str) -> str:
    return text if len(text) <= BUTTON_LIMIT else text[: BUTTON_LIMIT - 1] + "…"


def _columns(options: list[str], multi: bool) -> int:
    """Один столбец или два. Решаем по самой длинной подписи."""
    limit = TWO_COLUMNS_AT_MULTI if multi else TWO_COLUMNS_AT
    if len(options) < 3:
        return 1
    return 2 if max(_width(name) for name in options) <= limit else 1


def layout(survey: Survey, user_id: str) -> list[list[tuple[str, str]]]:
    """Раскладка кнопок под текущим шагом: строки из пар (подпись, действие).

    Чистая функция без единого объекта maxapi. Так её видит и тот, кто
    рисует настоящую клавиатуру, и тот, кто рисует картинки для мануала —
    и мануал не может разойтись с ботом.

    Действие — это payload кнопки.

    Держим список коротким. Варианты встают в два столбца, когда подписи
    это позволяют; служебные кнопки — «назад», «пропустить», «готово» —
    занимают одну строку внизу, а не по строке каждая.
    """
    rows: list[list[tuple[str, str]]] = []

    # Полный текст согласия приложен к сообщению, а не к вопросу: он
    # живёт до следующего ответа и идёт первой строкой — это справка,
    # а не вариант ответа, и путать их нельзя.
    reading = survey.reading(user_id)
    if reading:
        rows.append([("Полный текст согласия", "c:full")])

    stage = survey.stage(user_id)
    if stage == "consent":
        # Согласие — не вопрос анкеты, а вход в неё. Кнопки равновелики:
        # отказ не спрятан и не помечен как ошибка, это законный выбор.
        # Полный текст — между ними: чтобы прочитать его, не надо ни
        # соглашаться, ни отказываться, и уходить из чата тоже не надо.
        rows.append([("Согласен, продолжим", "c:y")])
        rows.append([("Прочитать полностью", "c:full")])
        rows.append([("Не согласен", "c:n")])
        return rows

    spot = survey.current(user_id)
    if spot is None:
        # Анкета закончена: оставляем только то, что осмысленно нажать
        rows.append([("Мои ответы", "m"), ("Заполнить заново", "n")])
        return rows

    step, question = spot
    if question["kind"] != "choice":
        # У вопроса без вариантов кнопок нет — но приложенный полный
        # текст остаётся: он относится к предыдущему шагу, а не к этому.
        return rows

    options: list[str] = question["options"]
    multi = bool(question.get("multi"))
    picked = survey.picked(user_id, step) if multi else []
    nothing = Survey.none_index(question) if multi else None

    # «Ничего из этого нет» — не признак наравне с остальными, а ответ
    # «признаков нет». Ему место внизу, отдельно от списка
    shown = [i for i in range(len(options)) if i != nothing]

    row: list[tuple[str, str]] = []
    per_row = _columns([options[i] for i in shown], multi)
    for index in shown:
        if multi:
            on = index in picked
            row.append((_fits((MARK_ON if on else MARK_OFF) + options[index]),
                        f"t:{step}:{index}"))
        else:
            row.append((_fits(options[index]), f"a:{step}:{index}"))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Нижняя строка: одно главное действие и, если есть куда, «назад»
    bottom: list[tuple[str, str]] = []
    person = survey.state.get(user_id) or {}
    if person.get("history"):
        bottom.append(("← Назад", "b"))

    if multi and picked:
        bottom.append((f"Готово · {len(picked)}", f"d:{step}"))
    elif multi and nothing is not None:
        bottom.append((options[nothing], f"a:{step}:{nothing}"))
    elif not question.get("required", True):
        bottom.append(("Пропустить", f"s:{step}"))
    elif multi:
        bottom.append(("Готово", f"d:{step}"))

    # Служебные кнопки просятся в одну строку — но только если обе туда
    # влезают. «Ничего не оформлено» рядом с «Назад» обрезается, а
    # обрезанная кнопка хуже лишней строки.
    if len(bottom) == 2 and max(_width(label) for label, _ in bottom) > TWO_COLUMNS_AT:
        rows.extend([button] for button in bottom)
    elif bottom:
        rows.append(bottom)

    return rows


# Действия, которые бот подсвечивает зелёным: согласие и «готово».
# Зелёный здесь значит «это шаг вперёд», а не «это правильный ответ».
def _positive(action: str, label: str) -> bool:
    return action == "c:y" or action.startswith("d:") or label.startswith(MARK_ON)


def keyboard_for(survey: Survey, user_id: str):
    """Клавиатура maxapi по раскладке из :func:`layout`.

    Ответ кнопкой идёт тем же путём, что и напечатанный номер, поэтому
    проверки и предупреждения работают одинаково, а печатать номера
    по-прежнему можно.
    """
    from maxapi.enums.intent import Intent
    from maxapi.types.attachments.buttons import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

    rows = layout(survey, user_id)
    if not rows:
        return None

    keyboard = InlineKeyboardBuilder()
    for row in rows:
        keyboard.row(*[
            CallbackButton(
                text=label, payload=action,
                intent=Intent.POSITIVE if _positive(action, label) else Intent.DEFAULT)
            for label, action in row
        ])
    return keyboard.as_markup()


# Что бот отвечает, когда человек прислал файл или фотографию.
FILES_TAKEN = "Файл получил, приложу к вашему обращению."

# Подпись под справочным ответом. Человек должен понимать, что это
# материалы службы, а не заключение и не совет врача.
СПРАВКА_ПОДПИСЬ = (
    "———\n"
    "Это выдержка из материалов службы. Координатор ответит подробнее "
    "при звонке, а если человеку плохо сейчас — звоните 103."
)


# Сколько подсказок предлагать. Больше четырёх человек не читает,
# а превращает в стену кнопок.
ПОДСКАЗОК = 4

# Подпись над кнопками-подсказками. Человек должен понимать, что это
# не варианты ответа на его вопрос, а другие вопросы, которые он может
# задать. Без этой строки кнопки читаются как выбор.
ЕЩЁ_СПРАШИВАЮТ = "Ещё об этом спрашивают:"


def _кнопки_подсказок(вопрос: str, кроме: str = ""):
    """Кнопки с готовыми вопросами. None — если предлагать нечего."""
    from maxapi.types.attachments.buttons import CallbackButton
    from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

    варианты = knowledge.подсказки(вопрос, сколько=ПОДСКАЗОК, кроме=кроме)
    if not варианты:
        return None
    keyboard = InlineKeyboardBuilder()
    for заголовок in варианты:
        # В payload кладём сам заголовок: он же ключ статьи. Кнопки
        # из старых сообщений от этого продолжают работать — статья
        # никуда не делась, а номера шагов anketы к ней отношения не имеют.
        keyboard.row(CallbackButton(text=_fits(knowledge.подпись(заголовок)),
                                    payload="k:" + заголовок[:60]))
    return keyboard.as_markup()


async def _отправить(bot, chat_id, who: str, текст: str, разметка=None) -> None:
    """Тот же способ адресации, что и у обычного ответа."""
    вложения = [разметка] if разметка else None
    if chat_id is not None:
        await bot.send_message(chat_id=chat_id, text=текст, attachments=вложения)
    else:
        await bot.send_message(user_id=int(who), text=текст, attachments=вложения)


async def справка(bot, chat_id, who: str, вопрос: str,
                  survey=None, уже_считали: bool = False) -> None:
    """Ответить на вопрос по базе знаний. Отдельной задачей, не в обработчике.

    MAX ждёт ответа на вебхук тридцать секунд и повторяет доставку, если
    не дождался. Обращение к модели идёт секунды, иногда десятки — держать
    ради него открытым обработчик события нельзя. Поэтому человек сразу
    получает подтверждение, а справка приходит отдельным сообщением.

    Если ответа в базе нет, это ещё не повод молчать. Что сказать —
    зависит от того, в какой раз подряд мы не поняли человека: лесенка
    из десяти ступеней лежит в fallback.py, счёт ведёт анкета.
    """
    try:
        текст = None
        if _ии and _ии.assistant.on():
            # urllib блокирующий: уводим его с цикла событий в поток
            текст = await asyncio.to_thread(_ии.assistant.reference, вопрос)
        if not текст:
            текст = knowledge.ответ_без_модели(вопрос)

        найдено = knowledge.найти(вопрос, сколько=1)
        показано = найдено[0].заголовок if найдено else ""
        разметка = _кнопки_подсказок(вопрос, кроме=показано)

        if текст:
            текст += "\n\n" + СПРАВКА_ПОДПИСЬ
            # Ответили — значит, поняли. Лесенка начинается сначала.
            if survey is not None and not уже_считали:
                survey.understood(who)
        elif уже_считали:
            # Анкета уже ответила этому человеку и уже предложила выход.
            # Сказать «не понял» второй раз за один ход — это перебор.
            log.info("%s: ответа в базе нет, анкета уже ответила", who)
            return
        else:
            попытка = survey.miss(who) if survey is not None else 1
            текст = fallback.фраза(попытка, fallback.ВОПРОС)
            if разметка is None:
                # Предлагать нечего даже кнопками — тогда хотя бы словами
                вступление, _ = knowledge.начать()
                текст += "\n\n" + вступление

        await _отправить(bot, chat_id, who, текст, разметка)
        log.info("%s: отправлена справка по базе", who)
    except Exception as error:                                  # noqa: BLE001
        # Справка — приятное дополнение. Её отказ не должен ничего ломать.
        log.warning("справка не отправилась: %s", error)


async def меню_тем(bot, chat_id, who: str, survey=None) -> None:
    """Показать, о чём вообще можно спросить. Для тех, кто не знает."""
    if survey is not None:
        survey.understood(who)
    try:
        вступление, вопросы = knowledge.начать()
        разметка = _кнопки_подсказок("")           # начальный набор
        await _отправить(bot, chat_id, who, вступление, разметка)
        log.info("%s: показано меню тем", who)
    except Exception as error:                                  # noqa: BLE001
        log.warning("меню тем не отправилось: %s", error)


async def статья(bot, chat_id, who: str, заголовок: str) -> bool:
    """Прислать статью, которую человек выбрал кнопкой, и подсказки к ней."""
    текст = knowledge.статья_целиком(заголовок)
    if not текст:
        return False
    await _отправить(bot, chat_id, who, текст + "\n\n" + СПРАВКА_ПОДПИСЬ,
                     _кнопки_подсказок(заголовок, кроме=заголовок))
    log.info("%s: отправлена статья по кнопке", who)
    return True


def _files_of(body) -> list[dict]:
    """Вложения входящего сообщения: имя, вид и адрес у MAX.

    Сам файл к себе не тянем. Он уже лежит у MAX, а держать копии
    фотографий и выписок у себя — значит хранить о людях больше,
    чем нужно, и отвечать за это хранилище.
    """
    found: list[dict] = []
    for item in (getattr(body, "attachments", None) or []):
        kind = getattr(item, "type", None)
        kind = getattr(kind, "value", kind)
        if kind in ("inline_keyboard", "reply_keyboard"):
            continue
        payload = getattr(item, "payload", None)
        found.append({
            "kind": str(kind or "file"),
            "name": getattr(item, "filename", None) or "",
            "url": getattr(payload, "url", None) or "",
            "size": getattr(item, "size", None) or 0,
        })
    return found


class Seen:
    """Что уже обработано. Защита от повторной доставки одного события.

    MAX повторяет доставку вебхука, если сервер не ответил за 30 секунд,
    и делает это до десяти раз. Без защиты один ответ человека запишется
    дважды, а анкета перескочит через вопрос. При long polling то же самое
    происходит после обрыва связи.

    Ключ берём тот, что платформа гарантирует уникальным: у сообщения —
    mid, у нажатия кнопки — callback_id. Держим последние ~5000 и
    вытесняем самые старые: этого хватает на сутки работы, а память
    не растёт.
    """

    def __init__(self, limit: int = 5000) -> None:
        self.limit = limit
        self._keys: dict[str, None] = {}

    def fresh(self, key: str | None) -> bool:
        """True — событие новое. False — уже обрабатывали, надо пропустить."""
        if not key:
            return True          # нечем отличить: лучше обработать, чем потерять
        if key in self._keys:
            return False
        self._keys[key] = None
        while len(self._keys) > self.limit:
            self._keys.pop(next(iter(self._keys)))
        return True


def build_dispatcher(survey: Survey):
    """Собрать обработчики сообщений. Отдельная функция — чтобы её было видно."""
    from maxapi import Dispatcher
    from maxapi.types import BotStarted, MessageCallback, MessageCreated

    dp = Dispatcher()
    seen = Seen()

    def note_if_finished(user_id: str, was_done: bool) -> None:
        """Сводка в окно бота, как только анкета закрылась."""
        person = survey.state.get(user_id, {})
        if person.get("finished") and not was_done:
            print()
            print("  ── НОВОЕ ОБРАЩЕНИЕ " + "─" * 39)
            print("  " + survey.brief(user_id))
            print("  " + "─" * 58)
            print()
        started, finished = survey.stats()
        log.info("Всего обращений: %s, заполнено до конца: %s", started, finished)

    async def say(bot, chat_id, user_id: str, text: str) -> None:
        """Отправить ответ вместе с кнопками текущего вопроса."""
        markup = keyboard_for(survey, user_id)
        attachments = [markup] if markup else None
        if chat_id is not None:
            await bot.send_message(chat_id=chat_id, text=text, attachments=attachments)
        else:
            await bot.send_message(
                user_id=int(user_id), text=text, attachments=attachments
            )

    @dp.bot_started()
    async def on_started(event: BotStarted) -> None:
        """Человек только что открыл диалог с ботом."""
        chat_id, user_id = event.get_ids()
        log.info("Новый человек: %s", user_id)
        text = survey.start(str(user_id))
        await say(event.bot, chat_id, str(user_id), text)

    @dp.message_created()
    async def on_message(event: MessageCreated) -> None:
        """Любое сообщение в диалоге."""
        chat_id, user_id = event.get_ids()
        body = event.message.body
        if not seen.fresh(getattr(body, "mid", None)):
            log.info("%s: повтор доставки, пропускаем", user_id)
            return
        incoming = ((body.text if body else None) or "").strip()
        # В журнал не пишем ни текст, ни длину: на отладке это помогало,
        # в боевой среде туда попадут имя, телефон и жалобы на здоровье.
        # Достаточно знать, что сообщение было и от кого.
        log.info("%s: сообщение", user_id)

        # «Спросить» — это не ответ на вопрос анкеты и не сообщение
        # координатору, а просьба показать темы. Через анкету его вести
        # нельзя: она ответит придиркой к формату, а координатор получит
        # в переписку слово «спросить».
        хочет_темы = incoming.lower().strip(" ?!.") in ASK_WORDS
        if хочет_темы and str(user_id) in survey.state:
            await меню_тем(event.bot, chat_id, str(user_id), survey)
            return

        was_done = bool(survey.state.get(str(user_id), {}).get("finished"))
        было_сообщений = len(survey.messages(str(user_id)))
        было_промахов = survey.misses(str(user_id))
        reply = survey.handle(str(user_id), incoming)

        # Фотография выписки или скан направления — это ответ на вопрос
        # координатора, а не мусор. Анкета их не разбирает, но потерять
        # их нельзя: складываем в переписку рядом с текстом.
        files = _files_of(body)
        if files:
            survey.note_message(str(user_id), incoming, files)
            reply = FILES_TAKEN + "\n\n" + reply

        # Анкета отложила сообщение в переписку — значит, это не ответ
        # на вопрос, а обращение к нам. На такое можно ответить по базе.
        спросили = len(survey.messages(str(user_id))) > было_сообщений
        # Анкета уже сочла это сообщение непонятым и уже ответила
        # человеку по-своему. Второй раз считать тот же промах нельзя.
        уже_считали = survey.misses(str(user_id)) > было_промахов

        await say(event.bot, chat_id, str(user_id), reply)
        note_if_finished(str(user_id), was_done)

        if спросили and incoming:
            asyncio.create_task(
                справка(event.bot, chat_id, str(user_id), incoming,
                        survey, уже_считали))

    @dp.message_callback()
    async def on_button(event: MessageCallback) -> None:
        """Человек нажал кнопку под вопросом."""
        chat_id, user_id = event.get_ids()
        who = str(user_id)
        if not seen.fresh(event.callback.callback_id):
            log.info("%s: повтор нажатия, пропускаем", who)
            return
        parts = (event.callback.payload or "").split(":")
        action = parts[0] if parts else ""
        # payload — служебный код кнопки («a:5:2»), ответа в нём нет,
        # поэтому его писать можно: без него разбор сбоев невозможен.
        log.info("%s нажал: %s", who, event.callback.payload)

        # Кнопка от прошлого вопроса: сообщения в чате остаются, и нажать
        # старую кнопку можно в любой момент. Молча применять её нельзя —
        # ответ уйдёт не в тот вопрос.
        def stale(step_text: str) -> bool:
            spot = survey.current(who)
            return not spot or str(spot[0]) != step_text

        body = event.message.body if event.message else None
        original = (body.text if body else None) or None

        async def repaint(text: str | None, attachments: list) -> None:
            """Переписать сообщение с кнопками. Его могли и удалить."""
            try:
                await event.edit(
                    text=text, attachments=attachments, raise_if_not_exists=False
                )
            except Exception as error:  # noqa: BLE001
                log.debug("Не удалось обновить кнопки: %s", error)
                await event.ack()

        if action == "k":
            # Кнопка-подсказка: человек выбрал готовый вопрос из базы.
            # Состояние анкеты не трогаем — это чтение, а не ответ.
            await event.ack()
            survey.understood(who)
            заголовок = (event.callback.payload or "")[2:]
            if not await статья(event.bot, chat_id, who, заголовок):
                log.info("%s: статья не найдена: %s", who, заголовок[:40])
            return

        if action == "c" and parts[1:2] == ["full"]:
            # Полный текст — просто чтение. Состояние не меняем, кнопки
            # под сообщением оставляем: человек читает и возвращается
            # к тому же выбору.
            await event.ack()
            await say(event.bot, chat_id, who, survey.consent_text(who))
            return

        if action == "c":
            # Сначала смотрим, где человек стоит, и только потом меняем
            # состояние: иначе старая кнопка из истории чата переписала бы
            # уже данное согласие новой датой.
            if survey.stage(who) != "consent":
                await event.ack(notification="Это уже решено, идём дальше.")
                return
            reply = (survey.grant_consent(who) if parts[1:2] == ["y"]
                     else survey.refuse_consent(who))
            await repaint(original, [])
            await say(event.bot, chat_id, who, reply)
            return

        if action == "t" and len(parts) == 3:
            step = int(parts[1])
            if not survey.toggle(who, step, int(parts[2])):
                await event.ack(notification="Это кнопка от другого вопроса.")
                return
            # Отмеченное пишем словами прямо в сообщении: галочка на кнопке
            # видна не всем и не всегда, а строка читается однозначно
            text = survey.question_text(who)
            names = survey.picked_names(who, step)
            if names:
                text += "\n\nОтмечено: " + ", ".join(names)
            await repaint(text, [keyboard_for(survey, who)])
            return

        was_done = bool(survey.state.get(who, {}).get("finished"))

        if action in {"a", "s", "d"} and len(parts) >= 2 and stale(parts[1]):
            await event.ack(notification="Это кнопка от другого вопроса.")
            return

        # Заголовок вопроса запоминаем до ответа: после него survey уже
        # смотрит на следующий вопрос
        asked = (
            survey.question_text(who, hint=False)
            if action in {"a", "s", "d"}
            else None
        )

        # chosen — что показать в самом сообщении вместо кнопок. В MAX
        # нажатие кнопки не остаётся в переписке, и без такой пометки
        # человек видит подряд одни вопросы, не понимая, что он ответил.
        chosen = ""
        if action == "a" and len(parts) == 3:
            spot = survey.current(who)
            chosen = spot[1]["options"][int(parts[2])] if spot else ""
            reply = survey.answer_by_numbers(who, [int(parts[2]) + 1])
        elif action == "s":
            chosen = "пропущено"
            reply = survey.handle(who, "далее")
        elif action == "d":
            step = int(parts[1])
            picked = survey.picked(who, step)
            spot = survey.current(who)
            if picked:
                options = spot[1]["options"] if spot else []
                chosen = ", ".join(options[i] for i in picked if i < len(options))
                reply = survey.answer_by_numbers(who, [i + 1 for i in picked])
            elif spot and not spot[1].get("required", True):
                chosen = "пропущено"
                reply = survey.handle(who, "далее")
            else:
                await event.ack(notification="Отметьте хотя бы один вариант.")
                return
        elif action == "b":
            reply = survey.handle(who, "назад")
        elif action == "n":
            reply = survey.restart_after_consent(who)
        elif action == "m":
            reply = survey.summary(who)
        else:
            await event.ack()
            return

        # Кнопки со старого сообщения убираем: иначе на один вопрос можно
        # ответить дважды, а в переписке остаётся два живых набора кнопок
        if asked and chosen:
            await repaint(asked + "\n\n➤ " + chosen, [])
        else:
            await repaint(original, [])

        await say(event.bot, chat_id, who, reply)
        note_if_finished(who, was_done)

    return dp


def _outgoing_files(message: dict) -> list | None:
    """Вложения оператора для отправки. Пропавший файл письмо не отменяет.

    Памятка по уходу, бланк согласия, фотография — обычная часть работы
    координатора. Отправляем как есть: MAX сам разбирает, картинка это
    или документ.
    """
    from maxapi.types.input_media import InputMedia

    ready = []
    for item in message.get("files") or []:
        path = item.get("path") or ""
        if not os.path.exists(path):
            log.warning("Вложение пропало, отправляем без него: %s",
                        item.get("name", ""))
            continue
        ready.append(InputMedia(path))
    return ready or None


async def outbox_worker(bot) -> None:
    """Отправляет сообщения, которые оператор поставил в очередь из CRM.

    CRM не может писать людям сама: связь с MAX есть только у бота. Поэтому
    она складывает сообщения файлами в папку outbox, а бот их разбирает.
    Каждое сообщение — отдельный файл, так что два процесса никогда не
    пишут в один и тот же файл и блокировки не нужны.
    """
    import crm_store

    while True:
        try:
            for message in crm_store.pending_messages():
                text = (
                    f"{message['text']}\n\n"
                    f"— {message['who']}, служба долговременного ухода"
                )
                try:
                    await bot.send_message(
                        user_id=int(message["user_id"]), text=text,
                        attachments=_outgoing_files(message),
                    )
                    crm_store.mark_sent(message)
                    log.info("Оператор %s написал %s", message["who"], message["user_id"])
                except Exception as error:  # noqa: BLE001
                    crm_store.mark_sent(message, error=str(error))
                    log.warning("Не отправилось %s: %s", message["user_id"], error)
        except Exception as error:  # noqa: BLE001
            # Очередь не должна ронять бота: анкеты важнее
            log.warning("Очередь сообщений: %s", error)
        await asyncio.sleep(3)


async def main() -> None:
    token = read_token()

    from maxapi import Bot

    # Варианты рисуем кнопками, поэтому перечислять их ещё и текстом не надо
    survey = Survey(list_options=False)
    bot = Bot(token)
    dp = build_dispatcher(survey)

    # Проверяем связь и токен до старта, чтобы не ловить непонятную ошибку
    # в цикле. На время проверки глушим повторные попытки библиотеки: без
    # этого человек получает двадцать строк «Backing off» вместо ответа.
    noisy = [logging.getLogger(name) for name in ("backoff", "maxapi")]
    previous = [logger.level for logger in noisy]
    for logger in noisy:
        logger.setLevel(logging.CRITICAL)
    try:
        me = await bot.get_me()
    except Exception as error:  # noqa: BLE001 — показываем человеку, а не трассировку
        text = str(error)
        print()
        print("=" * 62)

        if "CERTIFICATE_VERIFY_FAILED" in text or "SSLCertVerification" in text:
            # Самая частая причина на Windows: MAX подписан российским
            # корневым сертификатом, которого нет в хранилище системы.
            print("  Python не доверяет сертификату MAX.")
            print()
            print("  Токен тут ни при чём — до проверки токена дело даже")
            print("  не дошло.")
            print()
            print("  Чаще всего виновата устаревшая библиотека: у разных")
            print("  адресов MAX разные сертификаты, и старые версии")
            print("  обращаются к тому, который система не принимает.")
            print()
            print("  1. Обновите библиотеку — это решает почти всегда:")
            print()
            print("         python -m pip install --upgrade maxapi")
            print()
            print("  2. Если не помогло, отключите VPN и запустите снова.")
            print("     MAX — российский сервис, ему VPN не нужен.")
            print()
            print("  3. Подробная проверка скажет точнее, в чём дело:")
            print()
            print("         python diagnose.py")
            print()
            print("  Отключать проверку сертификатов нельзя: через это")
            print("  соединение идут токен и данные обратившихся людей.")
        else:
            print("  Не удалось подключиться к MAX.")
            print()
            print("  Проверьте две вещи:")
            print("  1. В token.txt лежит именно токен — одной строкой,")
            print("     без кавычек и лишних пробелов.")
            print("  2. Компьютер подключён к интернету.")
            print()
            print("  Подробная проверка связи: python diagnose.py")

        print()
        print(f"  Техническая часть: {text}")
        print("=" * 62)
        print()
        await bot.close_session()
        return

    # Связь есть — возвращаем обычный уровень сообщений, чтобы во время
    # работы были видны настоящие сбои
    for logger, level in zip(noisy, previous):
        logger.setLevel(level)

    name = getattr(me, "name", None) or getattr(me, "first_name", "") or "бот"
    username = getattr(me, "username", None)

    print()
    print("=" * 62)
    print(f"  Бот запущен: {name}" + (f" (@{username})" if username else ""))
    print()
    print("  Найдите его в MAX и напишите ему любое сообщение — анкета")
    print("  начнётся сама. Варианты ответа приходят кнопками.")
    print()
    print("  Ответы сохраняются в survey_responses.csv — файл открывается")
    print("  двойным щелчком в Excel.")
    print()
    print("  Чтобы остановить бота, нажмите Ctrl+C в этом окне.")
    print("=" * 62)
    print()

    started, finished = survey.stats()
    if started:
        log.info("Уже сохранено обращений: %s, из них заполнено: %s", started, finished)

    # Long polling не работает, если у бота осталась подписка на webhook
    try:
        await bot.delete_webhook()
    except Exception:  # noqa: BLE001 — подписки может не быть, это нормально
        pass

    # Меню команд внутри MAX. Без него человек открывает бота и видит пустой
    # чат: непонятно, что писать. Список хранится на стороне MAX, поэтому
    # достаточно отправить его при запуске.
    from maxapi.types import BotCommand

    try:
        await bot.set_commands(
            *(BotCommand(name=name, description=text) for name, text in COMMANDS)
        )
        log.info("Меню команд обновлено: %s", ", ".join("/" + n for n, _ in COMMANDS))
    except Exception as error:  # noqa: BLE001 — без меню бот всё равно работает
        log.warning("Не удалось обновить меню команд: %s", error)

    # Очередь сообщений от операторов крутится рядом с опросом MAX
    queue = asyncio.create_task(outbox_worker(bot))
    try:
        await dp.start_polling(bot)
    finally:
        queue.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен. Ответы сохранены.\n")

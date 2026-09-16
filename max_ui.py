#!/usr/bin/env python3
"""Transport-independent MAX UI/scenario helpers used by production."""
from __future__ import annotations
from typing import Any
import hashlib
import knowledge
from chatbot_survey import Survey
ASK_WORDS = {"спросить", "хочу спросить", "у меня вопрос", "вопрос", "задать вопрос", "что спросить", "что можно спросить", "о чём можно спросить", "о чем можно спросить", "не знаю что спросить", "не знаю, что спросить", "темы", "покажи темы", "список тем", "меню", "подсказки", "подскажи", "подскажите", "справка", "справочник", "информация", "инфо", "что ты умеешь", "что умеешь", "чем поможешь", "чем ты поможешь", "не знаю с чего начать", "с чего начать", "с чего начинать", "/ask", "/faq", "/menu", "/topics",
             # Подписи кнопок: человек читает кнопку и печатает её текст,
             # особенно если нажатие не сработало с первого раза.
             "спросить о другом", "просто почитать", "о чём рассказать",
             "о чем рассказать", "все темы"}
BUTTON_LIMIT = 64
TWO_COLUMNS_AT = 17.5
TWO_COLUMNS_AT_MULTI = 15.0
WIDE = set("шщмжюыфШЩМЖЮЫФ")
NARROW = set(" ьъiljt.,'!:;()-—·")
MARK_ON = "✅ "
FILES_TAKEN = "Файл получил, приложу к вашему обращению."
from chatbot_survey import СПРАВКА_ПОДПИСЬ   # noqa: F401  (ответ отдают оба транспорта)
ПОДСКАЗОК = 4
ЕЩЁ_СПРАШИВАЮТ = "Ещё об этом спрашивают:"
КАРТА_ЗАГОЛОВОК = "О чём рассказать?"
# Письмо словами стоит первым, а список тем — вторым. Разделов
# двенадцать, и на телефоне это длинный список, в котором проще
# растеряться, чем выбрать. Написать «пролежни» быстрее, чем прочитать
# все двенадцать названий, — и теперь это действительно работает:
# до недавнего времени поиск по свободному вопросу после анкеты
# не запускался вовсе, и совет был пустым.
КАРТА_ПОДПИСЬ = (
    "Проще всего написать вопрос своими словами — хоть «пролежни», "
    "хоть «маме нечем платить». Найду по всем материалам службы.\n\n"
    "Или выберите тему ниже."
)
ВСЕ_ТЕМЫ = "Все темы"
НАЗАД = "‹ Назад"
ДАЛЬШЕ = "Ещё ›"
К_АНКЕТЕ = "Вернуться к анкете"


def _width(text: str) -> float:
    return sum(1.35 if c in WIDE else 0.5 if c in NARROW else 1.0 for c in text)


def fits(text: str) -> str:
    return text if len(text) <= BUTTON_LIMIT else text[: BUTTON_LIMIT - 1] + "…"


def _columns(options: list[str], multi: bool) -> int:
    if len(options) < 3: return 1
    limit = TWO_COLUMNS_AT_MULTI if multi else TWO_COLUMNS_AT
    return 2 if max((_width(name) for name in options), default=0) <= limit else 1


def _in_questionnaire(survey: Survey | None, user_id: str) -> bool:
    return survey is not None and survey.current(user_id) is not None


def _user_state(survey: Any, user_id: str) -> dict[str, Any]:
    """Read state only through the explicit user-scoped production contract."""
    reader = getattr(survey, "user_state", None)
    if reader is None:
        raise TypeError("Production UI requires survey.user_state(user_id); shared Survey.state is not a valid source")
    value = reader(str(user_id))
    if not isinstance(value, dict):
        raise TypeError("survey.user_state(user_id) must return a dict")
    return value


def consent_keyboard(full: bool = False) -> list[list[list[str]]]:
    """Keyboard that keeps the person oriented while reading the consent."""
    if full:
        return [
            [["Согласен, продолжим", "c:y"]],
            [["Не согласен", "c:n"]],
            [["← Вернуться к краткому тексту", "c:back"]],
        ]
    return [
        [["Согласен, продолжим", "c:y"]],
        [["Прочитать полный текст", "c:full"]],
        [["Не согласен", "c:n"]],
        [["Просто почитать", "map"]],
    ]


def _article_callback(title: str) -> str:
    """Return a short, deterministic callback key rather than truncating a title.

    MAX callback payloads are bounded and titles are not stable identifiers. A
    digest keeps the payload short and collision-resistant while allowing every
    process to resolve the same article from the canonical knowledge source.
    """
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
    return "k:@" + digest


def layout(survey: Survey, user_id: str) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    if survey.stage(user_id) == "consent":
        return [[(label, action) for label, action in row] for row in [
            [("Согласен, продолжим", "c:y")],
            [("Прочитать полный текст", "c:full")],
            [("Не согласен", "c:n")],
            [("Просто почитать", "map")],
        ]]
    if survey.reading(user_id): rows.append([("Полный текст согласия", "c:full")])
    spot = survey.current(user_id)
    if spot is None:
        # Анкета пройдена. Кнопка тем стоит первой: без неё человек
        # должен угадать слово «спросить», а он его ниоткуда не знает —
        # и остаётся один на один с ботом, который «ничего не умеет».
        # «Продолжить анкету» здесь врало: продолжать нечего, действие
        # начинает её заново.
        rows.append([("Спросить о другом", "map")])
        if survey.есть_что_продолжить(user_id):
            rows.append([("Продолжить подробную анкету", "n")])
        rows.append([("Мои ответы", "m"), ("Заполнить заново", "r")]); return rows
    step, question = spot
    if question["kind"] != "choice": return rows
    options: list[str] = question["options"]; multi = bool(question.get("multi"))
    picked = survey.picked(user_id, step) if multi else []; nothing = Survey.none_index(question) if multi else None
    shown = [i for i in range(len(options)) if i != nothing]; row: list[tuple[str, str]] = []; per_row = _columns([options[i] for i in shown], multi)
    for index in shown:
        row.append((fits((MARK_ON if multi and index in picked else "") + options[index]), f"t:{step}:{index}" if multi else f"a:{step}:{index}"))
        if len(row) == per_row: rows.append(row); row = []
    if row: rows.append(row)
    bottom: list[tuple[str, str]] = []
    if (_user_state(survey, user_id).get("history") or []): bottom.append(("← Назад", "b"))
    if multi and picked: bottom.append((f"Готово · {len(picked)}", f"d:{step}"))
    elif multi and nothing is not None: bottom.append((options[nothing], f"a:{step}:{nothing}"))
    elif not question.get("required", True): bottom.append(("Пропустить", f"s:{step}"))
    elif multi: bottom.append(("Готово", f"d:{step}"))
    if len(bottom) == 2 and max(_width(label) for label, _ in bottom) > TWO_COLUMNS_AT: rows.extend([button] for button in bottom)
    elif bottom: rows.append(bottom)
    return rows


def questionnaire_keyboard(survey: Survey, user_id: str) -> list[list[list[str]]]:
    return [[[str(label), str(action)] for label, action in row] for row in layout(survey, user_id)]


def navigation_keyboard(action: str, args: list[str], survey: Survey | None, user_id: str) -> list[list[list[str]]]:
    rows: list[list[list[str]]] = []
    if action == "map":
        for branch in knowledge.карта(): rows.append([[fits(branch["название"]), "v:" + branch["id"] + ":1"]])
        if _in_questionnaire(survey, user_id): rows.append([[К_АНКЕТЕ, "q"]])
        return rows
    if action == "v" and args:
        number = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1; page = knowledge.страница(args[0], number)
        if not page: return [[[ВСЕ_ТЕМЫ, "map"]]]
        for title in page["статьи"]: rows.append([[fits(knowledge.подпись(title)), _article_callback(title)]])
        nav: list[list[str]] = []
        if page["номер"] > 1: nav.append([НАЗАД, f"v:{page['id']}:{page['номер'] - 1}"])
        if page["номер"] < page["всего"]: nav.append([ДАЛЬШЕ, f"v:{page['id']}:{page['номер'] + 1}"])
        if nav: rows.append(nav)
        rows.append([[ВСЕ_ТЕМЫ, "map"]])
        if _in_questionnaire(survey, user_id): rows.append([[К_АНКЕТЕ, "q"]])
        return rows
    if action == "k" and args:
        title = args[0]
        for neighbor in knowledge.соседи(title, сколько=3): rows.append([[fits(knowledge.подпись(neighbor)), _article_callback(neighbor)]])
        branch = knowledge.ветвь(knowledge.где(title) or ""); bottom: list[list[str]] = []
        if branch: bottom.append(["‹ " + branch["кратко"], f"v:{branch['id']}:1"])
        bottom.append([ВСЕ_ТЕМЫ, "map"]); rows.append(bottom)
        if _in_questionnaire(survey, user_id): rows.append([[К_АНКЕТЕ, "q"]])
        return rows
    if action == "q" and survey is not None: return questionnaire_keyboard(survey, user_id)
    return rows


def map_screen(survey: Survey | None = None, user_id: str = "") -> tuple[str, list[list[list[str]]]]:
    return КАРТА_ЗАГОЛОВОК + "\n\n" + КАРТА_ПОДПИСЬ, navigation_keyboard("map", [], survey, user_id)


def branch_screen(branch_id: str, page_number: int = 1, survey: Survey | None = None, user_id: str = "") -> tuple[str, list[list[list[str]]]] | None:
    page = knowledge.страница(branch_id, page_number)
    if not page: return None
    last = page["первая"] + len(page["статьи"]) - 1; header = page["название"]
    if page["всего"] > 1: header += f"\n\nТемы {page['первая']}–{last} из {page['статей']}"
    return header, navigation_keyboard("v", [branch_id, str(page_number)], survey, user_id)


def article_screen(title: str, survey: Survey | None = None, user_id: str = "") -> tuple[str, list[list[list[str]]]] | None:
    text = knowledge.статья_целиком(title)
    if not text: return None
    breadcrumbs = knowledge.путь(title)
    if breadcrumbs: text = breadcrumbs.rstrip(" ·") + "\n\n" + text
    return text + "\n\n" + СПРАВКА_ПОДПИСЬ, navigation_keyboard("k", [title], survey, user_id)


def files_of(body: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in (getattr(body, "attachments", None) or []):
        kind = getattr(item, "type", None); kind = getattr(kind, "value", kind)
        if kind in ("inline_keyboard", "reply_keyboard"): continue
        payload = getattr(item, "payload", None)
        found.append({"kind": str(kind or "file"), "name": getattr(item, "filename", None) or "", "url": getattr(payload, "url", None) or "", "size": getattr(item, "size", None) or 0})
    return found

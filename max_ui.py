#!/usr/bin/env python3
"""Transport-independent MAX UI/scenario helpers used by production."""
from __future__ import annotations
from typing import Any
import knowledge
from chatbot_survey import Survey
ASK_WORDS = {"спросить", "хочу спросить", "у меня вопрос", "вопрос", "задать вопрос", "что спросить", "что можно спросить", "о чём можно спросить", "о чем можно спросить", "не знаю что спросить", "не знаю, что спросить", "темы", "покажи темы", "список тем", "меню", "подсказки", "подскажи", "подскажите", "справка", "справочник", "информация", "инфо", "что ты умеешь", "что умеешь", "чем поможешь", "чем ты поможешь", "не знаю с чего начать", "с чего начать", "с чего начинать", "/ask", "/faq", "/menu", "/topics"}
BUTTON_LIMIT = 64
TWO_COLUMNS_AT = 17.5
TWO_COLUMNS_AT_MULTI = 15.0
WIDE = set("шщмжюыфШЩМЖЮЫФ")
NARROW = set(" ьъiljt.,'!:;()-—·")
MARK_ON = "✅ "
FILES_TAKEN = "Файл получил, приложу к вашему обращению."
СПРАВКА_ПОДПИСЬ = "———\nЭто выдержка из материалов службы. Координатор ответит подробнее при звонке, а если человеку плохо сейчас — звоните 103."
ПОДСКАЗОК = 4
ЕЩЁ_СПРАШИВАЮТ = "Ещё об этом спрашивают:"
КАРТА_ЗАГОЛОВОК = "О чём рассказать?"
КАРТА_ПОДПИСЬ = "Выберите, что ближе. Или просто напишите вопрос своими словами — я поищу по всем материалам."
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

def layout(survey: Survey, user_id: str) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    if survey.reading(user_id): rows.append([("Полный текст согласия", "c:full")])
    if survey.stage(user_id) == "consent":
        rows.extend([[("Согласен, продолжим", "c:y")], [("Прочитать полностью", "c:full")], [("Не согласен", "c:n")], [("Просто почитать", "map")]])
        return rows
    spot = survey.current(user_id)
    if spot is None:
        rows.append([("Мои ответы", "m"), ("Заполнить заново", "n")]); return rows
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
    if (survey.state.get(user_id) or {}).get("history"): bottom.append(("← Назад", "b"))
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
        for title in page["статьи"]: rows.append([[fits(knowledge.подпись(title)), "k:" + title[:60]]])
        nav: list[list[str]] = []
        if page["номер"] > 1: nav.append([НАЗАД, f"v:{page['id']}:{page['номер'] - 1}"])
        if page["номер"] < page["всего"]: nav.append([ДАЛЬШЕ, f"v:{page['id']}:{page['номер'] + 1}"])
        if nav: rows.append(nav)
        rows.append([[ВСЕ_ТЕМЫ, "map"]])
        if _in_questionnaire(survey, user_id): rows.append([[К_АНКЕТЕ, "q"]])
        return rows
    if action == "k" and args:
        title = args[0]
        for neighbor in knowledge.соседи(title, сколько=3): rows.append([[fits(knowledge.подпись(neighbor)), "k:" + neighbor[:60]]])
        branch = knowledge.ветвь(knowledge.где(title) or ""); bottom: list[list[str]] = []
        if branch: bottom.append(["‹ " + branch["кратко"], f"v:{branch['id']}:1"])
        bottom.append([ВСЕ_ТЕМЫ, "map"]); rows.append(bottom)
        if _in_questionnaire(survey, user_id): rows.append([[К_АНКЕТЕ, "q"]])
        return rows
    if action == "q" and survey is not None: return questionnaire_keyboard(survey, user_id)
    return rows

def map_screen(survey: Survey | None = None, user_id: str = "") -> tuple[str, list[list[list[str]]]]:
    return KАРТА_ЗАГОЛОВОК + "\n\n" + КАРТА_ПОДПИСЬ, navigation_keyboard("map", [], survey, user_id)

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

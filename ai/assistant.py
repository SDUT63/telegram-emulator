# -*- coding: utf-8 -*-
"""Сценарии, в которых модель приносит пользу, и границы, за которые она не заходит.

Главное правило проекта: клинические решения принимаются правилами и людьми,
а не вероятностной моделью. Модель формулирует, обобщает и подсказывает.
Она не назначает маршрут, не оценивает состояние и не отменяет стоп-сигналы.

Отвечает модель не «из головы», а по базе знаний службы: knowledge.py находит
подходящие статьи, они уходят в запрос, и системная подсказка требует
отвечать только по ним. Нет статьи — нет ответа, и человек слышит честное
«это уточнит координатор» вместо правдоподобной выдумки.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys

from . import budget, deident, provider

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import knowledge                                            # noqa: E402

log = logging.getLogger("сдут-ии")

# ------------------------------------------------------------------ границы
FORBIDDEN = {
    "ставить диагноз",
    "оценивать тяжесть состояния",
    "решать, вызывать ли скорую",
    "назначать маршрут М1–М5",
    "отменять или смягчать стоп-сигналы",
    "давать дозировки лекарств",
}

GUARD = (
    "Ты помощник службы долговременного ухода в Тольятти. "
    "Ты НЕ ставишь диагнозов, НЕ оцениваешь тяжесть состояния, "
    "НЕ решаешь вопрос о вызове скорой и НЕ назначаешь маршрут помощи. "
    "Если вопрос требует врача — так и скажи и назови, к кому обратиться. "
    "При признаках угрозы жизни первым делом называй номер 103. "
    "Отвечай коротко, простым языком, на «вы». Не выдумывай факты: "
    "если чего-то не знаешь, скажи, что это уточнит координатор."
)

# --------------------------------------------------------------- кэш ответов
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "ai_cache.json")


def _cache_key(*parts) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _cache_get(key):
    try:
        with open(CACHE_FILE, encoding="utf-8") as fh:
            return json.load(fh).get(key)
    except (OSError, json.JSONDecodeError):
        return None


def _cache_put(key, value):
    try:
        data = {}
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
        data[key] = value
        if len(data) > 3000:                       # не растим файл бесконечно
            data = dict(list(data.items())[-2000:])
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass


def _ask(system: str, user: str, *, max_tokens=600, cache=False,
         личное: str | None = None) -> str | None:
    """Общая обёртка: бюджет, деперсонализация, кэш, отказоустойчивость.

    `личное` — та часть запроса, которая пришла от человека и обязана
    быть очищена. Проверяем именно её, а не весь запрос: в наших
    материалах законно стоят даты приказов, и принимать «14.04.2025»
    за дату рождения — значит запретить справку по закону.

    Возвращает None, если модель не ответила, — вызывающий код обязан это
    пережить. Бот работает без модели, это не аварийный режим.
    """
    if not budget.allowed():
        log.warning("бюджет на модель исчерпан: %s", budget.report())
        return None

    # Падаем, а не отправляем
    deident.assert_clean(user if личное is None else личное)

    key = _cache_key(system, user)
    if cache:
        got = _cache_get(key)
        if got:
            return got

    p = provider.get()
    r = p.ask(system, user, max_tokens=max_tokens)
    if not r.ok or not r.text.strip():
        return None
    budget.add(r.cost_rub, r.tokens_in, r.tokens_out)
    if cache:
        _cache_put(key, r.text)
    return r.text


# ============================================================ сценарий 1
ОТКАЗ = (
    "Такого в материалах службы нет. Этот вопрос лучше задать координатору — "
    "он ответит при звонке."
)


def reference(question: str, knowledge_text: str | None = None) -> str | None:
    """Справочный помощник: ответ на вопрос по базе знаний службы.

    Персональных данных в вопросе быть не должно; на всякий случай текст
    всё равно чистится. Материалы подбирает knowledge.py, если их
    не передали явно.
    """
    if knowledge_text is None:
        статьи = knowledge.найти(question, сколько=3)
        if not статьи:
            return None                    # без материалов не отвечаем вовсе
        knowledge_text = knowledge.выдержка(статьи)
    if not knowledge_text.strip():
        return None

    system = GUARD + (
        " Отвечай ТОЛЬКО на основании приведённых ниже материалов службы. "
        "Если в них ответа нет — скажи ровно так: «" + ОТКАЗ + "» "
        "и ничего не добавляй. Не ссылайся на законы, которых нет "
        "в материалах, и не называй цифр, которых там нет.")
    спрошено = deident.clean(question)
    user = f"Материалы службы:\n{knowledge_text}\n\nВопрос человека: {спрошено}"
    return _ask(system, user, max_tokens=450, cache=True, личное=спрошено)


# ============================================================ сценарий 2
def draft_card(answers: dict, names: tuple = ()) -> str | None:
    """Черновик карточки для координатора: ответы анкеты → связный текст.

    Содержит сведения о здоровье. Только российский провайдер.
    Имя и телефон не отправляются вообще — координатор видит их в CRM.
    """
    if provider.get().name == "deepseek":
        log.error("Черновик карточки содержит сведения о здоровье. "
                  "Зарубежный провайдер для этого сценария запрещён.")
        return None
    safe = deident.safe_answers(answers, names=names)
    system = GUARD + (
        " Собери из ответов анкеты короткое описание ситуации для координатора: "
        "что с человеком, что уже есть дома, кто ухаживает, на что обратить "
        "внимание при звонке. Пять-семь предложений. Не добавляй того, "
        "чего нет в ответах.")
    user = "Ответы анкеты:\n" + "\n".join(f"{k}: {v}" for k, v in safe.items() if v)
    return _ask(system, user, max_tokens=500)


# ============================================================ сценарий 3
def read_free_text(text: str, names: tuple = ()) -> dict | None:
    """Разбор свободного ответа «Что ещё важно знать».

    Возвращает признаки, которые координатор проверит глазами.
    Никакой автоматики поверх этого результата не строится.
    """
    if provider.get().name == "deepseek":
        log.error("Свободный текст может содержать персональные данные. "
                  "Зарубежный провайдер запрещён.")
        return None
    чистый = deident.clean(text, *names)
    system = GUARD + (
        " Из текста родственника выдели признаки в формате JSON с ключами: "
        "urgent (булево — есть ли указание на резкое ухудшение), "
        "topics (список коротких тем), "
        "needs_doctor (булево), quote (одна фраза из текста, самая важная). "
        "Только JSON, без пояснений.")
    got = _ask(system, f"Текст: {чистый}", max_tokens=300)
    if not got:
        return None
    try:
        return json.loads(got[got.index("{"):got.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        log.warning("модель вернула не JSON, разбор пропущен")
        return None


# ============================================================ сценарий 4
def draft_message(purpose: str, context: str) -> str | None:
    """Черновик сообщения семье от координатора. Отправляет человек, не бот."""
    system = GUARD + (
        " Напиши короткое доброжелательное сообщение от координатора службы. "
        "Без канцелярита, на «вы», три-четыре предложения. "
        "Не обещай сроков и услуг, которых нет в задании.")
    return _ask(system,
                f"Задача: {purpose}\nЧто известно: {deident.clean(context)}",
                max_tokens=300)


# ------------------------------------------------------------- состояние
def on() -> bool:
    """Включена ли модель прямо сейчас: есть ключ и не исчерпан бюджет."""
    готов, _ = provider.get().ready()
    return готов and budget.allowed()


def status() -> str:
    p = provider.get()
    готов, почему = p.ready()
    return (f"Провайдер: {p.price['name']}, {почему}. "
            f"Статей в базе: {len(knowledge.загрузить())}. " + budget.report())

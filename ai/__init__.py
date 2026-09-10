# -*- coding: utf-8 -*-
"""Языковая модель для чат-бота СДУТ.

Всё, что связано с моделью, живёт здесь и включается одной переменной
окружения. По умолчанию модуль выключен: бот работает ровно так же,
как работал до его появления.

    AI_PROVIDER=yandex-lite        # yandex | yandex-lite | gigachat | deepseek | off
    YANDEX_API_KEY=...
    YANDEX_FOLDER_ID=...
    AI_MONTHLY_LIMIT_RUB=1500

Ключи только в переменных окружения. В репозиторий они не попадают
никогда — файл с ключами внесён в .gitignore.

Отвечает модель по базе знаний службы (knowledge.py), а не «из головы».
Границы — в ai/assistant.py: диагнозов не ставит, тяжести не оценивает,
маршрут не назначает, стоп-сигналы не трогает.
"""
from . import assistant, budget, deident, provider     # noqa: F401

__all__ = ["assistant", "budget", "deident", "provider"]

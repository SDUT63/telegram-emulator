#!/usr/bin/env python3
"""Production configuration helpers shared by MAX entrypoints.

This module deliberately contains no MAX SDK imports and no network or
application-dispatch logic. It is the single owner of the bot token lookup
and the command menu used by production launchers.
"""
from __future__ import annotations

import os
from pathlib import Path

TOKEN_FILE = "token.txt"

COMMANDS = [
    ("start", "Начать анкету"),
    ("ask", "Все темы: уход, документы, помощь"),
    ("answers", "Показать, что уже заполнено"),
    ("help", "Что можно написать боту"),
    ("cancel", "Прервать анкету"),
]


def read_token(base_dir: Path | None = None) -> str:
    """Return MAX bot token from env or the first active line in token.txt.

    Environment configuration has precedence. Missing configuration raises
    SystemExit with an operator-facing message instead of returning an empty
    credential to the MAX SDK.
    """
    token = (os.getenv("MAX_BOT_TOKEN") or "").strip()
    if token:
        return token

    here = base_dir or Path(__file__).resolve().parent
    path = here / TOKEN_FILE
    if path.exists():
        with path.open(encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line

    raise SystemExit(
        "Не найден токен бота. Создайте token.txt рядом с программой "
        "или задайте MAX_BOT_TOKEN."
    )

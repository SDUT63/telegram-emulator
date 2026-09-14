#!/usr/bin/env python3
"""Запуск ноутбучного пилота MAX-бота на SQLite."""
from __future__ import annotations

import asyncio

from transparent_max_pilot import main as run_bot

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Ctrl+C — это штатная остановка, а не сбой. Сообщение печатает
        # launcher.py: если печатать и здесь, человек видит его дважды.
        pass

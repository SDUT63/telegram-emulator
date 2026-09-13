#!/usr/bin/env python3
"""Запуск локального SQLite-пилота MAX-бота."""
from __future__ import annotations
import asyncio
from max_laptop_pilot_v2 import main
if __name__ == "__main__":
    asyncio.run(main())

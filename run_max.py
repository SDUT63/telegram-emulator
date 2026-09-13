#!/usr/bin/env python3
from __future__ import annotations
import asyncio
from transparent_max_pilot import main as run_bot

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass

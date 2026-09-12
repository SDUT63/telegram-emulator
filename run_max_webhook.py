#!/usr/bin/env python3
"""Production MAX webhook launcher using the same durable survey store."""
from __future__ import annotations

import asyncio

import max_webhook
from storage_sqlite import SQLiteSurvey


def main() -> None:
    max_webhook.Survey = SQLiteSurvey
    asyncio.run(max_webhook.main())


if __name__ == "__main__":
    main()

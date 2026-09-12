#!/usr/bin/env python3
"""Production MAX webhook launcher."""
from __future__ import annotations

import asyncio

import max_webhook
from scripts.check_outbound_paths import require_clean


def main() -> None:
    # Keep the same fail-closed outbound invariant as the long-polling entrypoint.
    require_clean()
    asyncio.run(max_webhook.main())


if __name__ == "__main__":
    main()

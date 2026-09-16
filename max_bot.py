"""Deprecated compatibility entrypoint.

Production MAX runs through max_webhook.py/run_max_postgres.py. This module
exists only so older local tooling can import the legacy emulator without
changing the production transport boundary.

A star import skips underscore-prefixed names, so the legacy module object is
re-exported under this name instead: callers that reach for internal helpers
(button width/fit checks) keep working against the single legacy definition.
"""
import sys

import legacy_max_bot
from legacy_max_bot import *  # noqa: F401,F403

sys.modules[__name__].__dict__.update(
    {name: value for name, value in vars(legacy_max_bot).items() if name.startswith("_") and not name.startswith("__")}
)

"""Deprecated compatibility entrypoint.

Production MAX runs through max_webhook.py/run_max_postgres.py. This module
exists only so older local tooling can import the legacy emulator without
changing the production transport boundary.
"""
from legacy_max_bot import *  # noqa: F401,F403

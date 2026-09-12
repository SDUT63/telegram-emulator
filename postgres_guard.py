#!/usr/bin/env python3
"""Fail-fast guard for deployments that require explicit PostgreSQL migrations."""
from __future__ import annotations

import os

import psycopg

REQUIRED_MIGRATIONS = {
    "001_initial.sql",
    "002_migration_meta.sql",
    "003_processed_events.sql",
    "004_outbox_messages.sql",
}


def require_migrations(db_url: str | None = None) -> None:
    """Require all production migrations before the application starts.

    The runtime storage adapters still keep idempotent CREATE TABLE statements
    for backwards compatibility with tests/pilots. Production entrypoints
    call this guard first, so an un-migrated production database fails closed.
    """
    if (os.getenv("SDUT_REQUIRE_MIGRATIONS") or "").strip() != "1":
        return

    dsn = (db_url or os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        raise RuntimeError("SDUT_DATABASE_URL is required when migrations are enforced")

    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    applied = {str(row[0]) for row in rows}
    missing = sorted(REQUIRED_MIGRATIONS - applied)
    if missing:
        raise RuntimeError(
            "PostgreSQL schema is not ready; apply migrations first: "
            + ", ".join(missing)
        )

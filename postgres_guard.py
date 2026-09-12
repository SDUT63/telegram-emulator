#!/usr/bin/env python3
"""Fail-fast guard for deployments that require explicit PostgreSQL migrations."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations" / "postgres"
REQUIRED_MIGRATIONS = {
    "001_initial.sql",
    "002_migration_meta.sql",
    "003_processed_events.sql",
    "004_outbox_messages.sql",
}


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_migrations(db_url: str | None = None) -> None:
    """Require all production migrations and reject known schema drift."""
    if (os.getenv("SDUT_REQUIRE_MIGRATIONS") or "").strip() != "1":
        return

    dsn = (db_url or os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        raise RuntimeError("SDUT_DATABASE_URL is required when migrations are enforced")

    try:
        with psycopg.connect(dsn) as conn:
            rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
            applied = {str(row[0]) for row in rows}
            missing = sorted(REQUIRED_MIGRATIONS - applied)
            if missing:
                raise RuntimeError(
                    "PostgreSQL schema is not ready; apply migrations first: "
                    + ", ".join(missing)
                )

            checksum_rows = conn.execute(
                "SELECT version,sha256 FROM schema_migration_checksums"
            ).fetchall()
            checksums = {str(row[0]): str(row[1]) for row in checksum_rows}
    except RuntimeError:
        raise
    except psycopg.errors.UndefinedTable as exc:
        raise RuntimeError(
            "PostgreSQL migration metadata is missing; run scripts/migrate_postgres.py first"
        ) from exc

    for version in sorted(REQUIRED_MIGRATIONS):
        path = MIGRATIONS / version
        if not path.is_file():
            raise RuntimeError(f"Required migration file is missing: {version}")
        expected = _fingerprint(path)
        actual = checksums.get(version)
        if actual is None:
            raise RuntimeError(
                f"Migration {version} has no recorded SHA-256; "
                "run scripts/migrate_postgres.py before production start"
            )
        if actual != expected:
            raise RuntimeError(
                f"Migration {version} was modified after application: "
                f"database={actual}, file={expected}. Create a new migration instead."
            )

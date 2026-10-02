#!/usr/bin/env python3
"""Apply ordered SQL migrations to SDUT_DATABASE_URL.

Usage:
    SDUT_DATABASE_URL=postgresql://... python scripts/migrate_postgres.py

The runner records each filename in schema_migrations and a SHA-256
fingerprint of the exact SQL file. Re-running the command is safe; changing a
migration that was already applied fails loudly instead of silently hiding
schema drift. Migrations are applied in one transaction per file and the
runner never drops or rewrites application data by itself.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations" / "postgres"


def dsn() -> str:
    value = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit("SDUT_DATABASE_URL не задан")
    return value


def fingerprint(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def main() -> None:
    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        raise SystemExit(f"Миграции не найдены: {MIGRATIONS}")

    with psycopg.connect(dsn()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migration_checksums (
                version TEXT PRIMARY KEY REFERENCES schema_migrations(version) ON DELETE CASCADE,
                sha256 TEXT NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {
            row[0]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        checksums = {
            row[0]: row[1]
            for row in conn.execute("SELECT version,sha256 FROM schema_migration_checksums").fetchall()
        }

        for path in files:
            version = path.name
            sql = path.read_text(encoding="utf-8")
            digest = fingerprint(sql)

            if version in applied:
                previous = checksums.get(version)
                if previous is None:
                    # Existing installations predate checksum tracking. Record
                    # the current deployed file once; subsequent modifications
                    # are then detected deterministically.
                    conn.execute(
                        "INSERT INTO schema_migration_checksums(version,sha256) VALUES(%s,%s) ON CONFLICT(version) DO NOTHING",
                        (version, digest),
                    )
                    continue
                if str(previous) != digest:
                    raise SystemExit(
                        f"Миграция {version} уже применена с другим SHA-256: "
                        f"database={previous}, file={digest}. Создайте новую миграцию вместо изменения старой."
                    )
                continue

            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES(%s)",
                    (version,),
                )
                conn.execute(
                    "INSERT INTO schema_migration_checksums(version,sha256) VALUES(%s,%s)",
                    (version, digest),
                )
            print(f"APPLIED {version}")


if __name__ == "__main__":
    main()

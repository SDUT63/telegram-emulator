#!/usr/bin/env python3
"""Apply ordered SQL migrations to SDUT_DATABASE_URL.

Usage:
    SDUT_DATABASE_URL=postgresql://... python scripts/migrate_postgres.py

The runner records each filename in schema_migrations and refuses to silently
skip an already-recorded migration. Migrations are applied in one transaction
per file. It never drops or rewrites application data by itself.
"""
from __future__ import annotations

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
        applied = {
            row[0]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        for path in files:
            version = path.name
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES(%s)",
                    (version,),
                )
            print(f"APPLIED {version}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from postgres_guard import require_migrations

pytestmark = pytest.mark.skipif(
    not os.getenv("SDUT_DATABASE_URL"),
    reason="SDUT_DATABASE_URL is required for PostgreSQL integration tests",
)


def test_migration_guard_rejects_modified_applied_migration(monkeypatch):
    import psycopg

    dsn = os.environ["SDUT_DATABASE_URL"]
    monkeypatch.setenv("SDUT_REQUIRE_MIGRATIONS", "1")
    path = Path(__file__).resolve().parents[1] / "migrations" / "postgres" / "001_initial.sql"
    expected = hashlib.sha256(path.read_bytes()).hexdigest()

    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT sha256 FROM schema_migration_checksums WHERE version='001_initial.sql'"
        ).fetchone()
        assert row is not None
        original = row[0]
        conn.execute(
            "UPDATE schema_migration_checksums SET sha256='tampered' WHERE version='001_initial.sql'"
        )

    try:
        with pytest.raises(RuntimeError, match="modified after application"):
            require_migrations(dsn)
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "UPDATE schema_migration_checksums SET sha256=%s WHERE version='001_initial.sql'",
                (original or expected,),
            )

#!/usr/bin/env python3
"""Purge privacy coordination records after the configured retention window.

The deletion tables intentionally contain only technical replay/coordination
metadata. They are still subject to retention and must not grow forever.
Run this job from a scheduler (cron/systemd/Kubernetes CronJob), not from the
web request path.
"""
from __future__ import annotations

import os

import psycopg


def _dsn() -> str:
    value = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit("SDUT_DATABASE_URL is required")
    return value


def _days(name: str, default: int, minimum: int = 1, maximum: int = 3650) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise SystemExit(f"{name} must be between {minimum} and {maximum}")
    return value


def main() -> None:
    # Keep replay tombstones longer than deleted-user coordination records.
    deleted_user_days = _days("SDUT_DELETED_USER_RETENTION_DAYS", 30)
    event_days = _days("SDUT_DELETED_EVENT_TOMBSTONE_RETENTION_DAYS", 30)
    with psycopg.connect(_dsn()) as conn:
        users = conn.execute(
            "DELETE FROM deleted_users WHERE deleted_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')",
            (deleted_user_days,),
        ).rowcount
        events = conn.execute(
            "DELETE FROM deleted_event_tombstones WHERE deleted_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')",
            (event_days,),
        ).rowcount
    print(f"deleted_users={users} deleted_event_tombstones={events}")


if __name__ == "__main__":
    main()

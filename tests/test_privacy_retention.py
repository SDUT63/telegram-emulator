"""Координационные записи удаления тоже подлежат хранению не вечно.

В deleted_users и deleted_event_tombstones нет ответов и контактов, но есть
идентификаторы. Без ретенции они копятся годами — это уже хранение данных
о людях, которые просили их стереть.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest


@pytest.fixture()
def postgres_dsn():
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")
    return dsn


def _run_purge(env_overrides: dict[str, str] | None = None) -> str:
    env = dict(os.environ)
    env.update(env_overrides or {})
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, os.path.join(root, "ops", "purge_privacy.py")],
        capture_output=True, text=True, env=env, cwd=root, check=True,
    )
    return result.stdout


def test_purge_removes_expired_records_and_keeps_recent_ones(postgres_dsn):
    import psycopg

    old_user = f"retention-old-{uuid.uuid4().hex[:12]}"
    fresh_user = f"retention-fresh-{uuid.uuid4().hex[:12]}"
    old_event = f"retention-event-old-{uuid.uuid4().hex}"
    fresh_event = f"retention-event-fresh-{uuid.uuid4().hex}"

    with psycopg.connect(postgres_dsn) as conn:
        conn.execute(
            "INSERT INTO deleted_users(user_id,deleted_at) VALUES (%s, CURRENT_TIMESTAMP - INTERVAL '90 days'), (%s, CURRENT_TIMESTAMP)",
            (old_user, fresh_user),
        )
        conn.execute(
            "INSERT INTO deleted_event_tombstones(event_id,event_type,event_hash,deleted_at) "
            "VALUES (%s,'message','h', CURRENT_TIMESTAMP - INTERVAL '90 days'), (%s,'message','h', CURRENT_TIMESTAMP)",
            (old_event, fresh_event),
        )
        conn.commit()

    try:
        _run_purge({"SDUT_DELETED_USER_RETENTION_DAYS": "30", "SDUT_DELETED_EVENT_TOMBSTONE_RETENTION_DAYS": "30"})

        with psycopg.connect(postgres_dsn) as conn:
            assert conn.execute("SELECT 1 FROM deleted_users WHERE user_id=%s", (old_user,)).fetchone() is None
            assert conn.execute("SELECT 1 FROM deleted_users WHERE user_id=%s", (fresh_user,)).fetchone() is not None
            assert conn.execute("SELECT 1 FROM deleted_event_tombstones WHERE event_id=%s", (old_event,)).fetchone() is None
            assert conn.execute("SELECT 1 FROM deleted_event_tombstones WHERE event_id=%s", (fresh_event,)).fetchone() is not None
    finally:
        with psycopg.connect(postgres_dsn) as conn:
            conn.execute("DELETE FROM deleted_users WHERE user_id = ANY(%s)", ([old_user, fresh_user],))
            conn.execute("DELETE FROM deleted_event_tombstones WHERE event_id = ANY(%s)", ([old_event, fresh_event],))
            conn.commit()


def test_purge_refuses_nonsense_retention(postgres_dsn):
    env = dict(os.environ)
    env["SDUT_DELETED_USER_RETENTION_DAYS"] = "0"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, os.path.join(root, "ops", "purge_privacy.py")],
        capture_output=True, text=True, env=env, cwd=root,
    )
    assert result.returncode != 0
    assert "SDUT_DELETED_USER_RETENTION_DAYS" in (result.stderr + result.stdout)

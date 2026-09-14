"""Общая обвязка тестов.

Каждый тест получает свежую анкету во временной папке: состояние
хранится в файлах, и без изоляции тесты видели бы ответы друг друга.
"""
import os
import sys

import pytest

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ЗДЕСЬ))   # код проекта
sys.path.insert(0, ЗДЕСЬ)                    # walk.py — общий проход по анкете


@pytest.fixture
def clean_outbox():
    """Пустая очередь на входе в тест.

    `claim` выбирает строки по возрастанию id, поэтому чужие остатки
    «голодают» строку теста: на свежей БД в CI это незаметно, на
    переиспользуемой — тест падает по порядку запуска, а не по существу.
    """
    dsn = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not dsn:
        pytest.skip("SDUT_DATABASE_URL is not configured")

    import psycopg

    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM outbox_messages")
        conn.execute("DELETE FROM outbox_delivery_tombstones")
        conn.commit()
    return dsn


@pytest.fixture
def survey(tmp_path, monkeypatch):
    from chatbot_survey import Survey

    monkeypatch.chdir(tmp_path)
    return Survey(storage_path=str(tmp_path / "responses.json"), list_options=False)


@pytest.fixture
def consented(survey):
    """Анкета, где согласие уже дано: для тестов про сами вопросы."""
    survey.handle("u1", "здравствуйте")
    survey.grant_consent("u1")
    return survey

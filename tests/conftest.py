"""Общая обвязка тестов.

Каждый тест получает свежую анкету во временной папке: состояние
хранится в файлах, и без изоляции тесты видели бы ответы друг друга.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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

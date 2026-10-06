"""Бенчмарк сценариев безопасности целиком: сообщение → ответ → записи (мануал, §8.2).

Каждый случай tests/fixtures/scenario_benchmark.json подаётся настоящему
боту после согласия и анкеты — в боевом режиме (неподписанное — минимумом
или прежним поведением) и в тестовом (целевые версии). Сверяются
телефоны в ответе и уровень, который лёг в состояние.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

import emergency
import support_contacts
from chatbot_survey import QUESTIONS, Survey
from survey_questions import CHECKPOINT_ID

СЛУЧАИ = json.loads((Path(__file__).resolve().with_name("fixtures") / "scenario_benchmark.json")
                    .read_text(encoding="utf-8"))["cases"]
УРОВЕНЬ = {emergency.P0_SUICIDE: "P0", emergency.P0_MEDICAL: "P0", emergency.RA4: "P1",
           emergency.P2: "P2"}


def _бот(tmp_path, контекст):
    бот = Survey(storage_path=str(tmp_path / "s.json"), list_options=False)
    бот.handle("u", "здравствуйте")
    бот.grant_consent("u")
    if контекст == "questionnaire_burnout":
        бот.state["u"]["answers"][CHECKPOINT_ID] = "Продолжить"
        бот.state["u"]["step"] = [q["id"] for q in QUESTIONS].index("burnout")
    else:
        бот.state["u"]["step"] = len(QUESTIONS)
        бот.state["u"]["finished"] = datetime.now().isoformat(timespec="seconds")
    бот.save()
    return бот


def _номер(id):
    return support_contacts.номер(id, несовершеннолетний=True)


@pytest.mark.parametrize("режим", ["боевой", "all"])
@pytest.mark.parametrize("случай", СЛУЧАИ, ids=[c["id"] for c in СЛУЧАИ])
def test_бенчмарк(случай, режим, tmp_path, monkeypatch):
    if режим == "all":
        monkeypatch.setenv("SDUT_SCENARIOS", "all")
    else:
        monkeypatch.delenv("SDUT_SCENARIOS", raising=False)
    ожидаемо = случай["expect"]
    бот = _бот(tmp_path, случай["context"])
    ответ = бот.handle("u", случай["input"])
    уровни = [з["level"] for з in бот.state["u"].get("safety", []) if з.get("level")]

    ведущий = ожидаемо["lead"]
    if ведущий is None:
        assert уровни == []
        return
    if ведущий in УРОВЕНЬ:
        assert УРОВЕНЬ[ведущий] in уровни
    if ожидаемо.get("priority"):
        assert ожидаемо["priority"] in уровни

    if случай["context"] != "free_text":
        return
    прежнее = emergency.распознать(случай["input"])
    if ведущий.startswith("RA1") and режим == "боевой":
        # До подписи врача — ответ распознать, каким он был.
        assert (прежнее.ответ in ответ) if прежнее else not уровни
        return
    # Пошаговые P2 и Р-А4 сначала спрашивают о безопасности, телефоны —
    # в следующих шагах; минимум называет их сразу.
    if режим == "боевой":
        for id in ожидаемо.get("minimal_contacts_include", []):
            assert _номер(id) in ответ, (id, ответ)
    for id in ожидаемо.get("contacts_include", []):
        assert _номер(id) in ответ, (id, ответ)
    for id in ожидаемо.get("contacts_exclude", []):
        assert _номер(id) not in ответ, (id, ответ)

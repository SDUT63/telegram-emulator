"""Сценарии безопасности в разговоре: анкета, кнопки, режим А (мануал по архиву, §3, §8).

Здесь проверяется то, что принадлежит разговору: когда сценарий
перехватывает сообщение, а когда нет; что ждёт ответа и какие кнопки
на экране; что записано — и что не записано — в состояние человека.
Фразы — пересказ, не цитаты из архива.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

import emergency
import max_laptop_pilot_v2
import max_ui
from chatbot_survey import CONSENT_SHORT, QUESTIONS, Survey


@pytest.fixture()
def все(monkeypatch):
    monkeypatch.setenv("SDUT_SCENARIOS", "all")


@pytest.fixture()
def боевой(monkeypatch):
    monkeypatch.delenv("SDUT_SCENARIOS", raising=False)


def _бот(tmp_path):
    return Survey(storage_path=str(tmp_path / "s.json"), list_options=False)


def _после_анкеты(бот, uid="u"):
    бот.handle(uid, "здравствуйте")
    бот.grant_consent(uid)
    бот.state[uid]["step"] = len(QUESTIONS)
    бот.state[uid]["finished"] = datetime.now().isoformat(timespec="seconds")
    бот.save()
    return uid


def _в_анкете(бот, uid="u"):
    бот.handle(uid, "здравствуйте")
    бот.grant_consent(uid)
    assert бот.current(uid) is not None
    return uid


def _нажать(бот, uid, подпись):
    for текст, действие in бот.кнопки_сценария(uid):
        if текст == подпись:
            _, сценарий, шаг, номер = действие.split(":")
            return бот.ответ_сценарию(uid, сценарий, шаг, int(номер))
    raise AssertionError(f"нет кнопки {подпись!r}: {бот.кнопки_сценария(uid)}")


# ---------------------------------------------------------------- режим А


@pytest.mark.parametrize("фраза", [
    "я на пределе, не справляюсь", "муж просит дать ему умереть", "не хочу больше жить",
])
def test_до_согласия_сценарий_ничего_не_оставляет(все, tmp_path, фраза):
    бот = _бот(tmp_path)
    ответ = бот.handle("u", фраза)
    assert CONSENT_SHORT in ответ
    человек = бот.state["u"]
    assert not человек.get("alerts") and not человек.get("safety")
    assert человек.get("scenario") is None and not человек.get("acked")
    assert бот.кнопки_сценария("u") == []


# ---------------------------------------------------------------- P2


def test_на_пределе_после_анкеты_ведёт_по_шагам(все, tmp_path):
    бот = _бот(tmp_path)
    uid = _после_анкеты(бот)
    ответ = бот.handle(uid, "я на пределе, не справляюсь")
    assert "мысли о том, чтобы не жить" in ответ
    assert [п for п, _ in бот.кнопки_сценария(uid)] == ["Да", "Нет", "Не хочу отвечать"]

    ответ = _нажать(бот, uid, "Нет")
    assert "Что сейчас тяжелее всего" in ответ
    ответ = _нажать(бот, uid, "Уход за близким")
    assert "Помощь на дому" in ответ and "Координатор свяжется" in ответ
    assert бот.кнопки_сценария(uid) == []

    записи = бот.state[uid]["safety"]
    assert {"level": "P2", "kind": "overwhelmed"}.items() <= записи[0].items()
    assert "branch_taken" in [з.get("event") for з in записи]


def test_на_пределе_посреди_анкеты_не_перехватывается(все, tmp_path):
    """Ответ на вопрос анкеты не теряется: о самочувствии спросит анкета."""
    бот = _бот(tmp_path)
    uid = _в_анкете(бот)
    бот.handle(uid, "я на пределе, не справляюсь")
    assert not бот.state[uid].get("safety")
    assert бот.кнопки_сценария(uid) == []


def test_ответ_словами_вместо_кнопки(все, tmp_path):
    бот = _бот(tmp_path)
    uid = _после_анкеты(бот)
    бот.handle(uid, "я на пределе, не справляюсь")
    assert "Что сейчас тяжелее всего" in бот.handle(uid, "нет, таких мыслей нет")


def test_да_словами_поднимает_до_p0(все, tmp_path):
    бот = _бот(tmp_path)
    uid = _после_анкеты(бот)
    бот.handle(uid, "я на пределе, не справляюсь")
    ответ = бот.handle(uid, "да")
    assert "психолог" in ответ
    assert ("P0", "suicide") in [(з.get("level"), з.get("kind")) for з in бот.state[uid]["safety"]]


def test_непонятный_ответ_ведёт_в_безопасную_ветку(все, tmp_path):
    бот = _бот(tmp_path)
    uid = _после_анкеты(бот)
    бот.handle(uid, "я на пределе, не справляюсь")
    ответ = бот.handle(uid, "сама не знаю, что сказать")
    assert "психологу" in ответ
    assert бот.кнопки_сценария(uid) == []


def test_новый_сигнал_важнее_ожидающего_вопроса(все, tmp_path):
    бот = _бот(tmp_path)
    uid = _после_анкеты(бот)
    бот.handle(uid, "я на пределе, не справляюсь")
    ответ = бот.handle(uid, "мама не дышит")
    assert ответ.startswith(emergency.СКОРАЯ)
    assert бот.кнопки_сценария(uid) == []


def test_кнопка_с_прошлого_экрана_ничего_не_делает(все, tmp_path):
    бот = _бот(tmp_path)
    uid = _после_анкеты(бот)
    бот.handle(uid, "я на пределе, не справляюсь")
    assert бот.ответ_сценарию(uid, emergency.P2, "need_q", 0) == ""
    assert бот.ответ_сценарию(uid, emergency.RA4, "safety_q", 0) == ""


def test_боевой_режим_минимум_без_кнопок(боевой, tmp_path):
    бот = _бот(tmp_path)
    uid = _после_анкеты(бот)
    ответ = бот.handle(uid, "я на пределе, не справляюсь")
    assert "Координатор свяжется с вами в рабочее время" in ответ
    assert бот.кнопки_сценария(uid) == []
    assert бот.state[uid]["safety"][0]["level"] == "P2"


@pytest.mark.parametrize("ответ", ["На пределе", "Не справляемся"])
def test_на_пределе_в_анкете_ставит_p2(боевой, tmp_path, ответ):
    """Вопрос анкеты «Как себя чувствует тот, кто ухаживает?» (Р-А2).

    Подпись варианта — ответ на вопрос, а не сигнал: он записывается в
    анкету, а уровень P2 ложится на обращение.
    """
    from survey_questions import CHECKPOINT_ID

    бот = _бот(tmp_path)
    uid = _в_анкете(бот)
    бот.state[uid]["answers"][CHECKPOINT_ID] = "Продолжить"
    бот.state[uid]["step"] = [q["id"] for q in QUESTIONS].index("burnout")
    бот.handle(uid, ответ)
    assert бот.state[uid]["answers"]["burnout"] == ответ
    запись = бот.state[uid]["safety"][0]
    assert (запись["level"], запись["kind"]) == ("P2", "overwhelmed")


def test_спокойный_ответ_в_анкете_не_ставит_p2(боевой, tmp_path):
    from survey_questions import CHECKPOINT_ID

    бот = _бот(tmp_path)
    uid = _в_анкете(бот)
    бот.state[uid]["answers"][CHECKPOINT_ID] = "Продолжить"
    бот.state[uid]["step"] = [q["id"] for q in QUESTIONS].index("burnout")
    бот.handle(uid, "Справляемся")
    assert not бот.state[uid].get("safety")


# ---------------------------------------------------------------- Р-А4


def test_ра4_посреди_анкеты_перехватывает_и_возвращает_к_вопросу(все, tmp_path):
    бот = _бот(tmp_path)
    uid = _в_анкете(бот)
    вопрос = бот.question_text(uid)
    ответ = бот.handle(uid, "муж просит дать ему умереть")
    assert "попытки причинить себе вред" in ответ
    assert вопрос not in ответ                     # сначала сценарий

    ответ = _нажать(бот, uid, "Нет")
    assert "координатору в первую очередь" in ответ
    ответ = _нажать(бот, uid, "Держусь")
    assert ответ.endswith(вопрос)                  # и анкета продолжается
    assert бот.state[uid]["safety"][0]["level"] == "P1"


def test_ра4_боевой_минимум_и_вопрос_анкеты(боевой, tmp_path):
    бот = _бот(tmp_path)
    uid = _в_анкете(бот)
    вопрос = бот.question_text(uid)
    ответ = бот.handle(uid, "муж просит дать ему умереть")
    assert "8-800-700-84-36" in ответ and ответ.endswith(вопрос)


# ---------------------------------------------------------------- кнопки


def test_на_экране_только_кнопки_сценария(все, tmp_path):
    бот = _бот(tmp_path)
    uid = _после_анкеты(бот)
    бот.handle(uid, "я на пределе, не справляюсь")
    class Боевой(Survey):
        """Боевой интерфейс читает состояние только через user_state."""
        def user_state(self, user_id):
            return dict(self.state.get(str(user_id)) or {})

    боевой_бот = Боевой(storage_path=str(tmp_path / "s.json"), list_options=False)
    for экран in (max_ui.layout(боевой_бот, uid), max_laptop_pilot_v2.rows(бот, uid)):
        действия = [д for ряд in экран for _, д in ряд]
        assert действия and all(д.startswith("z:") for д in действия)
        assert all(len(д.encode()) <= 64 for д in действия)


# ---------------------------------------------------------------- тест 3


def test_в_записях_нет_текста_человека(все, tmp_path):
    """Уровни и события — без единого слова из сообщения (мануал, §5.7)."""
    бот = _бот(tmp_path)
    uid = _после_анкеты(бот)
    фраза = "я на пределе, не справляюсь с мамой совсем"
    бот.handle(uid, фраза)
    _нажать(бот, uid, "Нет")
    _нажать(бот, uid, "Хочу, чтобы меня выслушали")
    записи = json.dumps(бот.state[uid]["safety"] + бот.state[uid]["alerts"], ensure_ascii=False)
    for кусок in ("не справляюсь с мамой", "с мамой совсем", фраза):
        assert кусок not in записи


# ---------------------------------------------------------------- тест 10


def test_старое_состояние_без_новых_полей_читается(tmp_path):
    путь = tmp_path / "s.json"
    путь.write_text(json.dumps({"u": {"step": 0, "answers": {}, "alerts": [],
                                      "consent": {"at": "2026-01-01T00:00:00", "version": "1.0"}}},
                               ensure_ascii=False), encoding="utf-8")
    бот = Survey(storage_path=str(путь), list_options=False)
    assert бот.ожидание_сценария("u") is None
    assert бот.кнопки_сценария("u") == []
    бот.handle("u", "привет")                       # и разговор идёт как прежде
    assert бот.state["u"]["safety"] == []

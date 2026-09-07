"""Клинические стоп-сигналы. Здесь ошибка стоит дороже всего остального."""
from survey_questions import QUESTIONS


def дойти_до_признаков(s, u="u1"):
    s.answer_by_numbers(u, [2]); s.handle(u, "Мария")
    s.handle(u, "89171234567"); s.handle(u, "далее")
    return s.current(u)


def test_острое_состояние_отправляет_в_103(consented):
    s = consented
    step, q = дойти_до_признаков(s)
    out = s.answer_by_numbers("u1", [q["options"].index("Тяжело дышит") + 1])
    assert "103" in out
    assert any("Острое состояние" in a for a in s.state["u1"]["alerts"])


def test_четыре_признака_отправляют_в_103_и_112(consented):
    s = consented
    step, q = дойти_до_признаков(s)
    мягкие = ["Боль", "Плохо ест", "Запор", "Недавно упал"]
    номера = [q["options"].index(n) + 1 for n in мягкие]
    out = s.answer_by_numbers("u1", номера)
    assert "103" in out and "112" in out
    assert any("много признаков" in a for a in s.state["u1"]["alerts"])


def test_три_мягких_признака_не_поднимают_тревогу(consented):
    s = consented
    step, q = дойти_до_признаков(s)
    номера = [q["options"].index(n) + 1 for n in ["Боль", "Запор", "Недавно упал"]]
    s.answer_by_numbers("u1", номера)
    assert s.state["u1"]["alerts"] == []


def test_ответы_сохраняются_несмотря_на_предупреждение(consented):
    s = consented
    step, q = дойти_до_признаков(s)
    s.answer_by_numbers("u1", [q["options"].index("Тяжело дышит") + 1])
    assert s.state["u1"]["answers"]["flags"] == "Тяжело дышит"
    assert s.current("u1") is not None, "анкета не должна закрываться"


def test_каждое_правило_ссылается_на_существующий_вариант(consented):
    """Подписи вариантов правились не раз — правила могли отстать."""
    for q in QUESTIONS:
        правила = ([q["alert"]] if q.get("alert") else []) + (q.get("alerts") or [])
        for r in правила:
            for o in r.get("options", ()):
                assert o in q["options"], f"{q['id']}: правило ссылается на «{o}»"


def test_у_каждого_правила_есть_текст_и_метка(consented):
    for q in QUESTIONS:
        правила = ([q["alert"]] if q.get("alert") else []) + (q.get("alerts") or [])
        for r in правила:
            assert r.get("text"), q["id"]
            assert r.get("label"), q["id"]

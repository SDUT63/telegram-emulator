"""Нагрузка, которую даст социальная реклама: много людей разом.

Пока бота знают пять человек, они пишут по очереди. По объявлению
приходят десятки семей одновременно, и вопрос перестаёт быть
теоретическим: не перемешаются ли карточки, не потеряется ли ответ,
не получит ли человек свой ответ дважды.

Проверка идёт настоящими потоками против настоящего PostgreSQL:
блокировки, транзакции и идемпотентность на заглушках не проверяются.
"""
from __future__ import annotations

import os
import threading
import traceback
import uuid

import pytest

from outbox_postgres import delivery_key
from storage_postgres import _TX_EVENT
from test_cold_traffic import ЖИВАЯ_РЕЧЬ

pytestmark = pytest.mark.skipif(
    not os.getenv("SDUT_DATABASE_URL"),
    reason="SDUT_DATABASE_URL is required for PostgreSQL integration tests",
)

ЛЮДЕЙ = 12


@pytest.fixture()
def анкета():
    """Анкета, убирающая за собой.

    Проверка нарочно делает много: двенадцать человек по тридцать три
    вопроса — это больше четырёхсот строк в очереди исходящих. Оставить
    их значит замедлить все последующие тесты и сломать те из них,
    которые осушают очередь целиком. Мусор за нагрузкой убирает тот,
    кто её создал.
    """
    from production_outbox import DurableProductionPostgresSurvey

    анкета = DurableProductionPostgresSurvey()
    следы: list[str] = []
    анкета._следы = следы                      # заполняется тестами ниже
    yield анкета

    if not следы:
        return
    import psycopg

    with psycopg.connect(os.environ["SDUT_DATABASE_URL"]) as conn:
        for таблица in ("outbox_messages", "audit_events", "processed_events",
                        "survey_state"):
            conn.execute(f"DELETE FROM {таблица} WHERE user_id = ANY(%s)", (следы,))
        conn.commit()


def _событие(анкета, eid: str, действие):
    токен = _TX_EVENT.set(eid)
    try:
        return действие()
    finally:
        _TX_EVENT.reset(токен)


def _карточка(user_id: str) -> dict:
    import psycopg

    with psycopg.connect(os.environ["SDUT_DATABASE_URL"]) as conn:
        строка = conn.execute(
            "SELECT state_json FROM survey_state WHERE user_id=%s", (user_id,)
        ).fetchone()
    return (строка[0] if строка else {}) or {}


def _в_несколько_потоков(дела) -> list[str]:
    беды: list[str] = []
    замок = threading.Lock()

    def обёртка(дело):
        try:
            дело()
        except Exception:                                   # pragma: no cover
            with замок:
                беды.append(traceback.format_exc(limit=4))

    нити = [threading.Thread(target=обёртка, args=(д,)) for д in дела]
    for н in нити:
        н.start()
    for н in нити:
        н.join()
    return беды


def test_десятки_людей_одновременно_не_путают_карточки(анкета):
    """Каждый проходит анкету целиком, все в одно и то же время."""
    люди = [f"реклама-{i}-{uuid.uuid4().hex[:8]}" for i in range(ЛЮДЕЙ)]
    анкета._следы.extend(люди)

    def проход(u: str):
        def шаг(текст: str):
            _событие(анкета, f"e-{uuid.uuid4().hex}",
                     lambda: анкета.handle(u, текст))

        шаг("здравствуйте")
        _событие(анкета, f"e-{uuid.uuid4().hex}", lambda: анкета.grant_consent(u))
        for _ in range(60):
            место = анкета.current(u)
            if место is None:
                break
            шаг(ЖИВАЯ_РЕЧЬ[место[1]["id"]])

    беды = _в_несколько_потоков([lambda u=u: проход(u) for u in люди])
    assert беды == [], беды[0]

    # Сверяем с базой, а не с памятью: память могла и соврать.
    ожидание = {
        "who": "О близком человеке", "relation": "Дочь или сын",
        "name": "Ольга Петровна", "phone": "89171234567",
        "age": "75–84 года", "mobility": "Не встаёт", "burnout": "На пределе",
    }
    плохие = []
    for u in люди:
        состояние = _карточка(u)
        ответы = состояние.get("answers") or {}
        расхождения = {к: (ответы.get(к), v) for к, v in ожидание.items()
                       if ответы.get(к) != v}
        if расхождения or not состояние.get("finished"):
            плохие.append((u, расхождения, bool(состояние.get("finished"))))
    assert плохие == [], плохие[:3]


def test_повтор_одного_события_не_отвечает_человеку_дважды(анкета):
    """MAX может доставить одно событие несколько раз — и доставляет.

    Две строки в очереди на одно событие — это два одинаковых сообщения
    человеку, а при ответе на вопрос ещё и два шага анкеты вместо одного.
    """
    u = f"повтор-{uuid.uuid4().hex[:8]}"
    анкета._следы.append(u)
    _событие(анкета, f"e-{uuid.uuid4().hex}", lambda: анкета.handle(u, "привет"))
    _событие(анкета, f"e-{uuid.uuid4().hex}", lambda: анкета.grant_consent(u))

    один = f"e-{uuid.uuid4().hex}"
    беды = _в_несколько_потоков([
        lambda: _событие(анкета, один, lambda: анкета.handle(u, "мама"))
        for _ in range(5)
    ])
    assert беды == [], беды[0]

    import psycopg

    with psycopg.connect(os.environ["SDUT_DATABASE_URL"]) as conn:
        строк = conn.execute(
            "SELECT COUNT(*) FROM outbox_messages WHERE delivery_key=%s",
            (delivery_key(один),),
        ).fetchone()[0]
        событий = conn.execute(
            "SELECT COUNT(*) FROM processed_events WHERE event_id=%s", (один,)
        ).fetchone()[0]

    assert строк == 1, "человек получил бы несколько одинаковых сообщений"
    assert событий == 1
    assert _карточка(u).get("step") == 1, "один ответ сдвинул анкету дважды"


def test_несколько_сообщений_одного_человека_разом_не_ломают_карточку(анкета):
    """Человек дописывает, не дождавшись ответа, — обычное дело.

    Порядок доставки при этом не гарантирован, и требовать от бота
    угадать его нельзя. Требовать можно другого: карточка остаётся
    целой, шаг соответствует записанным ответам, ничего не затёрто.
    """
    u = f"разом-{uuid.uuid4().hex[:8]}"
    анкета._следы.append(u)
    _событие(анкета, f"e-{uuid.uuid4().hex}", lambda: анкета.handle(u, "привет"))
    _событие(анкета, f"e-{uuid.uuid4().hex}", lambda: анкета.grant_consent(u))

    беды = _в_несколько_потоков([
        lambda т=т: _событие(анкета, f"e-{uuid.uuid4().hex}",
                             lambda: анкета.handle(u, т))
        for т in ("мама", "я дочь", "да знает", "Ольга Петровна")
    ])
    assert беды == [], беды[0]

    состояние = _карточка(u)
    ответы = состояние.get("answers") or {}
    from survey_questions import QUESTIONS

    # Шаг и ответы должны сходиться: всё, что записано, стоит до шага.
    до_шага = {в["id"] for в in QUESTIONS[: int(состояние.get("step") or 0)]}
    лишнее = set(ответы) - до_шага - {"district", "lift"}
    assert лишнее == set(), f"записано после текущего шага: {лишнее}"
    assert ответы.get("who") == "О близком человеке"

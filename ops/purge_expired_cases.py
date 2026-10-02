#!/usr/bin/env python3
"""Удаление анкет, у которых истёк срок хранения.

Почему это обязательно
----------------------
Часть 7 статьи 5 ФЗ-152: хранение персональных данных допускается
не дольше, чем этого требует цель обработки. Цель здесь — передать
обращение координатору и организовать помощь. Когда обращение закрыто
и помощь оказана, держать имя, телефон, адрес и сведения о здоровье
больше нечем оправдать.

До этого скрипта удалялось только то, о чём человек попросил сам
(«удалить»), и служебные записи о таких удалениях. Сами анкеты лежали
бессрочно — то есть срок хранения был «вечно», чего закон не допускает.

Срок задаёт служба, а не программа
----------------------------------
`SDUT_CASE_RETENTION_DAYS` не имеет значения по умолчанию, и без него
скрипт ничего не удаляет. Это намеренно: срок хранения — решение
оператора персональных данных, закреплённое в его политике, а не
догадка разработчика. Удалить чужие данные по догадке хуже, чем
не удалить.

Чтобы решение нельзя было просто забыть, `preflight.py` предупреждает,
когда срок не задан.

Чем это отличается от удаления по просьбе человека
--------------------------------------------------
Человек, попросивший стереть данные, отзывает согласие: для него
ставится отметка в `deleted_users`, и запоздавшее сообщение из MAX
не воскресит карточку. Здесь другое — согласие не отзывали, просто
вышел срок. Отметка не ставится: если человек обратится снова, он
должен пройти как новый, а не как заблокированный.

Карточка — это анкета и обращение
---------------------------------
С подключением модели обращений (PR #3) данные человека лежат в двух
местах: разговор и анкета — в `survey_state`, обращение со снимком
согласия, ответами, событиями и задачами — в `persons`, `cases` и
связанных таблицах. Срок по-прежнему один (Р5 не принято), но
«последнее изменение карточки» — это последнее из двух: изменение
анкеты или событие обращения. Иначе координатор, неделю ведущий
обращение человека, который сам больше не писал, потерял бы его
разговор посреди работы. А удаление уносит обращения целиком — иначе
ответы анкеты и снимок согласия пережили бы срок навсегда.

Запуск — из планировщика (cron/systemd timer), не из веб-запроса:

    SDUT_CASE_RETENTION_DAYS=365 python ops/purge_expired_cases.py
    SDUT_CASE_RETENTION_DAYS=365 python ops/purge_expired_cases.py --показать
"""

from __future__ import annotations

import os
import sys

import psycopg

ПЕРЕМЕННАЯ = "SDUT_CASE_RETENTION_DAYS"

# За один запуск — ограниченная порция, чтобы длинная транзакция
# не держала таблицу, в которую бот пишет прямо сейчас.
ПОРЦИЯ = 500


def _адрес() -> str:
    значение = (os.getenv("SDUT_DATABASE_URL") or "").strip()
    if not значение:
        raise SystemExit("SDUT_DATABASE_URL обязателен")
    return значение


def срок_хранения() -> int | None:
    """Дней хранения или None, если служба срок не назначила."""
    сырое = (os.getenv(ПЕРЕМЕННАЯ) or "").strip()
    if not сырое:
        return None
    try:
        дней = int(сырое)
    except ValueError:
        raise SystemExit(f"{ПЕРЕМЕННАЯ} должен быть целым числом дней")
    if дней < 1:
        raise SystemExit(f"{ПЕРЕМЕННАЯ} должен быть не меньше 1")
    if дней > 3650:
        raise SystemExit(f"{ПЕРЕМЕННАЯ} больше десяти лет — это опечатка?")
    return дней


def просроченные(conn, дней: int, предел: int = ПОРЦИЯ) -> list[str]:
    """Кого пора удалить. Срок считается от последнего изменения карточки.

    От последнего изменения, а не от начала: человек, который вернулся
    и дополнил анкету, обратился заново, и отсчёт для него начинается
    сначала.
    """
    строки = conn.execute(
        """
        WITH обращения AS (
            SELECT p.channel_user_id AS user_id, max(e.at) AS at
              FROM persons p
              JOIN cases c ON c.person_id = p.person_id
              JOIN case_events e ON e.case_id = c.case_id
             WHERE p.channel = 'max'
             GROUP BY p.channel_user_id
        ), карточки AS (
            SELECT s.user_id, greatest(s.updated_at, о.at) AS at
              FROM survey_state s LEFT JOIN обращения о ON о.user_id = s.user_id
            UNION ALL
            SELECT о.user_id, о.at
              FROM обращения о
             WHERE NOT EXISTS (SELECT 1 FROM survey_state s WHERE s.user_id = о.user_id)
        )
        SELECT user_id FROM карточки
         WHERE at < CURRENT_TIMESTAMP - make_interval(days => %s)
         ORDER BY at LIMIT %s
        """,
        (дней, предел),
    ).fetchall()
    return [str(строка[0]) for строка in строки]


def удалить(conn, user_id: str) -> None:
    """Стереть карточку целиком, под той же блокировкой, что и бот.

    Блокировка нужна, чтобы удаление не разошлось с событием, которое
    бот обрабатывает для этого же человека прямо сейчас.
    """
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (user_id,))
    conn.execute("DELETE FROM survey_state WHERE user_id=%s", (user_id,))
    conn.execute("DELETE FROM operator_cases WHERE user_id=%s", (user_id,))
    conn.execute("DELETE FROM outbox_messages WHERE user_id=%s", (user_id,))
    # Обращения — вместе со снимками согласия, анкетами, событиями,
    # задачами, направлениями, исходами и их автоматическими сообщениями
    # (каскад); затем сам человек.
    conn.execute(
        "DELETE FROM cases WHERE person_id IN "
        "(SELECT person_id FROM persons WHERE channel='max' AND channel_user_id=%s)",
        (user_id,),
    )
    conn.execute("DELETE FROM persons WHERE channel='max' AND channel_user_id=%s", (user_id,))
    # Идентификатор человека — тоже персональные данные. Само событие
    # остаётся: без него повторная доставка из MAX создала бы карточку
    # заново, уже после того, как срок хранения вышел.
    conn.execute("UPDATE processed_events SET user_id=NULL WHERE user_id=%s", (user_id,))
    conn.execute("DELETE FROM audit_events WHERE user_id=%s", (user_id,))


def main(аргументы: list[str] | None = None) -> int:
    аргументы = list(аргументы if аргументы is not None else sys.argv[1:])
    только_показать = "--показать" in аргументы or "--dry-run" in аргументы

    дней = срок_хранения()
    if дней is None:
        print(
            f"{ПЕРЕМЕННАЯ} не задан — ничего не удалено.\n"
            "Срок хранения назначает служба как оператор персональных данных.\n"
            "Пока он не назначен, анкеты хранятся бессрочно, а часть 7\n"
            "статьи 5 ФЗ-152 этого не допускает."
        )
        return 2

    with psycopg.connect(_адрес()) as conn:
        кого = просроченные(conn, дней)
        if только_показать:
            print(f"срок хранения: {дней} дн.; под удаление попадает: {len(кого)}")
            return 0
        for user_id in кого:
            удалить(conn, user_id)
        conn.commit()

    print(f"срок хранения: {дней} дн.; удалено карточек: {len(кого)}")
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())

# MAX → CASE: интеграция, шаг 1

## Цель

MAX остаётся интерфейсом диалога. Бизнес-состояние обращения хранится в CASE.

На этом шаге подключена только граница:

`MAX user_id → PERSON → CASE → INTAKE`.

Анкета и CASE не дублируют друг друга:
- состояние диалога остаётся в `survey_state`;
- идентичность человека и жизненный цикл обращения — в CASE;
- ответы анкеты проецируются в текущую версию INTAKE;
- номер обращения появляется только при переходе `DRAFT → NEW`;
- маршрутизация, направление, CRM, контроль и автоматические сообщения здесь не реализуются.

## Транзакция

CASE-репозиторий использует ту же PostgreSQL-транзакцию, которую открывает MAX-событие через `storage_postgres._TX_CONNECTION`.

Поэтому для принятого события:
- изменение `survey_state`;
- создание/обновление PERSON/CASE/INTAKE;
- события CASE

фиксируются одной транзакцией или откатываются вместе.

## Жизненный цикл шага

1. Человек даёт согласие.
2. Создаётся `PERSON(channel=max, channel_user_id=MAX user id)`.
3. Создаётся один открытый `CASE(DRAFT)`.
4. Создаётся снимок согласия и `INTAKE v1`.
5. После checkpoint CASE переходит в `NEW`; только здесь атомарно выдаётся номер `SDUT-YYYY-NNNNN`.
6. Последующие изменения анкеты обновляют текущую версию INTAKE.

## Маршрутизация — следующий срез

После фактического завершения анкеты `routing_rules.py` рассчитывает предложение маршрута и передаёт его в `CaseService.suggest_route()`.

- до завершения анкеты маршрут не фиксируется;
- в CASE сохраняются маршрут, основание, сигналы, сопутствующие маршруты и версия правил;
- повторный расчёт не перезаписывает уже сохранённый `suggested_route`;
- `ROUTE_CONFIRMED` выполняется только координатором и остаётся отдельным этапом.

## Что намеренно не сделано

- `ROUTE_CONFIRMED`;
- `REFERRAL`;
- миграция `operator_cases`;
- D+7/D+30;
- I18 автоматических сообщений;
- Mini App;
- AI.

Это следующие отдельные этапы, чтобы CASE оставался единственным источником бизнес-истины.


## Production completion — CASE-centered CRM and I18

The production PostgreSQL CRM now exposes CASE-domain operations instead of introducing a second lifecycle implementation:
- assignment/contact;
- route confirmation with an explicit reason;
- referral via immutable directory entry;
- service start;
- reassignment;
- closure.

The legacy `operator_cases` storage is retained only for the laptop/pilot compatibility path. Production CASE remains the lifecycle source of truth.

Automatic outbound messages are bound to the exact CASE with `case_id` and `automatic=true`. Closing a CASE cancels its queued automatic messages. The database uses the same per-person advisory lock as the outbox worker, so close-vs-send is serialized; the worker then revalidates the exact CASE immediately before calling MAX. A later CASE therefore cannot revive an automatic message belonging to an earlier closed CASE.

No automatic route confirmation, referral, medical transfer, or AI decision is introduced.

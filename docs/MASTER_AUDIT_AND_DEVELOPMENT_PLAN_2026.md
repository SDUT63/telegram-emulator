# SDUT MAX-бот — мастер-аудит и план развития

Дата обновления: 2026-09-13  
Рабочая ветка: `max-bot-complete-2026`  
Контрольная ветка: `claude/chat-bot-survey-publik-nq2w1s`

## 1. Архитектурная граница

Production MAX использует PostgreSQL как source of truth. Входящие события проходят через transactional dispatcher/storage; исходящие сообщения — только через durable PostgreSQL outbox и единственный MAX transport. `legacy_max_bot.py` оставлен для старого локального tooling; `max_bot.py` является только compatibility import и не является production transport.

SQLite допускается только как laptop pilot.

## 2. Критические исправления аудита

### Privacy / deletion — P0
- В production dispatcher должны перехватываться все согласованные варианты команды удаления до legacy scenario engine.
- Production deletion использует transaction-safe purge и `deleted_users`.
- Обработанные event IDs сохраняются в `deleted_event_tombstones`, чтобы повтор webhook после удаления не воскресил состояние.
- Удаляются state, audit, operator case и outbound queue.
- Удаление event leases ограничено event IDs конкретного пользователя.
- Plaintext CSV export в production facade отключён.

### Outbox race — P0/P1
Worker перед фактической отправкой повторно проверяет claim после получения per-user PostgreSQL advisory lock. Это закрывает гонку `worker claimed → deletion` / `deletion → worker claim`.

### Deployment — P1
- Docker image: Python 3.13, non-root user, встроенный urllib healthcheck.
- Compose: PostgreSQL 16 → migration one-shot → MAX Webhook; секреты не задаются в образе.
- Production shutdown закрывает MAX client session и HTTP runner.

### Observability — P1
- Центральный JSON/UTC logger.
- `/health`, `/ready` и безопасный `/metrics` endpoint.
- Outbox status/tombstone counts экспортируются без user ID, текста сообщений или медицинских данных.
- Метрики не являются доказательством наличия внешней системы alerting: Prometheus/Alertmanager или эквивалент должны быть подключены на инфраструктуре.

### Backup / DR — P1
- Добавлен `ops/backup_postgres.sh`: custom-format dump, `0600`, проверка `pg_restore --list`, ротация локальных копий.
- Добавлен `ops/restore_postgres.sh`: только явный target database, проверка dump и применение миграций к target.
- Зафиксирован инженерный baseline: RPO 24 часа, RTO 4 часа; это цели, а не измеренные гарантии.
- Требуется внешний encrypted backup storage и регулярный restore drill.

### CI — P1
- `pytest-asyncio` присутствует в requirements.
- CI получает PostgreSQL 16, migration gate, secret scan, outbound architecture check, compileall и pytest.
- Добавлен `workflow_dispatch`.
- Наличие workflow не считается доказательством успешного CI.

## 3. Что ещё не считается закрытым

1. Реальный GitHub Actions run на актуальном HEAD — на текущем HEAD run отсутствует.
2. Полный pytest runtime после синхронизации всех исторических тестовых контрактов.
3. Реальный MAX E2E через HTTPS/443 и reverse proxy.
4. Нагрузочное/chaos тестирование webhook/outbox.
5. Полноценный CRM RBAC и authentication context: `who` сейчас является параметром API и не доказывает личность оператора. Это остаётся P0/P1 security gap для операторского контура.
6. Внешний metrics collector + alerting + SLI/SLO dashboards.
7. Формальная юридическая retention policy для ПДн.
8. Безопасная compaction policy для `deleted_users` / `deleted_event_tombstones`; до утверждения legal policy автоматическое удаление tombstones запрещено.
9. Измеренный backup/restore RPO/RTO.

## 4. Следующий приоритет

### P0/P1 — до production acceptance

1. Закрыть CRM authentication/RBAC: principal из доверенного auth-контекста, роли `operator/senior/admin`, deny-by-default, аудит authorization decisions.
2. Довести CI до фактического runtime evidence и устранить все исторические test/API drift failures.
3. Провести PostgreSQL restore drill с отдельной БД и зафиксировать фактические RTO/RPO.
4. Подключить metrics collector и alert rules для storage outage, outbox backlog, dead-letter, provider failures и webhook availability.
5. Провести concurrency/load/chaos matrix.
6. Провести внешний MAX E2E через HTTPS/443.
7. После legal approval реализовать retention/compaction job с защитой replay tombstones.

## 5. Acceptance matrix

Обязательны: consent, отказ, restart, callbacks, navigation, STOP/urgent route, duplicate event, duplicate callback, concurrent same-user events, attachment, knowledge routing, completion, refusal, deletion, replay after deletion, outbox retry/dead-letter, provider timeout, webhook auth/retry, migration gate, DB outage, backup/restore, operator RBAC.

## 6. Правило доказательств

**Code review ≠ runtime proof.** В отчётах отдельно указывается:
- подтверждено статическим анализом;
- подтверждено unit/integration test;
- подтверждено GitHub Actions;
- подтверждено реальным MAX E2E.

Нельзя писать «production-ready», «CI green» или «все тесты проходят», пока соответствующее доказательство реально не получено.

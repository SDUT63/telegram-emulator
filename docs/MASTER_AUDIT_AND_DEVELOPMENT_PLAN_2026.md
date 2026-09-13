# SDUT MAX-бот — мастер-аудит и план развития

Дата обновления: 2026-09-13  
Рабочая ветка: `max-bot-complete-2026`  
Контрольная ветка: `claude/chat-bot-survey-publik-nq2w1s`

## 1. Архитектурная граница

Production MAX использует PostgreSQL как source of truth. Входящие события проходят через transactional dispatcher/storage; исходящие сообщения — только через durable PostgreSQL outbox и единственный MAX transport. `legacy_max_bot.py` оставлен для старого локального tooling; `max_bot.py` является compatibility import и не является production transport.

SQLite допускается только как laptop pilot.

## 2. Существенные исправления аудита

### Privacy / deletion — P0
- Production deletion использует transaction-safe purge и `deleted_users`.
- Обработанные event IDs сохраняются в `deleted_event_tombstones`, чтобы повтор webhook после удаления не воскресил состояние.
- Удаляются state, audit, operator case и outbound queue.
- Удаление event leases ограничено event IDs конкретного пользователя.
- Plaintext CSV export в production facade отключён.
- Добавлен отдельный retention job для технических deletion/replay records; фактический срок должен быть утверждён privacy/legal policy.

### Outbox race — P0/P1
Worker перед фактической отправкой повторно проверяет claim после получения per-user PostgreSQL advisory lock. Это закрывает гонку `worker claimed → deletion` / `deletion → worker claim`.

### Deployment / migrations — P1
- Docker image: Python 3.13, non-root user, встроенный urllib healthcheck.
- Compose: PostgreSQL 16 → migration one-shot → MAX Webhook.
- Production DSN и обязательные secrets должны передаваться через окружение/secret manager, а не в образе.
- Migration guard теперь обязательный: production не может отключить проверку через переменную bypass; применённые SQL сверяются с SHA-256.
- Production shutdown закрывает MAX client session и HTTP runner.
- Launcher имеет отдельный `prod` режим и требует `SDUT_ENV=production`; локальный `bot` остаётся SQLite pilot.

### Observability — P1
- Центральный JSON/UTC logger.
- `/health`, `/ready` и безопасный `/metrics` endpoint.
- Outbox status/tombstone counts экспортируются без user ID, текста сообщений или медицинских данных.
- Добавлены Prometheus alert rules для PostgreSQL outage, dead-letter, backlog и restart loop.
- Monitoring endpoint должен находиться в private monitoring network или за authenticated reverse proxy.

### Backup / DR — P1
- `ops/backup_postgres.sh`: custom-format dump, `0600`, `pg_restore --list`, ротация локальных копий.
- `ops/restore_postgres.sh`: только явно заданная target database, проверка dump и применение миграций к target.
- `ops/backup_cron.example`: ежедневный backup и ежедневная privacy-retention задача.
- Инженерный baseline: RPO 24 часа, RTO 4 часа; это цели, а не измеренные гарантии.
- Требуется внешний encrypted backup storage и регулярный restore drill.

### CRM security — P0/P1
- CRM больше не принимает произвольный `who` как доказательство личности оператора.
- Введён `OperatorPrincipal` и deny-by-default RBAC: `viewer < operator < supervisor < admin`.
- Чтение допускается от `viewer`, рабочие изменения — от `operator`, отмена контрольного звонка — от `supervisor`.
- Аутентификация principal должна выполняться на доверенном HTTP/admin boundary; этот boundary ещё не реализован в данном репозитории.

### CI / tests — P1
- `pytest-asyncio` присутствует в requirements.
- CI получает PostgreSQL 16, migration gate, secret scan, outbound architecture check, compileall и pytest.
- Добавлен `workflow_dispatch`.
- Исторические launcher/CRM тестовые контракты синхронизированы с текущими API.
- Наличие workflow не считается доказательством успешного CI.

## 3. Что ещё не считается закрытым

1. Реальный GitHub Actions run на актуальном HEAD — требуется получить runtime evidence.
2. Полный pytest runtime на актуальном HEAD; локальный runtime из текущей среды недоступен.
3. Реальный MAX E2E через HTTPS/443 и reverse proxy.
4. Нагрузочное/chaos тестирование webhook/outbox.
5. Доверенный authentication boundary для CRM и привязка `OperatorPrincipal` к реальной сессии/identity provider.
6. Внешний metrics collector + Alertmanager/on-call + SLI/SLO dashboards.
7. Формальная юридическая retention policy для ПДн.
8. Измеренный backup/restore RPO/RTO.
9. Проверка restore из внешнего encrypted backup storage.

## 4. Следующий приоритет

### P0/P1 — до production acceptance

1. Реализовать доверенный authentication boundary для CRM и журналировать authorization decisions без ПДн.
2. Получить фактический GitHub Actions runtime и устранить все оставшиеся test/API drift failures.
3. Провести PostgreSQL restore drill с отдельной БД и измерить фактические RTO/RPO.
4. Подключить Prometheus/Alertmanager или существующий организационный monitoring stack.
5. Провести concurrency/load/chaos matrix для webhook, PostgreSQL и outbox.
6. Провести внешний MAX E2E через HTTPS/443.
7. После утверждения legal retention policy скорректировать retention/compaction parameters.

## 5. Acceptance matrix

Обязательны: consent, отказ, restart, callbacks, navigation, STOP/urgent route, duplicate event, duplicate callback, concurrent same-user events, attachment, knowledge routing, completion, refusal, deletion, replay after deletion, outbox retry/dead-letter, provider timeout, webhook auth/retry, migration gate, DB outage, backup/restore, operator RBAC.

## 6. Правило доказательств

**Code review ≠ runtime proof.** В отчётах отдельно указывается:
- подтверждено статическим анализом;
- подтверждено unit/integration test;
- подтверждено GitHub Actions;
- подтверждено реальным MAX E2E.

Нельзя писать «production-ready», «CI green» или «все тесты проходят», пока соответствующее доказательство реально не получено.

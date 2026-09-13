# SDUT MAX-бот — мастер-аудит и план развития

Дата обновления: 2026-09-13  
Рабочая ветка: `max-bot-complete-2026`  
Контрольная ветка: `claude/chat-bot-survey-publik-nq2w1s`

## 1. Архитектурная граница

Production MAX использует PostgreSQL как source of truth. Входящие события проходят через transactional dispatcher/storage; исходящие сообщения — только через durable PostgreSQL outbox и единственный MAX transport. `legacy_max_bot.py` оставлен для старого локального tooling; `max_bot.py` является только compatibility import и не является production transport.

SQLite допускается только как laptop pilot.

## 2. Критические исправления аудита

### Privacy / deletion — P0
- Все основные варианты команды удаления (`удалить`, `удалите`, `сотри`, `сотрите`, `забудь меня`, `/delete` и варианты с данными/анкетой) перехватываются production dispatcher до legacy scenario engine.
- Production deletion использует transaction-safe purge и `deleted_users`.
- Обработанные event IDs сохраняются в `deleted_event_tombstones`, чтобы повтор webhook после удаления не воскресил состояние.
- Удаляются state, audit, operator case и outbound queue.
- Удаление event leases теперь ограничено ровно event IDs удалённого пользователя, а не временным окном.
- Plaintext CSV export в production facade отключён без исключения из legacy scenario hook: `export_csv()` не создаёт файл.
- Добавлен regression test для privacy boundary.

### Outbox race — P0/P1
Worker перед фактической отправкой повторно проверяет claim после получения per-user PostgreSQL advisory lock. Это закрывает гонку `worker claimed → deletion` / `deletion → worker claim`.

### Deployment — P1
- Docker image: Python 3.13, non-root user, встроенный urllib healthcheck.
- Compose: PostgreSQL 16 → migration one-shot → MAX Webhook; старый порт 5000, `admin123`, JSON volume и несуществующий integration entrypoint удалены.

### Observability — P1
- Центральный JSON/UTC logger добавлен.
- PostgreSQL launcher и MAX Webhook инициализируют structured logging.
- Технические production logs не должны содержать текст сообщений, медицинские ответы или идентификаторы пользователей.

### CI — P1
- `pytest-asyncio` присутствует в requirements.
- CI получает PostgreSQL 16, migration gate, secret scan, outbound architecture check, compileall и pytest.
- Добавлен `workflow_dispatch` для ручного запуска.
- Наличие workflow не считается доказательством успешного CI: новый run должен быть фактически получен и проверен.

## 3. Что ещё не считается закрытым

1. Реальный GitHub Actions run на актуальном HEAD.
2. Полный pytest после синхронизации всех исторических тестовых контрактов.
3. Backup/restore PostgreSQL и документированный RTO/RPO.
4. Формальная политика retention/backup для ПДн и её инфраструктурная реализация.
5. Реальный MAX E2E через HTTPS/443 и reverse proxy.
6. Нагрузочное/chaos тестирование webhook/outbox.
7. Полноценный CRM RBAC: caller-controlled `who` пока не является полноценной моделью авторизации.
8. Метрики и alerting: health/readiness есть, но production SLI/SLO и alert rules ещё требуют реализации.
9. Retention/compaction для `deleted_users` и документированная политика tombstone retention.

## 4. Acceptance matrix

Обязательны: consent, отказ, restart, callbacks, navigation, STOP/urgent route, duplicate event, duplicate callback, concurrent same-user events, attachment, knowledge routing, completion, refusal, deletion, replay after deletion, outbox retry/dead-letter, provider timeout, webhook auth/retry, migration gate, DB outage, backup/restore.

## 5. Правило доказательств

**Code review ≠ runtime proof.** В отчётах отдельно указывается:
- подтверждено статическим анализом;
- подтверждено unit/integration test;
- подтверждено GitHub Actions;
- подтверждено реальным MAX E2E.

Нельзя писать «production-ready», «CI green» или «все тесты проходят», пока соответствующее доказательство реально не получено.

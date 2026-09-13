# SDUT MAX: backup / restore / DR

## Scope

PostgreSQL is the production source of truth. SQLite and local CSV files are not production recovery sources.

## Backup

Run `ops/backup_postgres.sh` at least daily on infrastructure outside the application container. The dump is written with mode `0600` and is validated with `pg_restore --list` before publication.

Backups must be encrypted at rest by the backup/storage layer and copied to a separate failure domain. The repository does not contain credentials or encryption keys.

Recommended baseline until a formal institutional policy is approved:

- daily backups;
- 14 days online retention;
- at least one weekly copy retained separately;
- quarterly restore drill;
- backup monitoring must alert when the expected backup is absent or validation fails.

The 14-day value is an operational baseline, **not a legal retention decision for personal data**.

## Restore

`ops/restore_postgres.sh` requires an explicit `SDUT_RESTORE_DATABASE_URL` and refuses to restore implicitly into the source database. Restore into an isolated PostgreSQL instance first, then apply migrations and run the acceptance checks before any production cutover.

Minimum restore verification:

1. migration version is current;
2. application health/readiness is successful;
3. survey state can be read;
4. processed-event uniqueness remains intact;
5. outbox rows and tombstones are present;
6. a synthetic callback/message transaction can be processed without external delivery;
7. no plaintext export is produced.

## Recovery targets

Until infrastructure owners approve formal SLOs, the engineering baseline is:

- **RPO target: 24 hours** (daily backup cadence; improve if infrastructure permits);
- **RTO target: 4 hours** for restoration of the PostgreSQL-backed service.

These are targets, not measured guarantees. A restore drill must record actual RPO/RTO.

## Tombstones and personal-data retention

`deleted_users` and `deleted_event_tombstones` are security/replay-control records. They must not be compacted merely because the application data was deleted. Any future compaction job must be based on the documented MAX replay window plus the approved legal/privacy retention policy, and must preserve collision/replay safety.

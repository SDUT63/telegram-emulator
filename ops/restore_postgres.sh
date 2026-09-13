#!/usr/bin/env bash
set -euo pipefail

: "${SDUT_DATABASE_URL:?SDUT_DATABASE_URL is required}"
: "${SDUT_BACKUP_FILE:?SDUT_BACKUP_FILE is required}"

if [[ ! -f "$SDUT_BACKUP_FILE" ]]; then
  echo "Backup file does not exist: $SDUT_BACKUP_FILE" >&2
  exit 1
fi

# Restore only into an explicitly supplied target database. Never point this
# script at production without an approved maintenance window and fresh backup.
TARGET_DB_URL="${SDUT_RESTORE_DATABASE_URL:-}"
if [[ -z "$TARGET_DB_URL" ]]; then
  echo "SDUT_RESTORE_DATABASE_URL is required; refusing to restore into the source database" >&2
  exit 1
fi

pg_restore --list "$SDUT_BACKUP_FILE" >/dev/null
pg_restore --clean --if-exists --no-owner --no-acl --dbname="$TARGET_DB_URL" "$SDUT_BACKUP_FILE"
python scripts/migrate_postgres.py
printf 'restore=ok\n'

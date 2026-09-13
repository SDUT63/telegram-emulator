#!/usr/bin/env bash
set -euo pipefail

: "${SDUT_BACKUP_FILE:?SDUT_BACKUP_FILE is required}"
: "${SDUT_RESTORE_DATABASE_URL:?SDUT_RESTORE_DATABASE_URL is required}"

if [[ ! -f "$SDUT_BACKUP_FILE" ]]; then
  echo "Backup file does not exist: $SDUT_BACKUP_FILE" >&2
  exit 1
fi

# Restore only into an explicitly supplied target database. Never point this
# script at production without an approved maintenance window and fresh backup.
pg_restore --list "$SDUT_BACKUP_FILE" >/dev/null
pg_restore --clean --if-exists --no-owner --no-acl --dbname="$SDUT_RESTORE_DATABASE_URL" "$SDUT_BACKUP_FILE"
SDUT_DATABASE_URL="$SDUT_RESTORE_DATABASE_URL" python scripts/migrate_postgres.py
printf 'restore=ok\n'

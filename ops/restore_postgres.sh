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

# Зашифрованная копия расшифровывается во временный файл с правами 0600,
# который удаляется при любом выходе. Расшифрованный дамп не должен
# оставаться на диске после восстановления.
dump="$SDUT_BACKUP_FILE"
plain=""
trap 'if [[ -n "$plain" ]]; then shred -u "$plain" 2>/dev/null || rm -f "$plain"; fi' EXIT

if [[ "$SDUT_BACKUP_FILE" == *.enc ]]; then
  : "${SDUT_BACKUP_PASSPHRASE_FILE:?SDUT_BACKUP_PASSPHRASE_FILE is required to read an encrypted backup}"
  umask 077
  plain="$(mktemp "${TMPDIR:-/tmp}/sdut-restore-XXXXXX.dump")"
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
    -in "$SDUT_BACKUP_FILE" -out "$plain" -pass "file:$SDUT_BACKUP_PASSPHRASE_FILE"
  dump="$plain"
fi

pg_restore --list "$dump" >/dev/null
pg_restore --clean --if-exists --no-owner --no-acl --dbname="$SDUT_RESTORE_DATABASE_URL" "$dump"
SDUT_DATABASE_URL="$SDUT_RESTORE_DATABASE_URL" python scripts/migrate_postgres.py
printf 'restore=ok\n'

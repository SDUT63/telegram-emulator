#!/usr/bin/env bash
set -euo pipefail

# Production backup contract. The destination must be protected by the host's
# filesystem/backup encryption; pg_dump itself does not encrypt the dump.
: "${SDUT_DATABASE_URL:?SDUT_DATABASE_URL is required}"
BACKUP_DIR="${SDUT_BACKUP_DIR:-/var/backups/sdut}"
RETENTION_DAYS="${SDUT_BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tmp="$BACKUP_DIR/.sdut_${stamp}.dump.tmp"
out="$BACKUP_DIR/sdut_${stamp}.dump"
trap 'rm -f "$tmp"' EXIT

umask 077
pg_dump --format=custom --no-owner --no-acl --dbname="$SDUT_DATABASE_URL" --file="$tmp"
pg_restore --list "$tmp" >/dev/null
mv "$tmp" "$out"
chmod 600 "$out"

find "$BACKUP_DIR" -type f -name 'sdut_*.dump' -mtime "+$RETENTION_DAYS" -delete
printf 'backup=%s\n' "$out"

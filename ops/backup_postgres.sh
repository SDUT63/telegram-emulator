#!/usr/bin/env bash
set -euo pipefail

# Production backup contract.
#
# Дамп — это все анкеты целиком: имена, телефоны, адреса, состояние здоровья.
# pg_dump ничего не шифрует, поэтому копия шифруется здесь, до того как ляжет
# на диск: незашифрованный файл не должен существовать даже секунду.
#
# Ключ задаётся службой через SDUT_BACKUP_PASSPHRASE_FILE — путь к файлу с
# парольной фразой (права 0400, вне каталога бэкапов и вне репозитория).
# Хранение самого ключа — организационное решение, а не дело этого скрипта.
# Если переменная не задана, скрипт останавливается: тихо положить
# незашифрованную копию персональных данных хуже, чем не сделать бэкап.
: "${SDUT_DATABASE_URL:?SDUT_DATABASE_URL is required}"
: "${SDUT_BACKUP_PASSPHRASE_FILE:?SDUT_BACKUP_PASSPHRASE_FILE is required (path to the encryption passphrase)}"

if [[ ! -r "$SDUT_BACKUP_PASSPHRASE_FILE" ]]; then
  echo "Passphrase file is not readable: $SDUT_BACKUP_PASSPHRASE_FILE" >&2
  exit 1
fi
if [[ ! -s "$SDUT_BACKUP_PASSPHRASE_FILE" ]]; then
  echo "Passphrase file is empty: $SDUT_BACKUP_PASSPHRASE_FILE" >&2
  exit 1
fi
BACKUP_DIR="${SDUT_BACKUP_DIR:-/var/backups/sdut}"
RETENTION_DAYS="${SDUT_BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tmp="$BACKUP_DIR/.sdut_${stamp}.dump.tmp"
enc="$BACKUP_DIR/.sdut_${stamp}.dump.enc.tmp"
out="$BACKUP_DIR/sdut_${stamp}.dump.enc"
# Оба временных файла убираются при любом выходе, включая ошибку и прерывание.
trap 'rm -f "$tmp" "$enc"' EXIT

umask 077
pg_dump --format=custom --no-owner --no-acl --dbname="$SDUT_DATABASE_URL" --file="$tmp"
# Проверяем дамп до шифрования: битую копию незачем ни шифровать, ни хранить.
pg_restore --list "$tmp" >/dev/null

openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt \
  -in "$tmp" -out "$enc" -pass "file:$SDUT_BACKUP_PASSPHRASE_FILE"
shred -u "$tmp" 2>/dev/null || rm -f "$tmp"

mv "$enc" "$out"
chmod 600 "$out"

find "$BACKUP_DIR" -type f -name 'sdut_*.dump.enc' -mtime "+$RETENTION_DAYS" -delete
# Старые незашифрованные копии, если они остались от прежней версии скрипта,
# тоже подлежат удалению по сроку — но о них нужно знать.
if find "$BACKUP_DIR" -type f -name 'sdut_*.dump' -print -quit | grep -q .; then
  echo "WARNING: unencrypted backups from an older version are present in $BACKUP_DIR" >&2
fi
printf 'backup=%s\n' "$out"

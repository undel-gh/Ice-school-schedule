#!/bin/sh
set -eu

umask 077

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
BACKUP_TIMEOUT_SECONDS="${BACKUP_TIMEOUT_SECONDS:-3600}"
MONITORING_HTTP_TIMEOUT_SECONDS="${MONITORING_HTTP_TIMEOUT_SECONDS:-10}"

case "$BACKUP_INTERVAL_SECONDS" in
  *[!0-9]*|"") echo "BACKUP_INTERVAL_SECONDS must be a positive integer" >&2; exit 2 ;;
esac
case "$BACKUP_RETENTION_DAYS" in
  *[!0-9]*|"") echo "BACKUP_RETENTION_DAYS must be a non-negative integer" >&2; exit 2 ;;
esac
case "$BACKUP_TIMEOUT_SECONDS" in
  *[!0-9]*|"") echo "BACKUP_TIMEOUT_SECONDS must be a positive integer" >&2; exit 2 ;;
esac
if [ "$BACKUP_INTERVAL_SECONDS" -le 0 ]; then
  echo "BACKUP_INTERVAL_SECONDS must be positive" >&2
  exit 2
fi
if [ "$BACKUP_TIMEOUT_SECONDS" -le 0 ]; then
  echo "BACKUP_TIMEOUT_SECONDS must be positive" >&2
  exit 2
fi
case "$MONITORING_HTTP_TIMEOUT_SECONDS" in
  *[!0-9]*|"") echo "MONITORING_HTTP_TIMEOUT_SECONDS must be a positive integer" >&2; exit 2 ;;
esac
if [ "$MONITORING_HTTP_TIMEOUT_SECONDS" -le 0 ]; then
  echo "MONITORING_HTTP_TIMEOUT_SECONDS must be positive" >&2
  exit 2
fi

mkdir -p "$BACKUP_DIR"

ping_success() {
  if [ -z "${BACKUP_SUCCESS_PING_URL:-}" ]; then
    return 0
  fi

  if wget -q -T "$MONITORING_HTTP_TIMEOUT_SECONDS" -O /dev/null "$BACKUP_SUCCESS_PING_URL"; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup event=success_ping_sent"
  else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup event=success_ping_failed" >&2
  fi
}

run_backup() {
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  final="$BACKUP_DIR/ice_school_${timestamp}.dump"
  temp="$final.partial"

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup event=start target=$final"
  rm -f "$temp"

  if timeout "$BACKUP_TIMEOUT_SECONDS" \
    pg_dump --format=custom --no-owner --no-privileges --file="$temp" "$PGDATABASE"; then
    :
  else
    status=$?
    rm -f "$temp"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup event=failed stage=pg_dump exit_code=$status timeout_seconds=$BACKUP_TIMEOUT_SECONDS" >&2
    return 1
  fi

  if ! pg_restore --list "$temp" >/dev/null; then
    rm -f "$temp"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup event=failed stage=verify" >&2
    return 1
  fi

  mv "$temp" "$final"
  touch /tmp/backup_last_success
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup event=success target=$final"
  ping_success

  find "$BACKUP_DIR" -type f -name 'ice_school_*.dump' -mtime "+$BACKUP_RETENTION_DAYS" -print |
  while IFS= read -r old_backup; do
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup event=retention_delete target=$old_backup"
    rm -f "$old_backup"
  done
}

if [ "${1:-}" = "--once" ]; then
  run_backup
  exit $?
fi

trap 'exit 0' TERM INT

while :; do
  run_backup || true
  sleep "$BACKUP_INTERVAL_SECONDS" &
  wait $!
done

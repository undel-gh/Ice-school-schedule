#!/bin/sh
# Upload existing verified PostgreSQL dumps to an encrypted off-host restic repository.
set -eu
umask 077

BACKUP_DIR="${OFFSITE_BACKUP_DIR:-/backups}"
STATUS_DIR="${OFFSITE_STATUS_DIR:-/state}"
POLL_SECONDS="${OFFSITE_POLL_SECONDS:-300}"
KEEP_DAILY="${OFFSITE_KEEP_DAILY:-14}"
KEEP_WEEKLY="${OFFSITE_KEEP_WEEKLY:-8}"
KEEP_MONTHLY="${OFFSITE_KEEP_MONTHLY:-12}"
RESTIC_HOST="${OFFSITE_RESTIC_HOST:-ice-school-production}"
MARKER="$STATUS_DIR/last_uploaded"
SUCCESS="$STATUS_DIR/last_success"
FAILURE="$STATUS_DIR/last_failure"

log() { printf '%s offsite event=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"; }
fail() {
  # Preserve a negative result even when the previous successful upload is
  # recent. Otherwise Docker reports healthy for up to two backup intervals.
  # An error marker is private, contains no secrets, and survives restarts.
  if ! printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" > "$FAILURE"; then
    log "failed stage=state_write" >&2
  fi
  log "failed stage=$1" >&2
  return 1
}
positive_int() {
  case "$2" in ""|*[!0-9]*) echo "$1 must be positive integer" >&2; exit 2;; esac
  [ "$2" -gt 0 ] || { echo "$1 must be positive" >&2; exit 2; }
}
positive_int OFFSITE_POLL_SECONDS "$POLL_SECONDS"
positive_int OFFSITE_KEEP_DAILY "$KEEP_DAILY"
positive_int OFFSITE_KEEP_WEEKLY "$KEEP_WEEKLY"
positive_int OFFSITE_KEEP_MONTHLY "$KEEP_MONTHLY"

[ -d "$BACKUP_DIR" ] || { echo "Backup mount missing" >&2; exit 2; }
[ -d "$STATUS_DIR" ] || { echo "Offsite state mount missing" >&2; exit 2; }
[ -r "${RESTIC_PASSWORD_FILE:-/not-configured}" ] || { echo "Missing restic password file" >&2; exit 2; }
[ -r "${AWS_SHARED_CREDENTIALS_FILE:-/not-configured}" ] || { echo "Missing S3 credentials file" >&2; exit 2; }
[ -n "${RESTIC_REPOSITORY:-}" ] || { echo "RESTIC_REPOSITORY not configured" >&2; exit 2; }

newest_dump() {
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'ice_school_*.dump' -print |
    LC_ALL=C sort | tail -n 1
}
send_ping() {
  [ -n "${OFFSITE_SUCCESS_PING_URL:-}" ] || return 0
  if wget -q -T "${MONITORING_HTTP_TIMEOUT_SECONDS:-10}" -O /dev/null \
    "$OFFSITE_SUCCESS_PING_URL"; then
    log success_ping_sent
  else
    log success_ping_failed >&2
  fi
}
run_once() {
  dump="$(newest_dump)"
  [ -n "$dump" ] || { fail no_verified_dump; return 1; }
  [ -r "$dump" ] || { fail dump_not_readable; return 1; }
  dump_name="$(basename "$dump")"
  # Bind the acknowledgement to the repository URL. Switching endpoints
  # must never let a marker for the old repository suppress new uploads.
  repo_id="$(printf '%s' "$RESTIC_REPOSITORY" | sha256sum | cut -d ' ' -f 1)"
  previous=""
  if [ -f "$MARKER" ]; then
    IFS= read -r previous < "$MARKER" || true
  fi
  if [ "$previous" = "$repo_id $dump_name" ] && [ -f "$SUCCESS" ]; then
    # The repository may have been replaced/deleted after our last success.
    # Verify that the referenced dump still occurs in remote snapshots.
    if restic --retry-lock 10m snapshots --json --host "$RESTIC_HOST" \
        --tag ice-school-postgres | grep -Fq "$dump_name"; then
      if ! rm -f "$FAILURE"; then
        fail state_write
        return 1
      fi
      log "already_uploaded dump=$dump_name"
      return 0
    fi
    log "remote_snapshot_missing_or_unreachable dump=$dump_name"
  fi

  log "start dump=$dump_name"
  if ! restic --retry-lock 10m backup --host "$RESTIC_HOST" --tag ice-school-postgres \
       --group-by host,tags -- "$dump"; then
    fail restic_backup
    return 1
  fi
  # Dump filenames vary. Group retention by host+tag, not paths.
  if ! restic --retry-lock 10m forget --host "$RESTIC_HOST" --tag ice-school-postgres \
       --group-by host,tags --keep-daily "$KEEP_DAILY" \
       --keep-weekly "$KEEP_WEEKLY" --keep-monthly "$KEEP_MONTHLY" --prune; then
    fail restic_retention
    return 1
  fi
  temp="$STATUS_DIR/last_uploaded.$$"
  if ! printf '%s\n' "$repo_id $dump_name" > "$temp"; then
    fail status_write
    return 1
  fi
  if ! mv -f "$temp" "$MARKER" || ! touch "$SUCCESS" ||
     ! rm -f "$FAILURE"; then
    rm -f "$temp"
    fail status_write
    return 1
  fi
  log "success dump=$dump_name"
  send_ping
}
if [ "${1:-}" = "--once" ]; then
  run_once
  exit $?
fi
[ "$#" -eq 0 ] || { echo "Usage: offsite_backup.sh [--once]" >&2; exit 2; }
trap 'exit 0' TERM INT
while :; do
  run_once || true
  sleep "$POLL_SECONDS" &
  wait "$!" || true
done

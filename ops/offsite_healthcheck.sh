#!/bin/sh
set -eu
marker="${OFFSITE_STATUS_DIR:-/state}/last_success"
[ -f "$marker" ] || exit 1
# A failed upload must immediately override a previously fresh success.
[ ! -e "${OFFSITE_STATUS_DIR:-/state}/last_failure" ] || exit 1
interval="${BACKUP_INTERVAL_SECONDS:-86400}"
grace="${OFFSITE_HEALTH_GRACE_SECONDS:-3600}"
for value in "$interval" "$grace"; do
  case "$value" in ""|*[!0-9]*) exit 1;; esac
done
[ "$interval" -gt 0 ] || exit 1
now="$(date +%s)"
last="$(stat -c %Y "$marker")"
[ "$last" -le "$now" ] || exit 1
[ $((now - last)) -le $((interval * 2 + grace)) ]

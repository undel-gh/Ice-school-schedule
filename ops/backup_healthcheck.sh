#!/bin/sh
set -eu

marker=/tmp/backup_last_success
test -f "$marker"

interval="${BACKUP_INTERVAL_SECONDS:-86400}"
case "$interval" in
  *[!0-9]*|"") exit 1 ;;
esac

now="$(date +%s)"
last="$(stat -c %Y "$marker")"
max_age=$((interval * 2 + 300))

test $((now - last)) -le "$max_age"

#!/bin/sh
set -eu

health_dir="${SCHEDULER_HEALTH_DIR:-/tmp/ice_school_scheduler}"
grace="${SCHEDULER_HEALTH_GRACE_SECONDS:-300}"
lifecycle_interval="${SCHEDULER_LIFECYCLE_INTERVAL_SECONDS:-3600}"
generation_interval="${SCHEDULER_GENERATION_INTERVAL_SECONDS:-21600}"

for value in "$grace" "$lifecycle_interval" "$generation_interval"; do
  case "$value" in
    *[!0-9]*|"") exit 1 ;;
  esac
done

now="$(date +%s)"

check_marker() {
  name="$1"
  interval="$2"
  marker="$health_dir/$name.success"
  test -f "$marker"
  last="$(stat -c %Y "$marker")"
  max_age=$((interval * 2 + grace))
  test $((now - last)) -le "$max_age"
}

check_marker subscription_lifecycle "$lifecycle_interval"
check_marker lesson_generation "$generation_interval"

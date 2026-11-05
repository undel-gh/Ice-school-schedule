#!/usr/bin/env bash
# CI: demonstrate that an encrypted repo restores a PostgreSQL dump without the original host file.
set -Eeuo pipefail
umask 077
: "${OFFSITE_CI_IMAGE:=ice-school-offsite:ci}"
test -n "${BACKUP_SOURCE_DIR:-}"
test -n "$(find "$BACKUP_SOURCE_DIR" -maxdepth 1 -name 'ice_school_*.dump' -type f -print -quit)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/repo" "$tmp/state" "$tmp/restore" "$tmp/secrets"
printf '%s\n' 'CI-only-randomized-offsite-test-password' > "$tmp/secrets/restic-password"
printf '%s\n' '[default]' 'aws_access_key_id=test' 'aws_secret_access_key=test' > "$tmp/secrets/aws-credentials"
chmod 600 "$tmp/secrets/"*

docker build -f ops/offsite.Dockerfile -t "$OFFSITE_CI_IMAGE" .
common=(
  --rm
  --mount "type=bind,src=$tmp/repo,dst=/repo"
  --mount "type=bind,src=$tmp/secrets,dst=/secrets,readonly"
  -e RESTIC_REPOSITORY=/repo
  -e RESTIC_PASSWORD_FILE=/secrets/restic-password
  -e AWS_SHARED_CREDENTIALS_FILE=/secrets/aws-credentials
)
docker run "${common[@]}" --entrypoint restic "$OFFSITE_CI_IMAGE" init
docker run "${common[@]}" \
  --mount "type=bind,src=$BACKUP_SOURCE_DIR,dst=/backups,readonly" \
  --mount "type=bind,src=$tmp/state,dst=/state" \
  "$OFFSITE_CI_IMAGE" --once
docker run "${common[@]}" --entrypoint restic "$OFFSITE_CI_IMAGE" check --read-data
# Restore has *no* access to the original backup directory.
docker run "${common[@]}" \
  --mount "type=bind,src=$tmp/restore,dst=/restore" \
  --entrypoint restic "$OFFSITE_CI_IMAGE" restore latest --target /restore
dump="$(find "$BACKUP_SOURCE_DIR" -maxdepth 1 -type f -name 'ice_school_*.dump' | LC_ALL=C sort | tail -1)"
cmp "$dump" "$tmp/restore/backups/$(basename "$dump")"
printf '%s\n' 'wrong-password' > "$tmp/secrets/wrong-password"
if docker run "${common[@]}" \
    -e RESTIC_PASSWORD_FILE=/secrets/wrong-password \
    --entrypoint restic "$OFFSITE_CI_IMAGE" snapshots >/dev/null 2>&1; then
  echo "Restic unexpectedly opened encrypted repository with wrong password" >&2
  exit 1
fi
echo "Verified encrypted backup, repository integrity, full restore and wrong-password rejection"

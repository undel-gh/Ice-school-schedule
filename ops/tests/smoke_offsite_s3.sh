#!/usr/bin/env bash
# CI only: exercise the real S3 adapter against an ephemeral isolated MinIO server.
set -Eeuo pipefail
umask 077
: "${BACKUP_SOURCE_DIR:?}"
test -n "$(find "$BACKUP_SOURCE_DIR" -maxdepth 1 -type f -name 'ice_school_*.dump' -print -quit)"
tmp="$(mktemp -d)"
network="ice-school-offsite-ci-$$"
server="ice-school-minio-ci-$$"
cleanup() {
  docker rm -f "$server" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

mkdir -p "$tmp/secrets" "$tmp/state" "$tmp/restore"
printf '%s\n' 'test-only-restic-password-long-enough' > "$tmp/secrets/password"
printf '%s\n' '[default]' 'aws_access_key_id=offsite-ci-access' 'aws_secret_access_key=offsite-ci-strong-secret' > "$tmp/secrets/credentials"
chmod 600 "$tmp/secrets/"*
docker network create "$network" >/dev/null
docker run -d --name "$server" --network "$network" --network-alias minio \
  -p 127.0.0.1:19009:9000 \
  -e MINIO_ROOT_USER=offsite-ci-access \
  -e MINIO_ROOT_PASSWORD=offsite-ci-strong-secret \
  minio/minio:RELEASE.2025-09-07T16-13-09Z server /data >/dev/null

for attempt in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:19009/minio/health/live >/dev/null; then
    break
  fi
  sleep 2
done
curl -fsS http://127.0.0.1:19009/minio/health/live >/dev/null
python -m pip install -q boto3
python - <<'PY'
import boto3
boto3.client(
    "s3",
    endpoint_url="http://127.0.0.1:19009",
    aws_access_key_id="offsite-ci-access",
    aws_secret_access_key="offsite-ci-strong-secret",
    region_name="us-east-1",
).create_bucket(Bucket="ice-school-ci-backups")
PY
common=(
  --rm
  --network "$network"
  --mount "type=bind,src=$tmp/secrets,dst=/secrets,readonly"
  -e RESTIC_REPOSITORY=s3:http://minio:9000/ice-school-ci-backups/ci
  -e RESTIC_PASSWORD_FILE=/secrets/password
  -e AWS_SHARED_CREDENTIALS_FILE=/secrets/credentials
)
docker run "${common[@]}" --entrypoint restic ice-school-offsite:ci init
docker run "${common[@]}" \
  --mount "type=bind,src=$BACKUP_SOURCE_DIR,dst=/backups,readonly" \
  --mount "type=bind,src=$tmp/state,dst=/state" \
  ice-school-offsite:ci --once
docker run "${common[@]}" --entrypoint restic ice-school-offsite:ci check --read-data
docker run "${common[@]}" \
  --mount "type=bind,src=$tmp/restore,dst=/restore" \
  --entrypoint restic ice-school-offsite:ci restore latest --target /restore
dump="$(find "$BACKUP_SOURCE_DIR" -maxdepth 1 -type f -name 'ice_school_*.dump' | LC_ALL=C sort | tail -1)"
cmp "$dump" "$tmp/restore/backups/$(basename "$dump")"
echo "Encrypted upload/restore with real S3 protocol passed (ephemeral MinIO only)"

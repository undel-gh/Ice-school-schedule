#!/usr/bin/env bash
# Safe production lifecycle wrapper for compose.production.yaml.
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ENV_FILE="$ROOT_DIR/.env.production"
COMPOSE=(docker compose --project-directory "$ROOT_DIR" --env-file "$ENV_FILE" -f "$ROOT_DIR/compose.production.yaml")
ACTION=""
TARGET_REF=""
PURGE_DATA=0
OLD_SHA=""
CHECKED_OUT=0
MIGRATION_STARTED=0
RELEASE_IMAGE_TAG=""
CANDIDATE_IMAGE_TAG=""
declare -A CFG=()

info() { printf '[install] %s\n' "$*"; }
die() { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'HELP'
Usage:
  ./install.sh install
  ./install.sh update [--ref <tag|commit|branch>]
  ./install.sh reinstall
  ./install.sh status
  ./install.sh uninstall [--purge-data]

Run from an existing Git checkout on the production host.
An ordinary uninstall preserves named volumes, .env.production and backups.
--purge-data removes Compose-managed named volumes ONLY after interactive
confirmation; .env.production and host backup files are preserved.
HELP
}

on_exit() {
  local code=$?
  trap - EXIT
  # Candidate tags are disposable: after promotion the release tag retains
  # the image, while on failure the release tag was never modified.
  # Housekeeping must not override the command's original exit status.
  if [[ -n "$CANDIDATE_IMAGE_TAG" ]]; then
    if ! docker image rm "ice-school-app:$CANDIDATE_IMAGE_TAG" >/dev/null 2>&1; then
      info "WARNING: could not remove temporary image tag ice-school-app:$CANDIDATE_IMAGE_TAG"
    fi
  fi
  if (( code != 0 )) && [[ "$ACTION" == update ]]; then
    if (( CHECKED_OUT == 1 && MIGRATION_STARTED == 0 )); then
      info "Update failed before migrations; restoring source revision $OLD_SHA"
      git -C "$ROOT_DIR" checkout --detach "$OLD_SHA" >/dev/null 2>&1 ||
        info "Could not restore source revision automatically; inspect Git checkout."
    elif (( MIGRATION_STARTED == 1 )); then
      info "Update failed after migration gate started. Database schema may have changed."
      info "Do NOT blindly roll back the image or database. Inspect migrations and restore plan."
    fi
  fi
  exit "$code"
}
trap on_exit EXIT

parse_args() {
  (( $# >= 1 )) || { usage >&2; exit 2; }
  ACTION=$1
  shift
  case "$ACTION" in
    install|update|reinstall|status|uninstall) ;;
    -h|--help|help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  while (( $# > 0 )); do
    case "$1" in
      --ref)
        [[ "$ACTION" == update && $# -ge 2 && -z "$TARGET_REF" ]] ||
          die "--ref is available only once with update and needs a value"
        TARGET_REF=$2
        shift 2
        ;;
      --purge-data)
        [[ "$ACTION" == uninstall && "$PURGE_DATA" -eq 0 ]] ||
          die "--purge-data is available only with uninstall"
        PURGE_DATA=1
        shift
        ;;
      *) die "Unknown argument: $1" ;;
    esac
  done
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

require_docker() {
  need_command docker
  local version major
  version="$(docker compose version --short)" ||
    die "Docker Compose v2+ is required"
  version="${version#v}"
  major="${version%%.*}"
  [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 2 )) ||
    die "Docker Compose v2+ is required"
  docker info --format '{{.ServerVersion}}' >/dev/null ||
    die "Cannot connect to Docker daemon"
}

require_git() {
  need_command git
  [[ -d "$ROOT_DIR/.git" || -f "$ROOT_DIR/.git" ]] ||
    die "Run from a Git checkout; clone the repository first"
}

prepare_env() {
  if [[ ! -e "$ENV_FILE" ]]; then
    [[ -f "$ROOT_DIR/.env.production.example" ]] ||
      die "Production environment template is missing"
    cp --no-clobber "$ROOT_DIR/.env.production.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    die "Created .env.production. Edit APP_HOST, database credentials and Django secret, then rerun install."
  fi
  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] ||
    die ".env.production must be a regular file, not a symlink"
}

load_env() {
  CFG=()
  local line key value mode trimmed
  local assignment_re='^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$'
  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] ||
    die ".env.production is missing or is a symlink"
  mode="$(stat -c '%a' "$ENV_FILE")" || die "Cannot inspect .env.production"
  [[ "$mode" =~ ^[0-7]+$ ]] ||
    die "Invalid .env.production permissions"
  (( (8#$mode & 077) == 0 )) ||
    die ".env.production must not be readable by group/others (chmod 600)"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    trimmed="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$trimmed" || "${trimmed:0:1}" == "#" ]] && continue
    [[ "$line" =~ $assignment_re ]] ||
      die "Invalid .env.production format (expected KEY=VALUE)"
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]] ||
       [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
      value="${value:1:${#value}-2}"
    fi
    CFG["$key"]="$value"
  done < "$ENV_FILE"
  # Preserve existing deployments: offsite overlay is activated only when
  # explicitly configured, never by implicit Compose profiles.
  OFFSITE_ENABLED="${CFG[OFFSITE_ENABLED]:-0}"
  [[ "$OFFSITE_ENABLED" == 0 || "$OFFSITE_ENABLED" == 1 ]] ||
    die "OFFSITE_ENABLED must be 0 or 1"
  COMPOSE=(docker compose --project-directory "$ROOT_DIR" --env-file "$ENV_FILE"
    -f "$ROOT_DIR/compose.production.yaml")
  if [[ "$OFFSITE_ENABLED" == 1 ]]; then
    COMPOSE+=(-f "$ROOT_DIR/compose.offsite.yaml")
  fi
}

validate_env() {
  local key host allowed password secret backup_path
  for key in APP_HOST DJANGO_ALLOWED_HOSTS DJANGO_SECRET_KEY DJANGO_DEBUG \
             POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST \
             POSTGRES_PORT SCHOOL_TIME_ZONE DJANGO_SECURE_SSL_REDIRECT; do
    [[ -n "${CFG[$key]:-}" ]] || die "Missing required production setting $key"
  done
  host="${CFG[APP_HOST]}"
  [[ "$host" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ &&
     "$host" == *.* && "$host" != *..* &&
     "$host" != school.example.com && "$host" != *.example.com ]] ||
    die "APP_HOST must be a real configured DNS hostname"
  allowed=",${CFG[DJANGO_ALLOWED_HOSTS]//[[:space:]]/},"
  [[ "$allowed" == *",$host,"* ]] ||
    die "DJANGO_ALLOWED_HOSTS must include APP_HOST"
  [[ "${CFG[DJANGO_DEBUG]}" == 0 ]] ||
    die "DJANGO_DEBUG must be 0 in production"
  [[ "${CFG[DJANGO_SECURE_SSL_REDIRECT]}" == 1 ]] ||
    die "DJANGO_SECURE_SSL_REDIRECT must be 1"
  [[ "${CFG[POSTGRES_HOST]}" == db && "${CFG[POSTGRES_PORT]}" == 5432 ]] ||
    die "Production PostgreSQL must use db:5432"
  secret="${CFG[DJANGO_SECRET_KEY]}"
  password="${CFG[POSTGRES_PASSWORD]}"
  (( ${#secret} >= 32 )) && [[ "$secret" != *replace* ]] ||
    die "Set a strong non-placeholder DJANGO_SECRET_KEY"
  (( ${#password} >= 20 )) && [[ "$password" != *replace* ]] ||
    die "Set a strong non-placeholder POSTGRES_PASSWORD"
  backup_path="${CFG[BACKUP_HOST_PATH]:-./backups}"
  validate_offsite
  [[ "$backup_path" != *'$'* && "$backup_path" != *'~'* &&
     "$backup_path" != *'*'* && "$backup_path" != *'?'* &&
     "$backup_path" != *'['* ]] ||
    die "BACKUP_HOST_PATH must be a literal relative or absolute path"
}

validate_offsite() {
  [[ "$OFFSITE_ENABLED" == 1 ]] || return 0
  local repo key path mode
  repo="${CFG[OFFSITE_RESTIC_REPOSITORY]:-}"
  # TLS is mandatory on production S3 endpoints; disallow URL credentials.
  [[ "$repo" == s3:https://*/* && "$repo" != *@* ]] ||
    die "OFFSITE_RESTIC_REPOSITORY must use s3:https://endpoint/bucket/prefix"
  for key in OFFSITE_RESTIC_PASSWORD_HOST_FILE OFFSITE_AWS_CREDENTIALS_HOST_FILE; do
    path="${CFG[$key]:-}"
    [[ "$path" == /* && -f "$path" && ! -L "$path" ]] ||
      die "$key must be an absolute path to a regular non-symlink file"
    path="$(realpath -e "$path")" || die "$key cannot be resolved safely"
    [[ "$path" != "$ROOT_DIR" && "$path" != "$ROOT_DIR/"* ]] ||
      die "$key must be outside the Git checkout and local backups"
    mode="$(stat -c '%a' "$path")"
    [[ "$mode" =~ ^[0-7]+$ ]] && (( (8#$mode & 077) == 0 )) ||
      die "$key must not be readable by group or others (chmod 600)"
  done
}

prepare_backup_dir() {
  local backup_path
  backup_path="${CFG[BACKUP_HOST_PATH]:-./backups}"
  [[ "$backup_path" = /* ]] || backup_path="$ROOT_DIR/$backup_path"
  if [[ ! -d "$backup_path" ]]; then
    mkdir -p -- "$backup_path" ||
      die "Cannot create backup directory"
  fi
  [[ -d "$backup_path" ]] || die "Cannot access backup directory"
  if [[ -O "$backup_path" ]]; then
    chmod 700 "$backup_path"
  else
    info "Backup directory is root-owned; Docker backup service must verify write access"
  fi
}

compose_validate() {
  "${COMPOSE[@]}" config --quiet ||
    die "Production Compose validation failed"
}

container_state() {
  local service=$1 id
  id="$("${COMPOSE[@]}" ps --all --quiet "$service" 2>/dev/null || true)"
  if [[ -z "$id" ]]; then
    printf 'absent\n'
    return 0
  fi
  if [[ "$id" == *$'\n'* ]]; then
    printf 'ambiguous-multiple-containers\n'
    return 0
  fi
  docker inspect --format \
    '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{if .State.Running}}running{{else}}stopped{{end}}{{end}}' \
    "$id" 2>/dev/null || printf 'unknown\n'
}

wait_state() {
  local service=$1 wanted=$2 attempts=$3 current attempt
  for (( attempt=1; attempt<=attempts; attempt++ )); do
    current="$(container_state "$service")"
    if [[ "$current" == "$wanted" ]]; then
      info "$service: $wanted"
      return 0
    fi
    [[ "$current" == ambiguous-multiple-containers ]] &&
      die "$service has multiple container IDs; inspect 'docker compose ps -a' before retrying"
    [[ "$current" == unhealthy || "$current" == stopped ]] &&
      die "$service entered state $current"
    sleep 2
  done
  die "$service did not reach $wanted (last state: $current)"
}

require_existing_db() {
  [[ "$(container_state db)" == healthy ]] ||
    die "Database container is not healthy. Check status; do not run updates without a working DB."
}

backup_once() {
  info "Creating and validating a fresh PostgreSQL backup before modifying the installation"
  "${COMPOSE[@]}" run --rm --no-deps -T backup /ops/postgres_backup.sh --once ||
    die "Pre-update backup failed. No migration or rollout attempted."
  if [[ "$OFFSITE_ENABLED" == 1 ]]; then
    # A risky database upgrade must not proceed until the *new* dump has
    # reached encrypted off-host storage (not merely queued for polling).
    info "Uploading verified pre-migration dump to encrypted offsite repository"
    "${COMPOSE[@]}" build offsite-backup ||
      die "Cannot build offsite backup image; rollout stopped"
    "${COMPOSE[@]}" run --rm --no-deps -T offsite-backup --once ||
      die "Encrypted offsite backup failed; rollout stopped before migration"
  fi
}

backup_before_initial_migration() {
  local table_count
  table_count="$("${COMPOSE[@]}" exec -T db psql \
    -U "${CFG[POSTGRES_USER]}" -d "${CFG[POSTGRES_DB]}" -Atqc \
    "SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public';")" ||
    die "Cannot inspect database before install; refusing to migrate"
  [[ "$table_count" =~ ^[0-9]+$ ]] ||
    die "Unexpected database inspection output; refusing to migrate"
  if (( table_count > 0 )); then
    # Install can be rerun after uninstall; persisted data still needs backup.
    backup_once
  else
    info "New empty database: skipping empty pre-migration dump"
  fi
}

set_image_tag() {
  local sha tag
  sha="$(git -C "$ROOT_DIR" rev-parse --verify HEAD)" ||
    die "Cannot identify source revision"
  # Match the image reference used by manual Compose operations. Exporting a
  # transient commit tag would cause a later plain 'docker compose up' to
  # select the old :local image unless .env.production were modified.
  tag="${CFG[APP_IMAGE_TAG]:-local}"
  [[ "$tag" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] ||
    die "APP_IMAGE_TAG must be a valid Docker tag"
  RELEASE_IMAGE_TAG="$tag"
  export APP_IMAGE_TAG="$RELEASE_IMAGE_TAG"
  info "Application revision: $sha (release image: ice-school-app:$RELEASE_IMAGE_TAG)"
}

migration_gate() {
  local sha
  sha="$(git -C "$ROOT_DIR" rev-parse --verify HEAD)" ||
    die "Cannot identify source revision"
  # A unique candidate avoids overwriting the live release tag on build.
  # Compose prefers this exported value over APP_IMAGE_TAG in .env.production.
  CANDIDATE_IMAGE_TAG="candidate-${sha:0:12}-$$"
  [[ "$CANDIDATE_IMAGE_TAG" != "$RELEASE_IMAGE_TAG" ]] ||
    die "Candidate and release Docker tags must differ"

  export APP_IMAGE_TAG="$CANDIDATE_IMAGE_TAG"
  info "Building candidate image ice-school-app:$CANDIDATE_IMAGE_TAG"
  "${COMPOSE[@]}" build migrate ||
    die "Candidate build failed; release image tag is unchanged"

  MIGRATION_STARTED=1
  info "Running migrations on candidate image (release tag unchanged)"
  "${COMPOSE[@]}" run --rm --no-deps -T migrate ||
    die "Migration gate failed; release image tag is unchanged and rollout stopped"

  if [[ "$OFFSITE_ENABLED" == 1 ]]; then
    "${COMPOSE[@]}" build offsite-backup ||
      die "Offsite service build failed; release tag remains unchanged"
  fi

  # Promote only after migrate succeeds. A later ordinary Compose invocation
  # will select the same release tag as the installer, never a failed image.
  info "Promoting verified candidate to ice-school-app:$RELEASE_IMAGE_TAG"
  docker image tag \
    "ice-school-app:$CANDIDATE_IMAGE_TAG" \
    "ice-school-app:$RELEASE_IMAGE_TAG" ||
    die "Candidate tag promotion failed; release tag is unchanged"

  export APP_IMAGE_TAG="$RELEASE_IMAGE_TAG"
}

rollout_services() {
  info "Recreating application, scheduler and backup containers"
  "${COMPOSE[@]}" up -d --no-deps --force-recreate web scheduler backup
  wait_state web healthy 65
  info "Starting or recreating reverse proxy"
  "${COMPOSE[@]}" up -d --no-deps --force-recreate proxy
  wait_state proxy running 20
  if [[ "$OFFSITE_ENABLED" == 1 ]]; then
    info "Starting encrypted offsite backup uploader"
    "${COMPOSE[@]}" up -d --no-deps --force-recreate offsite-backup
    wait_state offsite-backup healthy 120
  else
    info "WARNING: OFFSITE_ENABLED=0; database dumps remain local only"
  fi
  info "Completed revision: $(git -C "$ROOT_DIR" rev-parse --verify HEAD)"
  "${COMPOSE[@]}" ps --all
}

run_install() {
  prepare_env
  load_env
  validate_env
  prepare_backup_dir
  compose_validate
  require_git
  set_image_tag
  info "Starting PostgreSQL (persistent volume retained)"
  "${COMPOSE[@]}" up -d db
  wait_state db healthy 65
  # A preserved volume may contain prior data even on a fresh container.
  backup_before_initial_migration
  migration_gate
  rollout_services
}

check_git_clean() {
  local changes
  changes="$(git -C "$ROOT_DIR" status --porcelain --untracked-files=normal)"
  [[ -z "$changes" ]] ||
    die "Tracked/untracked source changes detected. Use a clean production checkout."
}

resolve_ref() {
  local selected=$1 candidate=""
  if [[ -z "$selected" ]]; then
    candidate="refs/remotes/origin/main"
  else
    [[ "$selected" =~ ^[A-Za-z0-9_][A-Za-z0-9_./-]*$ &&
       "$selected" != *..* && "$selected" != *//* ]] ||
      die "Unsafe --ref value"
    if git -C "$ROOT_DIR" rev-parse --verify --quiet \
        "refs/remotes/origin/$selected^{commit}" >/dev/null; then
      candidate="refs/remotes/origin/$selected"
    elif git -C "$ROOT_DIR" rev-parse --verify --quiet \
        "refs/tags/$selected^{commit}" >/dev/null; then
      candidate="refs/tags/$selected"
    else
      candidate="$selected"
    fi
  fi
  git -C "$ROOT_DIR" rev-parse --verify --quiet --end-of-options \
    "$candidate^{commit}" || die "Unknown Git ref $selected"
}

run_update() {
  [[ -f "$ENV_FILE" ]] || die "Run install before update"
  load_env
  validate_env
  prepare_backup_dir
  compose_validate
  require_git
  check_git_clean
  OLD_SHA="$(git -C "$ROOT_DIR" rev-parse --verify HEAD)"
  info "Currently deployed checkout: $OLD_SHA"
  require_existing_db
  backup_once
  info "Fetching remote refs"
  git -C "$ROOT_DIR" fetch --prune --tags origin
  local new_sha
  new_sha="$(resolve_ref "$TARGET_REF")"
  info "Target revision: $new_sha"
  if [[ "$new_sha" != "$OLD_SHA" ]]; then
    git -C "$ROOT_DIR" checkout --detach "$new_sha"
    CHECKED_OUT=1
  fi
  # Revalidate after checkout: the target revision may alter Compose or env sample.
  load_env
  validate_env
  compose_validate
  set_image_tag
  migration_gate
  rollout_services
}

run_reinstall() {
  [[ -f "$ENV_FILE" ]] || die "Run install before reinstall"
  load_env
  validate_env
  prepare_backup_dir
  compose_validate
  require_git
  require_existing_db
  backup_once
  set_image_tag
  migration_gate
  rollout_services
}

run_status() {
  [[ -f "$ENV_FILE" ]] || die "Production environment not found"
  load_env
  compose_validate
  if command -v git >/dev/null 2>&1; then
    info "Checkout revision: $(git -C "$ROOT_DIR" rev-parse --verify HEAD 2>/dev/null || printf unknown)"
  fi
  "${COMPOSE[@]}" ps --all
  local service
  for service in db web scheduler backup proxy; do
    info "$service: $(container_state "$service")"
  done
  if [[ "$OFFSITE_ENABLED" == 1 ]]; then
    info "offsite-backup: $(container_state offsite-backup)"
    [[ "$(container_state offsite-backup)" == healthy ]] ||
      die "Encrypted offsite backup is not healthy"
  else
    info "WARNING: offsite-backup disabled; no encrypted external copies"
  fi
  [[ "$(container_state web)" == healthy ]] ||
    die "Application healthcheck is not healthy"
}

run_uninstall() {
  [[ -f "$ENV_FILE" ]] || die "Production environment not found"
  load_env
  compose_validate
  if (( PURGE_DATA )); then
    local expected answer
    expected="PURGE ${CFG[APP_HOST]:-UNCONFIGURED}"
    [[ -t 0 ]] ||
      die "--purge-data requires an interactive terminal; refusing non-interactive destruction"
    printf '[install] DANGER: named DB and Caddy volumes will be deleted.\n' >&2
    printf '[install] Host backups and .env.production will remain intact.\n' >&2
    read -r -p "Type '$expected' to confirm: " answer ||
      die "Destructive operation cancelled"
    [[ "$answer" == "$expected" ]] || die "Confirmation mismatch; cancelled"
    "${COMPOSE[@]}" down --remove-orphans --volumes
    info "Compose-managed named volumes deleted. Host backups and env retained."
  else
    "${COMPOSE[@]}" down --remove-orphans
    info "Uninstalled containers and network; DB, Caddy volumes, env and backups retained."
  fi
}

main() {
  parse_args "$@"
  require_docker
  case "$ACTION" in
    install) run_install ;;
    update) run_update ;;
    reinstall) run_reinstall ;;
    status) run_status ;;
    uninstall) run_uninstall ;;
  esac
}
main "$@"

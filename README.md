# Ice school schedule

Django/PostgreSQL application for schedule publication, RSVP, coach-confirmed attendance and entitlement accounting for a figure skating school.

## Development bootstrap

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

set -a
source .env.example
set +a

python manage.py migrate
python manage.py check
python manage.py makemigrations --check
pytest -q
```

SQLite is used when `POSTGRES_DB` is not set, which is useful for basic local checks. PostgreSQL is the target database and is required for the concurrency tests that will be added with the application-service layer.

## PostgreSQL environment

```bash
export POSTGRES_DB=ice_school
export POSTGRES_USER=ice_school
export POSTGRES_PASSWORD=change-me
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
```

The domain documentation lives in `docs/` and is the source for business rules implemented in the model/service layers.

Set `SCHOOL_TIME_ZONE` to the school's local IANA time zone (for example
`Europe/Riga`, `America/Toronto` or `Asia/Tokyo`). The open-source
default is `UTC`. `DJANGO_TIME_ZONE` may be configured separately; school
schedule forms and labels use `SCHOOL_TIME_ZONE`.


## PostgreSQL development database

The application uses SQLite when `POSTGRES_DB` is unset, which is sufficient for basic smoke tests. PostgreSQL is required for row-locking and concurrency tests.

Start PostgreSQL:

```bash
docker compose up -d db
```

Load the development environment:

```bash
set -a
source .env.example
set +a
```

Apply migrations and run the complete test suite:

```bash
python manage.py migrate
python manage.py check
pytest -q
```

With PostgreSQL enabled, the subscription concurrency test must run rather than be skipped.

To stop PostgreSQL:

```bash
docker compose down
```

Use `docker compose down -v` only when you intentionally want to delete the development database volume.


## Operational commands

The current foundation exposes the recurring scheduling/lifecycle operations as
Django management commands. These commands are a secondary interface for
system administration, automation, diagnostics and emergency operations.

The product requirement is **web-first**: managers and coaches must be able to
perform every normal human workflow through the authenticated web interface.
A management command must not be the only UI for a manager/coach operation.
Pure scheduler/lifecycle jobs may remain CLI/cron-only, but their failures and
conflicts must be visible and resolvable from the manager web UI.

The next subscription/web iteration is specified in
`docs/Абонементы — расчётные периоды, заморозка и web-операции.md`.

Available management commands:

```bash
python manage.py generate_lessons \
  --template <schedule-template-uuid> \
  --from-date 2026-09-01 \
  --until-date 2026-09-30

# recurring scheduler mode: all active templates for the next 60 days
python manage.py generate_lessons \
  --all-active \
  --horizon-days 60

python manage.py publish_daily_schedule --date 2026-09-23

python manage.py evaluate_lesson --lesson <lesson-uuid>

python manage.py complete_lesson --lesson <lesson-uuid>

python manage.py process_subscription_lifecycle --date 2026-09-23
```

These commands call the same application services used by the web/admin layer.

Additional administrative commands are available for explicit,
permission-checked operations:

```bash
python manage.py issue_subscription \
  --student <student-uuid> \
  --plan <plan-uuid> \
  --valid-from 2026-09-01 \
  --valid-until 2026-09-30 \
  --actor <username>

python manage.py cancel_subscription \
  --subscription <subscription-uuid> \
  --actor <username>

python manage.py confirm_lesson \
  --lesson <lesson-uuid> \
  --actor <username>

python manage.py cancel_lesson \
  --lesson <lesson-uuid> \
  --reason administrative \
  --actor <username>

python manage.py reschedule_lesson \
  --lesson <lesson-uuid> \
  --starts-at 2026-10-02T18:00:00 \
  --ends-at 2026-10-02T19:00:00 \
  --reason administrative \
  --actor <username>

python manage.py verify_medical_absence \
  --justification <justification-uuid> \
  --valid-until 2026-10-31 \
  --actor <username>

python manage.py reject_medical_absence \
  --justification <justification-uuid> \
  --actor <username>

python manage.py revoke_medical_absence \
  --justification <justification-uuid> \
  --actor <username>

python manage.py create_group_membership \
  --student <student-uuid> \
  --group <group-uuid> \
  --starts-on 2026-09-01 \
  --actor <username>

python manage.py update_group_membership \
  --membership <membership-uuid> \
  --starts-on 2026-09-01 \
  --ends-on 2027-05-31 \
  --actor <username>

python manage.py grant_administrative_makeup \
  --allowance <allowance-uuid> \
  --source-lesson <lesson-uuid> \
  --valid-from 2026-10-01 \
  --valid-until 2026-10-31 \
  --reason 'Administrative correction' \
  --actor <username>

python manage.py create_schedule_template \
  --group <group-uuid> \
  --lesson-type <lesson-type-uuid> \
  --coach <coach-profile-uuid> \
  --venue <venue-uuid> \
  --weekday 1 \
  --start-time 18:00 \
  --duration-minutes 60 \
  --valid-from 2026-09-01 \
  --actor <username>

python manage.py version_schedule_template \
  --template <schedule-template-uuid> \
  --effective-from 2026-10-01 \
  --start-time 19:00 \
  --actor <username>

python manage.py skip_template_occurrence \
  --template <schedule-template-uuid> \
  --date 2026-10-29 \
  --actor <username>
```

The named actor must possess the Django model permission required by the
underlying application service. Direct editing of lifecycle/ledger rows in
Django Admin remains prohibited.

Coverage rebinds and one-time entitlement administration currently remain
service-level operations and are intended for a
dedicated administrative UI rather than direct model editing.

## Financial responsibility boundary

The application owns operational school rules: schedules, attendance,
Subscription periods and ICE/HALL visit rights, make-up entitlements, paid
freeze/deferred-make-up eligibility, and the fact that a required fee was
confirmed by a manager.

It is **not** the accounting or payment system. Monetary tariff calculation,
personal discounts, price recalculation, credits, refunds, debt and the final
amount due are handled by accounting outside this application. A paid workflow
may store `fee_confirmed_at`, but it does not calculate or persist the amount
paid.

`BILLING_RECALCULATION` remains a reserved model enum for historical/future
compatibility, but it is not offered by the manager catalog; application
services and model validation reject creation or conversion of policy actions
to that type (including Django Admin/ModelForm paths). When a
legacy policy containing this action is versioned, the historical action stays
on the old version and is deliberately not copied to the new operational
version. Adding a monetary ledger or payment-provider integration is a separate
future scope.

## External identity login and invitations

Student, guardian and coach onboarding is invitation-only. An unknown Yandex or
VK ID account is never allowed to create a school account by simply visiting
the ordinary login page.

Managers create one-time invitations in the web UI at
`/manager/school/invitations/`. An invitation targets either:

- one Student with SELF or GUARDIAN access plus a human-readable account
  label such as "Мама Ани"; or
- a new CoachProfile with a fixed display name.

The raw invitation token is shown only once. The database stores only its
SHA-256 hash, expiration and lifecycle metadata. When the invited person
successfully authenticates, creation of the technical Django User,
ExternalIdentity and StudentAccess/CoachProfile happens in one transaction.

External-only users receive an unguessable technical username, an unusable
Django password and the human-readable display label from the invitation.
User-facing and manager-facing UI must show that label instead of the technical
username whenever possible. No provider access token, refresh token, email,
phone, avatar, first name or last name is persisted by this flow.

Configure either or both providers:

```bash
YANDEX_OAUTH_CLIENT_ID=...
YANDEX_OAUTH_CLIENT_SECRET=...   # optional when PKCE is accepted
YANDEX_OAUTH_SCOPE=login:info

VKID_CLIENT_ID=...
VKID_SCOPE=
```

Register the callback URLs exactly for the public school origin:

```text
https://<school-host>/accounts/external/yandex/callback/
https://<school-host>/accounts/external/vk/callback/
```

Both providers use Authorization Code + PKCE and a one-time `state`.
Yandex uses `S256`; VK ID follows the current official SDK and sends
`code_challenge_method=s256`. VK ID additionally returns a `device_id`
with the authorization code; it is required for the token exchange.

Yandex identity is **always** the pairwise `psuid`. If Yandex does not return
`psuid`, authentication fails; the implementation never falls back to the
global provider `id`, so one account cannot silently change subject format
between logins.

After onboarding, the ordinary login buttons resolve only an existing
ExternalIdentity. Authenticated users can connect the other configured
provider at `/accounts/external/identities/`; one internal User may have at
most one identity per provider.

If an already authenticated User opens another invitation and chooses a
provider that is not linked yet, that provider is linked to the **same User**
before the invitation is consumed. This prevents a parent who uses Yandex for
the first child and VK ID for the second invitation from accidentally creating
two school accounts.

External authentication is deliberately disabled for `is_staff`,
`is_superuser` and any account that has manager-operations permissions.
Those accounts must use local username/password authentication, preserving the
password/axes security boundary. Privileged signed-in sessions also cannot
accept external invitations.

Users may unlink one provider themselves only while another external provider
remains. Managers with `change_externalidentity` may unlink a compromised
provider. The final provider of an external-only User may be removed only
after that User has been deactivated. Deactivation is available in manager web
and prevents the inactive User from authenticating through Django's
ModelBackend.

For a compromised single-provider account the supported recovery procedure is:

```text
1. Deactivate the existing User.
2. Unlink the compromised ExternalIdentity.
3. Create an AccountInvitation of kind RECOVERY bound to that exact User.
4. Send the one-time recovery URL to the account owner.
   Treat the URL as a bearer secret: during compromise, deliver it through a
   trusted channel independent of the compromised account (for example in
   person, by phone, or via another verified messenger/account).
5. The owner authenticates with Yandex/VK while signed out.
6. The provider is linked to the existing User and the same User is reactivated.
```

A recovery invitation can be issued only when the target User is inactive,
non-privileged and has no remaining ExternalIdentity rows. It never creates a
replacement User, StudentAccess or CoachProfile, so all existing history and
school relationships remain attached to the same UUID.

For an external-only User, manager unlink also rotates the unusable password
value so already-issued Django sessions fail the session-auth-hash check.
Every unlink records `ExternalIdentityUnlinked`; recovery records
`AccountRecovered`.

Local username/password login remains the staff and emergency administration
channel.

## Privileged MFA

Local password authentication for privileged accounts is protected by TOTP
multi-factor authentication using `django-otp`.

MFA is mandatory when a User is any of:

- `is_staff=True`;
- `is_superuser=True`;
- assigned at least one manager-operation permission directly or through a
  Django Group.

External Yandex/VK login remains disabled for these accounts. The first factor
is the local Django password (and therefore remains protected by
`django-axes`); the second factor is TOTP from an authenticator application.
SMS and email OTP are deliberately not enabled.

Privilege promotion does not turn an existing external-auth session into a
valid first factor. If a signed-in external-only User gains staff/manager
privileges while it still has an unusable Django password, privileged access
is terminated and MFA enrollment is refused. A local password must first be
established through an administrative recovery procedure (for example Django
Admin or `changepassword`) or, preferably, a separate privileged account
must be used.

On the first successful password login, a privileged user must enroll a TOTP
device before an authenticated application session is created. The setup page
shows an `otpauth://` QR code and a manual secret. The enrollment code is
verified before the device is marked confirmed. The temporary password-only
pre-authentication state expires after `MFA_PREAUTH_TTL_SECONDS` (300 seconds
by default).

Enrollment also creates 10 one-time recovery codes. They are shown once, may
be used in any order, and each is deleted by the OTP backend when consumed.
MFA setup/challenge/recovery pages use `Cache-Control: no-store`.

The privileged verified session has an **absolute** maximum age controlled by
`MFA_PRIVILEGED_SESSION_MAX_AGE_SECONDS` (43200 seconds / 12 hours by default).
The timestamp of the successful MFA verification is stored in the server-side
session and checked by middleware on every privileged request. Ordinary session
activity cannot extend this deadline; after it expires, the user must enter the
local password and MFA again.
The MFA middleware also intercepts privileged Django sessions that existed
before MFA deployment, including access to `/admin/`; a password-only
session is therefore not grandfathered into privileged access. **Any**
privileged authenticated session that is not OTP-verified is signed out and
must re-enter the local password through the MFA-aware login flow before a
TOTP challenge or enrollment. This prevents stale/externally-created sessions
from being treated as the required local-password first factor. Anonymous
`/admin/login/` is redirected to the same MFA-aware local login flow, so
Django Admin cannot establish a separate password-only staff session.

Verified users manage MFA at:

```text
/accounts/mfa/security/
```

Self-service recovery-code regeneration and authenticator replacement both
require the current local password in addition to the already MFA-verified
session. Regeneration revokes all old recovery codes immediately.
Authenticator replacement is two-phase. The old confirmed TOTP remains valid
while a separate replacement device is pending. Only after the new TOTP code
is successfully verified does one transaction confirm the new device and
revoke the old device(s); a fresh recovery-code set is then issued. Abandoning
or timing out replacement therefore does not remove the last working
authenticator.

For server-side emergency recovery, `django-otp` provides
`addstatictoken`. Use:

```bash
python manage.py addstatictoken -h
```

to create a single emergency static token for the affected privileged account.
A static-only emergency login is allowed to pass the MFA challenge, but if the
account has no confirmed TOTP device it is immediately forced through fresh
TOTP enrollment. This makes the command a break-glass recovery mechanism, not
a permanent static-code MFA mode.

Sensitive TOTP/static-token admin helpers are hidden with
`OTP_ADMIN_HIDE_SENSITIVE_DATA=True`. Audit events record enrollment,
successful MFA authentication, recovery-code use/regeneration, and the start
of authenticator replacement; OTP values, QR secrets and recovery-code
contents are never copied into AuditEvent payloads.

### MFA deployment / rollout

Before deploying the MFA-enabled build:

1. Make sure at least one emergency superuser has a **usable local Django
   password** that is known through the normal privileged credential-handling
   procedure. An external-only account cannot bootstrap privileged MFA.
2. Install the updated application dependencies.
3. Run `python manage.py migrate` before serving the new build. The
   `django_otp` TOTP/static-device tables are required by middleware and the
   login flow.
4. Deploy the application, then sign in with the emergency/local privileged
   account and complete TOTP enrollment. For every newly created privileged
   employee account, deliver the initial local password over a trusted channel
   and require MFA enrollment immediately on the first login; whoever knows
   that password first can otherwise become the first person to bind TOTP.
5. Store the 10 displayed recovery codes outside the application session in an
   appropriately protected operational secret store.
6. Confirm access to both the manager UI and `/admin/` through a fresh
   password + TOTP login.

Existing privileged password sessions are intentionally not exempt from the
rollout. If they have no enrolled TOTP yet, the user must prove the local
password again before the application reveals a new MFA secret.

If a privileged user later loses both the authenticator and all recovery
codes, use the documented server-side `addstatictoken` break-glass path,
authenticate with password + that one-time token, and complete a fresh TOTP
enrollment. Do not disable the MFA middleware as a recovery procedure.

## Production checks

Production defaults are closed: `DJANGO_DEBUG` defaults to off and
`DJANGO_SECRET_KEY` is mandatory outside debug mode.

Before deployment, generate a real secret rather than using an example value:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Then run the deployment checks with production-like settings:

```bash
DJANGO_DEBUG=0 \
DJANGO_SECRET_KEY='<generated-random-secret>' \
DJANGO_ALLOWED_HOSTS='school.example' \
DJANGO_SECURE_SSL_REDIRECT=1 \
DJANGO_SECURE_HSTS_SECONDS=3600 \
python manage.py check --deploy
```

A `security.W021` warning is expected during an initial staged HSTS rollout
while `DJANGO_SECURE_HSTS_PRELOAD=0`. Do not enable preload merely to silence
the check. HSTS applies to the domain and can make HTTPS certificate/configuration
mistakes difficult to recover from.

Only after the final production hostname and all affected subdomains are
confirmed to be HTTPS-only should the deployment consider:

```bash
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=1
DJANGO_SECURE_HSTS_PRELOAD=1
```

The preload directive is a deployment policy decision, not a requirement for
the application to function.

Login attempts are rate-limited with `django-axes`; the default failure limit
is controlled by `AXES_FAILURE_LIMIT` (5 by default).

Axes uses two lockout scopes:

```text
(username + client IP)
OR
client IP
```

This avoids username-only denial of service while still limiting password
spraying from one address.

The application deliberately ignores `X-Forwarded-For`.
`X-Real-IP` is trusted **only** when the request's `REMOTE_ADDR` is present
in the comma-separated `TRUSTED_PROXY_IPS` setting. With the default empty
list, client-supplied `X-Real-IP` is ignored and Axes uses `REMOTE_ADDR`.
The production reverse proxy must overwrite `X-Real-IP` with the direct
client address rather than forwarding a client-supplied value.

For nginx, configure the proxy address or CIDR in Django and overwrite the
header:

```bash
# reverse proxy on the same host
TRUSTED_PROXY_IPS=127.0.0.1

# example Docker Compose network
TRUSTED_PROXY_IPS=172.18.0.0/16
```

```nginx
proxy_set_header X-Real-IP $remote_addr;
```

For Caddy:

```caddy
header_up X-Real-IP {remote_host}
```

The regression suite verifies this exact Axes configuration, including that a
spoofed `X-Forwarded-For` value is ignored.

`python manage.py check --deploy` emits `ice_school.W002` when production
settings leave `TRUSTED_PROXY_IPS` empty and `ice_school.W001` for invalid
IP/CIDR entries. An empty list is valid only for a deliberately direct
deployment where no reverse-proxy client IP header is used. For such a
deliberate direct deployment, silence only this specific warning:

```bash
SILENCED_SYSTEM_CHECKS=ice_school.W002
```

Only `ice_school.W002` is accepted from the
`SILENCED_SYSTEM_CHECKS` environment variable; built-in Django security
checks such as `security.W004` cannot be silenced through this deployment
shortcut.

Do not silence all deployment checks.

The IP-only lockout currently uses the same `AXES_FAILURE_LIMIT` (5 by
default) as the combined username+IP scope. This means several failed logins
from users sharing one NAT/public Wi-Fi address can temporarily block that
address for everyone. This is an explicit MVP trade-off against password
spraying; if it proves too aggressive in production, use a custom Axes
lockout policy with a higher IP-only threshold rather than removing the
username+IP scope.


### Schedule generation conflicts

Lesson generation treats an overlapping non-cancelled lesson of the same
training group as an existing regular slot only when the lesson type also
matches. A cross-type overlap (for example ICE covering a scheduled HALL slot)
creates a `LessonGenerationConflict` audit event and makes
`generate_lessons --all-active` finish with `CommandError`, so cron or
monitoring can alert an operator instead of silently dropping the lesson.

If the template already has its own concrete lesson for the exact slot,
including a CANCELLED lesson, that concrete lesson is authoritative for the
slot and no generation conflict is reported. Repeated unresolved conflicts
still fail the cron command, but the same
template/expected-start/conflicting-lesson audit event is recorded only once.

If a cross-type overlap is intentional and the template occurrence has not
been generated yet, accept that decision explicitly with
`skip_template_occurrence`. The command creates the template-owned occurrence
directly as CANCELLED even while the conflicting lesson remains in place.
Past dates are rejected. The skip records both the template-level
`ScheduleTemplateOccurrenceSkipped` event and a lesson-level
`LessonCancelled` event under one correlation ID. Future generation then sees
the template-owned CANCELLED occurrence as the authoritative decision for that
slot.

Direct cancellation of a DRAFT lesson is rejected when it has an active
`LessonEnrollment` or active `OneTimeEntitlement`. Use
`reschedule_lesson` instead so bookings can move to the replacement lesson.


## Production operations

The repository includes a small production Docker Compose stack in
`compose.production.yaml`. It deliberately stays on a single-host operational
model for the pilot:

```text
Internet
   |
   v
Caddy :80/:443
   |
   v
Gunicorn / Django
   |
   v
PostgreSQL

sidecars:
- scheduler -> Django lifecycle/generation commands
- backup    -> PostgreSQL custom-format dumps
```

Celery and Redis are intentionally not required for these recurring jobs. The
current scheduled commands are idempotent and the expected deployment has one
scheduler replica.

### First deployment

Create the production environment file and replace every example secret/domain:

```bash
cp .env.production.example .env.production
chmod 600 .env.production

mkdir -p backups
chmod 700 backups
```

At minimum set:

- `APP_HOST` and the matching `DJANGO_ALLOWED_HOSTS`;
- a long random `DJANGO_SECRET_KEY`;
- a long random `POSTGRES_PASSWORD`;
- the school time zone;
- external-provider credentials when Yandex/VK login is enabled.

Create public DNS for `APP_HOST` before starting the proxy. TCP 80 and 443
must reach this host so Caddy can obtain/renew the public TLS certificate.

Build and start:

```bash
docker compose \
  --env-file .env.production \
  -f compose.production.yaml \
  build

docker compose \
  --env-file .env.production \
  -f compose.production.yaml \
  up -d
```

Startup ordering is explicit:

1. PostgreSQL must become healthy.
2. the one-shot `migrate` service must finish successfully;
3. Gunicorn starts and must pass `/healthz/`;
4. Caddy starts proxying only after web is healthy;
5. scheduler and backup start after the database/migration prerequisites.

Inspect state and logs:

```bash
docker compose --env-file .env.production -f compose.production.yaml ps

docker compose --env-file .env.production -f compose.production.yaml \
  logs -f web proxy scheduler backup
```

Caddy keeps ordinary HTTP access logs, but deliberately skips request logging
for paths that can contain bearer/one-time secrets:

```text
/accounts/external/invite/*
/accounts/external/callback/*
/accounts/reset/*
```

This prevents invitation/recovery tokens, OAuth authorization callbacks and
Django password-reset tokens from being copied into proxy logs. Do not remove
these exclusions when changing access-log formatting.

Gunicorn access logging is deliberately disabled; otherwise the same sensitive
request target would be logged a second time behind Caddy and bypass these
path exclusions. Gunicorn error logs remain enabled. Caddy is the single
source of ordinary HTTP access logs.

All production services use Docker's `json-file` driver with bounded rotation
to avoid an unattended host filling its disk. Defaults are:

```text
DOCKER_LOG_MAX_SIZE=10m
DOCKER_LOG_MAX_FILES=5
```

These limits apply per container. Central/off-host log shipping can be added
later, but disabling local rotation is not recommended.

The database is not published to the host network. Gunicorn is exposed only on
the private Compose network; public traffic enters through Caddy.

Caddy has the fixed private address `172.30.0.10`, which matches the default
production `TRUSTED_PROXY_IPS`. If the `172.30.0.0/24` subnet conflicts with
the host/network environment, change both the Compose subnet/proxy address and
`TRUSTED_PROXY_IPS` together. Do not broaden trusted proxy addresses merely
to make the warning disappear.

Static files are collected into the application image and served by
WhiteNoise. The proxy does not need a writable static-files volume.

### Scheduler

The `scheduler` container runs both jobs immediately after startup, then
repeats them independently:

```text
process_subscription_lifecycle
    default interval: 3600 s

generate_lessons --all-active --horizon-days 60
    default interval: 21600 s
```

Configure them with:

```bash
SCHEDULER_LIFECYCLE_INTERVAL_SECONDS=3600
SCHEDULER_GENERATION_INTERVAL_SECONDS=21600
SCHEDULER_GENERATION_HORIZON_DAYS=60
```

A failed invocation is logged with its non-zero exit code and does **not**
terminate the scheduler process; the job is attempted again at its next
interval. This is important for generation conflicts: they remain visible as
command failures/audit events without taking down unrelated lifecycle work.

Normal operational inspection:

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  logs --since 24h scheduler
```

A manual diagnostic run still uses the same management command:

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  exec scheduler python manage.py process_subscription_lifecycle

docker compose --env-file .env.production -f compose.production.yaml \
  exec scheduler python manage.py generate_lessons \
  --all-active --horizon-days 60
```

Do not scale `scheduler` above one replica. The commands are designed to be
idempotent, but duplicate schedulers create unnecessary concurrent work and
duplicate operational noise.

### PostgreSQL backups

The `backup` service uses the same PostgreSQL 17 client generation as the
database server. By default it:

- runs immediately when the backup container starts;
- creates one custom-format `pg_dump` every 86400 seconds;
- writes to the host path in `BACKUP_HOST_PATH` (default `./backups`);
- uses `umask 077`;
- writes to `.partial`, validates it with `pg_restore --list`, and only then
  atomically renames it to `.dump`;
- removes dumps older than `BACKUP_RETENTION_DAYS` (default 14);
- updates a last-success marker only after a dump passes `pg_restore --list`.

The backup container healthcheck becomes unhealthy when no successful dump has
been produced for more than roughly two configured backup intervals
(`2 * BACKUP_INTERVAL_SECONDS + 300s`). A failed dump therefore remains
visible even though the loop intentionally stays alive to retry later.

Settings:

```bash
BACKUP_HOST_PATH=/var/backups/ice-school
BACKUP_INTERVAL_SECONDS=86400
BACKUP_RETENTION_DAYS=14
```

Run an additional backup immediately:

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  exec backup /ops/postgres_backup.sh --once
```

A local backup directory is **not** a complete backup strategy. Copy validated
dumps to encrypted/off-host storage under the organisation's backup policy and
regularly test restoration. Database backups contain school/personal data and
must be protected accordingly.

CI performs a basic restore smoke-test for the produced custom-format archive:
it restores the dump into a temporary PostgreSQL database and verifies that
Django migration metadata is readable. This catches structurally unusable
archives, but it does not replace periodic operator restore exercises against
real production backups.

### Restore procedure

Restoration is destructive. Select a known validated dump and stop application
writers first:

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  stop proxy web scheduler backup

set -a
source .env.production
set +a

BACKUP_FILE=ice_school_YYYYMMDDTHHMMSSZ.dump

docker compose --env-file .env.production -f compose.production.yaml \
  run --rm --no-deps -T backup cat "/backups/$BACKUP_FILE" | \
  docker compose --env-file .env.production -f compose.production.yaml \
  exec -T db pg_restore \
    --clean --if-exists --no-owner --no-privileges \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

The dump is deliberately read through a one-shot backup container instead of
the host shell. Backup files are created with `umask 077` and may therefore
be unreadable by the unprivileged host operator account; do not weaken dump
permissions merely to make restore piping convenient.

After restoring, apply any migrations required by the currently deployed
application image and start services again:

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  run --rm migrate

docker compose --env-file .env.production -f compose.production.yaml \
  up -d web scheduler backup proxy
```

Verify `/healthz/`, manager login/MFA, a representative schedule page, and
recent audit/subscription state after every restore exercise.

### Updating the application

For a normal single-host update:

```bash
git pull

docker compose --env-file .env.production -f compose.production.yaml build

docker compose --env-file .env.production -f compose.production.yaml up -d
```

The `migrate` service remains the migration gate. Before deploying a schema
change that is not backwards-compatible, take an explicit backup and follow the
migration's own rollback/forward-fix notes rather than assuming that reverting
the container image is sufficient.

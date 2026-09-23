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
Django management commands:

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
```

The named actor must possess the Django model permission required by the
underlying application service. Direct editing of lifecycle/ledger rows in
Django Admin remains prohibited.

Administrative makeup grants, coverage rebinds and one-time entitlement
administration currently remain service-level operations and are intended for a
dedicated administrative UI rather than direct model editing.

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

Lockouts are tracked independently by username **or** client IP. Production
deployment assumes one trusted reverse proxy (Caddy -> Gunicorn):

```bash
AXES_IPWARE_PROXY_COUNT=1
AXES_IPWARE_PROXY_ORDER=left-most
```

Axes checks `HTTP_X_FORWARDED_FOR` before `REMOTE_ADDR`. The reverse proxy
must strip or overwrite any client-supplied `X-Forwarded-For` value before
forwarding the request; otherwise clients could spoof addresses and bypass IP
rate limiting. Direct development/Codespaces should use
`AXES_IPWARE_PROXY_COUNT=0`.

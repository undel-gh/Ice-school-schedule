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

python manage.py publish_daily_schedule --date 2026-09-23

python manage.py evaluate_lesson --lesson <lesson-uuid>

python manage.py complete_lesson --lesson <lesson-uuid>

python manage.py process_subscription_lifecycle --date 2026-09-23
```

These commands call the same application services used by the web/admin layer.

Administrative operations that still require a dedicated UI/command in a
follow-up slice include issuing/cancelling subscriptions, confirming/cancelling
or rescheduling lessons, administrative makeup grants, coverage rebinds, and
medical justification verification/revocation. They must not be performed by
editing lifecycle/ledger rows directly in Django Admin.

## Production checks

Production defaults are closed: `DJANGO_DEBUG` defaults to off and
`DJANGO_SECRET_KEY` is mandatory outside debug mode.

Before deployment run:

```bash
DJANGO_DEBUG=0 \
DJANGO_SECRET_KEY='<strong-secret>' \
DJANGO_ALLOWED_HOSTS='school.example' \
python manage.py check --deploy
```

Login attempts are rate-limited with `django-axes`; the default failure limit
is controlled by `AXES_FAILURE_LIMIT` (5 by default).

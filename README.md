# Ice school schedule

Django/PostgreSQL application for schedule publication, RSVP, coach-confirmed attendance and entitlement accounting for a figure skating school.

## Development bootstrap

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python manage.py makemigrations
python manage.py migrate
python manage.py check
pytest
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

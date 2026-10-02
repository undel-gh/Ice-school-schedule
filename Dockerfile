FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app app

# Keep dependency installation cacheable across ordinary source-code changes.
# pyproject.toml remains the single source of truth for both build and runtime
# requirements; the application itself is installed after the source copy.
COPY pyproject.toml /app/pyproject.toml

RUN python -c "import pathlib, subprocess, sys, tomllib; data=tomllib.loads(pathlib.Path('/app/pyproject.toml').read_text()); requirements=[*data['build-system']['requires'], *data['project']['dependencies']]; subprocess.check_call([sys.executable, '-m', 'pip', 'install', *requirements])"

COPY . /app

RUN python -m pip install --no-deps --no-build-isolation . \
    && DJANGO_DEBUG=1 \
       DJANGO_STATICFILES_MANIFEST=1 \
       DJANGO_SECRET_KEY=build-only-static-collection \
       python manage.py collectstatic --noinput \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["gunicorn", "ice_school.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30", "--error-logfile", "-"]

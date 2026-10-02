FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app app

COPY . /app

RUN python -m pip install . \
    && DJANGO_DEBUG=1 \
       DJANGO_STATICFILES_MANIFEST=1 \
       DJANGO_SECRET_KEY=build-only-static-collection \
       python manage.py collectstatic --noinput \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["gunicorn", "ice_school.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30", "--error-logfile", "-"]

from unittest.mock import patch

import pytest
from django.db import DatabaseError
from django.urls import reverse


@pytest.mark.django_db
def test_healthz_reports_application_and_database_ready(client):
    response = client.get(reverse("healthz"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response["Cache-Control"].startswith("no-store")


@pytest.mark.django_db
def test_healthz_returns_503_when_database_probe_fails(client):
    with patch(
        "core.views.connection.cursor",
        side_effect=DatabaseError("database unavailable"),
    ):
        response = client.get(reverse("healthz"))

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy"}


@pytest.mark.django_db
def test_healthz_rejects_post(client):
    response = client.post(reverse("healthz"))

    assert response.status_code == 405

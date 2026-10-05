import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.views.defaults import server_error

User = get_user_model()


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_production_404_page_is_russian(client):
    response = client.get("/__definitely_missing__/not-found/")
    body = response.content.decode()

    assert response.status_code == 404
    assert "Страница не найдена" in body
    assert "Not Found" not in body
    assert "The requested resource was not found" not in body


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_production_403_page_is_russian(client):
    user = User.objects.create_user(
        username="error-page-user",
        password="test-password",
    )
    client.force_login(user)

    response = client.get(reverse("audit_manager:events"))
    body = response.content.decode()

    assert response.status_code == 403
    assert "Доступ запрещён" in body
    assert "403 Forbidden" not in body


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_production_500_page_is_russian_and_database_independent(
    django_assert_num_queries,
):
    request = RequestFactory().get("/__server_error__/")

    with django_assert_num_queries(0):
        response = server_error(request)
    body = response.content.decode()

    assert response.status_code == 500
    assert "Ошибка сервера" in body
    assert "Server Error" not in body

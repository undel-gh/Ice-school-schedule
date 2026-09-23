import pytest
from axes.helpers import get_client_ip_address
from django.conf import settings
from django.test import RequestFactory
from django.urls import reverse


def test_axes_proxy_ip_uses_x_real_ip_and_ignores_x_forwarded_for():
    request = RequestFactory().get(
        "/accounts/login/",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_REAL_IP="203.0.113.7",
        HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.7",
    )

    assert get_client_ip_address(request) == "203.0.113.7"


def test_axes_direct_request_falls_back_to_remote_addr():
    request = RequestFactory().get(
        "/accounts/login/",
        REMOTE_ADDR="203.0.113.7",
        HTTP_X_FORWARDED_FOR="1.2.3.4",
    )

    assert get_client_ip_address(request) == "203.0.113.7"


def test_axes_lockout_parameters_prevent_username_only_dos():
    assert settings.AXES_LOCKOUT_PARAMETERS == [
        ["username", "ip_address"],
        "ip_address",
    ]


@pytest.mark.django_db
def test_login_without_next_redirects_home(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="login-redirect",
        password="secret-password",
    )
    response = client.post(
        reverse("login"),
        {"username": user.username, "password": "secret-password"},
    )

    assert response.status_code == 302
    assert response.url == reverse("scheduling:home")


@pytest.mark.django_db
def test_logout_is_post(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="logout-user",
        password="secret-password",
    )
    client.force_login(user)

    get_response = client.get(reverse("logout"))
    assert get_response.status_code == 405

    post_response = client.post(reverse("logout"))
    assert post_response.status_code == 302
    assert post_response.url == reverse("login")

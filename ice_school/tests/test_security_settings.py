import pytest
from axes.helpers import get_client_ip_address, get_client_parameters
from django.conf import settings
from django.core.checks import run_checks, Tags
from django.test import RequestFactory, override_settings
from django.urls import reverse


@override_settings(TRUSTED_PROXY_IPS=("127.0.0.1",))
def test_axes_trusted_proxy_uses_x_real_ip_and_ignores_x_forwarded_for():
    request = RequestFactory().get(
        "/accounts/login/",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_REAL_IP="203.0.113.7",
        HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.7",
    )

    assert get_client_ip_address(request) == "203.0.113.7"


@override_settings(TRUSTED_PROXY_IPS=())
def test_axes_direct_request_ignores_spoofed_x_real_ip():
    request = RequestFactory().get(
        "/accounts/login/",
        REMOTE_ADDR="203.0.113.7",
        HTTP_X_REAL_IP="1.2.3.4",
        HTTP_X_FORWARDED_FOR="9.9.9.9",
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



@override_settings(TRUSTED_PROXY_IPS=("127.0.0.1",))
def test_axes_builds_combined_username_ip_and_ip_only_filters():
    request = RequestFactory().get(
        "/accounts/login/",
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_REAL_IP="203.0.113.7",
    )

    parameters = get_client_parameters(
        "victim",
        "203.0.113.7",
        "test-agent",
        request,
    )

    assert {
        "username": "victim",
        "ip_address": "203.0.113.7",
    } in parameters
    assert {"ip_address": "203.0.113.7"} in parameters
    assert {"username": "victim"} not in parameters



@override_settings(TRUSTED_PROXY_IPS=("172.18.0.0/16",))
def test_axes_trusted_proxy_supports_cidr():
    request = RequestFactory().get(
        "/accounts/login/",
        REMOTE_ADDR="172.18.0.5",
        HTTP_X_REAL_IP="203.0.113.7",
    )

    assert get_client_ip_address(request) == "203.0.113.7"


@override_settings(TRUSTED_PROXY_IPS=("::ffff:10.0.0.1",))
def test_axes_trusted_proxy_normalizes_ipv6_addresses():
    request = RequestFactory().get(
        "/accounts/login/",
        REMOTE_ADDR="::ffff:a00:1",
        HTTP_X_REAL_IP="2001:db8::7",
    )

    assert get_client_ip_address(request) == "2001:db8::7"


@override_settings(DEBUG=False, TRUSTED_PROXY_IPS=())
def test_production_check_warns_when_trusted_proxy_list_is_empty():
    warnings = run_checks(tags=[Tags.security])

    assert any(item.id == "ice_school.W002" for item in warnings)


@override_settings(
    DEBUG=False,
    TRUSTED_PROXY_IPS=("172.18.0.0/16", "not-a-network"),
)
def test_production_check_warns_about_invalid_trusted_proxy_entry():
    warnings = run_checks(tags=[Tags.security])

    assert any(item.id == "ice_school.W001" for item in warnings)



@override_settings(TRUSTED_PROXY_IPS=("10.0.0.0/8",))
def test_axes_trusted_proxy_matches_ipv4_mapped_remote_addr():
    request = RequestFactory().get(
        "/accounts/login/",
        REMOTE_ADDR="::ffff:10.0.0.5",
        HTTP_X_REAL_IP="203.0.113.7",
    )

    assert get_client_ip_address(request) == "203.0.113.7"

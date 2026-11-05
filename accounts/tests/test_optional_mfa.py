from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.external_auth import ExternalProfile
from accounts.mfa import MFA_LOGIN_METHOD_SESSION_KEY, MFA_PREAUTH_SESSION_KEY
from accounts.models import ExternalIdentity
from audit.models import AuditEvent


User = get_user_model()
PASSWORD = "secret-password"


def _token(device):
    totp = TOTP(
        device.bin_key,
        device.step,
        device.t0,
        device.digits,
        device.drift,
    )
    return str(totp.token()).zfill(device.digits)


def _user(name, *, totp=False):
    user = User.objects.create_user(username=name, password=PASSWORD)
    device = (
        TOTPDevice.objects.create(
            user=user,
            name="Authenticator",
            confirmed=True,
        )
        if totp else None
    )
    return user, device


def _login(client, user, next_url=None):
    data = {"username": user.username, "password": PASSWORD}
    if next_url:
        data["next"] = next_url
    return client.post(reverse("login"), data)


def _challenge(client, device, token=None):
    return client.post(
        reverse("mfa:challenge"),
        {
            "otp_device": device.persistent_id,
            "otp_token": token if token is not None else _token(device),
        },
    )


@pytest.mark.django_db
def test_ordinary_password_without_totp_stays_password_only():
    client = Client()
    user, _ = _user("optional-plain")

    response = _login(client, user)

    assert response.url == reverse("scheduling:home")
    assert str(client.session["_auth_user_id"]) == str(user.pk)
    assert client.session[MFA_LOGIN_METHOD_SESSION_KEY] == "local"
    assert MFA_PREAUTH_SESSION_KEY not in client.session
    page = client.get(reverse("mfa:security"))
    assert page.status_code == 200
    assert "защита входа по паролю выключена" in page.content.decode()


@pytest.mark.django_db
def test_ordinary_password_with_totp_needs_challenge_and_keeps_safe_next():
    client = Client()
    user, device = _user("optional-mfa-login", totp=True)
    target = reverse("mfa:security")

    password = _login(client, user, next_url=target)

    assert password.url == reverse("mfa:challenge")
    assert "_auth_user_id" not in client.session
    invalid = _challenge(client, device, token="000000")
    assert invalid.status_code == 200
    assert "_auth_user_id" not in client.session

    accepted = _challenge(client, device)
    assert accepted.url == target
    assert str(client.session["_auth_user_id"]) == str(user.id)
    assert client.session[DEVICE_ID_SESSION_KEY] == device.persistent_id
    assert client.session[MFA_LOGIN_METHOD_SESSION_KEY] == "local"
    assert client.get(target).status_code == 200


@pytest.mark.django_db
def test_ordinary_user_can_enable_totp_and_view_one_time_recovery_codes():
    client = Client()
    user, _ = _user("optional-enroll")
    _login(client, user)

    invalid = client.post(
        reverse("mfa:optional_enable"),
        {"password": "wrong-password"},
    )
    assert invalid.status_code == 400
    assert client.get(reverse("mfa:setup")).url == reverse("mfa:security")

    started = client.post(
        reverse("mfa:optional_enable"),
        {"password": PASSWORD},
    )
    assert started.url == reverse("mfa:setup")
    assert client.get(reverse("mfa:setup")).status_code == 200
    assert client.get(reverse("mfa:setup_qr")).status_code == 200

    device = TOTPDevice.objects.get(user=user, confirmed=False)
    enrolled = client.post(
        reverse("mfa:setup"),
        {"token": _token(device)},
    )
    assert enrolled.url == reverse("mfa:recovery_codes")
    device.refresh_from_db()
    assert device.confirmed is True
    assert client.session[DEVICE_ID_SESSION_KEY] == device.persistent_id
    assert client.session[MFA_LOGIN_METHOD_SESSION_KEY] == "local"
    codes = list(
        StaticToken.objects.filter(device__user=user).values_list(
            "token", flat=True
        )
    )
    assert len(codes) == 10
    page = client.get(reverse("mfa:recovery_codes"))
    assert page.status_code == 200
    assert all(code in page.content.decode() for code in codes)
    assert client.get(reverse("mfa:recovery_codes")).url == reverse(
        "scheduling:home"
    )
    assert AuditEvent.objects.filter(
        event_type="MFAEnrolled", aggregate_id=user.id
    ).exists()

    client.post(reverse("logout"))
    assert "_auth_user_id" not in client.session
    password = _login(client, user)
    assert password.url == reverse("mfa:challenge")
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_ordinary_recovery_code_completes_password_mfa_and_is_consumed():
    client = Client()
    user, _ = _user("optional-recovery", totp=True)
    recovery = StaticDevice.objects.create(
        user=user, name="Recovery codes", confirmed=True
    )
    code = StaticToken.objects.create(
        device=recovery, token=StaticToken.random_token()
    )
    assert _login(client, user).url == reverse("mfa:challenge")

    result = _challenge(client, recovery, token=code.token)

    assert result.url == reverse("scheduling:home")
    assert not StaticToken.objects.filter(pk=code.pk).exists()
    assert client.session[DEVICE_ID_SESSION_KEY] == recovery.persistent_id


@pytest.mark.django_db
def test_ordinary_disable_requires_password_and_a_fresh_otp():
    client = Client()
    user, device = _user("optional-disable", totp=True)
    recovery = StaticDevice.objects.create(
        user=user, name="Recovery codes", confirmed=True
    )
    code = StaticToken.objects.create(
        device=recovery, token=StaticToken.random_token()
    )
    _login(client, user)
    _challenge(client, device)

    wrong_password = client.post(
        reverse("mfa:optional_disable"),
        {
            "password": "wrong",
            "otp_device": recovery.persistent_id,
            "otp_token": code.token,
        },
    )
    assert wrong_password.status_code == 400
    assert StaticToken.objects.filter(pk=code.pk).exists()

    wrong_token = client.post(
        reverse("mfa:optional_disable"),
        {
            "password": PASSWORD,
            "otp_device": recovery.persistent_id,
            "otp_token": "invalid",
        },
    )
    assert wrong_token.status_code == 400
    assert TOTPDevice.objects.filter(pk=device.pk).exists()

    disabled = client.post(
        reverse("mfa:optional_disable"),
        {
            "password": PASSWORD,
            "otp_device": recovery.persistent_id,
            "otp_token": code.token,
        },
    )
    assert disabled.url == reverse("mfa:security")
    assert not TOTPDevice.objects.filter(user=user).exists()
    assert not StaticDevice.objects.filter(user=user).exists()
    assert DEVICE_ID_SESSION_KEY not in client.session
    assert AuditEvent.objects.filter(
        event_type="MFAOptionalDisabled", aggregate_id=user.id
    ).exists()
    assert client.get(reverse("mfa:security")).status_code == 200
    client.post(reverse("logout"))
    assert _login(client, user).url == reverse("scheduling:home")


@pytest.mark.django_db
def test_ordinary_authenticated_local_session_is_upgraded_after_enabling_mfa():
    client = Client()
    user, _ = _user("optional-old-session")
    _login(client, user)
    TOTPDevice.objects.create(user=user, confirmed=True)

    result = client.get(reverse("scheduling:home"))

    assert result.status_code == 302
    assert result.url.startswith(reverse("login"))
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_oauth_with_password_and_totp_remains_single_factor_for_ordinary_user(
    settings, monkeypatch
):
    client = Client()
    settings.YANDEX_OAUTH_CLIENT_ID = "test-yandex"
    user, device = _user("optional-oauth", totp=True)
    ExternalIdentity.objects.create(
        user=user,
        provider=ExternalIdentity.Provider.YANDEX,
        provider_subject="optional-external-id",
    )
    client.get(
        reverse("external_auth:login", kwargs={"provider": "yandex"})
    )
    flow = client.session["external_auth_flow"]
    monkeypatch.setattr(
        "accounts.external_views.exchange_authorization_code",
        lambda **kwargs: ExternalProfile(
            provider=ExternalIdentity.Provider.YANDEX,
            subject="optional-external-id",
        ),
    )
    callback = client.get(
        reverse("external_auth:callback", kwargs={"provider": "yandex"}),
        {"code": "test-code", "state": flow["state"]},
    )

    assert callback.url == reverse("scheduling:home")
    assert client.session[MFA_LOGIN_METHOD_SESSION_KEY] == "external"
    assert client.get(reverse("scheduling:home")).status_code == 200
    assert client.get(reverse("mfa:challenge")).url == reverse("scheduling:home")
    assert str(client.session["_auth_user_id"]) == str(user.id)
    # Security controls are restricted to local password + verified MFA.
    assert client.get(reverse("mfa:security")).url.startswith(reverse("login"))
    assert client.post(
        reverse("mfa:optional_disable"),
        {"password": PASSWORD},
    ).url.startswith(reverse("login"))

    # Signing in locally from an existing OAuth session starts a fresh
    # pre-authenticated challenge, not an MFA step inside the OAuth session.
    next_url = reverse("mfa:security")
    password = _login(client, user, next_url=next_url)
    assert password.url == reverse("mfa:challenge")
    assert "_auth_user_id" not in client.session
    assert _challenge(client, device).url == next_url
    assert client.get(next_url).status_code == 200


@pytest.mark.django_db
def test_ordinary_user_can_enable_totp_from_oauth_session_with_password():
    client = Client()
    user, _ = _user("optional-oauth-enroll")
    client.force_login(user)
    session = client.session
    session[MFA_LOGIN_METHOD_SESSION_KEY] = "external"
    session.save()

    started = client.post(
        reverse("mfa:optional_enable"),
        {"password": PASSWORD},
    )

    assert started.url == reverse("mfa:setup")
    device = TOTPDevice.objects.get(user=user, confirmed=False)
    confirmed = client.post(
        reverse("mfa:setup"), {"token": _token(device)}
    )
    assert confirmed.url == reverse("mfa:recovery_codes")
    assert client.session[MFA_LOGIN_METHOD_SESSION_KEY] == "local"
    assert client.get(reverse("mfa:security")).status_code == 200


@pytest.mark.django_db
def test_optional_mfa_security_regeneration_and_replacement_reuse_existing_flow():
    client = Client()
    user, device = _user("optional-actions", totp=True)
    _login(client, user)
    _challenge(client, device)
    new_codes = client.post(
        reverse("mfa:regenerate_recovery_codes"),
        {"password": PASSWORD},
    )
    assert new_codes.url == reverse("mfa:recovery_codes")
    assert StaticToken.objects.filter(device__user=user).count() == 10
    client.get(reverse("mfa:recovery_codes"))

    replacement = client.post(
        reverse("mfa:replace_authenticator"),
        {"password": PASSWORD},
    )
    assert replacement.url == reverse("mfa:setup")
    assert TOTPDevice.objects.filter(pk=device.pk, confirmed=True).exists()
    assert TOTPDevice.objects.filter(user=user, confirmed=False).count() == 1


@pytest.mark.django_db
def test_privileged_account_cannot_disable_required_mfa():
    client = Client()
    user = User.objects.create_superuser(
        username="optional-privileged", password=PASSWORD
    )
    client.force_login(user)
    response = client.post(
        reverse("mfa:optional_disable"),
        {"password": PASSWORD},
    )
    assert response.url == reverse("mfa:security")


@pytest.mark.django_db
def test_manager_permission_does_not_allow_optional_mfa_bypass_through_oauth_marker():
    client = Client()
    user, _ = _user("optional-promoted")
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="accounts",
            codename="add_student",
        )
    )
    client.force_login(user)
    session = client.session
    session[MFA_LOGIN_METHOD_SESSION_KEY] = "external"
    session.save()

    response = client.get(reverse("scheduling:home"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))
    assert "_auth_user_id" not in client.session

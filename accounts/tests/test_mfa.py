from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.mfa import MFA_PREAUTH_SESSION_KEY
from audit.models import AuditEvent


User = get_user_model()


def _totp_token(device: TOTPDevice) -> str:
    generator = TOTP(
        device.bin_key,
        device.step,
        device.t0,
        device.digits,
        device.drift,
    )
    return str(generator.token()).zfill(device.digits)


def _privileged_user(*, username="mfa-manager", password="secret-password"):
    return User.objects.create_superuser(
        username=username,
        password=password,
    )


def _begin_password_login(client, user, password="secret-password", next_url=None):
    data = {
        "username": user.username,
        "password": password,
    }
    if next_url is not None:
        data["next"] = next_url
    return client.post(reverse("login"), data)


@pytest.mark.django_db
def test_nonprivileged_local_password_login_does_not_require_mfa():
    client = Client()
    user = User.objects.create_user(
        username="ordinary-local",
        password="secret-password",
    )

    response = _begin_password_login(client, user)

    assert response.status_code == 302
    assert response.url == reverse("scheduling:home")
    assert str(client.session["_auth_user_id"]) == str(user.id)
    assert MFA_PREAUTH_SESSION_KEY not in client.session


@pytest.mark.django_db
def test_privileged_password_login_requires_enrollment_before_auth_session():
    client = Client()
    user = _privileged_user()

    response = _begin_password_login(client, user)

    assert response.status_code == 302
    assert response.url == reverse("mfa:setup")
    assert "_auth_user_id" not in client.session
    assert client.session[MFA_PREAUTH_SESSION_KEY]["user_id"] == str(user.id)


@pytest.mark.django_db
def test_privileged_enrollment_confirms_totp_and_issues_one_time_recovery_codes():
    client = Client()
    user = _privileged_user()
    _begin_password_login(client, user)

    setup = client.get(reverse("mfa:setup"))
    assert setup.status_code == 200
    assert setup["Cache-Control"].startswith("no-store")
    device = TOTPDevice.objects.get(user=user, confirmed=False)

    qr = client.get(reverse("mfa:setup_qr"))
    assert qr.status_code == 200
    assert qr["Content-Type"] == "image/svg+xml"
    assert qr["Cache-Control"].startswith("no-store")

    confirmed = client.post(
        reverse("mfa:setup"),
        {"token": _totp_token(device)},
    )
    assert confirmed.status_code == 302
    assert confirmed.url == reverse("mfa:recovery_codes")

    device.refresh_from_db()
    assert device.confirmed is True
    recovery_device = StaticDevice.objects.get(
        user=user,
        name="Recovery codes",
        confirmed=True,
    )
    codes = list(
        recovery_device.token_set.order_by("id").values_list(
            "token",
            flat=True,
        )
    )
    assert len(codes) == 10
    assert str(client.session["_auth_user_id"]) == str(user.id)
    assert client.session[DEVICE_ID_SESSION_KEY] == device.persistent_id
    assert 0 < client.session.get_expiry_age() <= 43200
    assert AuditEvent.objects.filter(
        event_type="MFAEnrolled",
        aggregate_id=user.id,
    ).exists()

    page = client.get(reverse("mfa:recovery_codes"))
    body = page.content.decode()
    assert page.status_code == 200
    assert page["Cache-Control"].startswith("no-store")
    for code in codes:
        assert code in body

    repeated = client.get(reverse("mfa:recovery_codes"))
    assert repeated.status_code == 302
    assert repeated.url == reverse("scheduling:home")


@pytest.mark.django_db
def test_privileged_login_with_confirmed_totp_requires_challenge():
    client = Client()
    user = _privileged_user()
    device = TOTPDevice.objects.create(
        user=user,
        name="Authenticator",
        confirmed=True,
    )

    password = _begin_password_login(client, user)
    assert password.status_code == 302
    assert password.url == reverse("mfa:challenge")
    assert "_auth_user_id" not in client.session

    challenge = client.post(
        reverse("mfa:challenge"),
        {
            "otp_device": device.persistent_id,
            "otp_token": _totp_token(device),
        },
    )

    assert challenge.status_code == 302
    assert challenge.url == reverse("scheduling:home")
    assert str(client.session["_auth_user_id"]) == str(user.id)
    assert client.session[DEVICE_ID_SESSION_KEY] == device.persistent_id
    assert AuditEvent.objects.filter(
        event_type="MFAAuthenticated",
        aggregate_id=user.id,
    ).exists()


@pytest.mark.django_db
def test_recovery_code_can_complete_login_and_is_consumed():
    client = Client()
    user = _privileged_user()
    TOTPDevice.objects.create(
        user=user,
        name="Authenticator",
        confirmed=True,
    )
    static_device = StaticDevice.objects.create(
        user=user,
        name="Recovery codes",
        confirmed=True,
    )
    token = StaticToken.objects.create(
        device=static_device,
        token=StaticToken.random_token(),
    )

    _begin_password_login(client, user)
    response = client.post(
        reverse("mfa:challenge"),
        {
            "otp_device": static_device.persistent_id,
            "otp_token": token.token,
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("scheduling:home")
    assert StaticToken.objects.filter(pk=token.pk).exists() is False
    assert AuditEvent.objects.filter(
        event_type="MFARecoveryCodeUsed",
        aggregate_id=user.id,
    ).exists()


@pytest.mark.django_db
def test_existing_privileged_password_session_is_intercepted_until_verified():
    client = Client()
    user = _privileged_user()
    device = TOTPDevice.objects.create(
        user=user,
        name="Authenticator",
        confirmed=True,
    )
    client.force_login(user)

    blocked = client.get(reverse("subscriptions:manager_operations"))

    assert blocked.status_code == 302
    assert blocked.url == reverse("mfa:challenge")

    verified = client.post(
        reverse("mfa:challenge"),
        {
            "otp_device": device.persistent_id,
            "otp_token": _totp_token(device),
        },
    )
    assert verified.status_code == 302
    assert verified.url == reverse("subscriptions:manager_operations")

    manager_page = client.get(reverse("subscriptions:manager_operations"))
    assert manager_page.status_code == 200


@pytest.mark.django_db
def test_existing_privileged_session_without_totp_is_forced_to_setup():
    client = Client()
    user = _privileged_user()
    client.force_login(user)

    response = client.get(reverse("subscriptions:manager_operations"))

    assert response.status_code == 302
    assert response.url == reverse("mfa:setup")


@pytest.mark.django_db
def test_admin_is_unavailable_to_unverified_privileged_session():
    client = Client()
    user = _privileged_user()
    TOTPDevice.objects.create(
        user=user,
        name="Authenticator",
        confirmed=True,
    )
    client.force_login(user)

    response = client.get(reverse("admin:index"))

    assert response.status_code == 302
    assert response.url == reverse("mfa:challenge")


@pytest.mark.django_db
def test_manager_permission_assignment_requires_mfa_even_without_staff_flag():
    client = Client()
    user = User.objects.create_user(
        username="scoped-manager-mfa",
        password="secret-password",
    )
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="accounts",
            codename="view_student",
        )
    )

    response = _begin_password_login(client, user)

    assert response.status_code == 302
    assert response.url == reverse("mfa:setup")
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_mfa_preserves_safe_next_destination():
    client = Client()
    user = _privileged_user()
    device = TOTPDevice.objects.create(
        user=user,
        name="Authenticator",
        confirmed=True,
    )
    target = reverse("subscriptions:manager_operations")

    password = _begin_password_login(client, user, next_url=target)
    assert password.url == reverse("mfa:challenge")

    verified = client.post(
        reverse("mfa:challenge"),
        {
            "otp_device": device.persistent_id,
            "otp_token": _totp_token(device),
        },
    )

    assert verified.status_code == 302
    assert verified.url == target


@pytest.mark.django_db
@override_settings(MFA_PREAUTH_TTL_SECONDS=60)
def test_expired_password_preauth_cannot_be_used_for_mfa_setup():
    client = Client()
    user = _privileged_user()
    _begin_password_login(client, user)
    session = client.session
    preauth = session[MFA_PREAUTH_SESSION_KEY]
    preauth["issued_at"] = (
        timezone.now() - timedelta(seconds=61)
    ).timestamp()
    session[MFA_PREAUTH_SESSION_KEY] = preauth
    session.save()

    response = client.get(reverse("mfa:setup"))

    assert response.status_code == 302
    assert response.url == reverse("login")
    assert MFA_PREAUTH_SESSION_KEY not in client.session


@pytest.mark.django_db
def test_invalid_setup_token_does_not_confirm_device():
    client = Client()
    user = _privileged_user()
    _begin_password_login(client, user)
    client.get(reverse("mfa:setup"))
    device = TOTPDevice.objects.get(user=user, confirmed=False)

    response = client.post(
        reverse("mfa:setup"),
        {"token": "000000"},
    )

    assert response.status_code == 200
    device.refresh_from_db()
    assert device.confirmed is False
    assert "_auth_user_id" not in client.session



@pytest.mark.django_db
def test_static_break_glass_without_totp_forces_fresh_totp_enrollment():
    client = Client()
    user = _privileged_user(username="mfa-break-glass")
    static_device = StaticDevice.objects.create(
        user=user,
        name="Emergency token",
        confirmed=True,
    )
    token = StaticToken.objects.create(
        device=static_device,
        token=StaticToken.random_token(),
    )

    password = _begin_password_login(client, user)
    assert password.status_code == 302
    assert password.url == reverse("mfa:challenge")

    challenge = client.post(
        reverse("mfa:challenge"),
        {
            "otp_device": static_device.persistent_id,
            "otp_token": token.token,
        },
    )

    assert challenge.status_code == 302
    assert challenge.url == reverse("mfa:setup")
    assert str(client.session["_auth_user_id"]) == str(user.id)
    assert client.session[DEVICE_ID_SESSION_KEY] == static_device.persistent_id
    assert StaticToken.objects.filter(pk=token.pk).exists() is False

    setup = client.get(reverse("mfa:setup"))
    assert setup.status_code == 200
    assert TOTPDevice.objects.filter(
        user=user,
        confirmed=False,
    ).exists()


@pytest.mark.django_db
def test_verified_privileged_user_can_regenerate_recovery_codes(client):
    user = _privileged_user(username="mfa-regenerate")
    client.force_login(user)
    old_device = StaticDevice.objects.create(
        user=user,
        name="Recovery codes",
        confirmed=True,
    )
    old_token = StaticToken.objects.create(
        device=old_device,
        token=StaticToken.random_token(),
    )

    page = client.get(reverse("mfa:security"))
    assert page.status_code == 200
    assert "1" in page.content.decode()
    assert page["Cache-Control"].startswith("no-store")

    response = client.post(
        reverse("mfa:regenerate_recovery_codes"),
        {"password": "secret-password"},
    )

    assert response.status_code == 302
    assert response.url == reverse("mfa:recovery_codes")
    assert StaticToken.objects.filter(pk=old_token.pk).exists() is False
    assert StaticToken.objects.filter(
        device__user=user,
        device__confirmed=True,
    ).count() == 10
    assert AuditEvent.objects.filter(
        event_type="MFARecoveryCodesRegenerated",
        aggregate_id=user.id,
    ).exists()


@pytest.mark.django_db
def test_recovery_code_regeneration_requires_current_password(client):
    user = _privileged_user(username="mfa-regenerate-wrong-password")
    client.force_login(user)
    recovery_device = StaticDevice.objects.create(
        user=user,
        name="Recovery codes",
        confirmed=True,
    )
    token = StaticToken.objects.create(
        device=recovery_device,
        token=StaticToken.random_token(),
    )

    response = client.post(
        reverse("mfa:regenerate_recovery_codes"),
        {"password": "wrong-password"},
    )

    assert response.status_code == 400
    assert StaticToken.objects.filter(pk=token.pk).exists()
    assert AuditEvent.objects.filter(
        event_type="MFARecoveryCodesRegenerated",
        aggregate_id=user.id,
    ).exists() is False


@pytest.mark.django_db
def test_verified_privileged_user_can_replace_authenticator(client):
    user = _privileged_user(username="mfa-replace")
    client.force_login(user)
    old_device = TOTPDevice.objects.get(user=user, confirmed=True)
    recovery_device = StaticDevice.objects.create(
        user=user,
        name="Recovery codes",
        confirmed=True,
    )
    recovery_token = StaticToken.objects.create(
        device=recovery_device,
        token=StaticToken.random_token(),
    )

    response = client.post(
        reverse("mfa:replace_authenticator"),
        {"password": "secret-password"},
    )

    assert response.status_code == 302
    assert response.url == reverse("mfa:setup")
    assert TOTPDevice.objects.filter(pk=old_device.pk).exists() is False
    assert StaticToken.objects.filter(pk=recovery_token.pk).exists()
    assert DEVICE_ID_SESSION_KEY not in client.session
    assert AuditEvent.objects.filter(
        event_type="MFAAuthenticatorReplacementStarted",
        aggregate_id=user.id,
    ).exists()

    setup = client.get(reverse("mfa:setup"))
    assert setup.status_code == 200
    assert TOTPDevice.objects.filter(
        user=user,
        confirmed=False,
    ).count() == 1


@pytest.mark.django_db
def test_authenticator_replacement_requires_current_password(client):
    user = _privileged_user(username="mfa-replace-wrong-password")
    client.force_login(user)
    old_device = TOTPDevice.objects.get(user=user, confirmed=True)

    response = client.post(
        reverse("mfa:replace_authenticator"),
        {"password": "wrong-password"},
    )

    assert response.status_code == 400
    assert TOTPDevice.objects.filter(pk=old_device.pk).exists()
    assert client.session[DEVICE_ID_SESSION_KEY] == old_device.persistent_id


@pytest.mark.django_db
def test_mfa_security_mutations_are_post_only(client):
    user = _privileged_user(username="mfa-post-only")
    client.force_login(user)

    regenerate = client.get(reverse("mfa:regenerate_recovery_codes"))
    replace = client.get(reverse("mfa:replace_authenticator"))

    assert regenerate.status_code == 405
    assert replace.status_code == 405

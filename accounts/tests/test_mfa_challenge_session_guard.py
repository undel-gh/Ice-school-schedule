from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.mfa import MFA_VERIFIED_AT_SESSION_KEY


User = get_user_model()


@pytest.mark.django_db
def test_nonprivileged_authenticated_user_keeps_session_on_mfa_challenge():
    client = Client()
    user = User.objects.create_user(
        username="ordinary-challenge-user",
        password="secret-password",
    )
    client.force_login(user)
    session = client.session
    session["mfa_challenge_guard_sentinel"] = "keep"
    session.save()

    response = client.get(reverse("mfa:challenge"))

    assert response.status_code == 302
    assert response.url == reverse("scheduling:home")
    assert str(client.session["_auth_user_id"]) == str(user.id)
    assert client.session["mfa_challenge_guard_sentinel"] == "keep"


@pytest.mark.django_db
def test_nonprivileged_user_without_local_password_keeps_session_on_mfa_challenge():
    client = Client()
    user = User.objects.create_user(username="oauth-only-challenge-user")
    user.set_unusable_password()
    user.save(update_fields=["password"])
    client.force_login(user)

    response = client.get(reverse("mfa:challenge"))

    assert response.status_code == 302
    assert response.url == reverse("scheduling:home")
    assert str(client.session["_auth_user_id"]) == str(user.id)


@pytest.mark.django_db
def test_unverified_privileged_session_still_requires_fresh_login_on_mfa_challenge():
    client = Client()
    user = User.objects.create_superuser(
        username="unverified-manager-challenge",
        password="secret-password",
    )
    client.force_login(user)

    response = client.get(reverse("mfa:challenge"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
@override_settings(MFA_PRIVILEGED_SESSION_MAX_AGE_SECONDS=60)
def test_stale_verified_privileged_session_still_requires_fresh_login():
    client = Client()
    user = User.objects.create_superuser(
        username="stale-manager-challenge",
        password="secret-password",
    )
    device = TOTPDevice.objects.create(
        user=user,
        name="Authenticator",
        confirmed=True,
    )
    client.force_login(user)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session[MFA_VERIFIED_AT_SESSION_KEY] = (
        timezone.now() - timedelta(seconds=61)
    ).timestamp()
    session.save()

    response = client.get(reverse("mfa:challenge"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))
    assert "_auth_user_id" not in client.session

import pytest
from django.test import Client
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.mfa import (
    MFA_VERIFIED_AT_SESSION_KEY,
    mfa_required_for_user,
)


@pytest.fixture(scope="session", autouse=True)
def _test_static_root(tmp_path_factory):
    """Give WhiteNoise an existing STATIC_ROOT during request-based tests."""

    from django.conf import settings

    settings.STATIC_ROOT = tmp_path_factory.mktemp("staticfiles")


class MFAAwareClient(Client):
    """
    Existing view tests use force_login() to skip authentication itself.

    Keep that meaning after privileged MFA enforcement by marking privileged
    force-login sessions as OTP-verified. MFA-specific tests use a plain
    django.test.Client instead.
    """

    def force_login(self, user, backend=None):
        super().force_login(user, backend=backend)
        if not mfa_required_for_user(user):
            return
        device = (
            TOTPDevice.objects.filter(user=user, confirmed=True)
            .order_by("created_at", "id")
            .first()
        )
        if device is None:
            device = TOTPDevice.objects.create(
                user=user,
                name="Test authenticator",
                confirmed=True,
            )
        session = self.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        from django.utils import timezone

        session[MFA_VERIFIED_AT_SESSION_KEY] = timezone.now().timestamp()
        session.save()


@pytest.fixture
def client():
    return MFAAwareClient()

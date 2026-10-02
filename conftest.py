import pytest
from django.test import Client
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.mfa import mfa_required_for_user


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
        session.save()


@pytest.fixture
def client():
    return MFAAwareClient()

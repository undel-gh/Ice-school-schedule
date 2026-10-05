import pytest
from django import forms
from django.contrib.auth import get_user_model
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.mfa_forms import LocalizedOTPTokenForm

User = get_user_model()


@pytest.mark.django_db
def test_localized_mfa_form_hides_unused_challenge_field():
    user = User.objects.create_user(
        username="mfa-hidden-challenge",
        password="test-password",
    )
    TOTPDevice.objects.create(
        user=user,
        name="Authenticator",
        confirmed=True,
    )

    form = LocalizedOTPTokenForm(user)
    html = form.as_p()

    assert isinstance(form.fields["otp_challenge"].widget, forms.HiddenInput)
    assert "Запрос к устройству" not in html
    assert "Способ проверки" in html
    assert "Одноразовый код" in html

from django import forms
from django.contrib.auth import authenticate
from django.utils.translation import ngettext_lazy
from django_otp import devices_for_user
from django_otp.forms import OTPTokenForm
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice


class MFASetupTokenForm(forms.Form):
    token = forms.CharField(
        label="Код из приложения",
        min_length=6,
        max_length=8,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
            }
        ),
    )

    def clean_token(self):
        return self.cleaned_data["token"].strip()


class MFAPasswordReauthForm(forms.Form):
    password = forms.CharField(
        label="Текущий пароль",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password"}
        ),
    )

    def __init__(self, user, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.request = request

    def clean_password(self):
        password = self.cleaned_data["password"]
        authenticated = authenticate(
            self.request,
            username=self.user.get_username(),
            password=password,
        )
        if authenticated is None or authenticated.pk != self.user.pk:
            raise forms.ValidationError("Неверный текущий пароль.")
        return password


class LocalizedOTPTokenForm(OTPTokenForm):
    """Russian UI wrapper around django-otp's token form."""

    otp_error_messages = dict(
        OTPTokenForm.otp_error_messages,
        token_required="Введите одноразовый код.",
        challenge_exception="Не удалось получить проверочный запрос устройства: {0}",
        not_interactive="Выбранное устройство не поддерживает проверочный запрос.",
        challenge_message="Проверочный запрос: {0}",
        invalid_token="Неверный код. Проверьте его и попробуйте ещё раз.",
        n_failed_attempts=ngettext_lazy(
            "Проверка временно заблокирована после %(failure_count)d неудачной попытки. Попробуйте позже.",
            "Проверка временно заблокирована после %(failure_count)d неудачных попыток. Попробуйте позже.",
            "failure_count",
        ),
        verification_not_allowed="Проверка кода временно недоступна.",
        device_required="Выберите способ проверки.",
    )

    def __init__(self, user, request=None, *args, **kwargs):
        super().__init__(user, request=request, *args, **kwargs)
        self.fields["otp_device"].label = "Способ проверки"
        self.fields["otp_token"].label = "Одноразовый код"
        self.fields["otp_challenge"].widget = forms.HiddenInput()
        self.fields["otp_token"].widget.attrs.update(
            {
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
            }
        )

    @staticmethod
    def device_choices(user):
        choices = []
        for device in devices_for_user(user):
            if isinstance(device, StaticDevice):
                label = "Резервные коды"
            elif isinstance(device, TOTPDevice):
                label = "Приложение-аутентификатор"
            else:
                label = device.name
            choices.append((device.persistent_id, label))
        return choices

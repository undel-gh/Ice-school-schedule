from django import forms
from django.contrib.auth import authenticate


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

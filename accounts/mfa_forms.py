from django import forms


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

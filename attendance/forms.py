from django import forms


class ManagerMedicalVerifyForm(forms.Form):
    valid_until = forms.DateField(
        label="Отработка действительна по",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
